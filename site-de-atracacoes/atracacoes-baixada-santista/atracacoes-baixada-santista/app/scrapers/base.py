from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, TypeVar

T = TypeVar("T")


class TerminalScraper(ABC):
    """Todo scraper de terminal implementa fetch() e devolve uma lista
    de dicts já no formato aceito por app.models.Atracacao (campos podem
    faltar/ser None, mas os nomes de chave devem bater)."""

    terminal_id: str  # "santos_brasil" | "dp_world" | "btp" | "ecoporto"

    @abstractmethod
    def fetch(self) -> List[Dict[str, Any]]:
        ...


def run_in_thread(func: Callable[[], T]) -> T:
    """Roda `func` numa thread nova e devolve o resultado (ou repropaga a
    exceção). Necessário para scrapers com Playwright Sync API: ela não
    roda na mesma thread de um loop asyncio já ativo (caso do FastAPI/
    Uvicorn), então isolamos a chamada numa thread sem loop nenhum."""
    result: Dict[str, Any] = {}

    def _target() -> None:
        try:
            result["value"] = func()
        except Exception as exc:  # noqa: BLE001
            result["error"] = exc

    thread = threading.Thread(target=_target)
    thread.start()
    thread.join()

    if "error" in result:
        raise result["error"]
    return result["value"]
