"""
Santos Brasil Tecon.

SINCRONIZAÇÃO AUTOMÁTICA — PENDENTE:

CAMINHO PRINCIPAL (em andamento): a Santos Brasil disponibiliza uma API
oficial e gratuita para clientes ("Santos Brasil Dev" / Integra Aqui:
https://www.santosbrasil.com.br/integraaqui/), com um produto dedicado
"Lista de Atracação" — inclusive com limite de requisição documentado.
Isso é MUITO melhor que qualquer scraping (mais estável, dentro dos
termos de uso).

O QUE FALTA:
1. Solicitar acesso à API "Lista de Atracação" no portal Integra Aqui
   (envolve CNPJ/dados da empresa — precisa ser feito por vocês; não
   posso criar contas ou me autenticar em nome de vocês).
2. Repassar as credenciais (client_id/client_secret ou API key) e a
   documentação de autenticação, e eu implemento este scraper com
   `requests` direto (sem navegador).

ALTERNATIVA explorada e descartada por ora: a página pública da Santos
Brasil (santosbrasil.com.br/v2021/lista-de-atracacao) está atrás de
proteção anti-bot Akamai (mesmo bloqueio do Ecoporto — "Access Denied"),
não dá pra automatizar.

OUTRA ALTERNATIVA testada parcialmente: o sistema oficial do governo
federal "Janela Única Portuária" tem uma consulta de atracações por
região (Regiao=ST p/ Santos), sem bloqueio anti-bot:
    https://www.janelaunicaportuaria.org.br/dte/relatorios/frmParAtracNavDados.aspx?codMenu=17&Regiao=ST
É um formulário ASP.NET clássico (não SPA) — os resultados aparecem
dentro de um <iframe name="resultado"> depois de preencher "Período"
(datas) e clicar no botão de imagem "Consultar" (#imgConsultar).
Testado em 2026-07-16 preenchendo período de hoje a +60 dias: o
formulário respondeu corretamente, mas retornou "Nenhum registro
encontrado!" — algum parâmetro de busca (tipo de pesquisa, formato de
data, ou o próprio filtro de região) provavelmente precisa de ajuste.
Vale retomar essa investigação se a API oficial demorar muito.

UPLOAD MANUAL (SEMI-AUTOMÁTICO) — FUNCIONANDO:

Enquanto a automação não sai, o cliente exporta a "Lista de Atracação"
pela área de cliente da Santos Brasil (botão de exportar, que baixa um
arquivo .xls) e sobe esse arquivo pelo site (endpoint POST /upload/
santos_brasil). Apesar da extensão .xls, o arquivo é na verdade uma
tabela HTML (não um binário Excel de verdade) — `parse_upload()` faz
esse parsing. Estrutura capturada em 2026-07-16, tabela com
id="tabelaatracacao", cabeçalho <th> com atributo `data-col` (ASCII,
estável) e colunas:

DEADLINE, BERTH_DIA_SEMANA, BERTH_HORARIO_INICIAL, BERTH_HORARIO_FINAL,
P_ATRACA, ID, NAVIO, VIAGEM, AGENCIA, PREVISAO_CHEGADA, CHEGADA,
PREVISAO_ATRACACAO, ATRACACAO, PREVISAO_SAIDA, SAIDA, BRC, SRV,
DIA_JANELA (datas no formato dd/mm/aaaa HH:MM).

Esse arquivo NÃO tem as datas de liberação de gate (Dry/Reefer) — essas
só existem num segundo relatório da Santos Brasil ("Lista de
Recebimento"), que só está disponível em PDF. O texto desse PDF não é
extraível de forma confiável (fontes sem ToUnicode CMap, texto vira
"(cid:XX)" embaralhado) — por isso pedimos pro cliente abrir esse
relatório na tela normal (sem gerar PDF) e salvar como página HTML
("Salvar como > Somente HTML"), e subir os DOIS arquivos juntos.
`parse_gate_upload()` faz o parsing desse segundo arquivo (que vem
salvo em Windows-1252, apesar do <meta charset="utf-8"> dele mentir) —
tabela simples, sem `data-col`, colunas por posição:

Número (ID), Berço (BRC), Navio, Viagem Armador, Viagem, Berth Windows,
Dia, Início, Fim, Deadline, Previsão de Chegada, Previsão Liberacao do
Dry, Previsão Liberação do Reefer, Liberacao do Dry, Liberação do Reefer
(datas no formato dd/mm/AA HH:MM — ano com 2 dígitos, diferente do
primeiro arquivo).

`merge_gate_data()` junta os dois usando o "Número (ID)" como chave —
é o mesmo código (`fonte_raw_id`) nos dois relatórios.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup

from .base import TerminalScraper

_DATE_FMT = "%d/%m/%Y %H:%M"
_GATE_DATE_FMT = "%d/%m/%y %H:%M"

# Chave = atributo `data-col` do <th> (minúsculo). "None" = coluna
# ignorada (não faz parte do schema unificado ou é redundante).
HEADER_MAP = {
    "deadline": "deadline_carga",
    "berth_dia_semana": None,
    "berth_horario_inicial": None,
    "berth_horario_final": None,
    "p_atraca": None,
    "id": "fonte_raw_id",
    "navio": "navio",
    "viagem": "viagem",
    "agencia": None,
    "previsao_chegada": "eta",
    "chegada": "ata",
    "previsao_atracacao": "etb",
    "atracacao": "atb",
    "previsao_saida": "etd",
    "saida": "atd",
    "brc": "berco",
    "srv": None,
    "dia_janela": None,
}


def _parse_date(value: str) -> Optional[datetime]:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, _DATE_FMT)
    except ValueError:
        return None


def parse_upload(content: bytes) -> List[Dict[str, Any]]:
    """Faz o parsing do arquivo .xls (na verdade HTML) exportado pela
    área de cliente da Santos Brasil."""
    html = content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="tabelaatracacao")
    if table is None:
        return []

    headers = [
        (th.get("data-col") or "").strip().lower()
        for th in table.find("thead").find_all("th")
    ]

    rows_out: List[Dict[str, Any]] = []
    for tr in table.find_all("tr"):
        if tr.find("th"):
            continue  # linha de cabeçalho
        cells = tr.find_all("td")
        if not cells or len(cells) != len(headers):
            continue

        record: Dict[str, Any] = {"terminal": "santos_brasil"}
        for header, cell in zip(headers, cells):
            field = HEADER_MAP.get(header)
            if field is None:
                continue
            text = cell.get_text(strip=True).replace("\xa0", " ").strip()
            if field == "viagem":
                # O campo vem com o código duplicado e espaços extras
                # (ex: "010E       010E"); usamos só o primeiro token.
                parts = text.split()
                record[field] = parts[0] if parts else None
            elif field in {
                "deadline_carga", "abertura_gate", "previsao_abertura_gate",
                "etb", "etd", "atb", "atd", "eta", "ata",
            }:
                record[field] = _parse_date(text)
            else:
                record[field] = re.sub(r"\s+", " ", text).strip() or None

        if record.get("navio"):
            rows_out.append(record)

    return rows_out


def _parse_gate_date(value: str) -> Optional[datetime]:
    value = (value or "").strip()
    if not value or value == "--":
        return None
    try:
        return datetime.strptime(value, _GATE_DATE_FMT)
    except ValueError:
        return None


def parse_gate_upload(content: bytes) -> Dict[str, Dict[str, Any]]:
    """Faz o parsing do relatório "Lista de Recebimento" da Santos Brasil
    (salvo como HTML pelo navegador, não a planilha principal). Colunas
    identificadas por posição (sem `data-col`):
    Número (ID), Berço, Navio, Viagem Armador, Viagem, Berth Windows,
    Dia, Início, Fim, Deadline, Previsão de Chegada, Previsão Liberacao
    do Dry, Previsão Liberação do Reefer, Liberacao do Dry, Liberação do
    Reefer.

    Devolve {fonte_raw_id: {"previsao_abertura_gate": ..., "abertura_gate": ...}}.
    """
    html = content.decode("cp1252", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        return {}

    body = table.find("tbody") or table
    gate_por_id: Dict[str, Dict[str, Any]] = {}
    for tr in body.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 14:
            continue
        textos = [c.get_text(strip=True) for c in cells]
        fonte_raw_id = textos[0]
        if not fonte_raw_id:
            continue
        gate_por_id[fonte_raw_id] = {
            "previsao_abertura_gate": _parse_gate_date(textos[11]),
            "abertura_gate": _parse_gate_date(textos[13]),
        }
    return gate_por_id


def merge_gate_data(
    records: List[Dict[str, Any]], gate_por_id: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Preenche previsao_abertura_gate/abertura_gate nos registros da
    planilha principal usando o relatório de liberação de gate, casando
    pelo "Número (ID)" — é o mesmo código (fonte_raw_id) nos dois
    relatórios."""
    for record in records:
        gate = gate_por_id.get(record.get("fonte_raw_id"))
        if not gate:
            continue
        if gate.get("previsao_abertura_gate"):
            record["previsao_abertura_gate"] = gate["previsao_abertura_gate"]
        if gate.get("abertura_gate"):
            record["abertura_gate"] = gate["abertura_gate"]
    return records


class SantosBrasilScraper(TerminalScraper):
    terminal_id = "santos_brasil"

    def fetch(self) -> List[Dict[str, Any]]:
        raise NotImplementedError(
            "Aguardando credenciais da API Integra Aqui (Lista de Atracação) "
            "ou retomar a investigação da Janela Única Portuária (ver docstring). "
            "Enquanto isso, use o upload manual (POST /upload/santos_brasil)."
        )
