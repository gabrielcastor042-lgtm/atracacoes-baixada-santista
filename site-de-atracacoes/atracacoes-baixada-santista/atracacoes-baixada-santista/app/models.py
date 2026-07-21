"""
Schema unificado de atracação.

Todos os scrapers (um por terminal) devem devolver uma lista de dicts
que possam ser convertidos para este modelo. Datas ficam em ISO 8601
(YYYY-MM-DDTHH:MM:SS) ou None quando não informadas pelo terminal.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import SQLModel, Field

from .timezone import agora_brasilia


class Atracacao(SQLModel, table=True):
    __tablename__ = "atracacoes"

    id: Optional[int] = Field(default=None, primary_key=True)

    # Identificação
    navio: str = Field(index=True)
    viagem: Optional[str] = Field(default=None, index=True)
    terminal: str = Field(index=True)  # santos_brasil | dp_world | btp | ecoporto
    berco: Optional[str] = None        # "Terminal de atracação" / berço físico

    # Prazos operacionais
    deadline_carga: Optional[datetime] = None
    previsao_abertura_gate: Optional[datetime] = None
    abertura_gate: Optional[datetime] = None

    # Janela do navio
    eta: Optional[datetime] = None  # Estimated Time of Arrival
    etb: Optional[datetime] = None  # Estimated Time of Berthing
    etd: Optional[datetime] = None  # Estimated Time of Departure
    ata: Optional[datetime] = None  # Actual Time of Arrival
    atb: Optional[datetime] = None  # Actual Time of Berthing
    atd: Optional[datetime] = None  # Actual Time of Departure

    # Metadados de sincronização
    fonte_raw_id: Optional[str] = None  # id/RAP original do terminal, p/ dedupe
    atualizado_em: datetime = Field(default_factory=agora_brasilia)
    # Quando o navio deixou de aparecer na leitura do terminal (None
    # enquanto ele continua aparecendo). É removido do banco só depois de
    # ficar SUMIDO_GRACE_DAYS dias seguidos sumido — ver sync.py.
    sumido_em: Optional[datetime] = None

    class Config:
        arbitrary_types_allowed = True


class SyncStatus(SQLModel, table=True):
    """Guarda quando cada fonte (scraper automático ou upload manual) foi
    sincronizada pela última vez, independente de ter mudado algum navio."""

    __tablename__ = "sync_status"

    terminal: str = Field(primary_key=True)  # santos_brasil | dp_world | btp | ecoporto
    atualizado_em: datetime
    registros: int
    erro: Optional[str] = None
