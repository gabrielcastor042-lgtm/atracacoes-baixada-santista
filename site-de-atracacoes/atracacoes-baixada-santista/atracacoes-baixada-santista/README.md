# Atracações — Baixada Santista

Web app para consultar em um só lugar as atracações previstas/atuais nos
terminais: Santos Brasil Tecon, DP World (Embraport), BTP e Ecoporto Santos.

## Status por terminal

| Terminal | Status | Estratégia |
|---|---|---|
| **Ecoporto Santos** | ✅ Funcionando | Scraping HTTP simples (tabela vem pronta no HTML) |
| **Santos Brasil Tecon** | 🟡 Pendente | Existe API oficial e gratuita ("Integra Aqui") — melhor caminho, precisa de cadastro/credenciais |
| **DP World (Embraport)** | 🟡 Pendente | Dados carregados via JS; precisa capturar o endpoint JSON via DevTools |
| **BTP** | 🟡 Pendente | Público via TAS "Consultas Livres" (`novo-tas.btp.com.br/ConsultasLivres/ListaAtracacaoIndex`) — só falta capturar o endpoint via DevTools, sem login |

## Como rodar

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

- `GET /buscar?q=NOME_DO_NAVIO` — busca por navio ou viagem em todos os terminais ativos
- `GET /buscar?terminal=ecoporto` — filtra por terminal
- `POST /sync` — força uma sincronização manual
- Sincronização automática a cada 5 min (`app/scheduler.py`)

## Estratégia para os 3 terminais pendentes

Em vez de caçar a API interna via DevTools, vamos usar **navegador
automatizado (Playwright)**: o scraper abre a página pública normalmente,
aplica o filtro, clica em "Pesquisar" e lê a tabela renderizada — a
mesma coisa que um humano faria, só que programável. Não precisa de
login em nenhum dos três (BTP via "Consultas Livres" do TAS, Santos
Brasil e Embraport são todos públicos).

Rode `scripts/inspect_page.py` localmente (ver instruções no próprio
arquivo) para cada um dos 3 links abaixo e me devolva o resultado —
com isso eu já escrevo o parser final de cada terminal:

- BTP: https://novo-tas.btp.com.br/ConsultasLivres/ListaAtracacaoIndex
- Santos Brasil: https://www.santosbrasil.com.br/v2021/lista-de-atracacao?unidade=tecon-santos&lista=lista-de-atracacao&atracadouro=TECON
- Embraport: https://www.embraportonline.com.br/Navios/Escala

(Alternativa pro Santos Brasil: existe também uma API oficial gratuita
— "Integra Aqui" — que dispensa até o navegador automatizado. Vale abrir
um chamado pedindo acesso, mas não é bloqueante: o Playwright já resolve
sem depender de cadastro.)

## Próximos passos (nesta ordem de impacto)

1. Rodar `scripts/inspect_page.py` para os 3 terminais pendentes e me
   mandar o resultado (ou os .html salvos em `capturas/`).
2. Eu escrevo `app/scrapers/{btp,santos_brasil,embraport}.py` usando
   Playwright, seguindo o mesmo padrão do `ecoporto.py`.
3. Colocar em um servidor com processo contínuo (VPS, Railway, Render
   etc. — o sandbox/artifacts do Claude não sustenta processo em
   background), configurar HTTPS e, se for expor publicamente, domínio.
   Como agora envolve Chromium (Playwright), vale usar Docker.
4. Frontend de busca (hoje só a API existe) — posso montar uma tela
   simples em seguida.

## Schema unificado (`app/models.py`)

navio, viagem, terminal, berco (terminal de atracação/berço), deadline_carga,
previsao_abertura_gate, eta, etb, etd, ata, atb, atd.
