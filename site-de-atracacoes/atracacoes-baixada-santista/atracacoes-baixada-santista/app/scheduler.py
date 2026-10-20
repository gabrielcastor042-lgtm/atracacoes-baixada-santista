from __future__ import annotations

import logging
import os
from datetime import datetime

import requests
from apscheduler.schedulers.background import BackgroundScheduler

from .sync import run_gate_sync, run_sync

logger = logging.getLogger("scheduler")

# O plano gratuito do Render "dorme" depois de ~15 min sem nenhum acesso
# externo — e com o processo suspenso o scheduler também para, então nada
# sincroniza até alguém abrir o site (confirmado em 2026-10-20: nenhuma
# sincronização entre 13:41 e 14:17, 43 s pra acordar). Pra evitar isso no
# horário em que o site é usado (inclusive pela atualização do Power BI),
# o próprio servidor acessa a URL pública dele mesmo a cada 10 min — o
# acesso passa pelo Render como tráfego externo, e assim ele não dorme.
#
# Limitado ao horário comercial de propósito: o plano gratuito tem 750 h
# de instância por mês por conta, e ficar acordado 24 h usaria quase tudo
# (~744 h). Das 06h às 22h são ~500 h/mês. Fora desse horário ele dorme
# normalmente e só acorda no primeiro acesso do dia (ele não consegue se
# acordar sozinho, porque dormindo o processo inteiro fica parado).
#
# RENDER_EXTERNAL_URL é definida automaticamente pelo Render; fora dele
# (ex.: rodando local) a variável não existe e o job não faz nada.
KEEPALIVE_INTERVALO_MINUTOS = 10
KEEPALIVE_HORAS = "6-21"  # cron: dispara das 06:00 até 21:50


def manter_acordado() -> None:
    url = os.environ.get("RENDER_EXTERNAL_URL")
    if not url:
        return
    try:
        requests.get(f"{url.rstrip('/')}/health", timeout=30)
    except requests.RequestException:
        logger.warning("Falha no acesso de manter-acordado", exc_info=True)

# A sincronização (BTP + Embraport via Playwright) demora mais de 1 minuto
# no servidor gratuito do Render, então usamos um intervalo maior pra não
# sobrepor execuções.
SYNC_INTERVAL_MINUTES = 10

# Deadline/gate da Santos Brasil (via API pública, sem navegador) — mais
# leve que o sync principal, mas mantido no mesmo intervalo por
# consistência com BTP/DP World.
GATE_SYNC_INTERVAL_MINUTES = 10


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
    scheduler.add_job(
        run_gate_sync,
        "interval",
        minutes=GATE_SYNC_INTERVAL_MINUTES,
        id="sync_gate_santos_brasil",
        next_run_time=datetime.now(scheduler.timezone),
    )
    scheduler.add_job(
        manter_acordado,
        "cron",
        minute=f"*/{KEEPALIVE_INTERVALO_MINUTOS}",
        hour=KEEPALIVE_HORAS,
        id="manter_acordado",
    )
    scheduler.start()
    logger.info(
        "Scheduler iniciado: sync geral a cada %d min, gate Santos Brasil a cada %d min (primeiras rodadas já disparadas)",
        SYNC_INTERVAL_MINUTES, GATE_SYNC_INTERVAL_MINUTES,
    )
    return scheduler
