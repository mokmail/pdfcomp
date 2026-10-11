# pdfkb — PDF extraction → knowledge base → semantic search

`pdfkb` turns PDF files into a searchable knowledge base. It evolved from the
original `pdfcomp` PDF-compare GUI (kept under `legacy/`).

For every PDF it extracts the text **per page**, splits it into overlapping
chunks, and stores each chunk twice:

* **as text** in a SQLite knowledge base (plus a readable Markdown export), and
* **as an embedding** in a Chroma vector store.

Every chunk keeps its full source trail — document id, file name, 1-based page
number, chunk index and character offsets — so any search hit can be traced
back to the exact page it came from. A Tkinter GUI lets you add PDFs and search
the extracted content.

## Architecture

```
PDF ──PyMuPDF──> per-page text ──chunk──> chunks ──┬─> SQLite (kb.sqlite)      text + metadata (+FTS5)
                                                   │        documents, chunks, meta, chunks_fts
                                                   └─> Chroma (chroma/)          embeddings
                                                            collection "pdf_chunks"
Search: embed(query) ──> Chroma cosine ─┐
        lexical terms ──> FTS5 / BM25 ──┴─> reciprocal-rank fusion ──> ranked hits
                                                                       (+ file, page, offsets)
```

Data lives in `kb_data/` (override with `PDFKB_DATA_DIR`):

| path                 | contents                                             |
|----------------------|------------------------------------------------------|
| `kb_data/kb.sqlite`  | knowledge base: documents, chunks, FTS5 index        |
| `kb_data/chroma/`    | vector store: embeddings keyed by chunk id           |
| `kb_data/text/`      | readable per-document Markdown exports               |

### Source referencing

Text and vectors share the same `chunk_id` (`<doc_id>-<index>`), and each chunk
row/metadata carries `doc_id`, `filename`, `page`, `chunk_index`, `start`,
`end`. A search result therefore always reports **file name + page**, and the
GUI can open the source PDF and copy a citation like `report.pdf p.12`.

## Embedding providers (auto-detected)

1. **Ollama** (preferred, local) — model `qwen3-embedding:0.6b` (1024-dim) by
   default. Override with `PDFKB_EMBED_MODEL` / `PDFKB_OLLAMA_URL`.
2. **sentence-transformers** — `all-MiniLM-L6-v2` (if installed).
3. **hash fallback** — deterministic hashed n-grams (512-dim) so the tool still
   works fully offline. Lower quality; use it only as a last resort.

The active provider and dimension are recorded per document. If the provider's
dimension changes you must re-index (the Library tab offers delete/reset).

## Install

```bash
python3 -m pip install -r requirements.txt
# tkinter ships with the python.org build; on Homebrew: brew install python-tk
```

## Run the GUI

```bash
python3 app.py          # or double-click run.command on macOS
```

Tabs:

* **Add PDFs** — pick files or a whole folder; extraction + embedding progress
  is shown in the log. Duplicates (same content sha1) are skipped unless you
  tick *Re-index*.
* **Search** — Hybrid / Semantic / Keyword query box. Results table shows
  score, source file and page; selecting a row shows the full chunk and its
  page context, with buttons to open the source PDF and copy the citation/text.
* **Library** — every indexed document: open PDF, export readable text, delete,
  or reset the whole knowledge base.

## Design — the kmail.at look

The GUI is deliberately styled as part of kmail.at. The tokens in
`pdfkb/theme.py` are lifted verbatim from the live site's CSS custom
properties and applied to every ttk widget:

| token          | light      | dark       |
|----------------|------------|------------|
| `--bg-void`    | `#ffffff`  | `#0a0a0a`  |
| `--bg-panel`   | `#fafafa`  | `#111111`  |
| `--bg-panel-hi`| `#f3f3f4`  | `#1a1a1a`  |
| `--text-ghost` | `#0c0c0d`  | `#f5f5f5`  |
| `--text-dim`   | `#52525b`  | `#a8a8ad`  |
| `--text-faint` | `#8c8c94`  | `#74747a`  |
| `--line-dim`   | `#e4e4e7`  | `#242426`  |
| `--line-hi`    | `#babac0`  | `#4a4a4e`  |

Near-monochrome (the site's rule is *no hue anywhere*), hairline borders,
Inter for display type and JetBrains Mono for micro-labels. The **◐ DARK /
◐ LIGHT** button in the header switches the pair live.

Fonts are resolved from what is installed: `Inter` + `JetBrains Mono` if
present (install with `brew install --cask font-inter font-jetbrains-mono`),
otherwise `Helvetica Neue` + `Menlo`.

## Command line (scripting / headless)

```bash
python3 -m pdfkb.cli ingest FILE.pdf [MORE.pdf ...] [--force]
python3 -m pdfkb.cli search "your question" -k 10 --mode hybrid   # or semantic|keyword
python3 -m pdfkb.cli list
python3 -m pdfkb.cli show <chunk_id>
python3 -m pdfkb.cli export <doc_id>
python3 -m pdfkb.cli delete <doc_id>
python3 -m pdfkb.cli reset --yes
```

Example:

```
$ python3 -m pdfkb.cli search "how does hybrid retrieval work" -k 2 --mode semantic
 1. [sim=0.594] sample.pdf p.2
    Vector Search and Retrieval A vector store keeps high dimensional embeddings …
```

## Tests

```bash
python3 tests/make_sample.py tests/sample.pdf   # generate a demo PDF
python3 tests/gui_smoke.py                       # headless GUI + search smoke test
```

## Docker (headless CLI only)

The GUI needs a display; in Docker use the CLI. See `Dockerfile`.

## Legacy

`legacy/pdfcomp.py`, `legacy/wido.py`, `legacy/thepdffile.py` are the original
PDF-compare tool, retained unchanged.
