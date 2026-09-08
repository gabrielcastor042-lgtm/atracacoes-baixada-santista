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
  - O casamento é por nome do navio + terminal, sem um identificador
    único tipo IMO nos outros scrapers — mas um mesmo navio aparece
    aqui várias vezes quando tem mais de uma viagem programada (cada
    linha é uma viagem, com seu próprio RAP). Por isso o casamento
    também usa a coluna ETA pra desambiguar: entre as viagens do mesmo
    navio nessa lista, ficamos com a de ETA mais próxima do ETA que o
    próprio terminal (DP World/Santos Brasil) já informou pro registro.
    Sem isso, BUG CONFIRMADO em produção (2026-09-08): todas as viagens
    futuras de um mesmo navio recebiam o RAP da última linha lida da
    página da Autoridade Portuária (ex.: STEPHANIE C com 3 viagens
    diferentes na DP World, todas mostrando o mesmo RAP). Se o registro
    do terminal não tiver ETA, ou nenhuma linha da Autoridade Portuária
    tiver ETA parseável, e houver mais de uma viagem candidata pro
    mesmo navio, preferimos deixar o RAP em branco a arriscar mostrar o
    de uma viagem errada.
"""
from __future__ import annotations

import re
import warnings
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests
import urllib3
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


# Formatos já vistos na coluna ETA dessa página (varia se vem com hora ou
# só a data) — tenta cada um até um bater.
_ETA_FORMATOS = ("%d/%m/%Y %H:%M", "%d/%m/%Y")


def _parse_eta(bruto: str) -> Optional[datetime]:
    bruto = (bruto or "").strip()
    if not bruto:
        return None
    for formato in _ETA_FORMATOS:
        try:
            return datetime.strptime(bruto, formato)
        except ValueError:
            continue
    return None


def _formatar_rap(bruto: str) -> str:
    """A coluna "Viagem" vem como "3365/2026" — padroniza pro mesmo
    formato usado em toda a plataforma (5 dígitos + espaço duplo + ano,
    igual ao que a BTP mostra), pra ficar consistente entre terminais e
    fácil de copiar e colar no sistema interno da empresa."""
    numero, _, ano = bruto.partition("/")
    if not ano:
        return bruto
    return f"{numero.strip().zfill(5)}  {ano.strip()}"


def fetch_rap_por_navio() -> Dict[str, Dict[str, List[Tuple[Optional[datetime], str]]]]:
    """Devolve {terminal_id: {navio_normalizado: [(eta, rap), ...]}} a
    partir da lista de atracações programadas da Autoridade Portuária.

    A lista é por NAVIO, não por navio+viagem já feito — por isso o valor
    é uma lista: um mesmo navio pode ter várias viagens programadas (cada
    uma com seu próprio RAP), e é `enriquecer_com_rap` quem decide qual
    delas casa com cada registro, usando a ETA."""
    resultado: Dict[str, Dict[str, List[Tuple[Optional[datetime], str]]]] = {
        tid: {} for tid in set(_LOCAL_PARA_TERMINAL.values())
    }

    try:
        resp = requests.get(URL, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    except requests.exceptions.SSLError:
        # O certificado desse site não vem com a cadeia completa (erro
        # "unable to get local issuer certificate") — navegadores toleram
        # isso buscando o certificado intermediário à parte, mas o
        # requests/OpenSSL não. Como é uma consulta pública, só de
        # leitura e sem credenciais, seguimos sem verificar o certificado
        # nesse caso específico em vez de perder o RAP inteiro.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)
            resp = requests.get(
                URL, timeout=30, headers={"User-Agent": "Mozilla/5.0"}, verify=False
            )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = tr.find_all("td")
            if len(cells) < 9:
                continue
            local = cells[3].get_text(strip=True).lower()
            navio = cells[4].get_text(strip=True)
            eta = _parse_eta(cells[2].get_text(strip=True))
            rap = cells[8].get_text(strip=True)
            if not navio or not rap:
                continue

            navio_norm = normalizar_navio(navio)
            rap_formatado = _formatar_rap(rap)
            for palavra, terminal_id in _LOCAL_PARA_TERMINAL.items():
                if palavra in local:
                    resultado[terminal_id].setdefault(navio_norm, []).append((eta, rap_formatado))

    total_navios = sum(len(tabela) for tabela in resultado.values())
    if total_navios == 0:
        # Essa página normalmente lista centenas de navios (é a lista de
        # TODO o porto) — zero é sinal de que a página veio vazia (ver
        # 2026-09-08: confirmado com um navegador de verdade também, não
        # é bloqueio anti-bot, parece instabilidade no site deles), não de
        # que ninguém tem RAP agora. Levanta erro pra quem chama tratar
        # como falha (e não sincronizar RAP nenhum nessa rodada) em vez de
        # silenciosamente apagar o RAP de todo mundo — ver sync.py.
        raise RuntimeError(
            "Lista de atracações programadas da Autoridade Portuária voltou "
            "vazia (0 navios) — provável instabilidade temporária no site "
            "deles."
        )

    return resultado


# Tolerância máxima entre a ETA que o terminal informa pro registro e a
# ETA da linha correspondente na Autoridade Portuária pra aceitar aquele
# RAP como sendo da mesma viagem (não só do mesmo navio). Generoso o
# bastante pra absorver pequena defasagem entre as duas fontes (uma
# atualiza a previsão um pouco antes da outra), mas bem menor que o
# intervalo típico entre duas viagens distintas do mesmo navio (em geral
# várias semanas, ver exemplos no bug original).
_TOLERANCIA_ETA = timedelta(days=3)


def enriquecer_com_rap(
    records: List[Dict[str, Any]],
    terminal_id: str,
    rap_lookup: Dict[str, Dict[str, List[Tuple[Optional[datetime], str]]]],
) -> None:
    """Preenche o campo "rap" de cada registro em memória (in-place),
    casando pelo nome do navio normalizado. Usado pra DP World e Santos
    Brasil — a BTP já traz o RAP direto do próprio terminal.

    Um mesmo navio pode ter mais de uma viagem candidata (ver
    `fetch_rap_por_navio`) — E MESMO QUANDO SÓ TEM UMA, ela pode não ser a
    viagem certa: a Autoridade Portuária lista principalmente viagens
    próximas, então um navio com várias viagens futuras nossas pode ter
    só a mais próxima delas na lista deles. Por isso a ETA da candidata
    sempre precisa estar a no máximo `_TOLERANCIA_ETA` da ETA que o
    próprio terminal já informou pro registro — sem isso, ou a última
    linha lida (bug original, 2026-09-08) ou a única viagem listada
    (variante do mesmo bug, achada na mesma investigação) acaba grudada
    em TODAS as viagens futuras do navio. Se não der pra confirmar a ETA
    de nenhum lado, ou nenhuma candidata cair dentro da tolerância, deixa
    o RAP em branco em vez de arriscar mostrar o de outra viagem."""
    tabela = rap_lookup.get(terminal_id, {})
    for record in records:
        candidatas = tabela.get(normalizar_navio(record.get("navio") or ""), [])
        eta_registro = record.get("eta")
        com_eta = [(eta, rap) for eta, rap in candidatas if eta is not None]

        if eta_registro is not None and com_eta:
            eta_mais_proxima, rap_mais_proximo = min(
                com_eta, key=lambda par: abs(par[0] - eta_registro)
            )
            diferenca = abs(eta_mais_proxima - eta_registro)
            record["rap"] = rap_mais_proximo if diferenca <= _TOLERANCIA_ETA else None
        elif len(candidatas) == 1 and eta_registro is None:
            # Não dá pra comparar ETA (falta de um dos lados), mas só
            # existe uma viagem candidata pra esse navio agora — não tem
            # com quem confundir.
            record["rap"] = candidatas[0][1]
        else:
            record["rap"] = None


if __name__ == "__main__":
    import json

    print(json.dumps(fetch_rap_por_navio(), indent=2, ensure_ascii=False, default=str))
