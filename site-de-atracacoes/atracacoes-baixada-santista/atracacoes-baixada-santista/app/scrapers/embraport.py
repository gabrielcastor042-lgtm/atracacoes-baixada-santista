"""
DP World / Embraport.

HISTÓRICO — até 2026-08-31 este scraper usava Playwright, abrindo
https://www.embraportonline.com.br/Navios/Escala, expandindo "Filtros",
clicando "Todos"/"Pesquisar" e lendo a tabela HTML renderizada.
Funcionava, mas tinha um problema crônico: a coluna "Previsão de
Atracação" é calculada de forma assíncrona pelo próprio site
(Knockout.js) — em testes, mais de 90% dos navios ainda não confirmados
vinham com essa coluna igual à "Previsão Chegada" (às vezes com
diferença de semanas/meses do valor real), porque nosso robô sempre
"tirava a foto" da tela antes do site terminar de atualizar esse campo
específico. Várias tentativas de esperar mais tempo não resolveram de
forma confiável.

ATUAL (desde 2026-09-01) — API DIRETA, achada inspecionando a aba
Network do navegador (a própria tela usa essa chamada AJAX/JSON pra
buscar os dados, sem depender de nenhum cálculo assíncrono no
navegador — o valor já vem pronto do servidor):

    POST https://www.embraportonline.com.br/Navios/buscarEscalaPesquisa
    Form Data: prNroOperacao, prDataInicial, prDataFinal, prArmador,
               prServico, skipresult, takeresult

CAUSA RAIZ do problema da primeira tentativa (2026-08-31, revertida):
mandávamos `prDataInicial` VAZIO. Isso faz a consulta no servidor deles
travar (timeout) ou devolver um array vazio de forma inconsistente —
provavelmente uma busca sem filtro de data nenhum é pesada demais pro
banco deles. Testado e confirmado em 2026-09-01: preenchendo
`prDataInicial` com uma data real (aqui, 30 dias atrás — mesma janela
já usada no resto do sistema), a API responde em 3-5 segundos, sempre,
de forma consistente (testado 6x seguidas sem falha nenhuma). O
`takeresult` grande (1000) NÃO é o problema — testado isoladamente.

Devolve um array JSON direto (sem paginação real observada — takeresult
não limita a quantidade retornada na prática, mas mantemos a lógica de
lote como segurança). Cada item tem um schema com nomes de campo
enganosos — confirmado comparando com um navio já com atracação/
desatracação reais (ZIM USA, viagem 17W, Visit=ZUSA17W, ATB real =
21/08/2026 08:56):

    campo bruto da API      | campo real (schema unificado) | evidência
    -------------------------|-------------------------------|----------
    PrevisaoChegadaDATA      | eta  (previsão de chegada)     | nome bate
    ETADATA                  | etb  (previsão de ATRACAÇÃO!)  | "ETA" é
                              |                                | apelido
                              |                                | errado —
                              |                                | é o que a
                              |                                | tela
                              |                                | mostra em
                              |                                | "Previsão
                              |                                | de
                              |                                | Atracação"
    ETDDATA                  | etd  (previsão de saída)       | nome bate
    ChegadaDATA               | ata  (chegada CONFIRMADA)      | 20/08 05:20,
                              |                                | antes do ATB
    ATADATA                  | atb  (atracação CONFIRMADA!)   | bate exato
                              |                                | com o ATB
                              |                                | real
                              |                                | (21/08
                              |                                | 08:56) —
                              |                                | "ATA" é
                              |                                | apelido
                              |                                | errado
    SaidaDATA                 | atd  (saída CONFIRMADA)        | 22/08 09:41,
                              |                                | depois do ATB

Os campos "*DATA" já vêm formatados como texto (dd/mm/aaaa HH:MM) —
usamos eles direto, sem precisar decodificar o "/Date(...)/ " (ticks
Unix em ms) dos campos irmãos sem sufixo.

O campo STATUS/ESTADO ("Previsto"/"Em Operação"/"Desatracado") já vem
junto nessa mesma consulta — não precisa de uma segunda chamada
separada pra pegar navios confirmados (a antiga aba "Desatracados").
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests

from .base import TerminalScraper

URL_PAGINA = "https://www.embraportonline.com.br/Navios/Escala"
URL_API = "https://www.embraportonline.com.br/Navios/buscarEscalaPesquisa"

_DATE_FMT = "%d/%m/%Y %H:%M"

# Mesma janela de retenção usada em sync.py (SUMIDO_GRACE_DAYS /
# ATB_ATD_GRACE_DAYS) — cobre navios já operados recentemente (pra
# preencher ATA/ATB/ATD) e todos os futuros (a API não limita o fim do
# período quando prDataFinal fica vazio).
_DIAS_RETROATIVOS = 30

_TAKE_POR_PAGINA = 1000
_MAX_PAGINAS = 20


def _parse_date(value: Optional[str]) -> Optional[datetime]:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, _DATE_FMT)
    except ValueError:
        return None


def _texto(item: Dict[str, Any], chave: str) -> Optional[str]:
    valor = (item.get(chave) or "").strip()
    return valor or None


class EmbraportScraper(TerminalScraper):
    terminal_id = "dp_world"

    def fetch(self) -> List[Dict[str, Any]]:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/plain, */*",
            "Referer": URL_PAGINA,
        })

        # CRÍTICO: prDataInicial NUNCA pode ficar vazio — é o que causava
        # timeout/resposta vazia (ver docstring do módulo). Sempre manda
        # uma data real.
        data_inicial = (datetime.now() - timedelta(days=_DIAS_RETROATIVOS)).strftime("%d/%m/%Y")

        itens: List[Dict[str, Any]] = []
        skip = 0
        for _ in range(_MAX_PAGINAS):
            resp = session.post(
                URL_API,
                data={
                    "prNroOperacao": "",
                    "prDataInicial": data_inicial,
                    "prDataFinal": "",
                    "prArmador": "",
                    "prServico": "",
                    "skipresult": skip,
                    "takeresult": _TAKE_POR_PAGINA,
                },
                timeout=60,
            )
            resp.raise_for_status()
            pagina = resp.json()
            if not isinstance(pagina, list) or not pagina:
                break
            itens.extend(pagina)
            if len(pagina) < _TAKE_POR_PAGINA:
                break
            skip += _TAKE_POR_PAGINA

        return self._parse(itens)

    def _parse(self, itens: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        rows_out: List[Dict[str, Any]] = []
        for item in itens:
            navio = _texto(item, "NAVIO")
            if not navio:
                continue

            deadline_texto = item.get("DryDeadlineDATA") or item.get("ReeferDeadlineDATA")

            record: Dict[str, Any] = {
                "terminal": self.terminal_id,
                "navio": navio,
                "viagem": _texto(item, "VIAGEMIN"),
                "fonte_raw_id": _texto(item, "Visit"),
                "berco": _texto(item, "Berco"),
                "eta": _parse_date(item.get("PrevisaoChegadaDATA")),
                "etb": _parse_date(item.get("ETADATA")),
                "etd": _parse_date(item.get("ETDDATA")),
                "ata": _parse_date(item.get("ChegadaDATA")),
                "atb": _parse_date(item.get("ATADATA")),
                "atd": _parse_date(item.get("SaidaDATA")),
                "previsao_abertura_gate": _parse_date(item.get("PrevisaoAberturaGateDATA")),
                "abertura_gate": _parse_date(item.get("AberturaGateDATA")),
                "deadline_carga": _parse_date(deadline_texto),
            }
            rows_out.append(record)

        return rows_out


if __name__ == "__main__":
    import json

    data = EmbraportScraper().fetch()
    print(json.dumps(data[:3], indent=2, ensure_ascii=False, default=str))
    print(f"\nTotal de registros: {len(data)}")