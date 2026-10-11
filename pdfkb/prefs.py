"""Persisted preferences: embedding, extraction, storage and libraries.

Stored as JSON in the launch directory (next to config.DATA_DIR), so switching
the knowledge-base location never loses the settings themselves.

A **library** is a named storage profile (where the knowledge base, the vector
store and the exports live). The flat storage keys are the *active* library's
configuration; `libraries` keeps every profile, so the user can define several
target knowledge bases and switch between them.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

from . import config

SETTINGS_PATH = config.DATA_DIR / "settings.json"

DEFAULTS = {
    # --- embedding ---
    "provider": "auto",          # auto | ollama | sentence-transformers | hash
    "model": "",                 # empty -> provider default
    "ollama_url": config.OLLAMA_URL,
    # --- extraction ---
    "extractor": "docling",      # docling (preferred) | pymupdf — falls back with a note
    "docling_ocr": "false",      # docling: OCR scanned pages
    # --- storage (blank = derived from data_dir) ---
    "data_dir": str(config.DATA_DIR),
    "sqlite_path": "",           # knowledge base file
    "vector_backend": "chroma",  # see vectors.BACKEND_SPECS
    "vector_path": "",           # local index directory (chroma/hnswlib)
    "vector_collection": "pdf_chunks",
    "vector_url": "",            # server backends: qdrant/opensearch/…
    "vector_api_key": "",
    "vector_index": "",          # server index/collection name (blank = vector_collection)
    "text_dir": "",              # readable exports
    "sources_dir": "",           # stored source PDFs
    # --- libraries ---
    "active_library": "default",
    "libraries": {},             # {name: {storage keys}}
}

PROVIDERS = [
    {"id": "auto", "label": "Auto-detect", "note": "Ollama → sentence-transformers → offline hash"},
    {"id": "ollama", "label": "Ollama", "note": "local server, e.g. qwen3-embedding:0.6b"},
    {"id": "sentence-transformers", "label": "sentence-transformers", "note": "local in-process model"},
    {"id": "hash", "label": "Offline hash", "note": "no model needed, low quality"},
]

STORAGE_KEYS = ("data_dir", "sqlite_path", "vector_backend", "vector_path",
                "vector_collection", "vector_url", "vector_api_key", "vector_index",
                "text_dir", "sources_dir")
EMBED_KEYS = ("provider", "model", "ollama_url")
EXTRACT_KEYS = ("extractor", "docling_ocr")
_DICT_KEYS = {"libraries"}


# ------------------------------------------------------------------- basics
def load() -> dict:
    p = Path(SETTINGS_PATH)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            merged = {**DEFAULTS, **data}
            if not isinstance(merged.get("libraries"), dict):
                merged["libraries"] = {}
            return merged
        except (OSError, ValueError):
            pass
    return dict(DEFAULTS)


def save(data: dict) -> dict:
    cur = load()
    for k, v in data.items():
        if k not in DEFAULTS:
            continue
        cur[k] = v if k in _DICT_KEYS else (None if v is None else str(v))
    Path(SETTINGS_PATH).parent.mkdir(parents=True, exist_ok=True)
    Path(SETTINGS_PATH).write_text(json.dumps(cur, indent=2) + "\n", encoding="utf-8")
    return cur


def embedder_kwargs() -> dict:
    s = load()
    return {"provider": s["provider"], "model": s["model"], "ollama_url": s["ollama_url"]}


# ------------------------------------------------------------------ storage
def _resolve(s: dict) -> dict:
    base = Path(s.get("data_dir") or config.DATA_DIR).expanduser()

    def sub(key, default_name):
        v = (s.get(key) or "").strip()
        return Path(v).expanduser() if v else base / default_name

    return {
        "data_dir": base,
        "sqlite_path": sub("sqlite_path", "kb.sqlite"),
        "vector_backend": (s.get("vector_backend") or "chroma").strip().lower(),
        "vector_path": sub("vector_path", "chroma"),
        "vector_collection": (s.get("vector_collection") or "pdf_chunks").strip(),
        "vector_url": (s.get("vector_url") or "").strip(),
        "vector_api_key": (s.get("vector_api_key") or "").strip(),
        "vector_index": (s.get("vector_index") or "").strip(),
        "text_dir": sub("text_dir", "text"),
        "sources_dir": sub("sources_dir", "sources"),
    }


def storage(s: dict | None = None) -> dict:
    """Resolved storage layout for the active library (absolute paths)."""
    return _resolve(s or load())


def ensure_storage_dirs(st=None):
    st = st or storage()
    for key in ("data_dir", "vector_path", "text_dir", "sources_dir"):
        Path(st[key]).mkdir(parents=True, exist_ok=True)
    Path(st["sqlite_path"]).parent.mkdir(parents=True, exist_ok=True)
    return st


def backup_kb(st=None):
    """Copy the knowledge-base file aside before a destructive/config change."""
    st = st or storage()
    src = Path(st["sqlite_path"])
    if not src.exists():
        return None
    dest = src.with_suffix(src.suffix + ".bak")
    try:
        import shutil
        c = sqlite3.connect(str(src))
        c.execute("PRAGMA wal_checkpoint(FULL)")
        c.close()
        shutil.copy2(src, dest)
        return str(dest)
    except Exception:  # noqa: BLE001
        return None


def storage_status(st=None) -> dict:
    """Resolved layout plus whether each piece exists and how big it is."""
    st = st or storage()
    out = {"paths": {k: str(v) for k, v in st.items()},
           "vector_backend": st["vector_backend"],
           "vector_collection": st["vector_collection"],
           "vector_url": st["vector_url"],
           "vector_index": st["vector_index"],
           "active_library": active_library()}
    sql = Path(st["sqlite_path"])
    out["sqlite"] = {"exists": sql.exists(),
                     "bytes": sql.stat().st_size if sql.exists() else 0}
    vp = Path(st["vector_path"])
    if st["vector_backend"] in ("sqlite",):
        out["vector"] = {"exists": sql.exists(), "note": "stored inside the knowledge-base file"}
    elif st["vector_backend"] in ("qdrant", "opensearch", "elasticsearch"):
        out["vector"] = {"exists": bool(st["vector_url"]), "path": st["vector_url"]}
    else:
        out["vector"] = {"exists": vp.exists(), "path": str(vp)}
    return out


# ---------------------------------------------------------------- libraries
def active_library() -> str:
    return load().get("active_library") or "default"


def _snapshot(s: dict) -> dict:
    return {k: s.get(k, "") for k in STORAGE_KEYS}


def libraries_map() -> dict:
    """All libraries, with the active one materialised from the live settings."""
    s = load()
    libs = dict(s.get("libraries") or {})
    libs[s.get("active_library") or "default"] = _snapshot(s)
    return libs


def libraries_list() -> list:
    """Libraries with their resolved location and document count."""
    s = load()
    active = s.get("active_library") or "default"
    out = []
    for name, cfg in libraries_map().items():
        st = _resolve(cfg)
        docs, exists = None, False
        p = Path(st["sqlite_path"])
        try:
            if p.exists():
                c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
                docs = c.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
                c.close()
                exists = True
        except Exception:  # noqa: BLE001
            docs = None
        out.append({"name": name, "active": name == active, "exists": exists,
                    "documents": docs, "backend": st["vector_backend"],
                    "data_dir": str(st["data_dir"]),
                    "vector": st["vector_url"] or str(st["vector_path"])})
    return sorted(out, key=lambda x: (not x["active"], x["name"] or ""))


def save_library(name: str, storage_cfg: dict | None = None) -> dict:
    """Create or update a library (defaults to the current storage settings)."""
    name = (name or "").strip()
    if not name:
        raise ValueError("a library needs a name")
    s = load()
    libs = libraries_map()
    cfg = _snapshot(s) if storage_cfg is None else {
        k: str(storage_cfg.get(k, "") or "") for k in STORAGE_KEYS}
    libs[name] = cfg
    data = {"libraries": libs}
    if name == (s.get("active_library") or "default"):
        data.update(cfg)
    return save(data)


def use_library(name: str) -> dict:
    """Switch the active library (the current one is snapshotted first)."""
    name = (name or "").strip()
    s = load()
    libs = libraries_map()
    if name not in libs:
        raise KeyError(f"unknown library: {name}")
    libs[s.get("active_library") or "default"] = _snapshot(s)
    data = {k: libs[name].get(k, "") for k in STORAGE_KEYS}
    data["libraries"] = libs
    data["active_library"] = name
    return save(data)


def _looks_like_kb(d: Path) -> bool:
    """Only ever remove a directory that really holds a knowledge base."""
    if not d.is_dir():
        return False
    return any((d / n).exists()
               for n in ("kb.sqlite", "chroma", "text", "sources", "settings.json"))


def _count_documents(sqlite_path: Path):
    try:
        c = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
        try:
            return c.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        finally:
            c.close()
    except Exception:  # noqa: BLE001
        return None


def _dir_size(d: Path) -> int:
    total = 0
    for p in d.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def delete_library(name: str, purge: bool = True) -> dict:
    """Delete a library — and, by default, everything it holds on disk.

    That is the knowledge-base file, the vector store, the exported text and the
    stored copies of the sources. Refuses the active library, refuses any
    directory another library (or the live settings) still points at, and only
    removes a directory that actually looks like a knowledge base.
    """
    name = (name or "").strip()
    s = load()
    libs = libraries_map()
    if name == (s.get("active_library") or "default"):
        raise ValueError("cannot delete the active knowledge base — switch to another first")
    cfg = libs.get(name)
    if cfg is None:
        raise KeyError(f"unknown library: {name}")

    st = _resolve(cfg)
    target = Path(st["data_dir"]).expanduser().resolve()

    libs.pop(name, None)
    save({"libraries": libs})      # forget it first: it can no longer be switched to

    report = {"library": name, "purged": None, "skipped": None, "documents": None,
              "freed_bytes": 0, "error": None}
    if not purge:
        report["skipped"] = "purge was not requested"
        return report

    # never take out a directory someone else is still using — the live one included
    in_use = {Path(_resolve(c)["data_dir"]).expanduser().resolve() for c in libs.values()}
    in_use.add(Path(storage(load())["data_dir"]).expanduser().resolve())
    in_use.discard(target)
    if target in {Path(_resolve(c)["data_dir"]).expanduser().resolve() for c in libs.values()} \
            or target == Path(storage(load())["data_dir"]).expanduser().resolve():
        report["skipped"] = "another knowledge base still uses this directory"
        return report
    if any(target in t.parents for t in in_use):
        report["skipped"] = "a live knowledge base lives inside this directory"
        return report
    if not _looks_like_kb(target):
        report["skipped"] = "nothing that looks like a knowledge base on disk"
        return report

    report["documents"] = _count_documents(Path(st["sqlite_path"]))
    report["freed_bytes"] = _dir_size(target)
    try:
        shutil.rmtree(target)
        report["purged"] = str(target)
    except OSError as ex:  # noqa: BLE001
        report["error"] = f"{type(ex).__name__}: {ex}"
    return report
