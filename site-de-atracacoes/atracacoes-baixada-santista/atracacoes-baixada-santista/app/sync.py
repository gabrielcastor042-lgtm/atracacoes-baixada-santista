from __future__ import annotations

import logging
from typing import List, Optional

from sqlmodel import select

from .database import get_session, init_db
from .models import Atracacao, SyncStatus
from .scrapers.base import TerminalScraper
from .scrapers.btp import BTPScraper
from .scrapers.embraport import EmbraportScraper
from .timezone import agora_brasilia

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
        existing.atualizado_em = agora_brasilia()
        session.add(existing)
    else:
        session.add(Atracacao(**record))


def sincronizar_terminal(session, terminal_id: str, records: List[dict]) -> None:
    """Upsert dos registros novos e remoção dos que sumiram dessa leitura
    completa da fonte — mantém só o que está atualmente ativo no
    terminal, sem acumular navios antigos pra sempre no banco."""
    chaves_atuais = set()
    for record in records:
        _upsert(session, record)
        chaves_atuais.add((record.get("navio"), record.get("viagem")))

    stmt = select(Atracacao).where(Atracacao.terminal == terminal_id)
    for existing in session.exec(stmt).all():
        if (existing.navio, existing.viagem) not in chaves_atuais:
            session.delete(existing)


def registrar_status(
    session, terminal: str, registros: Optional[int] = None, erro: Optional[str] = None
) -> None:
    """Marca quando essa fonte (scraper automático ou upload manual) foi
    sincronizada por último, mesmo que nenhum navio tenha mudado.

    Se `registros` for None (caso de erro), mantém a última contagem boa
    conhecida em vez de zerar — assim a tela não mostra "0 navios" quando
    na real os dados antigos continuam válidos no banco, só essa
    tentativa específica de sincronizar é que falhou."""
    status = session.get(SyncStatus, terminal)
    if status is None:
        status = SyncStatus(
            terminal=terminal, atualizado_em=agora_brasilia(), registros=registros or 0, erro=erro
        )
    else:
        status.atualizado_em = agora_brasilia()
        if registros is not None:
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
                if not records:
                    # Terminais como BTP/Embraport sempre têm navios; vazio
                    # aqui é sinal de falha temporária no site/scraper, não
                    # "nenhum navio de verdade" — tratamos como erro pra não
                    # sobrescrever os dados bons já gravados com um "0" falso.
                    raise RuntimeError(
                        "Nenhum registro retornado (provável falha temporária no site do terminal)"
                    )
                sincronizar_terminal(session, scraper.terminal_id, records)
                session.commit()
                results[scraper.terminal_id] = len(records)
                logger.info("Sincronizado %s: %d registros", scraper.terminal_id, len(records))
                registrar_status(session, scraper.terminal_id, len(records))
            except Exception as exc:  # noqa: BLE001
                logger.exception("Falha ao sincronizar %s", scraper.terminal_id)
                results[scraper.terminal_id] = f"erro: {exc}"
                registrar_status(session, scraper.terminal_id, erro=str(exc))
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run_sync())
