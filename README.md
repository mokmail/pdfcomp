# Knowledgebases — PDF extraction → knowledge base → semantic search

**Knowledgebases** turns PDF files into searchable knowledge bases. It has a **web
app** (branded as part of kmail.at, at `tools / knowledgebases`), a CLI, and an
optional desktop client. It evolved from the original `pdfcomp` PDF-compare GUI
(kept under `legacy/`).

The tool is called **Knowledgebases**; the Python package keeps its short name
`pdfkb` (and the `PDFKB_*` environment variables with it), so existing imports,
launch agents and scripts keep working.

For every PDF it extracts the text **per page**, splits it into overlapping
chunks, and stores each chunk twice:

* **as text** in a SQLite knowledge base (plus a readable Markdown export), and
* **as an embedding** in a vector store — several backends, chosen in Settings.

Every chunk keeps its full source trail — document id, file name, 1-based page
number, chunk index and character offsets — so any search hit can be traced back
to the exact page it came from.

## Architecture

```
PDF ──extract──> per-page text ──chunk──> chunks ──┬─> SQLite (kb.sqlite)   text + metadata (+FTS5)
   PyMuPDF | Docling                               └─> vector store          embeddings (pluggable)
Search: embed(query) ──> vector store ─┐
        lexical terms ──> FTS5 / BM25 ─┴─> reciprocal-rank fusion ──> ranked hits (+ file, page, offsets)
```

A **library** is a named storage profile; several can be defined and switched at
runtime (see *Libraries*). The active library's data lives under its **data
directory** (default `kb_data/`; override the default with `PDFKB_DATA_DIR`):

| path                | contents                                      |
|---------------------|-----------------------------------------------|
| `kb.sqlite`         | knowledge base: documents, chunks, FTS5 index |
| `chroma/`           | local vector store (chroma / hnswlib)         |
| `text/`             | readable per-document Markdown exports        |
| `sources/`          | copies of uploaded / fetched PDFs             |
| `settings.json`     | provider, extraction, storage, libraries      |
| `integrations.json` | saved source connections (mode 0600)          |

### One writer, many readers

Knowledge bases never share their content. Each library is its own directory, its
own database file and its own vector collection, and only the **active** one is
opened for writing:

* a **new** library is created with its own container — the paths submitted with
  the request are deliberately ignored, so two libraries cannot end up sharing a
  database and a collection;
* an existing library **cannot be moved onto** a container another one uses;
* every other library is opened **read-only** for cross-library search: no
  directories are created, no schema is applied, no WAL side files appear, and any
  attempt to write through that handle raises
  `this knowledge base is open read-only — only the active one (…) can save files`;
* a library whose files are gone simply reads as empty — it is not given a new
  database just because it was searched;
* each base records the **collection** its embeddings went into
  (`meta.vector_collection`), so a store that does not match is reported as
  `mismatch … re-ingest` instead of silently returning another base's chunks.

### Source referencing

Text and vectors share the same `chunk_id` (`<doc_id>-<index>`), and each chunk
carries `doc_id`, `filename`, `page`, `chunk_index`, `start`, `end`. Results
always report **file name + page**, link to the source PDF at that page so the
browser opens it there, and copy a citation (`report.pdf p.12`). Each hit also
names the **library** it came from *and* the **knowledge base** behind it (the
database file and the vector store) — leaf names only, so no server path leaks.

## Run

```bash
python3 -m pip install -r requirements.txt
python3 serve.py                 # http://127.0.0.1:8765
#   --host 0.0.0.0 --port 8765   to expose it, e.g. behind the NUC's Traefik
```

Four tabs: **Search** (the landing page) · **Sources** · **Settings** · **Library**.

The header is: the brand bar (logo, wordmark, breadcrumb, active-library selector,
theme switch) → **the menubar** → a **foldable info strip**, folded by default,
that states the setup in force — embedding model, current knowledge base,
extractor, vector store, collection, where it saves, and how much is indexed.
While anything is being installed, pulled or indexed, a badge and a progress rule
appear in that header from any tab.

## Search

Hybrid (semantic + keyword, fused by reciprocal rank), semantic, or keyword over
any number of knowledge bases at once: a **Search within** control picks one or
several libraries, and the fusion is by rank because scores from different stores
and models are not comparable. Changing the mode, the result count or the scope
re-runs the current query. Pressing Enter searches *and clears* the field.

Each result row is clickable and opens the **original document** in a modal, at
the page the hit came from, next to the matched chunk and the surrounding page
text; the modal scrolls when the content is taller than the window. The search
field carries the AI halo and sweeping ring in the brand palette.

## Settings

### Extraction

* **Docling** — **the default** (`pip install docling`): layout/table aware, can
  OCR scans. Slower and a large install, so it is an optional extra that is
  installed from the page; the OCR toggle applies to it. Availability is probed
  properly (a partially installed namespace package is reported as *not
  installed*, not as working).
* **PyMuPDF** — the fast, page-accurate fallback. While Docling is not installed
  the app extracts with PyMuPDF and says so in the ingest log, so a missing
  optional extra never blocks ingestion.

### Ingesting

Files are added from the **Sources → Local files** section (pick files, or pick a
folder; *force* re-reads ones already held). Ingesting reports per file, in plain
words:

```
  ✓ indexed + embedded   report.pdf  —  12 pages, 34 chunks, docling
  • already in the knowledge base — skipped   notes.pdf
  ! no text to index   scan.pdf
  ✕ failed — broken.pdf  not a PDF: broken.pdf

2 indexed + embedded  ·  1 already in the knowledge base  ·  1 failed
```

The same counts appear in the header, with the elapsed time, so a run is visible
from any page — and each file's progress (`indexing report.pdf 3/12 · 25%`) too.

### Deleting

Deleting is total, on purpose. Removing a **document** drops its rows, chunks,
FTS entries, **embeddings**, text export and stored source copy, and reports
exactly what went. Removing a **knowledge base** deletes its whole data directory
— the database, the vector store, the exports and the stored sources — after
listing how many documents it holds. Guards refuse to touch the active knowledge
base, a directory another knowledge base still uses, or a directory that does not
look like a knowledge base (so a purge can never walk off with an unrelated
folder).

### Embedding provider & model

Auto-detected, or pinned: **Ollama** (default `qwen3-embedding:0.6b`, 1024-dim) →
**sentence-transformers** (`all-MiniLM-L6-v2`) → **offline hash** fallback.
*Test connection* embeds a probe string and reports provider/model/dimension/
latency; *Install* runs `ollama pull` with streamed output. Changing the model
changes the dimension, so the app warns that vectors must be rebuilt. A bad
preference never bricks the app — it falls back to auto-detect and says so.

### Vector store

Twenty backends are registered, grouped, each with its status and what it needs:

| backend | kind | status |
|---|---|---|
| `sqlite` | in-file blobs, numpy cosine | **verified here** |
| `hnswlib` | local HNSW index file | **verified here** |
| `chroma` | embedded persistent Chroma | **verified here** |
| `qdrant` | server URL, or `:memory:` | **verified here** |
| `opensearch`, `elasticsearch` | k-NN index over the REST client | needs a server |
| `weaviate`, `milvus`, `pgvector`, `redis`, `lancedb`, `faiss`, `duckdb` | client library required | install |
| `pinecone`, `typesense`, `upstash`, `mongodb`, `azure-search`, `turbopuffer`, `vespa` | hosted services (url + key) | install |

"Verified here" means exercised end-to-end in this environment (ingest + semantic
search). "Needs a server" is implemented but was not exercised — no server was
reachable — so it fails loudly rather than pretending. Server fields (url, API
key, index) and a *Test vector store* button are on the page; if a selected
backend cannot be built the app falls back to SQLite and reports why.

### Storage

Data directory, knowledge-base file, vector backend/path/url/collection, text
exports and source copies are all editable, with a resolved readout. Applying new
storage backs the current knowledge-base file up first (`kb.sqlite.bak`).

### Libraries — multiple target knowledge bases

Each library is a named storage profile with its **own** knowledge-base file,
vector store, exports and source copies. Create one from the current settings,
switch with the header selector, update the active one, delete the extras. Every
switch backs the current knowledge base up. Each row shows its backend, document
count, container and vector **collection**, so it is obvious which one you are
searching — and no two can ever end up pointing at the same files (see *One
writer, many readers*).

## Sources (file servers + cloud)

A source is a saved, read-only connection. Files are copied into the active
library's `sources/` and ingested, so copies stay searchable even if the origin
goes away. Connections can also be **written to** — save a document's PDF or its
readable text back.

| type | what | needs |
|---|---|---|
| `local` | a folder — including synced cloud drives (iCloud Drive, Google Drive, OneDrive, Dropbox, Synology Drive, Nextcloud desktop clients); detected drives are offered as one-click presets, and a **Browse** button picks the folder instead of typing a path | — |
| `http` | an HTTP directory listing | — |
| `webdav` | Nextcloud / ownCloud / Synology / Box / Yandex Disk | — |
| `ssh` | a folder on a remote host over SSH/SFTP, using your own keys and `~/.ssh/config` | openssh |
| `ftp` | FTP / FTPS servers | — |
| `s3` | S3-compatible object storage: AWS S3, MinIO, Cloudflare R2, Backblaze B2, Wasabi, DigitalOcean Spaces, Linode | boto3 |
| `rclone` | **any of ~70 providers** rclone speaks — Google Drive, OneDrive, SharePoint, Dropbox, Box, pCloud, MEGA, GCS, Azure Blob… | `brew install rclone` + `rclone config` |
| `smb` | Windows / NAS shares | `pip install smbprotocol` |

The page also carries a *where to connect what* table (service → connector → how).
Credentials, if any, live in `integrations.json` (mode 0600).

## CLI (scripting / headless)

```bash
python3 -m pdfkb.cli ingest FILE.pdf [MORE.pdf ...] [--force]
python3 -m pdfkb.cli search "your question" -k 10 --mode hybrid   # or semantic|keyword
python3 -m pdfkb.cli list | show <chunk_id> | export <doc_id> | delete <doc_id> | reset --yes
```

The module path stays `pdfkb.cli`; the tool names itself **Knowledgebases** in its
help text. `reset` requires `--yes`; over HTTP the bulk reset requires an explicit
`confirm=RESET` token.

## Design — the kmail.at look

The web app is styled as part of kmail.at: the surfaces, type scale and hairline
geometry come from the live site's CSS custom properties (`:root` light, `.dark`
dark) — near-grey panels, hairline borders, Inter for display type and JetBrains
Mono for micro-labels. The header carries the site's logo mark, the `KMAIL.AT`
wordmark and a breadcrumb; a **◐ DARK / ◐ LIGHT** button switches the pair live.
The menubar sits above the setup strip and is deliberately flat — no boxes and no
bottom border, the active tab is underlined in the brand gradient.

On top of the neutral surfaces sits the **brand accent layer**, so the app is not
black and white:

| Accent | Colour (dark / light) | Where |
|---|---|---|
| green | `74 222 128` / `22 163 74` | primary buttons, the active tab, progress |
| cyan | `34 211 238` / `14 116 144` | references, citations, the library column, focus rings |
| lime | `163 230 53` / `77 124 15` | relevance scores |
| amber | `251 191 36` / `180 83 9` | warnings, a running install/pull |
| red | `248 113 113` / `220 38 38` | destructive actions and errors |

Icons are one thin-line set: a single 1.25px stroke weight, `currentColor` so an icon is
never a colour of its own, sized in `em` so it follows the text beside it, and injected from
a `data-ic` attribute — the button markup stays declarative and every glyph stays consistent.

The search field's AI halo, sweeping ring and sparkle mark are drawn in that palette,
and the header and result modal carry a brand gradient rule. Light-mode accents
are deliberately darker — the vivid dark-theme values fall to 3–4:1 contrast on
white — and coloured buttons take the brand ink `#0a0e0c` rather than white. A
verification pass audits the rendered page for both themes: every strong colour
must be one of the brand hues, and none may be left over from the browser
defaults.

## Tests

```bash
python3 tests/make_sample.py tests/sample.pdf   # generate a demo PDF
python3 tests/gui_smoke.py                       # desktop-client smoke test
```

The whole verification is one command — it boots an **isolated** instance (its own
`PDFKB_DATA_DIR` and port), so it can never touch a real knowledge base:

```bash
bash tests/run_verification.sh          # syntax, the read-only rules, then the web probe
hermes verify                           # the same thing through the saved recipe
```

`tests/verify_web.py` drives the HTTP API and the rendered page in headless Chrome and
covers extraction, providers, every reachable vector backend, storage switches, sources,
safeguards, ingest progress, multi-library search, knowledge-base isolation (including that
a read writes nothing into another base) and the destructive paths with their guards —
**236 checks, 0 failures, 0 skipped** as of the last run. `tests/verify_library_readonly.py`
proves the same rule at the library level: a read-only knowledge base refuses to index,
delete, wipe and export. The recipe lives in `.hermes/environment.json`; `tests/requirements-dev.txt`
carries the two optional backends it exercises (`hnswlib`, `qdrant-client`).

## Roadmap

**Not implemented yet** — recorded so the work is unambiguous when it is picked up.

**Other file types besides PDF.** The pipeline already separates extraction from storage
from presentation, so DOCX, XLSX, PPTX, HTML, Markdown, plain text, e-mail and images (OCR)
can be indexed, searched and cited exactly the way a PDF is. It needs a format registry, a
**locator** in place of the bare page number (`Sheet 2 · A1:F40`, `Slide 7`, `§ 4`), a
widened ingest whitelist, the same type filter in every source connector, and a preview
that falls back to *open in new tab* where the browser cannot render the original. PDF
behaviour, existing knowledge bases, citations and vector ids stay as they are. The
task-by-task plan is `.hermes/plans/2026-10-11_205850-more-file-types.md`.

## Docker Compose — the app, Docling, and the vector databases

The whole stack lives in `docker-compose.yml`. Everything except the app and Ollama is
behind a **profile**, so nothing you did not ask for ever starts:

```bash
cp .env.example .env            # optional: uncomment what you need
docker compose up -d            # the app (http://localhost:8765) + Ollama
docker compose --profile docling up -d          # + extraction as its own service
docker compose --profile qdrant up -d           # + Qdrant as the vector store
docker compose logs -f app
docker compose down             # keep the volumes; `down -v` deletes your knowledge bases
```

| service | what it is for | profile | host port |
|---|---|---|---|
| `app` | Knowledgebases itself — UI + JSON API, knowledge base and settings in the `kb` volume | *(default)* | 8765 |
| `ollama` | embeddings (`qwen3-embedding:0.6b` by default) | *(default)* | 11434 |
| `ollama-pull` | one-shot: pulls the embedding model, then exits | *(default)* | — |
| `docling` | **Docling as a service** — layout/table-aware extraction with OCR, so the app image needs no Docling at all | `docling` | 5001 |
| `qdrant` | vector database | `qdrant` | 6333, 6334 |
| `opensearch` | k-NN vector search | `opensearch` | 9200 |
| `elasticsearch` | dense-vector search | `elasticsearch` | 9300 → 9200 |
| `weaviate` | vector database | `weaviate` | 8080 |
| `pgvector` | PostgreSQL with the vector extension | `pgvector` | 5432 |
| `redis` | Redis Stack (RediSearch vectors) | `redis` | 6379 |
| `milvus` | Milvus standalone (+ its `etcd` and `minio`) | `milvus` | 19530 |
| `typesense` | vector search | `typesense` | 8108 |
| `mongodb` | MongoDB (Atlas-compatible local stand-in) | `mongodb` | 27017 |

### Pointing the app at a service

Either click it in **Settings → Storage → vector store** (then *Test vector store*), or
make the stack declarative in `.env` — the app then boots already connected:

```ini
PDFKB_VECTOR_BACKEND=qdrant
PDFKB_VECTOR_URL=http://qdrant:6333
PDFKB_DOCLING_URL=http://docling:5001
```

| backend | `PDFKB_VECTOR_BACKEND` | `PDFKB_VECTOR_URL` |
|---|---|---|
| Qdrant | `qdrant` | `http://qdrant:6333` |
| OpenSearch | `opensearch` | `http://opensearch:9200` |
| Elasticsearch | `elasticsearch` | `http://elasticsearch:9200` |
| Weaviate | `weaviate` | `http://weaviate:8080` |
| pgvector | `pgvector` | `postgresql://postgres:knowledgebases@pgvector:5432/knowledgebases` |
| Redis Stack | `redis` | `redis://redis:6379` |
| Milvus | `milvus` | `http://milvus:19530` |
| Typesense | `typesense` | `http://typesense:8108` |
| MongoDB | `mongodb` | `mongodb://knowledgebases:knowledgebases@mongodb:27017` |

### Pick a variant — never install what you do not use

The image comes in variants (Dockerfile build targets), so a laptop that searches locally
does not carry nine database clients, and a box that runs Docling as a service does not
carry torch twice:

| target | what it adds | about |
|---|---|---|
| `slim` *(default)* | nothing — PyMuPDF + Chroma + the web app; embedded vector stores only | 952 MB |
| `clients` | every self-hosted vector client: qdrant, opensearch/elasticsearch, weaviate, milvus, pgvector, redis, typesense, mongodb | 1.2 GB |
| `docling` | Docling (torch and friends) for in-process extraction | ~5 GB |
| `full` | clients + docling | ~5.5 GB |
| `custom` | exactly what `VECTOR_CLIENTS` / `WITH_DOCLING` name | varies |

```bash
docker build -t knowledgebases .                     # slim
docker build --target clients -t knowledgebases .    # + clients
docker build --target docling -t knowledgebases .    # + docling
docker build --target custom  -t knowledgebases . \  # + what you name
  --build-arg VECTOR_CLIENTS="qdrant-client" --build-arg WITH_DOCLING=0
```

`docker-compose.yml` selects it with `APP_VARIANT` (`slim` unless you say otherwise), and
every variant ends its build by importing the app: a client library that would silently move
starlette underneath the app fails the build instead of shipping a crash-looping image.

### The picker: `./bin/kb`

One command per choice — it assembles the profiles, the overlays and the image target:

```bash
./bin/kb up                    # app + Ollama, slim image, embedded vectors
./bin/kb up --host-ollama      # app only, using the Ollama already on this machine
./bin/kb up qdrant --host-ollama   # + Qdrant, still no Ollama container
./bin/kb up qdrant             # + Qdrant, app already pointed at it, target `clients`
./bin/kb up docling            # + extraction as a service (app stays slim, no torch)
./bin/kb up qdrant docling     # both, target `full`
./bin/kb up opensearch         # or elasticsearch weaviate pgvector redis milvus
                               #    typesense mongodb
./bin/kb build qdrant          # just build that variant's image
./bin/kb variants              # what you can ask for, with sizes
./bin/kb show qdrant docling   # the exact docker command, without running it
./bin/kb ps | logs | down | destroy
```

Underneath it is plain `docker compose` with per-database overlays (`compose/vector-qdrant.yml`,
`compose/extraction-docling.yml`, …), so you can use those directly if you prefer:

```bash
docker compose -f docker-compose.yml -f compose/vector-qdrant.yml --profile qdrant up -d
```

**Ollama on the machine beats Ollama in a container** on a laptop: it keeps the models it
already has and uses the GPU properly. `--host-ollama` uses the one running here — the app
reaches it at `host.docker.internal:11434`, and no ollama container or image is started or
pulled. Setting `PDFKB_OLLAMA_URL` to the host yourself does the same thing.

Server backends need their client library inside the app image — that is what the `clients`
target is for. Installing one at runtime works but a container loses it when it is recreated:

```ini
VECTOR_CLIENTS=qdrant-client          # space-separated: qdrant-client weaviate-client pymilvus …
WITH_DOCLING=0                        # 1 = Docling inside the app image as well (heavy)
```

An **empty knowledge base directory is not "no vectors"**: `chroma`, `sqlite` and
`hnswlib` are embedded and need no service at all, which is why the default profile runs
just the app and Ollama. Switching the vector store of an existing knowledge base means
re-ingesting — the app reports the collection stamp and says so when it does not match.

The third-party database services deliberately carry **no healthcheck**: each image ships
different tools, and a wrong probe is worse than none. The app's *Test vector store* button
is the check that a database is really reachable, and it reports what it got.

### Single container

```bash
docker build -t knowledgebases .
docker run --rm -p 8765:8765 -v $PWD/kb_data:/data knowledgebases
```

## Legacy

`legacy/pdfcomp.py`, `legacy/wido.py`, `legacy/thepdffile.py` are the original
PDF-compare tool, retained unchanged.
