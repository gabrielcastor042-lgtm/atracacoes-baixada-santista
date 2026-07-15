"""
Scraper do Ecoporto Santos.

A página https://op.ecoportosantos.com.br/externa/LineUpListaAtracacao/
renderiza a tabela de atracação diretamente no HTML (sem JS), então um
GET simples + parse com BeautifulSoup já resolve. Testado em 2026-07-15.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from .base import TerminalScraper

URL = "https://op.ecoportosantos.com.br/externa/LineUpListaAtracacao/"

# Mapeia o texto do cabeçalho da tabela (normalizado) -> campo do nosso schema.
# "None" = coluna ignorada (não faz parte do schema unificado ou é redundante).
HEADER_MAP = {
    "rap": "fonte_raw_id",
    "status": None,
    "navio": "navio",
    "viagem": "viagem",
    "berço atracação": "berco",
    "armador": None,
    "serv.": None,
    "deadline": "deadline_carga",
    "abertura gate cntr": "abertura_gate",
    "abertura gate cs": None,
    "abertura tra": None,
    "previsão atracação": "etb",
    "previsão abertura gate": "previsao_abertura_gate",
    "atracação": "atb",
    "previsão saída": "etd",
    "saída": "atd",
    "ordem": None,
}

_DATE_FMT = "%d/%m/%Y %H:%M"


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


class EcoportoScraper(TerminalScraper):
    terminal_id = "ecoporto"

    def fetch(self) -> List[Dict[str, Any]]:
        resp = requests.get(URL, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        return self._parse(resp.text)

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

            if record.get("navio"):
                rows_out.append(record)

        return rows_out


if __name__ == "__main__":
    import json

    data = EcoportoScraper().fetch()
    print(json.dumps(data[:3], indent=2, ensure_ascii=False))
    print(f"\nTotal de registros: {len(data)}")
