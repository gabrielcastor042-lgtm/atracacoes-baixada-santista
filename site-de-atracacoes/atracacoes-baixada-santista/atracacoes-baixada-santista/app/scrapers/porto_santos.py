"""
Autoridade Portuária de Santos — lista pública "Atracações Programadas".

Página: https://www.portodesantos.com.br/informacoes-operacionais/
operacoes-portuarias/navegacao-e-movimento-de-navios/
atracacoes-programadas/

Página pública, sem login e sem proteção anti-bot — o HTML já vem com a
tabela pronta (não precisa de Playwright, um requests.get() simples
resolve).

A coluna "Viagem" dessa lista, apesar do nome, não é o código de viagem
do armador — na prática é o número de RAP do terminal (confirmado
comparando com o RAP que o próprio portal da BTP mostra pros mesmos
navios). Como só a BTP expõe RAP na sua própria consulta, usamos essa
lista da Autoridade Portuária pra preencher o RAP também da DP World e
da Santos Brasil, casando por nome do navio + terminal ("Local").

Estrutura: a página tem uma tabela por bloco de horário (cada bloco
começa com uma linha "DD/MM/AAAA - HH:MM/HH:MM" e repete o cabeçalho).
Colunas por linha: Data, Hora, ETA, Local, Navio, IMO, Carga, Evento,
Viagem (= RAP), DUV, Mapa.

Limitações conhecidas:
  - Só lista navios ainda PROGRAMADOS (não cobre atracações já
    concluídas há mais tempo).
  - O casamento é só por nome do navio + terminal (sem um identificador
    único tipo IMO nos outros scrapers) — em teoria dois navios homônimos
    programados pro mesmo terminal na mesma janela poderiam colidir, mas
    é um cenário raro o suficiente pra aceitar nessa primeira versão.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

import requests
from bs4 import BeautifulSoup

URL = (
    "https://www.portodesantos.com.br/informacoes-operacionais/"
    "operacoes-portuarias/navegacao-e-movimento-de-navios/"
    "atracacoes-programadas/"
)

# Palavra que aparece na coluna "Local" -> nosso id de terminal.
_LOCAL_PARA_TERMINAL = {
    "btp": "btp",
    "tecon": "santos_brasil",
    "embraport": "dp_world",
}


def normalizar_navio(nome: str) -> str:
    sem_parenteses = re.sub(r"\([^)]*\)", " ", nome or "")
    return re.sub(r"\s+", " ", sem_parenteses).strip().upper()


def fetch_rap_por_navio() -> Dict[str, Dict[str, str]]:
    """Devolve {terminal_id: {navio_normalizado: rap}} a partir da lista
    de atracações programadas da Autoridade Portuária."""
    resultado: Dict[str, Dict[str, str]] = {tid: {} for tid in set(_LOCAL_PARA_TERMINAL.values())}

    resp = requests.get(URL, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = tr.find_all("td")
            if len(cells) < 9:
                continue
            local = cells[3].get_text(strip=True).lower()
            navio = cells[4].get_text(strip=True)
            rap = cells[8].get_text(strip=True)
            if not navio or not rap:
                continue

            navio_norm = normalizar_navio(navio)
            for palavra, terminal_id in _LOCAL_PARA_TERMINAL.items():
                if palavra in local:
                    resultado[terminal_id][navio_norm] = rap

    return resultado


def enriquecer_com_rap(
    records: List[Dict[str, Any]], terminal_id: str, rap_lookup: Dict[str, Dict[str, str]]
) -> None:
    """Preenche o campo "rap" de cada registro em memória (in-place),
    casando pelo nome do navio normalizado. Usado pra DP World e Santos
    Brasil — a BTP já traz o RAP direto do próprio terminal."""
    tabela = rap_lookup.get(terminal_id, {})
    for record in records:
        record["rap"] = tabela.get(normalizar_navio(record.get("navio") or ""))


if __name__ == "__main__":
    import json

    print(json.dumps(fetch_rap_por_navio(), indent=2, ensure_ascii=False))
