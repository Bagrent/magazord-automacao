# magazord-automacao

Batch product upload for the [Magazord](https://www.magazord.com.br/) platform,
driven by an Excel sheet. One row per SKU, image links in their own columns —
run one command and the store is updated instead of registering products
one at a time.

```bash
magazord validar produtos.xlsx              # check the sheet, no network
magazord enviar  produtos.xlsx              # dry run: builds every payload, sends nothing
magazord enviar  produtos.xlsx --confirmar  # actually upload

magazord catalogo ofertas.xlsx              # flip-book catalogue + PDF, no network
```

---

## ⚠️ Before the first real upload: fill in the API spec

`config/magazord.yaml` binds this project to the Magazord API. Every value in it
marked `##VERIFICAR##` is a **placeholder that has not been checked against the
official spec** — the machine this was written on could not reach
`docs.api.magazord.com.br` (blocked by network policy), so the endpoint paths and
payload field names are educated guesses, not facts.

**What is confirmed:** Magazord uses HTTP Basic Auth.
**What is not:** every path, every JSON field name, and how images are attached.

Open <https://docs.api.magazord.com.br/openapi.yaml>, then correct:

| Section in `config/magazord.yaml` | What to check |
|---|---|
| `endpoints.*.path` | The real paths for search / create / update / image |
| `endpoints.buscar_produto` | The query param that looks a product up by SKU, and where the id sits in the response |
| `campos` | The JSON key for each product field |
| `imagens.modo` | Whether Magazord accepts an image **URL** or wants a **multipart upload** |

No Python changes are needed. That file is the only thing tying the pipeline to
Magazord's contract — that is why it is a config file and not code.

Until it is filled in, `magazord validar` and `magazord enviar` (dry run) are
fully usable: they exercise the spreadsheet reading, validation, image checking
and payload building end to end.

---

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env      # then fill in the three values
```

`.env` holds your credentials and is git-ignored:

```
MAGAZORD_BASE_URL=https://SEU_DOMINIO.magazord.com.br/api/v2
MAGAZORD_USER=...
MAGAZORD_TOKEN=...
```

## The spreadsheet

`magazord modelo` writes `exemplos/modelo_produtos.xlsx` in the expected format.

| SKU | Nome | Descrição | EAN | Marca | Categoria | Preço | Estoque | … | Imagem 1 | Imagem 2 |
|-----|------|-----------|-----|-------|-----------|-------|---------|---|----------|----------|

**You do not have to rename your existing columns.** Headers are matched by
alias, ignoring case, accents, punctuation and bracketed units — `SKU`,
`Código`, `codigo do produto` and `Referência` all resolve to the same field, and
`Preço de Venda (R$)` matches `preco`. To teach it a header it doesn't know, add
the header to the right list in `config/mapeamento.yaml`.

Images work two ways, and you can mix them in one sheet:

- **Numbered columns** — `Imagem 1`, `Imagem 2`, `Foto 3`… picked up in numeric
  order (so `Imagem 10` correctly follows `Imagem 9`, not `Imagem 1`).
- **One delimited column** — `Imagens` holding several URLs separated by
  `;` `|` `,` or line breaks.

The first image found becomes the product's main image. Duplicate URLs are
dropped.

## What gets checked before anything is uploaded

`validar` and the dry run both catch, per row, without stopping the run:

- missing SKU or name
- **EAN/GTIN check digit** — a typo'd barcode is rejected rather than published
- duplicate SKUs, naming both row numbers
- prices and stock that aren't numbers, and negative values
- image URLs that 404, return HTML instead of an image, or exceed the size limit

Every rejected row lands in the report with its row number, column and reason,
so you fix the sheet once instead of discovering problems one upload at a time.

## Running an upload

```bash
# start small: the first 10 products only
magazord enviar produtos.xlsx --confirmar --limite 10

# the whole sheet
magazord enviar produtos.xlsx --confirmar
```

Useful flags:

| Flag | Effect |
|---|---|
| `--limite N` | Process at most N products — use for a pilot run |
| `--lote N` | Products per batch (default 50) |
| `--paralelismo N` | Concurrent uploads within a batch (default 4) |
| `--forcar` | Re-send everything, including unchanged products |
| `--sem-imagens` | Upload product data only |
| `--parar-no-erro` | Stop as soon as a batch has failures |

## Re-runs only send what changed

After each successful upload the product's content fingerprint is recorded in
`estado.sqlite3`. Re-running the same sheet sends only products that are new or
actually edited — so the normal workflow is to keep one master sheet, add the
newly launched products to it, and re-run.

```bash
magazord estado                      # what has been uploaded from this machine
magazord estado --esquecer CAM-001   # force one SKU to be re-sent next run
```

Duplicate protection does not rely on that file alone: before writing, each SKU
is looked up in Magazord itself, so a product that already exists is **updated,
never duplicated** — even on a fresh machine, or when someone registered it by
hand.

A failed product is never recorded as done, so the next run retries it.

## Reports

Each run writes `saida/relatorio-<timestamp>.xlsx`:

- **Envio** — one row per product: status (created / updated / unchanged /
  error), the Magazord id, images sent, and the error message
- **Linhas rejeitadas** — rows that never made it past validation

## Reliability

- Retries on `429` and `5xx` with exponential backoff and jitter, honouring
  `Retry-After`. `4xx` is not retried — it won't succeed on a second try.
- A rate limiter enforces a minimum gap between requests
  (`http.min_interval_seconds`); raise it if you see 429s.
- One bad product fails its own row and the run continues.
- Prices travel as strings, never floats, so `89.90` cannot become `89.9000001`.
- Blank cells are omitted from the payload rather than sent as empty values,
  so an empty column never wipes data already in the store.

---

# Catálogo folheável

`magazord catalogo` turns a price list -- or a folder of art the designer
already exported -- into a page-turning catalogue you can send by link, plus
the same pages as a PDF. It is the offline equivalent of the flip-book
services: everything is generated locally and the output is plain static
files, so there is no account, no upload, no watermark and no expiring link.

```bash
magazord modelo --tipo ofertas              # gera exemplos/modelo_ofertas.xlsx
magazord catalogo ofertas.xlsx              # monta saida/catalogo/
```

Open `saida/catalogo/index.html` and it is already a catalogue: two-page spread
on a computer, one page per swipe on a phone, thumbnails, zoom, full screen,
share, and a PDF download button.

## Three ways in

| Entrada | Comando | O que acontece |
|---|---|---|
| Uma planilha de ofertas | `magazord catalogo ofertas.xlsx` | cada linha vira uma página desenhada com a identidade da loja |
| Uma pasta com as artes prontas | `magazord catalogo artes/` | as imagens entram na ordem dos nomes dos arquivos, só ajustadas ao tamanho da página |
| A planilha de produtos do envio | `magazord catalogo produtos.xlsx --produtos` | os produtos com preço promocional viram páginas |

A planilha de ofertas tem uma linha por página:

| Produto | Preço de | Preço por | Imagem | Selo | Link |
|---|---|---|---|---|---|
| Base Liquida Franciny Ehlke Real Filter | 59,99 | 39,99 | `fotos/base.png` | | `https://loja/...` |
| Gloss Franciny Ehlke Chocochilli | 59,99 | 29,99 | `fotos/gloss.png` | NOVIDADE | |

Só `Produto` é obrigatório. `Imagem` aceita um arquivo ao lado da planilha ou
um link `https://` (baixado uma vez e guardado em cache). `Link` coloca um
botão **COMPRAR** na página, levando o cliente direto ao produto na loja.
Os cabeçalhos são reconhecidos por apelido, então `DE`/`POR`, `Preço
Promocional` ou `Foto` também funcionam.

## A identidade visual fica em um arquivo

`config/catalogo.yaml` define marca, cores, o texto grande da página e o
rodapé legal. Trocar a campanha é editar esse arquivo e rodar de novo -- nada
de mexer em código.

```yaml
marca:
  nome: "DISK"
  complemento: "KOSMETHICOS"
  logo: ""                    # um PNG aqui substitui o texto acima
cores:
  fundo_inicio: "#F6A340"
  destaque: "#F07817"
pagina:
  chamada: "PROMOÇÕES"
```

Para uma campanha pontual, as opções de linha de comando ganham do arquivo:

```bash
magazord catalogo ofertas.xlsx \
  --titulo "Ofertas de Setembro" \
  --subtitulo "Válido enquanto durarem os estoques" \
  --logo marca/logo.png \
  --chamada "SÓ HOJE" \
  --contracapa "Peça pelo WhatsApp (47) 99999-0000"
```

Flags úteis:

| Flag | Efeito |
|---|---|
| `--um-arquivo` | um único `.html` com imagens embutidas -- manda por WhatsApp, abre offline |
| `--sem-pdf` | não gera o PDF |
| `--sem-capa` | não gera a capa |
| `--selo-desconto` | carimba `-33%` calculado a partir dos dois preços |
| `--limite N` | usa só as N primeiras ofertas, para um teste rápido |
| `--fotos pasta/` | pasta base das imagens citadas na planilha |
| `--tema outro.yaml` | outra identidade visual |

## Publicar

A saída é estática. Suba a pasta `saida/catalogo/` em qualquer hospedagem
(GitHub Pages, Netlify, Vercel, o servidor da própria loja) e o link do
`index.html` é o catálogo. O endereço aceita `#p=4` para abrir direto numa
página -- útil para mandar "olha a página 4" no WhatsApp.

Para mandar o catálogo como arquivo, `--um-arquivo` gera um `.html` único que
funciona sem internet depois de baixado.

## Fontes

As páginas ficam melhores com uma fonte display pesada (Anton, Archivo Black,
Montserrat ExtraBold). O gerador procura em `assets/fontes/`, em `~/.fonts` e
nas fontes do sistema, nessa ordem, e cai para a fonte bold do sistema se não
achar nenhuma. Para fixar uma:

```yaml
fontes:
  titulo: "assets/fontes/Anton-Regular.ttf"
  texto: "assets/fontes/Montserrat-Regular.ttf"
```

---

## Tests

```bash
pytest
```

56 tests run the whole pipeline against a mocked Magazord API — creating,
updating, retrying, batching, idempotency and the CLI — with no network access
and no credentials.

## Layout

```
config/magazord.yaml      API binding — paths, field names, auth  ← fill this in
config/mapeamento.yaml    spreadsheet column aliases
src/magazord_automacao/
  planilha.py             Excel → Produto, with per-row validation
  models.py               the canonical Produto, EAN check, content hash
  client.py               HTTP client: auth, retry, backoff, rate limit
  mapper.py               Produto → Magazord JSON, driven by config
  imagens.py              image URL checking and downloading
  estado.py               what was uploaded, for incremental re-runs
  pipeline.py             batching, concurrency, dry run, error isolation
  relatorio.py            the .xlsx report
  cli.py                  command line
  catalogo/
    tema.py               the visual identity, read from catalogo.yaml
    oferta.py             one offer -- the content of one page
    fontes_dados.py       where pages come from: folder, offers sheet, products
    fontes.py             finding the display and text typefaces
    arte.py               drawing a page with Pillow
    pdf.py                the same pages as a PDF
    flipbook.py           writing the viewer
    construtor.py         ties it together into one output folder
    assets/               the viewer itself: html, css, js
```
