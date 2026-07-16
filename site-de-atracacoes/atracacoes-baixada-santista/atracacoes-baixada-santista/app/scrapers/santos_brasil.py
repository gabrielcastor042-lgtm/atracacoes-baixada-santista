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
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup

from .base import TerminalScraper

_DATE_FMT = "%d/%m/%Y %H:%M"

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


class SantosBrasilScraper(TerminalScraper):
    terminal_id = "santos_brasil"

    def fetch(self) -> List[Dict[str, Any]]:
        raise NotImplementedError(
            "Aguardando credenciais da API Integra Aqui (Lista de Atracação) "
            "ou retomar a investigação da Janela Única Portuária (ver docstring). "
            "Enquanto isso, use o upload manual (POST /upload/santos_brasil)."
        )
