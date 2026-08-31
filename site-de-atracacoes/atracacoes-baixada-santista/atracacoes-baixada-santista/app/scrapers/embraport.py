"""
DP World / Embraport.

HISTÓRICO — como esse scraper já foi implementado (até 2026-08-31): via
Playwright, abrindo https://www.embraportonline.com.br/Navios/Escala,
expandindo "Filtros", clicando "Todos"/"Pesquisar" e lendo a tabela HTML
renderizada. Funcionava, mas tinha um problema crônico: a coluna
"Previsão de Atracação" é calculada de forma assíncrona pelo próprio
site (Knockout.js) — enquanto o cálculo não termina, a célula mostra um
valor temporário idêntico ao de "Previsão Chegada", e não dava pra
confiar num tempo fixo de espera pra saber quando o valor real (e
diferente) já tinha chegado. 3 tentativas de ajustar esse tempo de
espera não resolveram de forma confiável.

ATUAL (desde 2026-08-31) — API DIRETA, achada inspecionando a aba
Network do navegador: a própria tela usa uma chamada AJAX/JSON pra
buscar os dados, sem precisar renderizar nada — muito mais rápida e sem
o problema de timing acima, porque o valor já vem pronto do servidor
(não depende de um cálculo assíncrono no navegador).

    POST https://www.embraportonline.com.br/Navios/buscarEscalaPesquisa
    Form Data: prNroOperacao, prDataInicial, prDataFinal, prArmador,
               prServico, skipresult, takeresult

Devolve um array JSON (sem paginação confiável documentada — por
segurança, pedimos em lotes e paramos quando um lote vier menor que o
pedido). Cada item tem um schema bem mais rico que o da tabela HTML, mas
com nomes de campo enganosos — confirmado comparando com um navio já
com atracação/desatracação reais (ZIM USA, viagem 17W, Visit=ZUSA17W,
ATB real = 21/08/2026 08:56):

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
Unix em ms, formato clássico de serialização JSON do ASP.NET) dos
campos irmãos sem sufixo.

O campo STATUS/ESTADO ("Previsto"/"Em Operação"/"Desatracado") já vem
junto nessa mesma consulta — não precisa mais de uma segunda chamada
separada pra pegar navios confirmados (a antiga aba "Desatracados").
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from .base import TerminalScraper

URL_PAGINA = "https://www.embraportonline.com.br/Navios/Escala"
URL_API = "https://www.embraportonline.com.br/Navios/buscarEscalaPesquisa"

_DATE_FMT = "%d/%m/%Y %H:%M"

# Tamanho do lote em cada página da API — grande o suficiente pra pegar
# tudo numa única chamada na prática (a lista costuma ter poucas
# centenas de navios), mas com paginação real como segurança caso a API
# limite o retorno por página no futuro.
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
            # Sem esse header a primeira tentativa (2026-08-31) demorou
            # mais de 60s e deu timeout — o servidor pode tratar
            # requisições sem Referer de forma mais lenta/suspeita.
            "Referer": URL_PAGINA,
        })
        # Visita a página normal primeiro — não sabemos se a API exige
        # algum cookie de sessão estabelecido nessa visita, mas replicar
        # o comportamento de um navegador de verdade não tem custo e
        # evita esse risco.
        try:
            session.get(URL_PAGINA, timeout=30)
        except Exception:
            pass

        itens: List[Dict[str, Any]] = []
        skip = 0
        for _ in range(_MAX_PAGINAS):
            resp = session.post(
                URL_API,
                data={
                    "prNroOperacao": "",
                    "prDataInicial": "",
                    "prDataFinal": "",
                    "prArmador": "",
                    "prServico": "",
                    "skipresult": skip,
                    "takeresult": _TAKE_POR_PAGINA,
                },
                timeout=120,
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