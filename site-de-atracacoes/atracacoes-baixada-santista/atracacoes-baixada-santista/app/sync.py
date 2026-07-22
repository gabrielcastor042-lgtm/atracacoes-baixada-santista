from __future__ import annotations

import logging
from datetime import timedelta
from typing import List, Optional

from sqlmodel import select

from .database import get_session, init_db
from .models import Atracacao, SyncStatus
from .scrapers.base import TerminalScraper
from .scrapers.btp import BTPScraper
from .scrapers.embraport import EmbraportScraper
from .scrapers.porto_santos import enriquecer_com_rap, fetch_rap_por_navio
from .timezone import agora_brasilia

logger = logging.getLogger("sync")

# Um navio que sumiu da leitura do terminal (ex.: desatracou e saiu da
# lista) só é removido do banco depois de ficar sumido por esse tanto de
# dias seguidos — assim ele continua visível/pesquisável no site por um
# tempo depois da operação terminar, em vez de desaparecer na hora.
SUMIDO_GRACE_DAYS = 30

# Adicione aqui novos scrapers assim que estiverem prontos:
# from .scrapers.santos_brasil import SantosBrasilScraper
# from .scrapers.ecoporto import EcoportoScraper  # bloqueado por anti-bot (Akamai), ver docstring
ACTIVE_SCRAPERS: List[TerminalScraper] = [
    BTPScraper(),
    EmbraportScraper(),
    # SantosBrasilScraper(),
    # EcoportoScraper(),
]


def _upsert(session, record: dict) -> None:
    """Dedupe por (terminal, navio, viagem). Se já existir, atualiza; senão, insere.
    Só é chamado para registros presentes na leitura atual, então sempre
    limpa `sumido_em` (o navio voltou a aparecer no terminal)."""
    stmt = select(Atracacao).where(
        Atracacao.terminal == record.get("terminal"),
        Atracacao.navio == record.get("navio"),
        Atracacao.viagem == record.get("viagem"),
    )
    existing = session.exec(stmt).first()

    if existing:
        for key, value in record.items():
            setattr(existing, key, value)
        existing.atualizado_em = agora_brasilia()
        existing.sumido_em = None
        session.add(existing)
    else:
        session.add(Atracacao(**record))


def sincronizar_terminal(session, terminal_id: str, records: List[dict]) -> Optional[str]:
    """Upsert dos registros novos e remoção dos que ficaram sumidos dessa
    fonte por SUMIDO_GRACE_DAYS dias seguidos — assim um navio que
    desatraca (some da lista do terminal) continua visível no site por um
    tempo, em vez de sumir na hora.

    Retorna uma mensagem de aviso (str) se a remoção foi pulada por
    segurança, ou None se tudo ocorreu normalmente."""
    chaves_atuais = set()
    for record in records:
        _upsert(session, record)
        chaves_atuais.add((record.get("navio"), record.get("viagem")))

    stmt = select(Atracacao).where(Atracacao.terminal == terminal_id)
    existentes = session.exec(stmt).all()
    sumidos = [e for e in existentes if (e.navio, e.viagem) not in chaves_atuais]

    agora = agora_brasilia()
    prazo = timedelta(days=SUMIDO_GRACE_DAYS)
    a_remover = [e for e in sumidos if e.sumido_em and (agora - e.sumido_em) > prazo]

    # Trava de segurança: um site de terminal instável pode retornar uma
    # leitura parcial (ex.: só 10 de ~157 navios) sem lançar erro nenhum.
    # Se isso acontecesse sem essa checagem, uma sequência de leituras
    # parciais poderia apagar dados bons em massa depois do prazo de
    # carência vencer. Só confiamos na remoção em massa quando o total a
    # remover é, no máximo, metade do que já tínhamos cadastrado.
    if existentes and len(a_remover) > len(existentes) * 0.5:
        aviso = (
            f"Leitura suspeita: {len(records)} navio(s) encontrados contra "
            f"{len(existentes)} já cadastrados. Remoção de navios sumidos foi "
            f"pulada por segurança nessa sincronização."
        )
        logger.warning("%s: %s", terminal_id, aviso)
        return aviso

    for existing in a_remover:
        session.delete(existing)

    # Os que sumiram agora (e ainda não estavam marcados) só ganham a
    # marca de "sumido" — a remoção de verdade só acontece depois do
    # prazo de carência, no bloco acima, numa sincronização futura.
    for existing in sumidos:
        if existing.sumido_em is None:
            existing.sumido_em = agora
            session.add(existing)

    return None


def registrar_status(
    session, terminal: str, registros: Optional[int] = None, erro: Optional[str] = None
) -> None:
    """Marca quando essa fonte (scraper automático ou upload manual) foi
    sincronizada por último, mesmo que nenhum navio tenha mudado.

    Se `registros` for None (caso de erro), mantém a última contagem boa
    conhecida em vez de zerar — assim a tela não mostra "0 navios" quando
    na real os dados antigos continuam válidos no banco, só essa
    tentativa específica de sincronizar é que falhou."""
    status = session.get(SyncStatus, terminal)
    if status is None:
        status = SyncStatus(
            terminal=terminal, atualizado_em=agora_brasilia(), registros=registros or 0, erro=erro
        )
    else:
        status.atualizado_em = agora_brasilia()
        if registros is not None:
            status.registros = registros
        status.erro = erro
    session.add(status)
    session.commit()


def run_sync() -> dict:
    init_db()
    results = {}

    # Consultado uma única vez por rodada e compartilhado entre os
    # terminais (evita repetir a mesma requisição). Se falhar, seguimos
    # sem RAP nessa rodada — não é motivo pra derrubar a sincronização.
    try:
        rap_lookup = fetch_rap_por_navio()
    except Exception:
        logger.warning("Falha ao consultar RAP na Autoridade Portuária de Santos", exc_info=True)
        rap_lookup = {}

    with get_session() as session:
        for scraper in ACTIVE_SCRAPERS:
            try:
                records = scraper.fetch()
                if not records:
                    # Terminais como BTP/Embraport sempre têm navios; vazio
                    # aqui é sinal de falha temporária no site/scraper, não
                    # "nenhum navio de verdade" — tratamos como erro pra não
                    # sobrescrever os dados bons já gravados com um "0" falso.
                    raise RuntimeError(
                        "Nenhum registro retornado (provável falha temporária no site do terminal)"
                    )

                if scraper.terminal_id == "btp":
                    # A BTP já expõe o RAP na própria consulta.
                    for record in records:
                        record["rap"] = record.get("fonte_raw_id")
                else:
                    enriquecer_com_rap(records, scraper.terminal_id, rap_lookup)

                aviso = sincronizar_terminal(session, scraper.terminal_id, records)
                session.commit()
                results[scraper.terminal_id] = len(records)
                logger.info("Sincronizado %s: %d registros", scraper.terminal_id, len(records))
                if aviso:
                    registrar_status(session, scraper.terminal_id, erro=aviso)
                else:
                    registrar_status(session, scraper.terminal_id, len(records))
            except Exception as exc:  # noqa: BLE001
                logger.exception("Falha ao sincronizar %s", scraper.terminal_id)
                results[scraper.terminal_id] = f"erro: {exc}"
                registrar_status(session, scraper.terminal_id, erro=str(exc))
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run_sync())
