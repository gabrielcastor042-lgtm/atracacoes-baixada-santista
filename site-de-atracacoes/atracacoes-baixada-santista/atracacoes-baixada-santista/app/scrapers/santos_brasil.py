"""
Santos Brasil Tecon.

GATE/DEADLINE AUTOMATIZADO (achado em 2026-08-28) — FUNCIONANDO:

A própria Santos Brasil expõe, sem login e sem bloqueio anti-bot, a API
JSON que o mooring-list dela usa internamente pro relatório "Lista de
Recebimento" (o antigo segundo arquivo, que era salvo como HTML e subido
manual):
    https://www.santosbrasil.com.br/v2021/mooring-list/pesquisa?unidade=tecon-santos&lista=recebimento-de-exportacao&atracadouro=&pesquisa=&dataInicial=&dataFinal=&statusNavio=
`fetch_gate_data()` consulta essa API direto via `requests` (sem
navegador) e devolve deadline/gate por `fonte_raw_id`, no mesmo formato
que `parse_gate_upload()` já produzia a partir do HTML. O `sync.py` roda
isso a cada 30 minutos (ver `sincronizar_gate_santos_brasil` /
`run_gate_sync`), só ATUALIZANDO navios que já existem no banco (vindos
do upload manual do arquivo principal) — não cria navio novo, porque
falta ETA/ETB pra isso, e essa API só cobre navios de exportação (que
passam por recebimento de carga antes de embarcar).

IMPORTANTE — a "Lista de Atracação" principal (ETA/ETB/ATB/ATD)
continua manual: essa não tem equivalente público, fica atrás de login
na área de cliente. `arquivo_gate` no upload manual virou OPCIONAL (só
serve de reforço/override se a API cair ou não cobrir algum navio).

SINCRONIZAÇÃO AUTOMÁTICA DA LISTA PRINCIPAL — PENDENTE:

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
não dá pra automatizar. A API de mooring-list acima também tem um valor
`lista=lista-de-atracacao`, mas retorna sempre vazio sem estar logado —
só a de recebimento (gate) é pública.

OUTRA ALTERNATIVA testada em 2026-08-28: o sistema "Janela Única
Portuária" (frmParAtracNavDados.aspx, Regiao=ST) tem uma consulta de
atracações por região, sem bloqueio anti-bot e sem login. Confirmado
funcionando (formulário ASP.NET clássico: GET pra pegar __VIEWSTATE,
POST com o período pra pesquisar — máximo 30 dias por consulta — e GET
em frmResultado.aspx na mesma sessão pra pegar o resultado). Comparado
com a Santos Brasil real: pra navios que JÁ atracaram/saíram (ATB/ATD
confirmados) os dados batem muito bem (diferença de minutos). PORÉM pra
navios PREVISTOS/futuros, os dados não são confiáveis — vários navios
diferentes apareceram com a mesma previsão de atracação idêntica
(valor genérico de cadastro, não uma previsão real atualizada), com
diferenças de até semanas comparado à Santos Brasil. Por isso essa
fonte NÃO é usada pra prever navios futuros, só serviria (se um dia for
implementada) pra confirmar ATB/ATD de navios já operados.

UPLOAD MANUAL (SEMI-AUTOMÁTICO) — FUNCIONANDO:

Enquanto a automação da lista principal não sai, o cliente exporta a
"Lista de Atracação" pela área de cliente da Santos Brasil (botão de
exportar, que baixa um arquivo .xls) e sobe esse arquivo pelo site
(endpoint POST /upload/santos_brasil, campo `arquivo_excel`). Apesar da
extensão .xls, o arquivo é na verdade uma tabela HTML (não um binário
Excel de verdade) — `parse_upload()` faz esse parsing. Estrutura
capturada em 2026-07-16, tabela com id="tabelaatracacao", cabeçalho
com atributo `data-col` (ASCII, estável, pode ou não vir dentro de
<thead>/<tr> dependendo do export) e colunas:

DEADLINE, BERTH_DIA_SEMANA, BERTH_HORARIO_INICIAL, BERTH_HORARIO_FINAL,
P_ATRACA, ID, NAVIO, VIAGEM, AGENCIA, PREVISAO_CHEGADA, CHEGADA,
PREVISAO_ATRACACAO, ATRACACAO, PREVISAO_SAIDA, SAIDA, BRC, SRV,
DIA_JANELA (datas no formato dd/mm/aaaa HH:MM). Colunas novas
desconhecidas (ex: PREV_MOV_EMB/DESC/REM, vistas num export de
2026-08-24) são ignoradas automaticamente — o parsing é por posição
entre cabeçalho e célula, não por um total fixo de colunas.

O campo `arquivo_gate` no mesmo endpoint agora é OPCIONAL — o gate já é
atualizado sozinho a cada 30 min via `fetch_gate_data()` (ver acima).
Se enviado mesmo assim, `parse_gate_upload()` faz o parsing do HTML
(vem salvo em Windows-1252, apesar do <meta charset="utf-8"> dele
mentir) — tabela simples, sem `data-col`, colunas por posição:

Número (ID), Berço (BRC), Navio, Viagem Armador, Viagem, Berth Windows,
Dia, Início, Fim, Deadline, Previsão de Chegada, Previsão Liberacao do
Dry, Previsão Liberação do Reefer, Liberacao do Dry, Liberação do Reefer
(datas no formato dd/mm/AA HH:MM — ano com 2 dígitos, diferente do
primeiro arquivo — mesmo formato que `fetch_gate_data()` recebe da API).

`merge_gate_data()` junta os dois usando o "Número (ID)" como chave —
é o mesmo código (`fonte_raw_id`) nos relatórios.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
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

    # Busca os <th> em qualquer lugar dentro da tabela — variações já
    # vistas: com <thead>, sem <thead> mas dentro de um <tr>, e (mais
    # recente) soltos direto dentro da <table>, sem nenhum <tr> ao redor.
    # Amarrar a busca a thead/tr específico já quebrou 2 vezes com
    # exports diferentes; buscar por <th> direto funciona nos 3 casos.
    headers = [
        (th.get("data-col") or "").strip().lower()
        for th in table.find_all("th")
    ]
    if not headers:
        return []

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


_GATE_API_URL = (
    "https://www.santosbrasil.com.br/v2021/mooring-list/pesquisa"
    "?unidade=tecon-santos&lista=recebimento-de-exportacao"
    "&atracadouro=&pesquisa=&dataInicial=&dataFinal=&statusNavio="
)


def fetch_gate_data() -> Dict[str, Dict[str, Any]]:
    """Consulta a API pública (sem login, achada em 2026-08-28) que a
    própria Santos Brasil usa internamente pro relatório "Lista de
    Recebimento" — mesmo dado do upload manual do arquivo_gate, só que
    direto via HTTP, sem precisar salvar/subir o HTML. Datas vêm no mesmo
    formato dd/mm/aa HH:MM do upload manual (reusa _parse_gate_date).

    Só cobre navios de exportação (que passam por recebimento de carga
    antes de embarcar) — NÃO é a Lista de Atracação principal (ETA/ETB/
    ATB/ATD), essa continua atrás de login e precisa do upload manual do
    arquivo .xls.

    Devolve {fonte_raw_id: {"deadline_carga": ..., "previsao_abertura_gate": ..., "abertura_gate": ...}}.
    """
    resp = requests.get(_GATE_API_URL, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    data = resp.json()
    if not data.get("Success"):
        return {}

    gate_por_id: Dict[str, Dict[str, Any]] = {}
    for item in data.get("VRecebimentoExportacao") or []:
        fonte_raw_id = (item.get("ID") or "").strip()
        if not fonte_raw_id:
            continue
        gate_por_id[fonte_raw_id] = {
            "deadline_carga": _parse_gate_date(item.get("DEADLINE")),
            "previsao_abertura_gate": _parse_gate_date(item.get("PREVISAO_LIBERACAO_DRY")),
            "abertura_gate": _parse_gate_date(item.get("LIBERACAO_DRY")),
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