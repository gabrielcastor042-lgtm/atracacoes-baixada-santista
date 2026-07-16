"""
Ecoporto Santos — PENDENTE.

Página: https://op.ecoportosantos.com.br/externa/LineUpListaAtracacao/

Funcionava com um GET simples (`requests`) até 2026-07-15. Depois disso
passou a bloquear com "403 Forbidden", e mesmo usando um navegador
automatizado de verdade (Playwright) o resultado é uma página de
"Access Denied" servida pela Akamai (proteção anti-bot corporativa,
detecta sinais de automação no navegador, não é só IP ou User-Agent).

Não vale a pena tentar contornar esse tipo de proteção (é um jogo de
gato-e-rato instável e esbarra em contornar deliberadamente um sistema
de segurança). O caminho certo é pedir ao Ecoporto uma forma oficial de
acesso aos dados (API, feed, ou permissão explícita) — como já estamos
fazendo com a Santos Brasil.
"""
from typing import Any, Dict, List

from .base import TerminalScraper


class EcoportoScraper(TerminalScraper):
    terminal_id = "ecoporto"

    def fetch(self) -> List[Dict[str, Any]]:
        raise NotImplementedError(
            "Bloqueado por proteção anti-bot (Akamai). Aguardando acesso oficial do Ecoporto."
        )
