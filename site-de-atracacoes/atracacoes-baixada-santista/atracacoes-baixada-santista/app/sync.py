from __future__ import annotations

import logging
from datetime import timedelta
from typing import List, Optional

from sqlalchemy import or_
from sqlmodel import func, select

from .database import get_session, init_db
from .models import Atracacao, RapConhecido, SyncStatus
from .scrapers.base import TerminalScraper
from .scrapers.btp import BTPScraper
from .scrapers.embraport import EmbraportScraper
from .scrapers.porto_santos import (
    RapLookup,
    carregar_rap_conhecido,
    enriquecer_com_rap,
    fetch_rap_por_navio,
    mesclar_rap_lookup,
    persistir_rap_conhecido,
)
from .scrapers.santos_brasil import fetch_gate_data
from .timezone import agora_brasilia

logger = logging.getLogger("sync")

# Um navio que sumiu da leitura do terminal (ex.: desatracou e saiu da
# lista) só é removido do banco depois de ficar sumido por esse tanto de
# dias seguidos — assim ele continua visível/pesquisável no site por um
# tempo depois da operação terminar, em vez de desaparecer na hora.
SUMIDO_GRACE_DAYS = 30

# Além da carência acima (que só conta a partir de quando o navio some da
# leitura do terminal — e alguns terminais mantêm navios já operados
# listados por semanas), removemos direto pela data real de ATB/ATD: se
# já passou desse tanto de dias desde a atracação/saída confirmada, o
# registro sai do banco independente de ainda aparecer na fonte ou não.
# O mesmo prazo também é usado como referência quando nem ATA, ATB nem
# ATD nunca foram preenchidos (a Autoridade Portuária de Santos às vezes
# omite esses eventos) — nesse caso usamos o ETA como base.
ATB_ATD_GRACE_DAYS = 30

# Quanto tempo guardar um avistamento de RAP no cache persistente
# (app.models.RapConhecido) depois da ETA daquela viagem. Um pouco mais
# generoso que ATB_ATD_GRACE_DAYS de propósito: o registro de atracação
# correspondente pode demorar um pouco mais que isso pra ser removido
# (ver remover_atracacoes_antigas), e não custa nada manter o avistamento
# de RAP um pouco além disso.
RAP_CACHE_GRACE_DAYS = 45

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
    limpa `sumido_em` (o navio voltou a aparecer no terminal).

    Um valor None no registro novo NUNCA apaga um valor já conhecido no
    banco — alguns terminais somem com a previsão (ETA/ETB/ETD) do
    próprio site assim que a confirmação real (ATA/ATB/ATD) acontece, e
    sem essa checagem a previsão seria perdida na sincronização seguinte.

    EXCEÇÃO: "rap" é recalculado do zero a cada sincronização (ver
    enriquecer_com_rap em scrapers/porto_santos.py) — None ali é uma
    resposta deliberada ("não achamos mais correspondência confiável pra
    essa viagem"), não a ausência do dado, então deixamos sobrescrever
    mesmo quando vier None. Sem essa exceção, um RAP que deixou de ser
    válido (ex.: já foi usado por engano numa viagem errada, ver bug de
    2026-09-08) ficaria preso no banco pra sempre."""
    stmt = select(Atracacao).where(
        Atracacao.terminal == record.get("terminal"),
        Atracacao.navio == record.get("navio"),
        Atracacao.viagem == record.get("viagem"),
    )
    existing = session.exec(stmt).first()

    if existing:
        for key, value in record.items():
            if value is None and key != "rap":
                continue
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


def remover_atracacoes_antigas(session) -> int:
    """Remove atracações obsoletas, direto pela data real (sem depender do
    navio ter sumido da leitura do terminal primeiro — alguns terminais
    mantêm navios já operados na listagem por semanas, o que atrasava
    demais a carência antiga baseada só em `sumido_em`). Dois critérios:

    1. O evento confirmado mais recente (ATD, ou ATB quando não há ATD)
       já passou de ATB_ATD_GRACE_DAYS dias.
    2. Nenhum evento real (ATA/ATB/ATD) foi preenchido — a Autoridade
       Portuária de Santos às vezes omite esses dados — mas o ETA já
       passou do mesmo prazo, então o registro claramente ficou parado/
       obsoleto."""
    limite = agora_brasilia() - timedelta(days=ATB_ATD_GRACE_DAYS)
    stmt = select(Atracacao).where(
        or_(
            Atracacao.atd < limite,
            (Atracacao.atd.is_(None)) & (Atracacao.atb < limite),  # type: ignore[union-attr]
            (
                Atracacao.ata.is_(None)
                & Atracacao.atb.is_(None)
                & Atracacao.atd.is_(None)
                & (Atracacao.eta < limite)
            ),
        )
    )
    antigos = session.exec(stmt).all()
    for existing in antigos:
        session.delete(existing)
    return len(antigos)


def remover_rap_conhecido_antigo(session) -> int:
    """Remove do cache persistente (RapConhecido) avistamentos cuja ETA já
    passou de RAP_CACHE_GRACE_DAYS — depois desse prazo o registro de
    atracação correspondente já deveria ter sido removido também (ver
    remover_atracacoes_antigas), então não sobra nada pra esse
    avistamento casar. Sem essa limpeza, o cache cresceria pra sempre."""
    limite = agora_brasilia() - timedelta(days=RAP_CACHE_GRACE_DAYS)
    stmt = select(RapConhecido).where(RapConhecido.eta < limite)
    antigos = session.exec(stmt).all()
    for existing in antigos:
        session.delete(existing)
    return len(antigos)


def reaplicar_rap_santos_brasil(session, rap_lookup: RapLookup) -> int:
    """Preenche o RAP dos navios da Santos Brasil que JÁ estão no banco,
    usando o mesmo casamento por navio + ETA do resto do sistema.

    A Santos Brasil não tem scraper automático (a lista principal vem por
    upload manual), então antes o RAP dela só era recalculado no momento
    de cada upload — e um navio que entrava na lista da Autoridade
    Portuária depois do upload ficava sem RAP até o próximo (caso real,
    2026-10-20: APL NEW JERSEY, COPIAPO e MSC PERLE com RAP disponível na
    fonte e no cache, mas em branco no site desde o upload da véspera).

    Só preenche ou corrige quando acha correspondência — nunca apaga um
    RAP já salvo, porque o upload pode ter casado usando uma entrada sem
    ETA que não fica no cache (ver persistir_rap_conhecido). Devolve
    quantos registros mudaram."""
    existentes = session.exec(
        select(Atracacao).where(Atracacao.terminal == "santos_brasil")
    ).all()
    registros = [{"navio": e.navio, "eta": e.eta} for e in existentes]
    enriquecer_com_rap(registros, "santos_brasil", rap_lookup)

    atualizados = 0
    for existing, registro in zip(existentes, registros):
        novo = registro.get("rap")
        if novo and novo != existing.rap:
            existing.rap = novo
            existing.atualizado_em = agora_brasilia()
            session.add(existing)
            atualizados += 1
    return atualizados


def sincronizar_gate_santos_brasil(session) -> dict:
    """Atualiza deadline/gate dos navios da Santos Brasil que JÁ estão
    cadastrados (via upload manual do arquivo principal), usando a API
    pública de Lista de Recebimento (`fetch_gate_data`). Não cria navio
    novo — falta ETA/ETB pra isso, e essa API só cobre navios de
    exportação — só complementa quem já existe, casando por
    `fonte_raw_id`. Retorna {"atualizados": quantos mudaram nessa
    rodada, "cobertos": quantos navios têm gate vindo da API}."""
    gate_por_id = fetch_gate_data()
    if not gate_por_id:
        return {"atualizados": 0, "cobertos": 0}

    stmt = select(Atracacao).where(Atracacao.terminal == "santos_brasil")
    existentes = session.exec(stmt).all()

    atualizados = 0
    cobertos = 0
    for existing in existentes:
        gate = gate_por_id.get(existing.fonte_raw_id)
        if not gate:
            continue
        cobertos += 1
        mudou = False
        for campo in ("deadline_carga", "previsao_abertura_gate", "abertura_gate"):
            valor = gate.get(campo)
            if valor and getattr(existing, campo) != valor:
                setattr(existing, campo, valor)
                mudou = True
        if mudou:
            existing.atualizado_em = agora_brasilia()
            session.add(existing)
            atualizados += 1
    return {"atualizados": atualizados, "cobertos": cobertos}


def run_gate_sync() -> int:
    """Roda a cada GATE_SYNC_INTERVAL_MINUTES (ver scheduler.py) pra
    manter deadline/gate da Santos Brasil atualizados sem depender do
    próximo upload manual do arquivo principal. Registra um status
    próprio ("santos_brasil_gate") pra ficar visível na tela quando essa
    atualização automática rodou pela última vez."""
    init_db()
    with get_session() as session:
        try:
            resultado = sincronizar_gate_santos_brasil(session)
            if resultado["atualizados"]:
                session.commit()
                logger.info(
                    "Gate Santos Brasil: %d navio(s) atualizado(s) via API (%d cobertos no total)",
                    resultado["atualizados"], resultado["cobertos"],
                )
            registrar_status(session, "santos_brasil_gate", resultado["cobertos"])
            return resultado["atualizados"]
        except Exception:  # noqa: BLE001
            logger.exception("Falha ao sincronizar gate da Santos Brasil via API")
            registrar_status(session, "santos_brasil_gate", erro="Falha ao consultar a API de gate")
            return 0


def contar_ativos(session, terminal_id: str) -> int:
    """Quantos navios desse terminal estão de fato no banco agora — usado
    pro status mostrado no site, pra não confundir com o tamanho de uma
    leitura pontual (que pode vir parcial por instabilidade do site do
    terminal, mesmo sem erro, e nesses casos a carência de 30 dias já
    protege os dados; só o número mostrado ficava enganoso)."""
    stmt = select(func.count()).select_from(Atracacao).where(Atracacao.terminal == terminal_id)
    return session.exec(stmt).one()


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
    # terminais (evita repetir a mesma requisição). Se falhar, seguimos só
    # com o histórico já acumulado no cache (ver abaixo) — não é motivo
    # pra derrubar a sincronização. fetch_rap_por_navio já levanta erro
    # sozinho se a página vier vazia (0 navios) — sinal de instabilidade
    # no site deles, não de que ninguém tem RAP agora.
    rap_fresco: Optional[RapLookup] = None
    try:
        rap_fresco = fetch_rap_por_navio()
    except Exception:
        logger.warning("Falha ao consultar RAP na Autoridade Portuária de Santos", exc_info=True)

    with get_session() as session:
        if rap_fresco is not None:
            # Grava no cache persistente ANTES de casar — assim, mesmo
            # que essa leitura seja a única vez que uma viagem específica
            # apareça na janela rolante deles, o RAP dela já fica salvo
            # pra sempre (ver docstring de porto_santos.py).
            persistir_rap_conhecido(session, rap_fresco)

        # O casamento sempre usa a leitura fresca de agora SOMADA a todo o
        # histórico já visto e guardado no banco — assim um RAP capturado
        # uma vez não se perde nunca mais, mesmo que a leitura fresca
        # falhe nessa rodada ou a viagem já tenha saído da janela deles
        # (bug real, 2026-09-16: COSCO SHIPPING PERU 009W).
        rap_lookup = mesclar_rap_lookup(rap_fresco, carregar_rap_conhecido(session))

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
                total_atual = contar_ativos(session, scraper.terminal_id)
                results[scraper.terminal_id] = total_atual
                logger.info(
                    "Sincronizado %s: %d registros lidos, %d ativos no banco",
                    scraper.terminal_id, len(records), total_atual,
                )
                if aviso:
                    registrar_status(session, scraper.terminal_id, erro=aviso)
                else:
                    registrar_status(session, scraper.terminal_id, total_atual)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Falha ao sincronizar %s", scraper.terminal_id)
                results[scraper.terminal_id] = f"erro: {exc}"
                registrar_status(session, scraper.terminal_id, erro=str(exc))

        # A Santos Brasil não está em ACTIVE_SCRAPERS (lista principal só
        # por upload manual), mas o RAP dela pode ser atualizado a cada
        # rodada com o mesmo lookup — ver reaplicar_rap_santos_brasil.
        try:
            rap_sb = reaplicar_rap_santos_brasil(session, rap_lookup)
            if rap_sb:
                session.commit()
                logger.info("RAP Santos Brasil: %d navio(s) preenchido(s)/corrigido(s)", rap_sb)
            results["santos_brasil_rap_atualizados"] = rap_sb
        except Exception:  # noqa: BLE001
            session.rollback()
            logger.exception("Falha ao reaplicar RAP da Santos Brasil")

        # Roda pra todos os terminais de uma vez (inclusive Santos Brasil,
        # que só é atualizada pelo upload manual, não pelos scrapers acima).
        removidos = remover_atracacoes_antigas(session)
        removidos_cache = remover_rap_conhecido_antigo(session)
        if removidos or removidos_cache:
            session.commit()
            logger.info(
                "Removidas %d atracação(ões) obsoletas e %d avistamento(s) de RAP "
                "velho(s) (mais de %d/%d dias)",
                removidos, removidos_cache, ATB_ATD_GRACE_DAYS, RAP_CACHE_GRACE_DAYS,
            )
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run_sync())
