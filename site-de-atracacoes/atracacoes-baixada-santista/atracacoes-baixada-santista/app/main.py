from __future__ import annotations

import io
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, List, Optional

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from sqlmodel import select

from .database import get_session, init_db
from .models import Atracacao, SyncStatus
from .scheduler import start_scheduler
from .scrapers.santos_brasil import parse_upload
from .sync import _upsert, registrar_status, run_sync

# (chave da coluna, cabeçalho na planilha)
_EXPORT_COLUMNS = [
    ("navio", "Navio"),
    ("viagem", "Viagem"),
    ("terminal", "Terminal"),
    ("berco", "Berço"),
    ("eta", "ETA"),
    ("etb", "ETB"),
    ("etd", "ETD"),
    ("ata", "ATA"),
    ("atb", "ATB"),
    ("atd", "ATD"),
    ("deadline_carga", "Deadline carga"),
    ("previsao_abertura_gate", "Previsão abertura gate"),
    ("abertura_gate", "Abertura gate"),
    ("fonte_raw_id", "Fonte (ID original)"),
    ("atualizado_em", "Atualizado em"),
]

_DATE_COLUMNS = {
    "eta", "etb", "etd", "ata", "atb", "atd",
    "deadline_carga", "previsao_abertura_gate", "abertura_gate", "atualizado_em",
}


def _valor_exportado(row: Atracacao, col: str) -> Any:
    if col == "abertura_gate":
        # Se não tem a abertura efetiva, usa a previsão como referência.
        return row.abertura_gate or row.previsao_abertura_gate
    return getattr(row, col)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    run_sync()  # primeira carga ao subir
    start_scheduler()
    yield


app = FastAPI(title="Atracações - Porto de Santos", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # restrinja em produção
    allow_methods=["*"],
    allow_headers=["*"],
)


def _filtrar_atracacoes(q: Optional[str], terminal: Optional[str]):
    stmt = select(Atracacao)
    if terminal:
        stmt = stmt.where(Atracacao.terminal == terminal)
    if q:
        like = f"%{q.upper()}%"
        stmt = stmt.where(
            (Atracacao.navio.like(like))  # type: ignore[union-attr]
            | (Atracacao.viagem.like(like))  # type: ignore[union-attr]
        )
    return stmt.order_by(Atracacao.eta.is_(None), Atracacao.eta)


@app.get("/buscar", response_model=List[Atracacao])
def buscar(
    q: Optional[str] = Query(None, description="Nome do navio, viagem ou terminal"),
    terminal: Optional[str] = Query(None),
):
    with get_session() as session:
        stmt = _filtrar_atracacoes(q, terminal)
        return session.exec(stmt).all()


@app.get("/exportar")
def exportar(
    q: Optional[str] = Query(None, description="Nome do navio, viagem ou terminal"),
    terminal: Optional[str] = Query(None),
):
    """Exporta em Excel (.xlsx) as atracações que batem com o filtro atual
    (mesmos parâmetros da busca)."""
    with get_session() as session:
        stmt = _filtrar_atracacoes(q, terminal)
        rows = session.exec(stmt).all()

    wb = Workbook()
    ws = wb.active
    ws.title = "Atracações"

    headers = [label for _, label in _EXPORT_COLUMNS]
    ws.append(headers)

    for row in rows:
        ws.append([_valor_exportado(row, col) for col, _ in _EXPORT_COLUMNS])

    date_format = 'DD/MM/YYYY " - "HH:MM'
    for col_index, (col, _) in enumerate(_EXPORT_COLUMNS, start=1):
        letter = get_column_letter(col_index)
        ws.column_dimensions[letter].width = 20
        if col in _DATE_COLUMNS:
            for cell in ws[letter][1:]:  # pula o cabeçalho
                cell.number_format = date_format

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=atracacoes.xlsx"},
    )


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
        registrar_status(session, "santos_brasil", len(records))

    return {"terminal": "santos_brasil", "registros": len(records)}


@app.get("/status", response_model=List[SyncStatus])
def status():
    """Quando cada fonte (scraper automático ou upload manual) foi
    sincronizada pela última vez."""
    with get_session() as session:
        return session.exec(select(SyncStatus)).all()


@app.get("/health")
def health():
    return {"status": "ok"}


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
