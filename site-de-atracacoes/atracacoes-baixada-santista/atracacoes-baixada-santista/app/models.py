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
    # RAP (Registro de Atracação Programada): na BTP vem direto do
    # terminal (fonte_raw_id); nos demais, é casado por nome do navio
    # contra a lista pública da Autoridade Portuária de Santos — ver
    # app/scrapers/porto_santos.py.
    rap: Optional[str] = None

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


class RapConhecido(SQLModel, table=True):
    """Cache persistente de RAPs já vistos na lista pública "Atracações
    Programadas" da Autoridade Portuária, por (terminal, navio, ETA) — ver
    app/scrapers/porto_santos.py.

    Essa página só mostra uma janela rolante de poucos dias (nem histórico
    nem futuro distante) — sem esse cache, o RAP de uma viagem que não foi
    capturada bem na hora certa (site deles fora do ar, ou o upload manual
    da Santos Brasil caindo fora da janela) fica perdido pra sempre assim
    que a viagem sai da lista deles, mesmo já tendo acontecido de verdade
    (caso real: COSCO SHIPPING PERU 009W, RAP 04173/2026, achado só na
    consulta manual do usuário em 2026-09-16 — nosso sistema nunca chegou
    a ver essa viagem na janela certa). Guardando cada avistamento aqui
    pra sempre (até o navio ficar velho o bastante pra não interessar
    mais, ver limpeza em sync.py), o casamento em enriquecer_com_rap passa
    a usar todo o histórico já visto, não só o que a página mostra nesse
    instante."""

    __tablename__ = "rap_conhecido"

    terminal: str = Field(primary_key=True)
    navio_normalizado: str = Field(primary_key=True)
    eta: datetime = Field(primary_key=True)
    rap: str
    visto_em: datetime = Field(default_factory=agora_brasilia)
