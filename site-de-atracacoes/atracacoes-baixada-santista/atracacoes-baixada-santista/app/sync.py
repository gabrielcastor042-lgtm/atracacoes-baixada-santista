from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from sqlmodel import select

from .database import get_session, init_db
from .models import Atracacao, SyncStatus
from .scrapers.base import TerminalScraper
from .scrapers.btp import BTPScraper
from .scrapers.embraport import EmbraportScraper

logger = logging.getLogger("sync")

# Adicione aqui novos scrapers assim que estiverem prontos:
# from .scrapers.santos_brasil import SantosBrasilScraper
# from .scrapers.ecoporto import EcoportoScraper  # bloqueado por anti-bot (Akamai), ver docstring
ACTIVE_SCRAPERS: List[TerminalScraper] = [
    BTPScraper(),
    EmbraportScraper(),
    # SantosBrasilScraper(),
    # EcoportoScraper(),
]


def _upsert(session, record: dict) -> None:
    """Dedupe por (terminal, navio, viagem). Se já existir, atualiza; senão, insere."""
    stmt = select(Atracacao).where(
        Atracacao.terminal == record.get("terminal"),
        Atracacao.navio == record.get("navio"),
        Atracacao.viagem == record.get("viagem"),
    )
    existing = session.exec(stmt).first()

    if existing:
        for key, value in record.items():
            setattr(existing, key, value)
        existing.atualizado_em = datetime.utcnow()
        session.add(existing)
    else:
        session.add(Atracacao(**record))


def registrar_status(
    session, terminal: str, registros: int, erro: Optional[str] = None
) -> None:
    """Marca quando essa fonte (scraper automático ou upload manual) foi
    sincronizada por último, mesmo que nenhum navio tenha mudado."""
    status = session.get(SyncStatus, terminal)
    if status is None:
        status = SyncStatus(terminal=terminal, atualizado_em=datetime.utcnow(), registros=registros, erro=erro)
    else:
        status.atualizado_em = datetime.utcnow()
        status.registros = registros
        status.erro = erro
    session.add(status)
    session.commit()


def run_sync() -> dict:
    init_db()
    results = {}
    with get_session() as session:
        for scraper in ACTIVE_SCRAPERS:
            try:
                records = scraper.fetch()
                for record in records:
                    _upsert(session, record)
                session.commit()
                results[scraper.terminal_id] = len(records)
                logger.info("Sincronizado %s: %d registros", scraper.terminal_id, len(records))
                registrar_status(session, scraper.terminal_id, len(records))
            except Exception as exc:  # noqa: BLE001
                logger.exception("Falha ao sincronizar %s", scraper.terminal_id)
                results[scraper.terminal_id] = f"erro: {exc}"
                registrar_status(session, scraper.terminal_id, 0, erro=str(exc))
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run_sync())
