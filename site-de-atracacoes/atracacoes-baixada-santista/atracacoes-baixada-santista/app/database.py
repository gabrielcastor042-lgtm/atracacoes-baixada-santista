from __future__ import annotations

import os

from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError
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
    _ensure_columns()


# (coluna, tipo SQL) adicionadas depois da criação inicial da tabela.
_COLUNAS_NOVAS = [
    ("sumido_em", "TIMESTAMP"),
    ("rap", "VARCHAR"),
]


def _ensure_columns() -> None:
    """create_all() só cria tabelas que ainda não existem — não adiciona
    colunas novas a uma tabela já existente. Como o projeto não usa uma
    ferramenta de migração (Alembic etc.), aplicamos aqui os poucos ALTER
    TABLE necessários quando o schema evolui; se a coluna já existir, o
    erro é ignorado."""
    for coluna, tipo in _COLUNAS_NOVAS:
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE atracacoes ADD COLUMN {coluna} {tipo}"))
        except (OperationalError, ProgrammingError):
            pass


def get_session() -> Session:
    return Session(engine)
