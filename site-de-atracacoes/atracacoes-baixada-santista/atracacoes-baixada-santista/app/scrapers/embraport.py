"""
DP World / Embraport — PENDENTE.

Página: https://www.embraportonline.com.br/Navios/Escala

Confirmado (2026-07-15): a tabela vem vazia no HTML inicial, mesmo
alterando parâmetros de URL — os filtros (Previstos/Em Operação/
Desatracados/Omitidos/Todos) são aplicados 100% no front-end via
JavaScript, que dispara uma chamada a um endpoint de dados (provavelmente
retornando JSON, chamado ao clicar em "Pesquisar" ou ao carregar a página).

O QUE FALTA:
1. Abrir a página no Chrome com o DevTools (F12) → aba "Network", filtro
   "Fetch/XHR".
2. Remover os filtros de status (deixar em "Todos") e clicar em
   "Pesquisar".
3. Localizar a requisição que retorna os dados da tabela (normalmente um
   POST para algo como /Navios/EscalaDados ou similar) e copiar:
   - URL completa
   - Método (GET/POST)
   - Payload/body enviado
   - Um exemplo da resposta (JSON)
4. Me repassar essas informações aqui no chat, ou reconectar a extensão
   "Claude in Chrome" para eu capturar isso diretamente.

Assim que tivermos o endpoint, este scraper vira uma chamada HTTP direta
(requests), sem necessidade de navegador — muito mais rápido e estável
para rodar a cada 5 minutos.
"""
from typing import Any, Dict, List

from .base import TerminalScraper


class EmbraportScraper(TerminalScraper):
    terminal_id = "dp_world"

    def fetch(self) -> List[Dict[str, Any]]:
        raise NotImplementedError(
            "Aguardando captura do endpoint JSON via DevTools/Network (ver docstring)."
        )
