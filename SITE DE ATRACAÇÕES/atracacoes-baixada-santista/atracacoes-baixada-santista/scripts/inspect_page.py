"""
Script de inspeção — RODE ISSO NO SEU COMPUTADOR (não no sandbox do Claude).

Ele abre a página com um navegador automatizado (Playwright), clica em
"Pesquisar" se existir, espera a tabela carregar via JS, e imprime:
  - todas as tabelas encontradas na página
  - o cabeçalho (colunas) de cada uma
  - as 2 primeiras linhas de dados

Também salva o HTML completo renderizado em ./capturas/<slug>.html,
pra eu poder analisar com calma se precisar.

COMO USAR:
    pip install playwright
    playwright install chromium
    python scripts/inspect_page.py "https://novo-tas.btp.com.br/ConsultasLivres/ListaAtracacaoIndex"

Depois roda de novo pro Santos Brasil e pro Embraport:
    python scripts/inspect_page.py "https://www.santosbrasil.com.br/v2021/lista-de-atracacao?unidade=tecon-santos&lista=lista-de-atracacao&atracadouro=TECON"
    python scripts/inspect_page.py "https://www.embraportonline.com.br/Navios/Escala"

Me manda de volta o que aparecer no terminal (ou os arquivos .html da
pasta capturas/) que eu já escrevo o parser de cada terminal.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT_DIR = Path(__file__).parent.parent / "capturas"
OUT_DIR.mkdir(exist_ok=True)


def slugify(url: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", url.lower()).strip("-")[:60]


def inspect(url: str) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        print(f"\n=== Abrindo {url} ===")
        page.goto(url, wait_until="networkidle", timeout=60000)

        # Tenta clicar em qualquer botão/link com texto "Pesquisar" ou "Buscar".
        for texto in ["Pesquisar", "Buscar", "Search"]:
            try:
                locator = page.get_by_text(texto, exact=False).first
                if locator.count() > 0:
                    print(f"Clicando em botão com texto '{texto}'...")
                    locator.click(timeout=5000)
                    page.wait_for_load_state("networkidle", timeout=15000)
                    break
            except Exception:
                continue

        # Dá um tempo extra pra chamadas AJAX mais lentas.
        page.wait_for_timeout(3000)

        html = page.content()
        fname = OUT_DIR / f"{slugify(url)}.html"
        fname.write_text(html, encoding="utf-8")
        print(f"HTML completo salvo em: {fname}")

        tables = page.query_selector_all("table")
        print(f"\n{len(tables)} tabela(s) encontrada(s) na página.\n")

        for i, table in enumerate(tables):
            headers = [
                h.inner_text().strip()
                for h in table.query_selector_all("th")
            ]
            rows = table.query_selector_all("tbody tr") or table.query_selector_all("tr")
            print(f"--- Tabela {i} ---")
            print(f"Colunas ({len(headers)}): {headers}")
            print(f"Total de linhas: {len(rows)}")
            for row in rows[:2]:
                cells = [c.inner_text().strip() for c in row.query_selector_all("td")]
                if cells:
                    print(f"  exemplo: {cells}")
            print()

        browser.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print('Uso: python scripts/inspect_page.py "https://..."')
        sys.exit(1)
    inspect(sys.argv[1])
