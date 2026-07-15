"""
BTP - Brasil Terminal Portuário.

Página pública "Consultas Livres" do sistema TAS:
    https://novo-tas.btp.com.br/ConsultasLivres/ListaAtracacaoIndex

A tabela vem vazia no HTML inicial; os dados são carregados via AJAX
depois de clicar em "Pesquisar", então usamos Playwright para reproduzir
esse clique e ler a tabela já renderizada (mesmo padrão do
scripts/inspect_page.py). Estrutura capturada em 2026-07-15:

Colunas: RAP, NAVIO, VIAGEM, AGÊNCIA, DT. PREV. CHEGADA, DT. CHEGADA,
DT. PREV. ATRAC., DT. ATRACAÇÃO, DT. PREV. SAÍDA, DT. SAÍDA,
ABERTURA DE GATE DRY, ABERTURA DE GATE REEFER, DEAD-LINE, SERVIÇO
(datas no formato dd/mm/aaaa HH:MM:SS, com segundos).
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from .base import TerminalScraper

URL = "https://novo-tas.btp.com.br/ConsultasLivres/ListaAtracacaoIndex"

# "None" = coluna ignorada (não faz parte do schema unificado ou é redundante).
HEADER_MAP = {
    "rap": "fonte_raw_id",
    "navio": "navio",
    "viagem": "viagem",
    "agência": None,
    "dt. prev. chegada": "eta",
    "dt. chegada": "ata",
    "dt. prev. atrac.": "etb",
    "dt. atracação": "atb",
    "dt. prev. saída": "etd",
    "dt. saída": "atd",
    "abertura de gate dry": "abertura_gate",
    "abertura de gate reefer": None,
    "dead-line": "deadline_carga",
    "serviço": None,
}

_DATE_FMT = "%d/%m/%Y %H:%M:%S"


def _parse_date(value: str) -> Optional[str]:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, _DATE_FMT).isoformat()
    except ValueError:
        return None


def _normalize_header(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


class BTPScraper(TerminalScraper):
    terminal_id = "btp"

    def fetch(self) -> List[Dict[str, Any]]:
        html = self._render()
        return self._parse(html)

    def _render(self) -> str:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(URL, wait_until="networkidle", timeout=60000)

            try:
                locator = page.get_by_text("Pesquisar", exact=False).first
                if locator.count() > 0:
                    locator.click(timeout=5000)
                    page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass

            page.wait_for_timeout(3000)
            html = page.content()
            browser.close()
            return html

    def _parse(self, html: str) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table")
        if table is None:
            return []

        headers = [
            _normalize_header(th.get_text())
            for th in table.find("thead").find_all("th")
        ] if table.find("thead") else [
            _normalize_header(th.get_text()) for th in table.find("tr").find_all(["th"])
        ]

        rows_out: List[Dict[str, Any]] = []
        seen_raps: set = set()
        body = table.find("tbody") or table
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

            # A página tem tabelas duplicadas (versão desktop/mobile) com o
            # mesmo conteúdo; dedupe por RAP dentro desta própria chamada.
            rap = record.get("fonte_raw_id")
            if record.get("navio") and rap not in seen_raps:
                seen_raps.add(rap)
                rows_out.append(record)

        return rows_out


if __name__ == "__main__":
    import json

    data = BTPScraper().fetch()
    print(json.dumps(data[:3], indent=2, ensure_ascii=False))
    print(f"\nTotal de registros: {len(data)}")
