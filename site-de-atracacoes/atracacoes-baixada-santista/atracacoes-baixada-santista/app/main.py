from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlmodel import select

from .database import get_session, init_db
from .models import Atracacao
from .scheduler import start_scheduler
from .scrapers.santos_brasil import parse_upload
from .sync import _upsert, run_sync


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    run_sync()  # primeira carga ao subir
    start_scheduler()
    yield


app = FastAPI(title="Atracações Baixada Santista", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # restrinja em produção
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/buscar", response_model=List[Atracacao])
def buscar(
    q: Optional[str] = Query(None, description="Nome do navio, viagem ou terminal"),
    terminal: Optional[str] = Query(None),
):
    with get_session() as session:
        stmt = select(Atracacao)
        if terminal:
            stmt = stmt.where(Atracacao.terminal == terminal)
        if q:
            like = f"%{q.upper()}%"
            stmt = stmt.where(
                (Atracacao.navio.like(like))  # type: ignore[union-attr]
                | (Atracacao.viagem.like(like))  # type: ignore[union-attr]
            )
        stmt = stmt.order_by(Atracacao.eta.is_(None), Atracacao.eta)
        return session.exec(stmt).all()


@app.post("/sync")
def sync_now():
    """Dispara uma sincronização manual (útil para testes/depuração)."""
    return run_sync()


@app.post("/upload/santos_brasil")
async def upload_santos_brasil(file: UploadFile = File(...)):
    """Recebe o arquivo .xls (na verdade HTML) exportado manualmente da
    área de cliente da Santos Brasil e sincroniza os navios no banco."""
    content = await file.read()
    records = parse_upload(content)
    if not records:
        raise HTTPException(422, "Nenhum navio encontrado no arquivo enviado.")

    with get_session() as session:
        for record in records:
            _upsert(session, record)
        session.commit()

    return {"terminal": "santos_brasil", "registros": len(records)}


@app.get("/health")
def health():
    return {"status": "ok"}


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
