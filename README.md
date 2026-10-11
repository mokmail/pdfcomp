# pdfkb — PDF extraction → knowledge base → semantic search

`pdfkb` turns PDF files into searchable knowledge bases. It has a **web app**
(branded as part of kmail.at), a CLI, and an optional desktop client. It evolved
from the original `pdfcomp` PDF-compare GUI (kept under `legacy/`).

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

### Source referencing

Text and vectors share the same `chunk_id` (`<doc_id>-<index>`), and each chunk
carries `doc_id`, `filename`, `page`, `chunk_index`, `start`, `end`. Results
always report **file name + page**, link to `/api/pdf/<doc_id>#page=N` so the
browser opens the source PDF at that page, and copy a citation (`report.pdf p.12`).

## Run

```bash
python3 -m pip install -r requirements.txt
python3 serve.py                 # http://127.0.0.1:8765
#   --host 0.0.0.0 --port 8765   to expose it, e.g. behind the NUC's Traefik
```

Five tabs: **Add PDFs** · **Search** · **Sources** · **Settings** · **Library**.
A header selector switches the active library, and a status indicator shows any
install/`ollama pull` running — from any tab, with its live output in Settings.

## Settings

### Extraction

* **PyMuPDF** (default) — fast, page-accurate text layer.
* **Docling** — optional extra (`pip install docling`): layout/table aware, can
  OCR scans. Slower and a large install. Installable from the page; the OCR
  toggle applies to it. Availability is probed properly (a partially installed
  namespace package is reported as *not installed*, not as working).

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

Each library is a named storage profile (its own knowledge-base file, vector
store and exports). Create one from the current settings, switch with the header
selector, update the active one, delete the extras. Every switch backs the
current knowledge base up. Each row shows its backend, document count and
location, so it is obvious which collection you are searching.

## Sources (file servers + cloud)

A source is a saved, read-only connection. Files are copied into `sources/` and
ingested, so copies stay searchable even if the origin goes away. Connections can
also be **written to** — save a document's PDF or its readable text back.

| type | what | needs |
|---|---|---|
| `local` | a folder — including synced cloud drives (iCloud Drive, Google Drive, OneDrive, Dropbox, Synology Drive, Nextcloud desktop clients); detected drives are offered as one-click presets | — |
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

`reset` requires `--yes`; over HTTP the bulk reset requires an explicit
`confirm=RESET` token.

## Design — the kmail.at look

The web app is styled as part of kmail.at: the tokens are lifted verbatim from
the live site's CSS custom properties (`:root` light, `.dark` dark) — near
monochrome (no hue anywhere), hairline borders, Inter for display type and
JetBrains Mono for micro-labels. The header carries the site's logo mark
(grayscale, colour on hover), the `KMAIL.AT` wordmark and a breadcrumb; a
**◐ DARK / ◐ LIGHT** button switches the pair live.

## Tests

```bash
python3 tests/make_sample.py tests/sample.pdf   # generate a demo PDF
python3 tests/gui_smoke.py                       # desktop-client smoke test
```

Verification of the web app is run against an **isolated** instance (its own
`PDFKB_DATA_DIR` and port) so a test run can never touch a real knowledge base.

## Docker

```bash
docker build -t pdfkb .
docker run --rm -p 8765:8765 -v $PWD/kb_data:/data pdfkb
```

## Legacy

`legacy/pdfcomp.py`, `legacy/wido.py`, `legacy/thepdffile.py` are the original
PDF-compare tool, retained unchanged.
