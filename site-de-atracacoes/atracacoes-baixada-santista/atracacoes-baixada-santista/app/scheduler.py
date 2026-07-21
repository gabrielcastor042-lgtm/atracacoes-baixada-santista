from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from .sync import run_sync

logger = logging.getLogger("scheduler")

# A sincronização (BTP + Embraport via Playwright) demora mais de 1 minuto
# no servidor gratuito do Render, então usamos um intervalo maior pra não
# sobrepor execuções.
SYNC_INTERVAL_MINUTES = 10


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="America/Sao_Paulo")
    scheduler.add_job(
        run_sync,
        "interval",
        minutes=SYNC_INTERVAL_MINUTES,
        id="sync_atracacoes",
        # Dispara a primeira sincronização quase imediatamente, mas em
        # segundo plano (thread própria do scheduler) — não trava a
        # inicialização do servidor esperando o scraping terminar.
        next_run_time=datetime.now(scheduler.timezone),
    )
    scheduler.start()
    logger.info("Scheduler iniciado: sync a cada %d minutos (primeira rodada já disparada)", SYNC_INTERVAL_MINUTES)
    return scheduler
