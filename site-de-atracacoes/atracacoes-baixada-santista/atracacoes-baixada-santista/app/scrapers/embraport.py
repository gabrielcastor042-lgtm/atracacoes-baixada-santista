"""
DP World / Embraport.

Página: https://www.embraportonline.com.br/Navios/Escala

Fluxo pra pegar o máximo de registros (confirmado pelo cliente):
  1. Expandir o painel "Filtros" (começa recolhido).
  2. Na seção "Escala de Navios", selecionar a aba "Todos".
  3. Clicar em "Pesquisar".
  4. A lista carrega por scroll infinito — rolar até o fim repetidamente
     até o número de navios parar de crescer.

A tabela só é preenchida via JS (Knockout.js) depois desse fluxo, daí o
uso de Playwright. Estrutura capturada em 2026-07-16:

Colunas: Navio, Viagem, Visita, Armador, Serviço, Berço,
Previsão de Abertura Gate, Abertura de Gate, Deadline (Armador),
Previsão Chegada, Previsão de Atracação, Previsão de Saída, Detalhes
(datas no formato dd/mm/aaaa HH:MM, sem segundos).

A página tem outras tabelas que não são a de navios — uma de lookup
(0 colunas, pares ['Viagem', 'Sigla Navio']) e, se a Data Inicial for
preenchida manualmente com formato errado, uma de registros "STOPPAGE"
(paralisação, não é navio — por isso não preenchemos essa data, o
próprio site já aplica um default sozinho). Identificamos a tabela
certa pelo conteúdo (procurando a coluna "Navio" via atributo
`nomecoluna`), em vez de assumir "a primeira tabela da página".

O texto visível dos cabeçalhos vem com acentos corrompidos (encoding
quebrado no próprio servidor, ex: "Servi�o"). Por isso usamos o
atributo HTML `nomecoluna` de cada <th> (ASCII, estável) em vez do
texto exibido.

A tabela real também tem 2 <tbody>: o primeiro vem sempre vazio
(placeholder do template Knockout) e o segundo (com o binding
"foreach") é onde as linhas de verdade ficam.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from .base import TerminalScraper, run_in_thread

URL = "https://www.embraportonline.com.br/Navios/Escala"

# Chave = atributo `nomecoluna` do <th> (minúsculo). "None" = coluna
# ignorada (não faz parte do schema unificado ou é redundante).
HEADER_MAP = {
    "navio": "navio",
    "viagemin": "viagem",
    "visita": "fonte_raw_id",
    "agencia": None,  # Armador
    "servico": None,
    "berco": "berco",
    "aberturagatedata": "previsao_abertura_gate",  # rótulo: "Previsão de Abertura Gate"
    "previsaogatedata": "abertura_gate",  # rótulo: "Abertura de Gate"
    "drydeadlinedata": "deadline_carga",
    "previsaochegadadata": "eta",
    "atracacaodata": "etb",
    "previsaosaidadata": "etd",
}

_DATE_FMT = "%d/%m/%Y %H:%M"

_TABLE_WITH_NAVIO_SELECTOR = 'table:has(th[nomecoluna="NAVIO" i])'


def _parse_date(value: str) -> Optional[datetime]:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, _DATE_FMT)
    except ValueError:
        return None


def _find_navio_table(soup: BeautifulSoup):
    """Acha a tabela cujo cabeçalho tem uma coluna com nomecoluna="NAVIO"
    (case-insensitive) — a página tem outras tabelas irrelevantes."""
    for table in soup.find_all("table"):
        for th in table.find_all("th"):
            if (th.get("nomecoluna") or "").strip().upper() == "NAVIO":
                return table
    return None


class EmbraportScraper(TerminalScraper):
    terminal_id = "dp_world"

    def fetch(self) -> List[Dict[str, Any]]:
        html = run_in_thread(self._render)
        return self._parse(html)

    def _render(self) -> str:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(URL, wait_until="networkidle", timeout=60000)

            # O app (Knockout.js) demora mais que o "networkidle" pra
            # terminar de desenhar os filtros.
            page.wait_for_timeout(5000)

            # O painel "Filtros" começa recolhido; os campos só ficam
            # visíveis/clicáveis depois de expandir essa seção.
            try:
                page.get_by_text("Filtros", exact=True).first.click(timeout=10000)
                page.wait_for_timeout(1000)
            except Exception:
                pass

            try:
                page.get_by_text("Todos", exact=True).first.click(timeout=15000)
                page.wait_for_timeout(1000)
            except Exception:
                pass

            try:
                btn = page.get_by_text("Pesquisar", exact=True).first
                btn.scroll_into_view_if_needed(timeout=10000)
                page.wait_for_timeout(500)
                btn.click(timeout=15000)
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass

            # A pesquisa demora pra responder, e o número de linhas oscila
            # entre 0 e um valor transitório enquanto isso (ex: um
            # "Aguarde..." passageiro). Só aceitamos quando a contagem
            # fica igual em duas checagens seguidas (2s de intervalo).
            navio_table = page.locator(_TABLE_WITH_NAVIO_SELECTOR)
            previous_count = -1
            stable_count = 0
            for _ in range(20):
                count = navio_table.locator("tbody tr").count()
                if count > 0 and count == previous_count:
                    stable_count = count
                    break
                previous_count = count
                page.wait_for_timeout(2000)

            # A lista carrega por scroll infinito (só busca mais navios
            # quando o usuário rola a tela) — rola até o fim repetidamente
            # até a contagem parar de crescer.
            if stable_count > 0:
                rows_locator = navio_table.locator("tbody tr")
                last_count = stable_count
                for _ in range(15):
                    rows_locator.last.scroll_into_view_if_needed(timeout=5000)
                    page.wait_for_timeout(2000)
                    new_count = rows_locator.count()
                    if new_count <= last_count:
                        break
                    last_count = new_count
                stable_count = last_count

            if stable_count > 0:
                # Pega o outerHTML da tabela diretamente, no exato momento
                # em que sabemos que está estável — page.content() (a
                # página inteira) chega tarde demais, o app já limpou a
                # tabela de novo até lá.
                html = navio_table.evaluate("el => el.outerHTML")
            else:
                html = ""

            browser.close()
            return html

    def _parse(self, html: str) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        table = _find_navio_table(soup)
        if table is None:
            return []

        header_row = table.find("thead") or table.find("tr")
        headers = [
            (th.get("nomecoluna") or "").strip().lower()
            for th in header_row.find_all("th")
        ]

        # A tabela tem 2 <tbody>: o primeiro é sempre vazio (placeholder do
        # template Knockout) e o segundo (com o binding "foreach") é onde
        # as linhas de verdade ficam — table.find("tbody") pegaria só o
        # primeiro, vazio.
        rows_out: List[Dict[str, Any]] = []
        bodies = table.find_all("tbody")
        body = next((b for b in bodies if b.find("tr")), None) or table
        for tr in body.find_all("tr"):
            cells = tr.find_all("td")
            if not cells or len(cells) != len(headers):
                continue

            record: Dict[str, Any] = {"terminal": self.terminal_id}
            for header, cell in zip(headers, cells):
                field = HEADER_MAP.get(header)
                if field is None:
                    continue
                text = cell.get_text(strip=True)
                if field in {
                    "deadline_carga", "abertura_gate", "previsao_abertura_gate",
                    "etb", "etd", "atb", "atd", "eta", "ata",
                }:
                    record[field] = _parse_date(text)
                else:
                    record[field] = text or None

            if record.get("navio"):
                rows_out.append(record)

        return rows_out


if __name__ == "__main__":
    import json

    data = EmbraportScraper().fetch()
    print(json.dumps(data[:3], indent=2, ensure_ascii=False, default=str))
    print(f"\nTotal de registros: {len(data)}")
