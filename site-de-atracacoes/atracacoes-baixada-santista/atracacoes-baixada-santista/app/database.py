from __future__ import annotations

import os

from sqlmodel import SQLModel, Session, create_engine

# Em produção, defina a variável de ambiente DATABASE_URL apontando pra um
# Postgres externo (ex: Neon) — assim os dados sobrevivem a reinícios do
# servidor. Sem essa variável, cai no SQLite local (bom pra desenvolvimento).
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./atracacoes.db")

# Neon/Postgres às vezes fornecem a string com "postgres://", mas o
# SQLAlchemy exige o prefixo "postgresql://".
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, echo=False, connect_args=_connect_args)


def init_db() -> None:
    from . import models  # noqa: F401  (garante que a tabela seja registrada)
    SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    return Session(engine)
