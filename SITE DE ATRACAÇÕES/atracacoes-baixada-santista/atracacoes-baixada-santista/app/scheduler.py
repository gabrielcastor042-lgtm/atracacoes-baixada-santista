from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from .sync import run_sync

logger = logging.getLogger("scheduler")

# "Tempo real" pedido = atualização a cada 5 minutos.
# Ajuste conforme os limites de requisição de cada terminal (ver README).
SYNC_INTERVAL_MINUTES = 5


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="America/Sao_Paulo")
    scheduler.add_job(
        run_sync,
        "interval",
        minutes=SYNC_INTERVAL_MINUTES,
        id="sync_atracacoes",
    )
    scheduler.start()
    logger.info("Scheduler iniciado: sync a cada %d minutos", SYNC_INTERVAL_MINUTES)
    return scheduler
