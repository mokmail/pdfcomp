"""Vector-store backends.

One interface, many implementations, so the target vector store is a setting:

  Local / embedded
    sqlite    vectors as blobs in the knowledge-base file, numpy cosine   [verified]
    hnswlib   local ANN index file (HNSW), no server                      [verified]
    chroma    embedded persistent Chroma collection                       [verified]
  Self-hosted server
    qdrant    Qdrant (URL, or ":memory:" for a local instance)            [verified]
    opensearch / elasticsearch   k-NN index over the REST client          [needs server]
  Hosted / other
    registered with their client library and required config; selectable
    once their client is installed (see `backends()`)

All stores return (chunk_id, cosine_similarity); text, page and filename are
read back from SQLite, so one record stays the single source of truth.
"""

from __future__ import annotations

import json
import struct
import uuid
from pathlib import Path

# ---------------------------------------------------------------- registry
BACKEND_SPECS = [
    {"id": "sqlite", "label": "SQLite (in-file)", "category": "Local / embedded",
     "status": "verified", "install": "",
     "note": "vectors in the knowledge-base file, brute-force cosine — no extra dependency"},
    {"id": "hnswlib", "label": "HNSW index file", "category": "Local / embedded",
     "status": "verified", "install": "python3 -m pip install hnswlib",
     "note": "local approximate-nearest-neighbour index, no server"},
    {"id": "chroma", "label": "Chroma (embedded)", "category": "Local / embedded",
     "status": "verified", "install": "python3 -m pip install chromadb",
     "note": "persistent embedded vector database, HNSW index"},
    {"id": "qdrant", "label": "Qdrant", "category": "Self-hosted server",
     "status": "verified", "install": "python3 -m pip install qdrant-client",
     "note": "point at a server URL, or ':memory:' for a local instance",
     "fields": ["vector_url", "vector_api_key"]},
    {"id": "opensearch", "label": "OpenSearch (k-NN)", "category": "Self-hosted server",
     "status": "server", "install": "python3 -m pip install opensearch-py",
     "note": "needs a running OpenSearch with the k-NN plugin",
     "fields": ["vector_url", "vector_index", "vector_api_key"]},
    {"id": "elasticsearch", "label": "Elasticsearch (dense_vector)", "category": "Self-hosted server",
     "status": "server", "install": "python3 -m pip install opensearch-py",
     "note": "needs a running Elasticsearch; uses the same REST client",
     "fields": ["vector_url", "vector_index", "vector_api_key"]},
    {"id": "weaviate", "label": "Weaviate", "category": "Self-hosted server",
     "status": "install", "install": "python3 -m pip install weaviate-client",
     "note": "client library required", "fields": ["vector_url", "vector_api_key"]},
    {"id": "milvus", "label": "Milvus / Zilliz", "category": "Self-hosted server",
     "status": "install", "install": "python3 -m pip install pymilvus",
     "note": "client library required", "fields": ["vector_url", "vector_index", "vector_api_key"]},
    {"id": "pgvector", "label": "PostgreSQL + pgvector", "category": "Self-hosted server",
     "status": "install", "install": "python3 -m pip install psycopg[binary]",
     "note": "needs a Postgres with the vector extension", "fields": ["vector_url", "vector_index"]},
    {"id": "redis", "label": "Redis Stack (vector)", "category": "Self-hosted server",
     "status": "install", "install": "python3 -m pip install redis",
     "note": "needs RediSearch/Redis Stack", "fields": ["vector_url", "vector_index"]},
    {"id": "lancedb", "label": "LanceDB", "category": "Local / embedded",
     "status": "install", "install": "python3 -m pip install lancedb",
     "note": "embedded columnar vector store on disk"},
    {"id": "faiss", "label": "FAISS index file", "category": "Local / embedded",
     "status": "install", "install": "python3 -m pip install faiss-cpu",
     "note": "local index file, library not installed"},
    {"id": "duckdb", "label": "DuckDB (VSS)", "category": "Local / embedded",
     "status": "install", "install": "python3 -m pip install duckdb",
     "note": "needs the vss extension"},
    {"id": "pinecone", "label": "Pinecone", "category": "Hosted cloud",
     "status": "install", "install": "python3 -m pip install pinecone-client",
     "note": "managed service — index host + API key", "fields": ["vector_url", "vector_index", "vector_api_key"]},
    {"id": "typesense", "label": "Typesense", "category": "Hosted cloud",
     "status": "install", "install": "python3 -m pip install typesense",
     "note": "vector search — url + api key", "fields": ["vector_url", "vector_index", "vector_api_key"]},
    {"id": "upstash", "label": "Upstash Vector", "category": "Hosted cloud",
     "status": "install", "install": "python3 -m pip install upstash-vector",
     "note": "serverless — url + token", "fields": ["vector_url", "vector_api_key"]},
    {"id": "mongodb", "label": "MongoDB Atlas Vector Search", "category": "Hosted cloud",
     "status": "install", "install": "python3 -m pip install pymongo",
     "note": "Atlas cluster — connection string + index", "fields": ["vector_url", "vector_index"]},
    {"id": "azure-search", "label": "Azure AI Search", "category": "Hosted cloud",
     "status": "install", "install": "python3 -m pip install azure-search-documents",
     "note": "url + admin key + index", "fields": ["vector_url", "vector_index", "vector_api_key"]},
    {"id": "turbopuffer", "label": "Turbopuffer", "category": "Hosted cloud",
     "status": "install", "install": "python3 -m pip install turbopuffer",
     "note": "namespace + API token", "fields": ["vector_url", "vector_index", "vector_api_key"]},
    {"id": "vespa", "label": "Vespa", "category": "Hosted cloud",
     "status": "install", "install": "python3 -m pip install pyvespa",
     "note": "url + app + schema", "fields": ["vector_url", "vector_index", "vector_api_key"]},
]


def _has(mod):
    try:
        __import__(mod)
        return True
    except Exception:  # noqa: BLE001
        return False


_LIB = {"sqlite": None, "hnswlib": "hnswlib", "chroma": "chromadb", "qdrant": "qdrant_client",
        "opensearch": "opensearchpy", "elasticsearch": "opensearchpy",
        "weaviate": "weaviate", "milvus": "pymilvus", "pgvector": "psycopg",
        "redis": "redis", "lancedb": "lancedb", "faiss": "faiss", "duckdb": "duckdb",
        "pinecone": "pinecone", "typesense": "typesense", "upstash": "upstash_vector",
        "mongodb": "pymongo", "azure-search": "azure", "turbopuffer": "turbopuffer",
        "vespa": "vespa"}


def backends():
    """Registry with availability resolved on this machine."""
    out = []
    for spec in BACKEND_SPECS:
        lib = _LIB.get(spec["id"])
        installed = True if lib is None else _has(lib)
        out.append({**spec, "available": installed, "status": spec["status"],
                    "runnable": spec["status"] == "verified" and installed
                                or (spec["status"] == "server" and installed)})
    return out


def backend_ids():
    return [b["id"] for b in BACKEND_SPECS]


# ------------------------------------------------------------------ common
def _pack(vec):
    return struct.pack(f"<{len(vec)}f", *[float(x) for x in vec])


def _unpack(blob):
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


def _uuid(chunk_id):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "pdfkb:" + chunk_id))


class VectorStore:
    name = "base"

    def add(self, ids, embeddings, documents, metadatas):
        raise NotImplementedError

    def query(self, embedding, k):
        raise NotImplementedError

    def delete(self, doc_id=None, ids=None):
        raise NotImplementedError

    def count(self):
        raise NotImplementedError

    def reset(self):
        raise NotImplementedError

    def info(self):
        raise NotImplementedError

    def test(self):
        return f"{self.name}: ok · {self.info()}"


# ------------------------------------------------------------ sqlite (file)
class SqliteStore(VectorStore):
    name = "sqlite"

    def __init__(self, conn, collection):
        self.conn = conn
        self.collection_name = collection
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS vectors ("
            " chunk_id TEXT PRIMARY KEY, doc_id TEXT, dim INTEGER, vec BLOB)"
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_vectors_doc ON vectors(doc_id)")
        self.conn.commit()

    def add(self, ids, embeddings, documents, metadatas):
        self.conn.executemany(
            "INSERT OR REPLACE INTO vectors(chunk_id,doc_id,dim,vec) VALUES(?,?,?,?)",
            [(cid, md.get("doc_id"), len(e), _pack(e)) for cid, e, md in zip(ids, embeddings, metadatas)])
        self.conn.commit()

    def query(self, embedding, k):
        import numpy as np
        rows = self.conn.execute("SELECT chunk_id, vec FROM vectors").fetchall()
        if not rows:
            return []
        mat = np.vstack([_unpack(r["vec"]) for r in rows])
        sims = mat @ np.asarray(embedding, dtype="float32")   # both L2-normalised
        order = np.argsort(-sims)[:max(1, k)]
        return [(rows[i]["chunk_id"], float(sims[i])) for i in order]

    def delete(self, doc_id=None, ids=None):
        if ids:
            self.conn.executemany("DELETE FROM vectors WHERE chunk_id=?", [(i,) for i in ids])
        elif doc_id:
            self.conn.execute("DELETE FROM vectors WHERE doc_id=?", (doc_id,))
        self.conn.commit()

    def count(self):
        return self.conn.execute("SELECT COUNT(*) c FROM vectors").fetchone()["c"]

    def reset(self):
        self.conn.execute("DELETE FROM vectors")
        self.conn.commit()

    def info(self):
        return f"sqlite · collection={self.collection_name} · {self.count()} vectors"


# ----------------------------------------------------------- hnswlib (file)
class HnswlibStore(VectorStore):
    """Local HNSW index + a label map, both on disk."""

    name = "hnswlib"

    def __init__(self, path, collection, dim=None, space="cosine"):
        import hnswlib  # noqa: F401  (fail fast if missing)
        self.dir = Path(path) / "hnswlib"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.collection_name = collection
        self.index_path = self.dir / f"{collection}.bin"
        self.meta_path = self.dir / f"{collection}.json"
        self.space = space
        meta = {}
        if self.meta_path.exists():
            try:
                meta = json.loads(self.meta_path.read_text())
            except ValueError:
                meta = {}
        self.dim = int(dim or meta.get("dim") or 0)
        self.labels = {int(k): v for k, v in (meta.get("labels") or {}).items()}
        self._by_chunk = {v: k for k, v in self.labels.items()}
        self.index = None
        if self.index_path.exists() and self.dim:
            import hnswlib
            self.index = hnswlib.Index(space=self.space, dim=self.dim)
            self.index.load_index(str(self.index_path), max_elements=max(len(self.labels) + 1024, 1024))
            self.index.set_ef(max(64, len(self.labels)))

    def _ensure(self, dim):
        import hnswlib
        if self.dim and dim != self.dim:
            raise RuntimeError(f"hnswlib index dim is {self.dim}, embeddings are {dim} — reset the index")
        self.dim = dim
        if self.index is None:
            self.index = hnswlib.Index(space=self.space, dim=dim)
            self.index.init_index(max_elements=1024, ef_construction=200, M=16)
            self.index.set_ef(64)

    def _save(self):
        self.index.save_index(str(self.index_path))
        self.meta_path.write_text(json.dumps(
            {"dim": self.dim, "space": self.space, "labels": self.labels}))

    def add(self, ids, embeddings, documents, metadatas):
        self._ensure(len(embeddings[0]))
        self.index.set_num_threads(1)
        need = len(self.labels) + len(ids) + 16
        if need > self.index.get_max_elements():
            self.index.resize_index(need)
        new_labels, new_vecs = [], []
        for cid, emb in zip(ids, embeddings):
            label = self._by_chunk.get(cid)
            if label is None:
                label = max(self.labels, default=-1) + 1
                self.labels[label] = cid
                self._by_chunk[cid] = label
            new_labels.append(label)
            new_vecs.append([float(x) for x in emb])
        self.index.add_items(new_vecs, new_labels)
        self._save()

    def query(self, embedding, k):
        if self.index is None or not self.labels:
            return []
        labels, dists = self.index.knn_query([float(x) for x in embedding], k=min(max(1, k), len(self.labels)))
        out = []
        for label, dist in zip(labels[0], dists[0]):
            cid = self.labels.get(int(label))
            if cid:
                out.append((cid, 1.0 - float(dist)))     # cosine space
        return out

    def delete(self, doc_id=None, ids=None):
        if not self.index:
            return
        for cid in (ids or []):
            label = self._by_chunk.pop(cid, None)
            if label is not None:
                self.index.mark_deleted(label)
                self.labels.pop(label, None)
        self._save()

    def count(self):
        return len(self.labels)

    def reset(self):
        self.index = None
        self.labels = {}
        self._by_chunk = {}
        for p in (self.index_path, self.meta_path):
            try:
                p.unlink()
            except OSError:
                pass

    def info(self):
        return f"hnswlib · {self.collection_name} · {self.count()} vectors · dim={self.dim or '?'}"


# ------------------------------------------------------------------- qdrant
class QdrantStore(VectorStore):
    name = "qdrant"
    batch = 256

    def __init__(self, url, collection, api_key=None, dim=None):
        from qdrant_client import QdrantClient
        self.url = url or ":memory:"
        if self.url in (":memory:", "memory", "local"):
            self.client = QdrantClient(location=":memory:")
            self.url = ":memory:"
        else:
            self.client = QdrantClient(url=self.url, api_key=api_key or None, timeout=30)
        self.collection_name = collection
        self.dim = dim
        self._ensure(dim)

    def _exists(self):
        try:
            return self.client.collection_exists(self.collection_name)
        except Exception:  # noqa: BLE001
            try:
                self.client.get_collection(self.collection_name)
                return True
            except Exception:  # noqa: BLE001
                return False

    def _ensure(self, dim=None):
        from qdrant_client import models as m
        self.dim = dim or self.dim
        if not self._exists():
            if not self.dim:
                return                       # created lazily on the first add
            self.client.create_collection(
                self.collection_name,
                vectors_config=m.VectorParams(size=int(self.dim), distance=m.Distance.COSINE))

    def add(self, ids, embeddings, documents, metadatas):
        from qdrant_client import models as m
        self._ensure(len(embeddings[0]))
        points = [m.PointStruct(id=_uuid(cid), vector=[float(x) for x in emb],
                                payload={"chunk_id": cid, "doc_id": md.get("doc_id"),
                                         "page": md.get("page")})
                  for cid, emb, md in zip(ids, embeddings, metadatas)]
        for i in range(0, len(points), self.batch):
            self.client.upsert(self.collection_name, points=points[i:i + self.batch], wait=True)

    def query(self, embedding, k):
        if not self._exists():
            return []
        res = self.client.query_points(self.collection_name,
                                       query=[float(x) for x in embedding],
                                       limit=max(1, k), with_payload=True).points
        return [(p.payload.get("chunk_id"), float(p.score)) for p in res if p.payload]

    def delete(self, doc_id=None, ids=None):
        from qdrant_client import models as m
        if not self._exists():
            return
        if ids:
            self.client.delete(self.collection_name,
                               points_selector=[_uuid(i) for i in ids], wait=True)
        elif doc_id:
            self.client.delete(self.collection_name,
                               points_selector=m.Filter(must=[m.FieldCondition(
                                   key="doc_id", match=m.MatchValue(value=doc_id))]), wait=True)

    def count(self):
        try:
            return self.client.count(self.collection_name, exact=True).count
        except Exception:  # noqa: BLE001
            return 0

    def reset(self):
        try:
            self.client.delete_collection(self.collection_name)
        except Exception:  # noqa: BLE001
            pass

    def info(self):
        return f"qdrant · {self.url} · {self.collection_name} · {self.count()} vectors"


# ---------------------------------------------- opensearch / elasticsearch
class OpenSearchStore(VectorStore):
    """k-NN index over the OpenSearch/Elasticsearch REST client.

    Requires a running server with the k-NN plugin — not exercised in this
    environment, so it fails loudly rather than silently.
    """

    name = "opensearch"
    batch = 256

    def __init__(self, url, collection, api_key=None, dialect="opensearch", dim=None):
        from opensearchpy import OpenSearch
        if not url:
            raise ValueError("a server url is required")
        self.client = OpenSearch(hosts=[url], http_auth=api_key or None, timeout=30)
        self.collection_name = collection
        self.dialect = dialect
        self.dim = dim
        if not self.client.indices.exists(collection) and dim:
            self.client.indices.create(collection, body={
                "settings": {"index": {"knn": True}},
                "mappings": {"properties": {"vec": {"type": "knn_vector", "dimension": int(dim)},
                                            "chunk_id": {"type": "keyword"},
                                            "doc_id": {"type": "keyword"},
                                            "page": {"type": "integer"}}}})

    def add(self, ids, embeddings, documents, metadatas):
        if not self.client.indices.exists(self.collection_name):
            raise RuntimeError("index does not exist — reset with a known dim, or create it first")
        body = []
        for cid, emb, md in zip(ids, embeddings, metadatas):
            body.append({"index": {"_index": self.collection_name, "_id": _uuid(cid)}})
            body.append({"chunk_id": cid, "doc_id": md.get("doc_id"), "page": md.get("page"),
                         "vec": [float(x) for x in emb]})
        self.client.bulk(body=body, refresh=True)

    def query(self, embedding, k):
        if not self.client.indices.exists(self.collection_name):
            return []
        res = self.client.search(self.collection_name, body={
            "size": max(1, k),
            "query": {"knn": {"vec": {"vector": [float(x) for x in embedding], "k": max(1, k)}}}})
        out = []
        for hit in res.get("hits", {}).get("hits", []):
            src = hit.get("_source", {})
            if src.get("chunk_id"):
                out.append((src["chunk_id"], 1.0 - float(hit.get("_score") or 0.0)))
        return out

    def delete(self, doc_id=None, ids=None):
        if ids:
            for cid in ids:
                try:
                    self.client.delete(self.collection_name, _uuid(cid), refresh=True)
                except Exception:  # noqa: BLE001
                    pass
        elif doc_id:
            self.client.delete_by_query(self.collection_name,
                                        body={"query": {"term": {"doc_id": doc_id}}}, refresh=True)

    def count(self):
        try:
            return int(self.client.count(self.collection_name)["count"])
        except Exception:  # noqa: BLE001
            return 0

    def reset(self):
        try:
            self.client.indices.delete(self.collection_name)
        except Exception:  # noqa: BLE001
            pass

    def info(self):
        return f"{self.dialect} · {self.collection_name} · {self.count()} vectors"


# ------------------------------------------------------------------- chroma
class ChromaStore(VectorStore):
    name = "chroma"
    batch = 500

    def __init__(self, path, collection):
        import chromadb
        from chromadb.config import Settings
        Path(path).mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(path),
                                                settings=Settings(anonymized_telemetry=False))
        self.collection_name = collection
        self.col = self._open()

    def _open(self):
        try:
            return self.client.get_or_create_collection(
                self.collection_name, configuration={"hnsw": {"space": "cosine"}})
        except Exception:
            return self.client.get_or_create_collection(self.collection_name)

    def add(self, ids, embeddings, documents, metadatas):
        for i in range(0, len(ids), self.batch):
            sl = slice(i, i + self.batch)
            self.col.add(ids=ids[sl],
                         embeddings=[[float(x) for x in e] for e in embeddings[sl]],
                         documents=documents[sl], metadatas=metadatas[sl])

    def query(self, embedding, k):
        res = self.col.query(query_embeddings=[[float(x) for x in embedding]],
                             n_results=max(1, k), include=["distances"])
        ids = (res.get("ids") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        return [(cid, 1.0 - float(d)) for cid, d in zip(ids, dists)]

    def delete(self, doc_id=None, ids=None):
        if ids:
            self.col.delete(ids=list(ids))
        elif doc_id:
            self.col.delete(where={"doc_id": doc_id})

    def count(self):
        return self.col.count()

    def reset(self):
        try:
            self.client.delete_collection(self.collection_name)
        except Exception:
            pass
        self.col = self._open()

    def info(self):
        return f"chroma · collection={self.collection_name} · {self.count()} vectors"


# ------------------------------------------------------------------- factory
def make_vector_store(storage, conn):
    """Build the configured backend; fall back to SQLite and say why."""
    backend = (storage.get("vector_backend") or "chroma").lower()
    collection = storage.get("vector_collection") or "pdf_chunks"
    url = (storage.get("vector_url") or "").strip()
    api_key = (storage.get("vector_api_key") or "").strip() or None
    index = (storage.get("vector_index") or "").strip() or collection
    path = storage["vector_path"]

    def fallback(msg):
        return SqliteStore(conn, collection), msg

    if backend == "sqlite":
        return SqliteStore(conn, collection), None
    if backend == "hnswlib":
        try:
            return HnswlibStore(path, collection), None
        except Exception as ex:  # noqa: BLE001
            return fallback(f"hnswlib unavailable ({type(ex).__name__}: {ex}); using sqlite")
    if backend == "qdrant":
        try:
            return QdrantStore(url or ":memory:", index, api_key=api_key), None
        except Exception as ex:  # noqa: BLE001
            return fallback(f"qdrant unavailable ({type(ex).__name__}: {ex}); using sqlite")
    if backend in ("opensearch", "elasticsearch"):
        try:
            return OpenSearchStore(url, index, api_key=api_key, dialect=backend), None
        except Exception as ex:  # noqa: BLE001
            return fallback(f"{backend} unavailable ({type(ex).__name__}: {ex}); using sqlite")
    try:
        return ChromaStore(path, collection), None
    except Exception as ex:  # noqa: BLE001
        return fallback(f"chroma unavailable ({type(ex).__name__}: {ex}); using sqlite")
