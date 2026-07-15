from __future__ import annotations

from sqlmodel import SQLModel, Session, create_engine

DATABASE_URL = "sqlite:///./atracacoes.db"
engine = create_engine(DATABASE_URL, echo=False)


def init_db() -> None:
    from . import models  # noqa: F401  (garante que a tabela seja registrada)
    SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    return Session(engine)
