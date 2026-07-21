"""
DP World / Embraport.

Página: https://www.embraportonline.com.br/Navios/Escala

Fluxo pra pegar o máximo de registros (confirmado pelo cliente):
  1. Expandir o painel "Filtros" (começa recolhido).
  2. Na seção "Escala de Navios", selecionar a aba "Todos".
  3. Clicar em "Pesquisar".
  4. A lista carrega por scroll infinito — rolar até o fim repetidamente
     até o número de navios parar de crescer.

A tabela só é preenchida via JS (Knockout.js) depois desse fluxo, daí o
uso de Playwright.

Colunas da aba "Todos" (datas previstas): Navio, Viagem, Visita, Armador,
Serviço, Berço, Chegada, Previsão de Abertura Gate, Abertura de Gate,
Deadline (Armador), Previsão Chegada, Previsão de Atracação, Previsão de
Saída, Detalhes (datas no formato dd/mm/aaaa HH:MM, sem segundos).

Bug do próprio site: várias colunas de data compartilham o mesmo atributo
HTML `nomecoluna` (ex.: "Chegada", "Previsão Chegada" e "Previsão de
Atracação" são todas `AtracacaoDATA`). Por isso o parser identifica cada
coluna pelo texto visível do cabeçalho (único), não pelo atributo — ao
contrário do que a primeira versão deste scraper fazia (o que fazia o ETA
nunca ser capturado, já que o atributo que o código antigo procurava
nunca existiu de verdade).

Dados CONFIRMADOS (atracação/operação/saída reais) não aparecem na aba
"Todos" — só na aba "Desatracados", e só depois de preencher o filtro
"Período Inicial" (também sofre do mesmo bug de nomecoluna duplicado:
"Atracado", "Início da Operação" e "Fim da Operação" dividem o atributo
`AberturaGateDATA`). O campo de data é `#edDataInicial`; preencher via
`.fill()` não funciona (o app usa uma máscara que só reage a eventos de
teclado de verdade) — precisa simular digitação com `page.keyboard.type`.
Mesmo com o filtro certo, a aba retorna algumas linhas de navios ainda
não atracados (ruído) — por isso só confiamos numa linha quando a coluna
"Atracado" vem preenchida.

A página tem outras tabelas que não são a de navios — uma de lookup
(0 colunas, pares ['Viagem', 'Sigla Navio']) e, se a Data Inicial for
preenchida com um valor que o app não reconhece, uma de registros
"STOPPAGE" (paralisação, não é navio). Identificamos a tabela certa pelo
conteúdo (procurando a coluna "Navio" via atributo `nomecoluna`), em vez
de assumir "a primeira tabela da página".

A tabela real também tem 2 <tbody>: o primeiro vem sempre vazio
(placeholder do template Knockout) e o segundo (com o binding
"foreach") é onde as linhas de verdade ficam.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from .base import TerminalScraper, run_in_thread

URL = "https://www.embraportonline.com.br/Navios/Escala"

# Chave = texto visível do cabeçalho (minúsculo). Coluna ausente daqui =
# ignorada (não faz parte do schema unificado, é redundante, ou é
# ambígua demais pra confiar — ver docstring do módulo).
_TEXT_MAP = {
    "navio": "navio",
    "viagem": "viagem",
    "visita": "fonte_raw_id",
    "berço": "berco",
    "previsão de abertura gate": "previsao_abertura_gate",
    "abertura de gate": "abertura_gate",
    "deadline (armador)": "deadline_carga",
    "previsão chegada": "eta",
    "previsão de atracação": "etb",
    "previsão de saída": "etd",
}

_DATE_FIELDS = {
    "deadline_carga", "abertura_gate", "previsao_abertura_gate", "eta", "etb", "etd",
}

_DATE_FMT = "%d/%m/%Y %H:%M"

_TABLE_WITH_NAVIO_SELECTOR = 'table:has(th[nomecoluna="NAVIO" i])'


def _parse_date(value: str) -> Optional[datetime]:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, _DATE_FMT)
    except ValueError:
        return None


def _find_navio_table(soup: BeautifulSoup):
    """Acha a tabela cujo cabeçalho tem uma coluna com nomecoluna="NAVIO"
    (case-insensitive) — a página tem outras tabelas irrelevantes."""
    for table in soup.find_all("table"):
        for th in table.find_all("th"):
            if (th.get("nomecoluna") or "").strip().upper() == "NAVIO":
                return table
    return None


def _linhas_com_texto(html: str):
    """Devolve (headers_texto_em_lista, [linhas como dict header->texto])
    da tabela de navios, usando o texto visível do cabeçalho como chave
    (não o atributo `nomecoluna`, que se repete em várias colunas)."""
    soup = BeautifulSoup(html, "html.parser")
    table = _find_navio_table(soup)
    if table is None:
        return [], []

    header_row = table.find("thead") or table.find("tr")
    headers = [th.get_text(strip=True).lower() for th in header_row.find_all("th")]

    bodies = table.find_all("tbody")
    body = next((b for b in bodies if b.find("tr")), None) or table

    linhas = []
    for tr in body.find_all("tr"):
        cells = tr.find_all("td")
        if not cells or len(cells) != len(headers):
            continue
        linhas.append(dict(zip(headers, [c.get_text(strip=True) for c in cells])))
    return headers, linhas


def _parse_confirmados(html: str) -> Dict[str, Dict[str, datetime]]:
    """Extrai os dados confirmados (atracação/saída reais) da aba
    "Desatracados". Devolve {fonte_raw_id: {"atb"/"atd": datetime}}.

    Só confia numa linha quando a coluna "Atracado" vem preenchida — a
    aba às vezes lista navios que ainda nem atracaram (ruído do próprio
    site). A coluna "Chegada" dessa aba é IGNORADA de propósito: ela
    sempre vem idêntica ao ETA (Previsão Chegada), ou seja, não é um dado
    confirmado de verdade — é o mesmo valor previsto reaparecendo com
    outro rótulo (mais um efeito do bug de `nomecoluna` duplicado)."""
    _, linhas = _linhas_com_texto(html)

    resultado: Dict[str, Dict[str, datetime]] = {}
    for linha in linhas:
        visita = linha.get("visita")
        atracado = linha.get("atracado")
        if not visita or not atracado:
            continue

        confirmados: Dict[str, datetime] = {"atb": _parse_date(atracado)}
        saida = linha.get("saída")
        if saida:
            confirmados["atd"] = _parse_date(saida)
        resultado[visita] = confirmados

    return resultado


class EmbraportScraper(TerminalScraper):
    terminal_id = "dp_world"

    def fetch(self) -> List[Dict[str, Any]]:
        html_previstos = run_in_thread(lambda: self._render("Todos"))
        records = self._parse(html_previstos)

        try:
            data_inicial = (datetime.now() - timedelta(days=30)).strftime("%d/%m/%Y")
            html_confirmados = run_in_thread(
                lambda: self._render("Desatracados", data_inicial)
            )
            confirmados = _parse_confirmados(html_confirmados)
            for record in records:
                extra = confirmados.get(record.get("fonte_raw_id"))
                if extra:
                    record.update(extra)
        except Exception:
            # A confirmação de atracação/operação é um complemento — se
            # essa segunda consulta falhar, não derruba a sincronização
            # inteira (os dados previstos, obrigatórios, já foram obtidos).
            pass

        return records

    def _render(self, aba: str = "Todos", data_inicial: Optional[str] = None) -> str:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(URL, wait_until="networkidle", timeout=60000)

            # O app (Knockout.js) demora mais que o "networkidle" pra
            # terminar de desenhar os filtros.
            page.wait_for_timeout(5000)

            # O painel "Filtros" começa recolhido; os campos só ficam
            # visíveis/clicáveis depois de expandir essa seção.
            try:
                page.get_by_text("Filtros", exact=True).first.click(timeout=10000)
                page.wait_for_timeout(1000)
            except Exception:
                pass

            if data_inicial:
                try:
                    campo = page.locator("#edDataInicial")
                    campo.click()
                    campo.fill("")
                    # O campo usa uma máscara que só reage a eventos de
                    # teclado reais — .fill() sozinho não commitava o
                    # valor no observable do Knockout.
                    page.keyboard.type(data_inicial, delay=80)
                    page.keyboard.press("Tab")
                    page.wait_for_timeout(500)
                except Exception:
                    pass

            try:
                page.get_by_text(aba, exact=True).first.click(timeout=15000)
                page.wait_for_timeout(1000)
            except Exception:
                pass

            try:
                btn = page.get_by_text("Pesquisar", exact=True).first
                btn.scroll_into_view_if_needed(timeout=10000)
                page.wait_for_timeout(500)
                btn.click(timeout=15000)
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass

            # A pesquisa demora pra responder, e o número de linhas oscila
            # entre 0 e um valor transitório enquanto isso (ex: um
            # "Aguarde..." passageiro). Só aceitamos quando a contagem
            # fica igual em duas checagens seguidas (2s de intervalo).
            navio_table = page.locator(_TABLE_WITH_NAVIO_SELECTOR)
            previous_count = -1
            stable_count = 0
            for _ in range(20):
                count = navio_table.locator("tbody tr").count()
                if count > 0 and count == previous_count:
                    stable_count = count
                    break
                previous_count = count
                page.wait_for_timeout(2000)

            # A lista carrega por scroll infinito (só busca mais navios
            # quando o usuário rola a tela) — rola até o fim repetidamente
            # até a contagem parar de crescer.
            if stable_count > 0:
                rows_locator = navio_table.locator("tbody tr")
                last_count = stable_count
                for _ in range(15):
                    rows_locator.last.scroll_into_view_if_needed(timeout=5000)
                    page.wait_for_timeout(2000)
                    new_count = rows_locator.count()
                    if new_count <= last_count:
                        break
                    last_count = new_count
                stable_count = last_count

            if stable_count > 0:
                # Pega o outerHTML da tabela diretamente, no exato momento
                # em que sabemos que está estável — page.content() (a
                # página inteira) chega tarde demais, o app já limpou a
                # tabela de novo até lá.
                html = navio_table.evaluate("el => el.outerHTML")
            else:
                html = ""

            browser.close()
            return html

    def _parse(self, html: str) -> List[Dict[str, Any]]:
        headers, linhas = _linhas_com_texto(html)
        if not headers:
            return []

        rows_out: List[Dict[str, Any]] = []
        for linha in linhas:
            # "ata" nunca é preenchido por esse scraper (ver docstring do
            # módulo) — fica explícito em None pra limpar qualquer valor
            # antigo que tenha ficado gravado no banco por uma versão
            # anterior do parser, já que o upsert só sobrescreve as
            # chaves presentes no dict.
            record: Dict[str, Any] = {"terminal": self.terminal_id, "ata": None}
            for header_texto, texto in linha.items():
                field = _TEXT_MAP.get(header_texto)
                if field is None:
                    continue
                record[field] = _parse_date(texto) if field in _DATE_FIELDS else (texto or None)

            if record.get("navio"):
                rows_out.append(record)

        return rows_out


if __name__ == "__main__":
    import json

    data = EmbraportScraper().fetch()
    print(json.dumps(data[:3], indent=2, ensure_ascii=False, default=str))
    print(f"\nTotal de registros: {len(data)}")
