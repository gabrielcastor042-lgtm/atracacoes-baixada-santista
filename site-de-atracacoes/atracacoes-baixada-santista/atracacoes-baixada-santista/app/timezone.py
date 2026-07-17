"""Horário de Brasília usado em todos os timestamps gerados pela própria
aplicação (atualizado_em, status de sincronização). As datas dos navios
(ETA/ETB/etc.) já vêm nesse fuso direto dos sites dos terminais, então
não precisam de conversão."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

_BRASILIA_TZ = ZoneInfo("America/Sao_Paulo")


def agora_brasilia() -> datetime:
    """Hora atual de Brasília, como datetime "naive" (sem tzinfo) — pra
    ficar no mesmo formato das outras datas já gravadas no banco."""
    return datetime.now(_BRASILIA_TZ).replace(tzinfo=None)
