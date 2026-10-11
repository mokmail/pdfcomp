"""Embedding backends.

All embedders return L2-normalised float vectors so that cosine similarity
is well defined and comparable regardless of which backend produced them.
Provider order: Ollama (local, preferred) -> sentence-transformers ->
deterministic hash fallback (always works, even fully offline).
"""

from __future__ import annotations

import hashlib
import math

import requests

from . import config


def normalize(vec):
    s = math.sqrt(sum(float(x) * float(x) for x in vec))
    if s == 0.0:
        return [0.0] * len(vec)
    return [float(x) / s for x in vec]


class Embedder:
    name = "base"
    model = ""
    dim = 0

    def embed(self, texts):
        """texts: list[str] -> list[list[float]] (normalised)."""
        raise NotImplementedError

    def info(self):
        return f"{self.name}:{self.model} (dim={self.dim or '?'})"


class OllamaEmbedder(Embedder):
    name = "ollama"

    def __init__(self, model=None, url=None, batch=32, timeout=180):
        self.model = model or config.OLLAMA_EMBED_MODEL
        self.url = (url or config.OLLAMA_URL).rstrip("/")
        self.batch = batch
        self.timeout = timeout
        self.dim = 0

    def available(self):
        try:
            return requests.get(self.url + "/api/tags", timeout=5).status_code == 200
        except Exception:
            return False

    def embed(self, texts):
        out = []
        for i in range(0, len(texts), self.batch):
            batch = [t if t and t.strip() else " " for t in texts[i:i + self.batch]]
            r = requests.post(
                self.url + "/api/embed",
                json={"model": self.model, "input": batch},
                timeout=self.timeout,
            )
            r.raise_for_status()
            embs = (r.json() or {}).get("embeddings") or []
            if len(embs) != len(batch):
                raise RuntimeError(
                    "ollama returned %d embeddings for %d inputs" % (len(embs), len(batch))
                )
            out.extend(normalize(e) for e in embs)
        if out and not self.dim:
            self.dim = len(out[0])
        return out


class SentenceTransformerEmbedder(Embedder):
    name = "sentence-transformers"

    def __init__(self, model=None):
        from sentence_transformers import SentenceTransformer  # heavy, lazy
        self.model = model or config.ST_MODEL
        self._m = SentenceTransformer(self.model)
        self.dim = int(self._m.get_sentence_embedding_dimension())

    def embed(self, texts):
        texts = [t if t and t.strip() else " " for t in texts]
        embs = self._m.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return [[float(x) for x in e] for e in embs]


class HashEmbedder(Embedder):
    """Deterministic offline fallback: signed hashing of words + char 3-grams.

    Low quality but real vectors, so the app still indexes and searches when
    no model server is reachable.
    """

    name = "hash"

    def __init__(self, dim=None):
        self.dim = int(dim or config.HASH_DIM)
        self.model = f"hash-{self.dim}"

    def _vec(self, text):
        v = [0.0] * self.dim
        toks = text.lower().split()
        joined = " ".join(toks)
        grams = list(toks)
        for i in range(max(0, len(joined) - 2)):
            grams.append(joined[i:i + 3])
        for tok in grams:
            h = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16)
            v[h % self.dim] += 1.0 if (h >> 8) & 1 else -1.0
        return normalize(v)

    def embed(self, texts):
        return [self._vec(t) for t in texts]


class EmbedderError(RuntimeError):
    """Raised when an explicitly requested provider cannot be used."""


def list_ollama_models(url=None):
    """Installed Ollama models, or raise if the server is unreachable."""
    r = requests.get((url or config.OLLAMA_URL).rstrip("/") + "/api/tags", timeout=8)
    r.raise_for_status()
    return (r.json() or {}).get("models", [])


def probe_embedder(provider=None, model=None, ollama_url=None):
    """Build a provider and embed one string — the settings 'Test' path."""
    import time
    e = get_embedder(provider, model, ollama_url)
    t = time.time()
    vec = e.embed(["pdf knowledge base embedding probe"])
    return {"provider": e.name, "model": e.model, "dim": len(vec[0]),
            "ms": round((time.time() - t) * 1000, 1)}


def get_embedder(provider=None, model=None, ollama_url=None):
    """Return an embedder for the requested provider.

    provider: auto | ollama | sentence-transformers | hash.
    'auto' falls back gracefully; an explicit provider raises EmbedderError
    instead of silently using something else.
    """
    p = (provider or "auto").strip().lower()
    order = {
        "auto": ["ollama", "sentence-transformers", "hash"],
        "": ["ollama", "sentence-transformers", "hash"],
        "ollama": ["ollama"],
        "sentence-transformers": ["sentence-transformers"],
        "st": ["sentence-transformers"],
        "hash": ["hash"],
    }.get(p, ["ollama", "sentence-transformers", "hash"])
    errors = []
    for choice in order:
        try:
            if choice == "ollama":
                e = OllamaEmbedder(model=model or None, url=ollama_url or None)
                if e.available():
                    e.embed(["probe"])              # confirms model + dimension
                    if e.dim:
                        return e
                errors.append(f"ollama: server or model '{model or config.OLLAMA_EMBED_MODEL}' unavailable")
            elif choice == "sentence-transformers":
                return SentenceTransformerEmbedder(model=model or None)
            elif choice == "hash":
                return HashEmbedder()
        except Exception as ex:  # noqa: BLE001
            errors.append(f"{choice}: {type(ex).__name__}: {ex}")
    if p not in ("auto", ""):
        raise EmbedderError("; ".join(errors) or f"provider '{p}' unavailable")
    e = HashEmbedder()
    e.errors = errors
    return e
