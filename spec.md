# Lector Webapp — Especificação Técnica e de Produto (`spec.md` · Screen v3)

**Versão base:** Figma **Screen v3** (arquivo `SHell`, página `"Projeto | Lector "` `124:2`) + engine `epub-translate` (`0.2.0`) em `D:\projetos\lector\epub-translate-0.2.0`.  
**Stack:** Python `>=3.11` (backend local + CLI existente) + HTML/CSS/JS puro servido localmente (sem Node, sem React/JSX, sem build).  
**Tokens visuais:** [DESIGN.md](D:/projetos/lector/DESIGN.md) e coleção Figma `Lector DS`.

---

## 1. Arquitetura de Interface — "Tradutor em uma tela" (`Screen v3`)

Diferente da v2 (5 telas com navegação no topo), a **Screen v3** concentra todo o fluxo principal em **uma única tela (`1440×950`)** com 3 colunas, uma **status row** inferior dinâmica (4 estados) e **8 overlays modais** para ações secundárias e inspeção detalhada (`User Flow 130:9124`).

### 1.1 Fluxo principal e ramos secundários (`130:9124`)
1. **Carregar livro** (`Arquivo EPUB`) → **Configurar** (`Idiomas, glossário, motor e modelo`) → **Prévia** (`Segmentos, chamadas e cache`)
2. A partir da Prévia:
   - **`Apenas simular`** → abre `overlay/simulacao-detalhes`
   - **`Iniciar tradução`** → ativa **Traduzir** (`status row` entra em `Em andamento`)
3. Durante **Traduzir**:
   - **`Cancelar`** → abre `overlay/cancelar-detalhes` → confirma parada (`Screen v3 — Parada`, `Retomar`)
   - Clicar na `status row` → expande `overlay/status-detalhes` (métricas, capítulos, log)
4. Ao concluir (`Concluída`):
   - `status row` muda para `Concluído` (`100%` + CTA **`Abrir local do arquivo`**)
   - **`Revisar`** (Card 3) → abre `overlay/revisao-detalhes` → **`Abrir Revisão e QA`** troca para `overlay/revisao-expandida` (QA + leitura lado a lado + `Salvar cópia…` / `Abrir pasta`)

---

## 2. Tokens Visuais, Estados e Laws of UX

### 2.1 Tokens (`DESIGN.md` / `Lector DS`)
- **Superfícies:** `#ffffff` (cards `r28` sem sombra e selects `r980`), `#f5f5f7` (fundo do stage, dropzone `r28`, log strip `r28`, cards de achados `r20`), `#e6e6e8` (trilha de progresso e botão primário desabilitado), `#d6d6d6` (divisores `1px`).
- **Textos e bordas:** `#1d1d1f` (Ink), `#707070` (Slate), `#86868b` (Steel — borda `1px` de selects/botões outline e texto desabilitado).
- **Acentos:**
  - `#0071e3` (Pricing Blue): único CTA primário azul ativo por tela/overlay (`height: 44px`, `r9999`, texto `#ffffff`). Hover `#0066cc`, pressionado `#0054a8`.
  - `#0066cc` (Apple Blue): links textuais (`Trocar arquivo`, `Editar`, `Como instalar`, `Ajustar`, `Revisar`, `Adicionar termo`, `Ver todos no relatório`). Pressionado `#0054a8`.
  - `#b64400` (Launch Orange): apenas texto `12px` solto sem pill (`não encontrado`, `4 incertos · 2 avisos`, `Aviso`, `incerto · seg N`).
- **Overlays:** backdrop `rgba(0, 0, 0, 0.40)`, transição fade `0.25s`, fechar ao clicar fora ou pressionar `Esc`.
- **Tipografia:** `'SF Pro', system-ui, -apple-system, sans-serif` (`400`, `500`, `600`). *(Nota: evitar `Arial` nos `<select>` para manter conformidade estrita com `DESIGN.md`.)*

### 2.2 Estados de Botões (`131:9621`)
- **Primário (`44px`, `r9999`):** Padrão `#0071e3`, Hover `#0066cc`, Pressionado `#0054a8`, Foco (anel externo visível), Desabilitado `#e6e6e8` + texto `#86868b`.
- **Secundário (`44px`, `r9999`):** Padrão `1px #86868b` transparente, Hover `#f5f5f7`, Pressionado `#e6e6e8`, Foco (anel externo), Desabilitado texto `#86868b`.
- **Link:** Padrão/Hover `#0066cc`, Pressionado `#0054a8`, Desabilitado `#86868b`.

### 2.3 Responsividade, Viewport sem Scroll e Envelope `16:9` / `21:9`
- **Decisão técnica (CSS Grid + Flexbox nativo, sem Bootstrap):**
  - *Por que não Bootstrap:* Bootstrap não resolve viewport travada em `100dvh` sem scroll nem limite `16:9` vs `21:9`, além de injetar CSS opinativo (raios/cores/resets) que conflita com os tokens estritos do [DESIGN.md](D:/projetos/lector/DESIGN.md) e adiciona dependência externa desnecessária.
  - *Solução adotada:* CSS Grid + Flexbox nativo + `clamp()` + `100dvh` (zero dependências, 100% offline e fiel ao `Lector DS`).
- **Regra de proporção `16:9` × `21:9`:**
  - `body` ocupa `100%` da tela com `background: #f5f5f7`.
  - O container principal (`.app-shell`) é centralizado com **`max-width: min(100vw, 177.78dvh)`** (`16 / 9 = 1.7778`):
    - Até **`16:9`** (ex.: `1366×768`, `1440×900`, `1920×1080`, `2560×1440`): expande **100% na largura e na altura**.
    - Acima de `16:9`, incluindo **`21:9`** (ex.: `2560×1080`, `3440×1440`): o conteúdo trava na largura equivalente a `16:9` e **apenas o fundo `#f5f5f7` expande nas laterais**.
- **Tela principal sem scroll (`height: 100dvh; overflow: hidden;`):**
  - `.app-shell` usa `display: grid; grid-template-rows: auto minmax(0, 1fr) auto;` com `padding` e `gap` fluidos via `clamp()`.
  - A área central (`columns`) usa `grid-template-columns: repeat(3, minmax(0, 1fr))` preenchendo `100%` da altura restante (`min-height: 0`).
  - Os 3 cards usam `display: flex; flex-direction: column; height: 100%;`:
    - **Card 1 (`Livro`):** a dropzone `.epub` usa `flex: 1 1 auto; min-height: 96px;` para encolher/crescer conforme a altura do monitor mantendo o rodapé (`Glossário · Editar`) alinhado.
    - **Cards 2 (`Motor`) e 3 (`Prévia`):** espaçador `flex: 1` mantém todos os rodapés (`Glossário`, `Lotes`, `Revisão`) alinhados na mesma linha horizontal.
  - **Fallback seguro para janelas muito pequenas** (`@media (max-width: 1023px), (max-height: 679px)`): libera scroll vertical e empilha colunas para nunca cortar controles em split-screen/telas minúsculas.
  - **Overlays:** `position: fixed; inset: 0;` limitado a `max-height: calc(100dvh - 48px)`, com rolagem interna apenas nas listas longas (`Capítulos`, `translate_map`, `Revisão de segmentos`).

---

## 3. Tela Principal (`Screen v3` — 4 Estados)

### 3.1 Cabeçalho (`page-header`)
- Título: **`Traduza um livro inteiro, localmente.`**
- Ações à direita (`44px`):
  - **`Apenas simular`** (Secundário outline) → abre `overlay/simulacao-detalhes`.
  - **`Iniciar tradução`** (Primário `#0071e3`) → inicia o job e atualiza a `status row`.

### 3.2 Coluna 1 — `Livro` (`card/1`)
- **Dropzone `.epub` (`#f5f5f7 r28`):** aceita drag-and-drop ou clique (`Trocar arquivo`), exibindo `<nome>.epub` e `EPUB <versão> · <N> capítulos`. Ao carregar, dispara inspeção instantânea sem chamar LLM.
- **Idiomas (`Origem → Destino`):** `Origem` pré-preenchido via OPF do livro (`Tesler`), editável; `Destino` default `preferences.json` (`pt-BR`).
- **Toggle `"Só conteúdo principal"` (`Índice e capítulos`):** ligado por padrão (`main_content_only = true`). Quando ligado, traduz apenas índice (`nav`/`ncx`) e capítulos principais, preservando capa, copyright, folha de rosto, colofão, dedicatória, glossário do livro e demais arquivos auxiliares no idioma original. A lista `skip_files` foi removida da UI.
- **Rodapé (`Glossário`):** resumo `<N> termos` + link **`Editar`** → abre `overlay/glossario-detalhes`.

### 3.3 Coluna 2 — `Motor` (`card/2`)
- **Provedor (CLI):** lista `claude`, `codex`, `ollama`, `gemini` em linhas `r980` com seleção única.
  - Detecta disponibilidade em tempo real (via `PATH` ou caminho customizado salvo em `models.json`).
  - Se instalado: exibe `instalado` (`#707070`).
  - Se ausente: exibe `não encontrado` (`#b64400`) + link **`Como instalar`** → abre `overlay/como-instalar-detalhes`.
- **Modelo:** pills de modelos sugeridos do provedor selecionado (ex.: `sonnet`, `opus`, `haiku`) + pill **`outro…`** (abre/foca input de texto para nome personalizado).
- **Toggle `"Reaproveitar cache"` (`--reuse-cache`):** permite reaproveitar segmentos compatíveis já traduzidos por outros modelos no `.tmcache`.
- **Rodapé (`Lotes`):** resumo `<batch_segments> segmentos · <timeout> s` (ex.: `60 segmentos · 600 s` do `models.json`) + link **`Ajustar`** → abre `overlay/lotes-detalhes`.

### 3.4 Coluna 3 — `Prévia` (`card/3`)
- **Métricas principais:** `<N> segmentos` e `~<M> chamadas` (atualizadas ao trocar livro, `Só conteúdo principal`, provedor, modelo, tamanho de lote ou `--reuse-cache`).
- **Cache:** `<exact> já em cache · restam ~<pending>` (e contagem cross-model quando `--reuse-cache` estiver ligado).
- **Rodapé (`Revisão`):** exibe resumo dos achados da última/atual execução (`<U> incertos · <W> avisos` em `#b64400`, ou estado limpo `0 pendências`) + link **`Revisar`** → abre `overlay/revisao-detalhes`.

### 3.5 `status row` Inferior (4 estados)
1. **Ocioso / Sem job ativo:** mostra estado pronto para traduzir ou último job interrompido recuperado do disco (`Zeigarnik` com botão **`Retomar`**).
2. **Traduzindo (`131:9774`):** percentual herói (`62%`), `Em andamento` + `~<min> restante`, botão secundário `44px` **`Cancelar`** (abre `overlay/cancelar-detalhes`), barra `#e6e6e8` / `#1d1d1f`. Clicar no corpo da barra expande `overlay/status-detalhes`.
3. **Concluído (`131:9897`):** `100%`, `Concluído · <N> segmentos · <E> erros`, CTA primário `44px #0071e3` **`Abrir local do arquivo`** (abre pasta no Explorer).
4. **Parada (`131:10020`):** `<X>%`, `Interrompida · parou no lote <k> de <total>`, CTA primário `44px #0071e3` **`Retomar`** (reaproveita `.tmcache` salvo até o último lote concluído).

---

## 4. Especificação dos 8 Overlays

1. **`overlay/status-detalhes` (`138:10204`):** painel expandido acima da `status row` (`1344×480` + base):
   - 4 métricas: `done/total` segmentos, acertos de cache, tempo médio (`X s por lote`), tempo restante.
   - Lista **`Capítulos`**: nome do arquivo, estado (`Concluído`, `Traduzindo` em `#0066cc`, `Na fila`), barra `4px` (`#e6e6e8` / `#0071e3`) e contagem `done/total`.
   - **`log strip`** (`#f5f5f7 r28`): eventos em tempo real via SSE.
   - `status row` fixada na base do painel.
2. **`overlay/glossario-detalhes` (`138:10273`):** painel `889×610` ancorado ao Card 1:
   - Coluna A: **`Mapa de tradução · translate_map`** (inputs origem/destino `r980` + remover `×` + link `Adicionar termo`).
   - Coluna B: **`Manter no original · keep_in_source`** (chips `r9999` removíveis + adicionar) e **`Nunca traduzir · never_translate`** (chips `r9999` removíveis + adicionar).
   - Ação: CTA primário **`Salvar glossário`** (persiste `glossary.json`; campo `tone` preservado no JSON).
3. **`overlay/lotes-detalhes` (`138:10386`):** painel `435×610` ancorado ao Card 2:
   - **`Tamanho dos lotes`** com 3 opções radio para o provedor selecionado:
     - **Menos lotes** (`Mais rápido`, `2×` os limites padrão do provedor; ex. `claude`: `120 seg · 4.000 palavras · ~12 chamadas`).
     - **Padrão** (`Recomendado`, valores do provedor em `models.json`; ex. `claude`: `60 seg · 2.000 palavras · ~24 chamadas`).
     - **Mais lotes** (`Mais seguro`, `0,5×` os limites padrão do provedor; ex. `claude`: `30 seg · 1.000 palavras · ~48 chamadas`).
   - Ação: CTA primário **`Aplicar`** (atualiza a prévia do Card 3 imediatamente e salva o preset/preferência).
4. **`overlay/simulacao-detalhes` (`149:10430`):** painel `435×610` ancorado ao Card 3:
   - Exibe `Simulação` (`segmentos`, `palavras`, `chamadas estimadas`) + detalhamento `Cache` (`<exact> exatos · <cross> de outros modelos` e `Restam ~<N> segmentos para traduzir`).
   - Ação: CTA primário **`Iniciar tradução`**.
5. **`overlay/revisao-detalhes` (`149:10458`):** painel `435×610` ancorado ao Card 3:
   - Resumo `<U> incertos · <W> avisos`, preview dos primeiros achados (`Aviso` / `incerto · seg N` com texto original e traduzido).
   - Ação: CTA primário **`Abrir Revisão e QA`** → troca diretamente para `overlay/revisao-expandida`.
6. **`overlay/revisao-expandida` (`149:10543`):** painel amplo `1344×610`:
   - Esquerda: **`Achados de QA`** (cards `#f5f5f7 r20`, severidade `#b64400`, arquivo/segmento, detalhe + link **`Ver todos no relatório`** que abre/visualiza `.report.md`).
   - Direita: **`Revisão de segmentos`** (somente leitura na v1; filtros `incertos (N)` / `avisos` / `todos`; leitura lado a lado `Original` × `Tradução` preservando marcadores `⟦n⟧…⟦/n⟧`).
   - Base: **`Salvar cópia…`** (Secundário `44px`) e **`Abrir pasta`** (Primário `#0071e3 44px`).
7. **`overlay/como-instalar-detalhes` (`149:10484`):** painel `435×610` ancorado ao Card 2:
   - Mostra provedor `não encontrado`, instruções objetivas de instalação, campo **`Binário`** (para o usuário informar o caminho completo do executável caso não esteja no `PATH`, salvo em `models.json`), `Modelos sugeridos` e botão primário **`Verificar de novo`** (valida executável no backend e atualiza status).
8. **`overlay/cancelar-detalhes` (`149:10512`):** popover `435×263` acima da `status row`:
   - Texto: *"Lote X de Y · done/total segmentos. O que já foi traduzido fica salvo e você pode retomar de onde parou."* + barra `#e6e6e8`/`#0071e3` + percentual.
   - Ações `44px`: **`Continuar traduzindo`** (Secundário outline) e **`Cancelar tradução`** (Primário `#0071e3`).

---

## 5. Backend Python + HTML/JS Local (`epub_translate.web`)

- **Entrada:** `python -m epub_translate.web` / script `lector-web` e `lector.bat` (sobe servidor local em `127.0.0.1:8788` e abre o navegador; CLI `cli.py` segue funcional).
- **Static assets:** `epub_translate/web_static/` (`index.html`, `styles.css`, `app.js`) sem dependências externas.
- **API REST + SSE:**
  - `GET /api/state` — retorna `models.json` (com disponibilidade real de cada binário via `shutil.which` ou caminho configurado), `preferences.json`, resumo do `glossary.json` e `last_job.json` (para retomar ao reabrir).
  - `POST /api/providers/verify` — atualiza caminho customizado do binário em `models.json` e revalida executável.
  - `POST /api/inspect` — recebe upload `.epub` (ou caminho local) e parâmetros atuais (`target_lang`, `main_content_only`, `provider`, `model`, `batch_mode`, `reuse_cache`); retorna metadados, prévia (`segments`, `words`, `calls`, `exact_cache`, `cross_cache`, `pending`) sem gastar chamadas.
  - `POST /api/jobs` — inicia tradução em background (salva preferências usadas + atualiza `last_job.json` por lote).
  - `GET /api/jobs/{id}/events` — stream SSE (`batch_started`, `batch_completed` por capítulo, `retry`, `repair`, `log`, `finished`, `cancelled`, `ts` para `"atualizado há X s"`).
  - `POST /api/jobs/{id}/cancel` — aciona `cancel_flag` cooperativa entre lotes, salva `.tmcache` e transita para `Parada`.
  - `GET /api/jobs/{id}/result` — retorna estatísticas Peak-End, achados de QA, segmentos filtráveis (`uncertain`/`warn`/`all`) e link do `.report.md` / `.epub`.
  - `GET /api/jobs/{id}/download` e `POST /api/open-folder` — download do EPUB ou abertura no Explorer.
  - `PUT /api/glossary` — valida e grava `glossary.json` (`translate_map`, `keep_in_source`, `never_translate`, `main_content_only`).
  - `PUT /api/preferences` — salva preferências e preset de lotes (`less` = `2x`, `default` = `1x`, `more` = `0.5x`).

---

## 6. Decisões Consolidadas (`2026-11-24`)

1. **Tela única + overlays (Screen v3):** substitui as 5 telas da v2; `Revisão de segmentos` permanece **somente leitura na v1**.
2. **Só conteúdo principal ligado por padrão:** filtra pré/pós-textuais não-capítulo mantendo os arquivos intactos no EPUB final (`qa.py` sem falsos positivos).
3. **Timeout e lotes:** `models.json` mantém `timeout=600` e `60 seg / 2000 palavras` para `claude` (em vez dos `300 s` mostrados no mockup da Screen v3); o ajuste de lotes aplica `2×` (`Menos lotes`), `1×` (`Padrão`) e `0,5×` (`Mais lotes`) sobre a configuração base do provedor.
4. **Fontes:** usar `'SF Pro'` / fallback de sistema conforme `DESIGN.md`, sem `Arial` nos `<select>`.
