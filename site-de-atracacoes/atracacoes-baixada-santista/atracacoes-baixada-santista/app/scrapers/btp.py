"""
BTP - Brasil Terminal Portuário — PENDENTE.

Página correta (obrigado ao cliente por confirmar): o sistema TAS tem uma
seção "Consultas Livres", que é PÚBLICA (não exige login):

    https://novo-tas.btp.com.br/ConsultasLivres/ListaAtracacaoIndex

(A URL antiga que eu tinha usado, portaldocliente.btp.com.br, é um portal
de cliente separado e exige login — não é essa que vamos usar.)

Confirmado (2026-07-15): a página abre normalmente sem sessão/cookie,
mas a tabela "Programação de Atracação" vem vazia no HTML — os dados são
carregados via JS/AJAX depois que o usuário aplica um filtro e clica em
"Pesquisar" (mesmo padrão do Santos Brasil e do Embraport).

O QUE FALTA:
1. Abrir a página no Chrome com DevTools (F12) → aba Network → filtro
   Fetch/XHR.
2. Selecionar um filtro (ex: "Previstos") e clicar em "Pesquisar".
3. Capturar a requisição que retorna os dados: URL, método (GET/POST),
   payload (se POST) e um exemplo de resposta.
4. Repassar essas informações aqui no chat (print serve).

Como esse endpoint é público, não deve haver necessidade de cookies de
sessão/autenticação — só reproduzir a mesma chamada com `requests`.
"""
from typing import Any, Dict, List

from .base import TerminalScraper


class BTPScraper(TerminalScraper):
    terminal_id = "btp"

    def fetch(self) -> List[Dict[str, Any]]:
        raise NotImplementedError(
            "Aguardando login manual + captura do endpoint via DevTools/Network."
        )
