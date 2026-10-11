"""Knowledge base + vector store.

SQLite holds the extracted *text* and all provenance metadata
(doc_id, file name, page number, chunk index, char offsets, sha1).
A Chroma persistent collection holds the *embeddings* under the same
chunk ids, so a semantic hit and a keyword hit resolve to the same record.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import chromadb
from chromadb.config import Settings

from . import config
from .embeddings import get_embedder
from .extract import chunk_pages, extract_pages

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
    embed_dim      INTEGER
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
    def __init__(self, data_dir=None, embedder=None, collection=None):
        if data_dir:
            data_dir = Path(data_dir)
            self.sqlite_path = str(data_dir / "kb.sqlite")
            self.chroma_dir = str(data_dir / "chroma")
            self.text_dir = data_dir / "text"
        else:
            self.sqlite_path = str(config.SQLITE_PATH)
            self.chroma_dir = str(config.CHROMA_DIR)
            self.text_dir = config.TEXT_DIR
        self.text_dir.mkdir(parents=True, exist_ok=True)
        Path(self.chroma_dir).mkdir(parents=True, exist_ok=True)

        self.conn = sqlite3.connect(self.sqlite_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.has_fts = True
        try:
            self.conn.executescript(FTS_SCHEMA)
        except sqlite3.OperationalError:
            self.has_fts = False          # FTS5 not compiled into this sqlite
        self.conn.commit()

        self.client = chromadb.PersistentClient(
            path=self.chroma_dir, settings=Settings(anonymized_telemetry=False)
        )
        self.collection_name = collection or config.COLLECTION
        self.collection = self._open_collection()

        self.embedder = embedder or get_embedder()

    # ------------------------------------------------------------------ setup
    def _open_collection(self):
        try:
            return self.client.get_or_create_collection(
                self.collection_name, configuration={"hnsw": {"space": "cosine"}}
            )
        except Exception:
            return self.client.get_or_create_collection(self.collection_name)

    def provider_info(self):
        return self.embedder.info()

    def _stored_dim(self):
        row = self.conn.execute("SELECT value FROM meta WHERE key='embed_dim'").fetchone()
        return int(row["value"]) if row else None

    # ---------------------------------------------------------------- ingest
    def has_document(self, sha1):
        row = self.conn.execute("SELECT doc_id FROM documents WHERE sha1=?", (sha1,)).fetchone()
        return row["doc_id"] if row else None

    def add_document(self, pdf_path, force=False, progress=None):
        """Extract one PDF and store it as text + embeddings.

        Returns a summary dict. progress(done, total, message) is optional.
        """
        def say(done, total, msg):
            if progress:
                progress(done, total, msg)

        say(0, 1, f"Reading {Path(pdf_path).name}")
        meta, pages = extract_pages(pdf_path)

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
        say(1, 3, f"Embedding {len(texts)} chunks via {self.embedder.info()}")
        embs = self.embedder.embed(texts)
        dim = len(embs[0])
        stored = self._stored_dim()
        if stored and stored != dim:
            raise RuntimeError(
                f"Embedding dimension changed ({stored} -> {dim}). "
                "Re-index from the Library tab or reset the knowledge base."
            )

        chunk_ids = [f"{meta['doc_id']}-{c['chunk_index']:05d}" for c in chunks]

        say(2, 3, "Writing text + metadata to the knowledge base")
        self.conn.execute(
            "INSERT OR REPLACE INTO documents(doc_id,path,filename,sha1,npages,nchunks,"
            "title,added_at,embed_provider,embed_model,embed_dim) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (meta["doc_id"], meta["path"], meta["filename"], meta["sha1"], meta["npages"],
             len(chunks), meta["title"], time.time(), self.embedder.name,
             self.embedder.model, dim),
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

        say(3, 4, f"Writing {len(chunks)} embeddings to the vector store")
        for i in range(0, len(chunk_ids), config.CHROMA_BATCH):
            sl = slice(i, i + config.CHROMA_BATCH)
            self.collection.add(
                ids=chunk_ids[sl],
                embeddings=[list(map(float, e)) for e in embs[sl]],
                documents=texts[sl],
                metadatas=[{
                    "doc_id": meta["doc_id"],
                    "filename": meta["filename"],
                    "page": int(chunks[j]["page"]),
                    "chunk_index": int(chunks[j]["chunk_index"]),
                    "path": meta["path"],
                } for j in range(*sl.indices(len(chunk_ids)))],
            )

        say(4, 4, "Exporting readable text")
        self.export_text(meta["doc_id"])

        return {"doc_id": meta["doc_id"], "status": "added", "filename": meta["filename"],
                "npages": meta["npages"], "nchunks": len(chunks), "dim": dim}

    # ------------------------------------------------------------ text export
    def export_text(self, doc_id):
        """Write a human-readable markdown copy of the extracted text."""
        doc = self.conn.execute("SELECT * FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
        if not doc:
            return None
        rows = self.conn.execute(
            "SELECT page,text FROM chunks WHERE doc_id=? ORDER BY chunk_index", (doc_id,)
        ).fetchall()
        # de-duplicate overlapping text by page, keeping first occurrence per page
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
        dest = Path(self.text_dir) / f"{Path(doc['filename']).stem}.{doc_id[:8]}.md"
        dest.write_text("\n".join(out), encoding="utf-8")
        return str(dest)

    # -------------------------------------------------------------- search
    def semantic_search(self, query, k=10):
        qe = self.embedder.embed([query])[0]
        res = self.collection.query(
            query_embeddings=[qe], n_results=max(1, k),
            include=["documents", "metadatas", "distances"],
        )
        ids = res.get("ids", [[]])[0]
        docs = res.get("documents", [[]])[0]
        metas = res.get("metadatas", [[]])[0]
        dists = res.get("distances", [[]])[0]
        out = []
        for cid, text, md, dist in zip(ids, docs, metas, dists):
            # cosine space -> distance in [0,2]; similarity = 1 - distance
            out.append(self._mk_result(cid, md, text, sim=1.0 - float(dist)))
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
            md = {"doc_id": r["doc_id"], "filename": r["filename"],
                  "page": int(r["page"]), "path": self._doc_path(r["doc_id"])}
            out.append(self._mk_result(r["chunk_id"], md, r["text"], kw=-float(r["score"])))
        return out

    def _like_search(self, query, k):
        terms = _tokenize(query)
        if not terms:
            return []
        where = " OR ".join(["text LIKE ?"] * len(terms))
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
        path = md.get("path", "") or self._doc_path(md.get("doc_id"))
        return {
            "chunk_id": chunk_id,
            "doc_id": md.get("doc_id"),
            "filename": fname,
            "page": page,
            "chunk_index": md.get("chunk_index"),
            "text": text,
            "sim": sim,
            "kw": kw,
            "score": sim if sim is not None else (kw or 0.0),
            "path": path,
            "citation": f"{fname} p.{page}",
        }

    def _doc_path(self, doc_id):
        row = self.conn.execute("SELECT path FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
        return row["path"] if row else ""

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
            "embed_provider,embed_model,embed_dim FROM documents ORDER BY added_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def stats(self):
        docs = self.conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
        chunks = self.conn.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"]
        return {"documents": docs, "chunks": chunks,
                "vectors": self.collection.count(),
                "provider": self.embedder.info(),
                "sqlite": self.sqlite_path, "chroma": self.chroma_dir}

    def delete_document(self, doc_id):
        self.conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
        if self.has_fts:
            self.conn.execute("DELETE FROM chunks_fts WHERE doc_id=?", (doc_id,))
        self.conn.execute("DELETE FROM documents WHERE doc_id=?", (doc_id,))
        self.conn.commit()
        try:
            self.collection.delete(where={"doc_id": doc_id})
        except Exception:
            pass

    def reset(self):
        """Wipe the whole knowledge base and vector store."""
        self.conn.executescript(
            "DELETE FROM chunks; DELETE FROM chunks_fts; DELETE FROM documents; DELETE FROM meta;"
        )
        self.conn.commit()
        try:
            self.client.delete_collection(self.collection_name)
        except Exception:
            pass
        self.collection = self._open_collection()


def _tokenize(text):
    import re
    return [t for t in re.split(r"[^\w\u00c0-\u024f]+", (text or "").lower()) if len(t) > 1]
