"""
Santos Brasil Tecon — PENDENTE.

A Santos Brasil disponibiliza uma API oficial e gratuita para clientes
("Santos Brasil Dev" / Integra Aqui: https://www.santosbrasil.com.br/integraaqui/),
com um produto dedicado "Lista de Atracação" (Lista Especial, Lista Geral,
Lista Armador) — inclusive com limite de requisição documentado.

Isso é MUITO melhor que raspar a página pública (que só carrega os dados
via JavaScript após o load, então scraping simples não funciona nela).

O QUE FALTA:
1. Criar/usar uma conta de cliente Santos Brasil na Área do Cliente
   (https://www.santosbrasil.com.br/v2021/area-do-cliente) e solicitar
   acesso à API "Lista de Atracação" no portal Integra Aqui.
   -> Isso precisa ser feito por vocês (envolve CNPJ/dados da empresa);
      eu não posso criar contas ou me autenticar em nome de vocês.
2. Depois de ter as credenciais (provavelmente client_id/client_secret ou
   API key), me repassem a documentação de autenticação da API de Lista
   de Atracação e eu implemento este scraper usando requests direto
   (sem precisar de navegador, mais estável e dentro dos termos de uso).

Alternativa se o acesso à API demorar: usamos os parâmetros de URL que
vocês já mapearam (unidade, lista, atracadouro, dataInicial) + inspeção
via DevTools/Chrome para achar o endpoint JSON que a própria página
consome — mas isso é scraping "por fora" e mais frágil a mudanças no site.
"""
from typing import Any, Dict, List

from .base import TerminalScraper


class SantosBrasilScraper(TerminalScraper):
    terminal_id = "santos_brasil"

    def fetch(self) -> List[Dict[str, Any]]:
        raise NotImplementedError(
            "Aguardando credenciais da API Integra Aqui (Lista de Atracação) "
            "ou o endpoint JSON interno capturado via DevTools."
        )
