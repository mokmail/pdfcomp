"""Knowledge base + vector store.

SQLite holds the extracted *text* and all provenance metadata
(doc_id, file name, page number, chunk index, char offsets, sha1).
The vector store (Chroma or in-file SQLite, see `vectors.py`) holds the
*embeddings* under the same chunk ids, so a semantic hit and a keyword hit
resolve to the same record and can be traced back to their source.

Paths (data dir, knowledge-base file, vector store, exports, source copies)
come from `prefs.storage()` and are therefore configurable at runtime.
"""

from __future__ import annotations

import time
from pathlib import Path

import sqlite3

from . import prefs
from .embeddings import EmbedderError, get_embedder
from .extract import chunk_pages, extract_pages
from .vectors import (BACKEND_SPECS, backends, make_vector_store,  # noqa: F401
                      backend_ids)

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id         TEXT PRIMARY KEY,
    path           TEXT,
    filename       TEXT,
    sha1           TEXT,
    npages         INTEGER,
    nchunks        INTEGER,
    title          TEXT,
    added_at       REAL,
    embed_provider TEXT,
    embed_model    TEXT,
    embed_dim      INTEGER,
    extractor      TEXT
);
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id    TEXT PRIMARY KEY,
    doc_id      TEXT,
    page        INTEGER,
    chunk_index INTEGER,
    start       INTEGER,
    end         INTEGER,
    text        TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_chunks_page ON chunks(doc_id, page);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED, doc_id UNINDEXED, filename, page UNINDEXED, text,
    tokenize='unicode61'
);
"""


class KB:
    def __init__(self, data_dir=None, embedder=None, storage=None):
        st = storage or prefs.storage()
        if data_dir:
            base = Path(data_dir)
            st = {**st, "data_dir": base, "sqlite_path": base / "kb.sqlite",
                  "text_dir": base / "text", "sources_dir": base / "sources",
                  "vector_path": base / "chroma"}
        self.storage = st
        self.sqlite_path = str(st["sqlite_path"])
        self.text_dir = Path(st["text_dir"])
        for d in (Path(st["data_dir"]), self.text_dir, Path(st["sources_dir"]),
                  Path(st["sqlite_path"]).parent):
            Path(d).mkdir(parents=True, exist_ok=True)

        self.conn = sqlite3.connect(self.sqlite_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        # migrate older knowledge bases that predate the extractor column
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(documents)")}
        if "extractor" not in cols:
            self.conn.execute("ALTER TABLE documents ADD COLUMN extractor TEXT")
        self.has_fts = True
        try:
            self.conn.executescript(FTS_SCHEMA)
        except sqlite3.OperationalError:
            self.has_fts = False          # FTS5 not compiled into this sqlite
        self.conn.commit()

        self.vstore, self.vstore_error = make_vector_store(st, self.conn)

        self.embedder_error = None
        if embedder is not None:
            self.embedder = embedder
        else:
            try:
                self.embedder = get_embedder(**prefs.embedder_kwargs())
            except EmbedderError as ex:
                # never lock the app out on a bad preference — fall back and say so
                self.embedder_error = str(ex)
                self.embedder = get_embedder("auto")

    # ------------------------------------------------------------------ setup
    def provider_info(self):
        info = self.embedder.info()
        if self.embedder_error:
            info += f"  [fell back — requested provider failed: {self.embedder_error}]"
        return info

    def _stored_dim(self):
        row = self.conn.execute("SELECT value FROM meta WHERE key='embed_dim'").fetchone()
        return int(row["value"]) if row else None

    def _doc_meta(self, doc_id):
        row = self.conn.execute(
            "SELECT filename,path FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
        return (row["filename"], row["path"]) if row else ("", "")

    # ---------------------------------------------------------------- ingest
    def has_document(self, sha1):
        row = self.conn.execute("SELECT doc_id FROM documents WHERE sha1=?", (sha1,)).fetchone()
        return row["doc_id"] if row else None

    def add_document(self, pdf_path, force=False, progress=None):
        """Extract one PDF and store it as text + embeddings."""
        def say(done, total, msg):
            if progress:
                progress(done, total, msg)

        say(0, 4, f"Reading {Path(pdf_path).name}")
        prefs_now = prefs.load()
        extractor = prefs_now.get("extractor") or "pymupdf"
        ocr = str(prefs_now.get("docling_ocr", "")).lower() in ("true", "1", "on", "yes")
        # the long step, and the one worth naming: an extraction engine can sit here for minutes
        say(1, 4, f"Extracting text with {extractor}" + (" (OCR)" if ocr else "")
                  + (" — the first Docling run downloads its models" if extractor == "docling" else ""))
        meta, pages = extract_pages(pdf_path, extractor=extractor, ocr=ocr)

        existing = self.has_document(meta["sha1"])
        if existing and not force:
            say(1, 1, f"Already in the knowledge base: {meta['filename']}")
            return {"doc_id": existing, "status": "skipped",
                    "filename": meta["filename"], "nchunks": 0}
        if existing and force:
            self.delete_document(existing)

        chunks = chunk_pages(pages)
        if not chunks:
            return {"doc_id": meta["doc_id"], "status": "empty",
                    "filename": meta["filename"], "nchunks": 0,
                    "message": "No extractable text (scanned image PDF?)"}

        texts = [c["text"] for c in chunks]
        how = (f"{self.embedder.info()} (extracted with {meta.get('extractor', 'pymupdf')}"
               + (f" — {meta['extractor_note']}" if meta.get("extractor_note") else "") + ")")
        # embed in batches so a long PDF reports real progress instead of one silent stall
        embs, batch = [], 32
        for i in range(0, len(texts), batch):
            upto = min(i + batch, len(texts))
            say(2 + i / len(texts), 4, f"Embedding chunks {upto}/{len(texts)} via {how}")
            embs.extend(self.embedder.embed(texts[i:upto]))
        dim = len(embs[0])
        stored = self._stored_dim()
        if stored and stored != dim:
            raise RuntimeError(
                f"Embedding dimension changed ({stored} -> {dim}). "
                "Reset the knowledge base and re-ingest, or pick the previous provider."
            )

        chunk_ids = [f"{meta['doc_id']}-{c['chunk_index']:05d}" for c in chunks]

        say(3, 4, "Writing text + metadata to the knowledge base")
        self.conn.execute(
            "INSERT OR REPLACE INTO documents(doc_id,path,filename,sha1,npages,nchunks,"
            "title,added_at,embed_provider,embed_model,embed_dim,extractor) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (meta["doc_id"], meta["path"], meta["filename"], meta["sha1"], meta["npages"],
             len(chunks), meta["title"], time.time(), self.embedder.name,
             self.embedder.model, dim, meta.get("extractor", extractor)),
        )
        self.conn.executemany(
            "INSERT OR REPLACE INTO chunks(chunk_id,doc_id,page,chunk_index,start,end,text) "
            "VALUES(?,?,?,?,?,?,?)",
            [(cid, meta["doc_id"], c["page"], c["chunk_index"], c["start"], c["end"], c["text"])
             for cid, c in zip(chunk_ids, chunks)],
        )
        if self.has_fts:
            self.conn.execute("DELETE FROM chunks_fts WHERE doc_id=?", (meta["doc_id"],))
            self.conn.executemany(
                "INSERT INTO chunks_fts(chunk_id,doc_id,filename,page,text) VALUES(?,?,?,?,?)",
                [(cid, meta["doc_id"], meta["filename"], c["page"], c["text"])
                 for cid, c in zip(chunk_ids, chunks)],
            )
        self.conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('embed_dim',?)", (str(dim),))
        self.conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('embed_provider',?)",
                          (self.embedder.info(),))
        self.conn.commit()

        say(3, 4, f"Writing {len(chunks)} embeddings to the {self.vstore.info()}")
        self.vstore.add(chunk_ids, embs, texts,
                        [{"doc_id": meta["doc_id"], "page": int(c["page"]),
                          "chunk_index": int(c["chunk_index"]), "filename": meta["filename"]}
                         for c in chunks])

        say(4, 4, "Exporting readable text")
        self.export_text(meta["doc_id"])

        return {"doc_id": meta["doc_id"], "status": "added", "filename": meta["filename"],
                "npages": meta["npages"], "nchunks": len(chunks), "dim": dim,
                "extractor": meta.get("extractor", "pymupdf"),
                "extractor_note": meta.get("extractor_note", "")}

    # ------------------------------------------------------------ text export
    def export_path(self, doc_id):
        row = self.conn.execute("SELECT filename FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
        if not row:
            return None
        return self.text_dir / f"{Path(row['filename']).stem}.{doc_id[:8]}.md"

    def export_text(self, doc_id):
        """Write a human-readable markdown copy of the extracted text."""
        doc = self.conn.execute("SELECT * FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
        if not doc:
            return None
        rows = self.conn.execute(
            "SELECT page,text FROM chunks WHERE doc_id=? ORDER BY chunk_index", (doc_id,)
        ).fetchall()
        out = [f"# {doc['filename']}", "",
               f"- source: `{doc['path']}`",
               f"- pages: {doc['npages']}    chunks: {doc['nchunks']}",
               f"- sha1: {doc['sha1']}", ""]
        cur_page = None
        for r in rows:
            if r["page"] != cur_page:
                out.append(f"\n## Page {r['page']}\n")
                cur_page = r["page"]
            out.append(r["text"])
        self.text_dir.mkdir(parents=True, exist_ok=True)
        dest = self.text_dir / f"{Path(doc['filename']).stem}.{doc_id[:8]}.md"
        dest.write_text("\n".join(out), encoding="utf-8")
        return str(dest)

    # -------------------------------------------------------------- search
    def semantic_search(self, query, k=10):
        qe = self.embedder.embed([query])[0]
        hits = self.vstore.query(qe, k)
        if not hits:
            return []
        ids = [cid for cid, _ in hits]
        rows = self.conn.execute(
            "SELECT c.chunk_id, c.doc_id, c.page, c.chunk_index, c.text, d.filename, d.path "
            "FROM chunks c JOIN documents d ON d.doc_id=c.doc_id "
            f"WHERE c.chunk_id IN ({','.join('?' * len(ids))})", ids).fetchall()
        by_id = {r["chunk_id"]: r for r in rows}
        out = []
        for cid, sim in hits:
            r = by_id.get(cid)
            if not r:
                continue
            md = {"doc_id": r["doc_id"], "page": int(r["page"]), "chunk_index": r["chunk_index"],
                  "filename": r["filename"], "path": r["path"]}
            out.append(self._mk_result(cid, md, r["text"], sim=sim))
        return out

    def keyword_search(self, query, k=10):
        if not self.has_fts:
            return self._like_search(query, k)
        terms = [t for t in _tokenize(query) if t]
        if not terms:
            return []
        match = " OR ".join('"%s"' % t.replace('"', '""') for t in terms)
        try:
            rows = self.conn.execute(
                "SELECT chunk_id, doc_id, filename, page, text, bm25(chunks_fts) AS score "
                "FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY score LIMIT ?",
                (match, max(1, k)),
            ).fetchall()
        except sqlite3.OperationalError:
            return self._like_search(query, k)
        out = []
        for r in rows:
            fname, path = self._doc_meta(r["doc_id"])
            md = {"doc_id": r["doc_id"], "filename": r["filename"] or fname,
                  "page": int(r["page"]), "path": path}
            out.append(self._mk_result(r["chunk_id"], md, r["text"], kw=-float(r["score"])))
        return out

    def _like_search(self, query, k):
        terms = _tokenize(query)
        if not terms:
            return []
        where = " OR ".join(["c.text LIKE ?"] * len(terms))
        rows = self.conn.execute(
            f"SELECT c.chunk_id,c.doc_id,c.page,c.text,d.filename,d.path FROM chunks c "
            f"JOIN documents d ON d.doc_id=c.doc_id WHERE {where} LIMIT ?",
            tuple(f"%{t}%" for t in terms) + (k,),
        ).fetchall()
        return [self._mk_result(r["chunk_id"],
                                {"doc_id": r["doc_id"], "filename": r["filename"],
                                 "page": int(r["page"]), "path": r["path"]},
                                r["text"], kw=1.0) for r in rows]

    def search(self, query, k=10, mode="hybrid"):
        mode = (mode or "hybrid").lower()
        if mode == "semantic":
            return self.semantic_search(query, k)
        if mode == "keyword":
            return self.keyword_search(query, k)

        pool = max(k * 3, 20)
        sem = self.semantic_search(query, pool)
        kw = self.keyword_search(query, pool)
        merged = {}
        for rank, r in enumerate(sem):
            r["_rrf"] = r.get("_rrf", 0.0) + 1.0 / (60 + rank + 1)
            merged[r["chunk_id"]] = r
        for rank, r in enumerate(kw):
            if r["chunk_id"] in merged:
                merged[r["chunk_id"]]["kw"] = r.get("kw")
                merged[r["chunk_id"]]["_rrf"] += 1.0 / (60 + rank + 1)
            else:
                r["_rrf"] = 1.0 / (60 + rank + 1)
                merged[r["chunk_id"]] = r
        out = sorted(merged.values(), key=lambda x: x["_rrf"], reverse=True)[:k]
        for r in out:
            r["score"] = r.pop("_rrf", 0.0)
        return out

    def _mk_result(self, chunk_id, md, text, sim=None, kw=None):
        page = int(md.get("page", 0))
        fname = md.get("filename", "")
        path = md.get("path", "") or self._doc_meta(md.get("doc_id"))[1]
        return {
            "chunk_id": chunk_id, "doc_id": md.get("doc_id"), "filename": fname,
            "page": page, "chunk_index": md.get("chunk_index"), "text": text,
            "sim": sim, "kw": kw,
            "score": sim if sim is not None else (kw or 0.0),
            "path": path, "citation": f"{fname} p.{page}",
        }

    def page_context(self, doc_id, page, before=1, after=1):
        rows = self.conn.execute(
            "SELECT page,text FROM chunks WHERE doc_id=? AND page BETWEEN ? AND ? "
            "ORDER BY chunk_index",
            (doc_id, page - before, page + after),
        ).fetchall()
        return [{"page": r["page"], "text": r["text"]} for r in rows]

    # ------------------------------------------------------------- library
    def documents(self):
        rows = self.conn.execute(
            "SELECT doc_id,filename,path,npages,nchunks,title,added_at,"
            "embed_provider,embed_model,embed_dim,extractor FROM documents ORDER BY added_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def stats(self):
        docs = self.conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
        chunks = self.conn.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"]
        return {"documents": docs, "chunks": chunks, "vectors": self.vstore.count(),
                "provider": self.provider_info(),
                "vector_backend": self.vstore.name,
                "vector_store": self.vstore.info(),
                "vector_error": self.vstore_error, "sqlite": self.sqlite_path,
                "data_dir": str(self.storage["data_dir"])}

    def delete_document(self, doc_id):
        """Remove a document entirely: rows, chunks, FTS index, embeddings, export.

        Returns a report so the caller can verify the purge really happened
        instead of trusting a silent success.
        """
        export = self.export_path(doc_id)
        ids = [r["chunk_id"] for r in self.conn.execute(
            "SELECT chunk_id FROM chunks WHERE doc_id=?", (doc_id,)).fetchall()]
        self.conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
        if self.has_fts:
            self.conn.execute("DELETE FROM chunks_fts WHERE doc_id=?", (doc_id,))
        self.conn.execute("DELETE FROM documents WHERE doc_id=?", (doc_id,))
        self.conn.commit()

        vector_error, vectors_removed, vectors_left = None, 0, None
        try:
            before = self.vstore.count()
            self.vstore.delete(doc_id=doc_id, ids=ids)
            vectors_left = self.vstore.count()
            vectors_removed = max(0, before - vectors_left)
        except Exception as ex:  # noqa: BLE001 — report it, never fail silently
            vector_error = f"{type(ex).__name__}: {ex}"

        export_removed = False
        if export and Path(export).exists():
            try:
                Path(export).unlink()
                export_removed = True
            except OSError:
                pass

        left = self.conn.execute(
            "SELECT COUNT(*) c FROM chunks WHERE doc_id=?", (doc_id,)).fetchone()["c"]
        return {"doc_id": doc_id, "chunks_removed": len(ids), "chunks_left": left,
                "vectors_removed": vectors_removed, "vectors_left": vectors_left,
                "vector_error": vector_error, "export_removed": export_removed}

    def reset(self):
        """Wipe the whole knowledge base and vector store."""
        self.conn.executescript(
            "DELETE FROM chunks; DELETE FROM chunks_fts; DELETE FROM documents; DELETE FROM meta;"
        )
        self.conn.commit()
        try:
            self.vstore.reset()
        except Exception:
            pass


def _tokenize(text):
    import re
    return [t for t in re.split(r"[^\w\u00c0-\u024f]+", (text or "").lower()) if len(t) > 1]
