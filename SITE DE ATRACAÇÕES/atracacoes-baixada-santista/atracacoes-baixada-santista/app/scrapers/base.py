from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Dict, Any


class TerminalScraper(ABC):
    """Todo scraper de terminal implementa fetch() e devolve uma lista
    de dicts já no formato aceito por app.models.Atracacao (campos podem
    faltar/ser None, mas os nomes de chave devem bater)."""

    terminal_id: str  # "santos_brasil" | "dp_world" | "btp" | "ecoporto"

    @abstractmethod
    def fetch(self) -> List[Dict[str, Any]]:
        ...
