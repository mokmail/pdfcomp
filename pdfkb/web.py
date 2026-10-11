"""Starlette web app for Knowledgebases.

JSON API over the same `pdfkb` engine the CLI uses, plus a static front-end
styled with the kmail.at design tokens.

    python3 serve.py            # http://127.0.0.1:8765

Blocking KB work (embedding, sqlite, chroma, ssh) runs in a threadpool so the
event loop stays free. Uploaded PDFs are copied into the knowledge base's
`sources/` folder, so `/api/pdf/<doc_id>` serves the original file back to the
browser — which is what makes "open source at page N" work.

Settings cover the embedding provider/model (with `ollama pull`) and the storage
layout (data dir, knowledge-base file, vector backend/location/collection).
Sources (`/api/sources`) are saved connections to a file server or other origin.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import config, prefs, sources
from . import __version__
from .embeddings import get_embedder, list_ollama_models, probe_embedder  # noqa: F401
from .extract import EXTRACTORS, docling_available, docling_service_ready, docling_service_url, docling_version
from .store import KB
from .vectors import backends as vector_backends
from .vectors import make_vector_store

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
MASK = "••••••"
_STARTED = time.time()        # the footer reports how long this instance has been up

_kb: KB | None = None
_kbs: dict = {}          # non-active libraries, opened on demand for cross-library search
_job = {"kind": None, "target": None, "lines": [], "ok": None, "running": False}
# What ingestion is doing right now. Its own record (not _job) so an install and an
# ingest can run at once, and so the header can show the ingest while the user is on
# any other page — the extraction/embedding keeps running server-side either way.
_ingest = {"running": False, "ok": None, "filename": None, "index": 0, "total": 0,
           "detail": "", "pct": 0, "started": None, "done": None, "results": []}
_lock = threading.Lock()


def kb() -> KB:
    global _kb
    if _kb is None:
        _kb = KB()
    return _kb


def _kb_for(lib: str) -> KB | None:
    """The KB for a named library. The active one is the writable handle; any other is
    opened strictly read-only, so searching across knowledge bases can never save a file
    into one of them. A library whose files are gone reads as empty (None)."""
    if not lib or lib == prefs.active_library():
        return kb()
    with _lock:
        if lib not in _kbs:
            cfg = prefs.libraries_map().get(lib)
            if cfg is None:
                raise KeyError(f"unknown library: {lib}")
            try:
                _kbs[lib] = KB(storage=prefs.storage(cfg), readonly=True)
            except Exception:  # noqa: BLE001  nothing to read, and nothing gets created
                _kbs[lib] = None
        return _kbs[lib]


def reload_kb():
    global _kb
    _kb = None
    with _lock:
        _kbs.clear()


# ---------------------------------------------------------------- helpers
def _about(k: "KB") -> dict:
    """Facts about this installation, for the footer: nothing hard-coded, nothing guessed."""
    import platform

    st = prefs.storage()
    db = Path(st["sqlite_path"])
    try:
        db_bytes = db.stat().st_size if db.exists() else 0
    except OSError:
        db_bytes = 0
    try:
        pages = k.conn.execute("SELECT COALESCE(SUM(npages), 0) p FROM documents").fetchone()["p"]
    except Exception:                                     # noqa: BLE001
        pages = 0
    return {
        "version": __version__,
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.release()}",
        "started": int(_STARTED),
        "uptime_s": int(time.time() - _STARTED),
        "pages": int(pages),
        "libraries": len(prefs.libraries_list()),
        "db_file": db.name,
        "db_bytes": int(db_bytes),
        "backends": len(vector_backends()),
        "connectors": len(sources.connectors()),
        "extractors": len(EXTRACTORS),
    }


def _stats() -> dict:
    k = kb()
    s = k.stats()
    st = prefs.storage()
    return {"provider": s["provider"], "documents": s["documents"], "chunks": s["chunks"],
            "vectors": s["vectors"], "vector_backend": s["vector_backend"],
            "vector_store": s["vector_store"], "library": prefs.active_library(),
            "vector_collection": st["vector_collection"],
            "collection_stamp": s.get("collection_stamp"),
            "collection_match": s.get("collection_match"),
            "extractor": prefs.load().get("extractor") or "pymupdf",
            "vector_error": s.get("vector_error"), "sqlite": s["sqlite"], "data_dir": s["data_dir"],
            "about": _about(k)}


def _stored_dim():
    return kb()._stored_dim()


def _kb_label(lib: str) -> dict:
    """Which knowledge base a library actually lives in.

    A library is only a name; the knowledge base is the store behind it. Two entries
    can point at the same one, so every hit names it outright — the folder + database
    file, and the vector store holding the embeddings. Folder/file only: the full
    server path never leaves the machine.
    """
    try:
        cfg = prefs.libraries_map().get(lib)
        st = prefs.storage(cfg) if cfg else prefs.storage()
    except Exception:  # noqa: BLE001
        return {"file": None, "store": None}
    d, db = Path(st["data_dir"]), Path(st["sqlite_path"])
    return {"file": f"{d.name}/{db.name}", "folder": d.name,
            "store": f"{st['vector_backend']}·{st['vector_collection']}"}


def _hit(h: dict, lib: str | None = None, kb: dict | None = None) -> dict:
    h = dict(h)
    lib = lib or h.get("library") or prefs.active_library()
    h["library"] = lib
    h["knowledge_base"] = kb if kb is not None else _kb_label(lib)
    # the URL carries the library, so a hit always opens the copy it came from
    h["url"] = f"/api/pdf/{lib}/{h['doc_id']}#page={h['page']}"
    h.pop("path", None)
    return h


def _resolve_libs(asked):
    """Keep only known libraries; fall back to the active one."""
    known = {l["name"] for l in prefs.libraries_list()}
    libs = [x for x in (asked or []) if x in known]
    return libs or [prefs.active_library()]


def _search_multi(q, mode, k, libs):
    """Search one or several libraries and merge the ranked lists."""
    labels = {lib: _kb_label(lib) for lib in libs}
    if len(libs) == 1:
        one = _kb_for(libs[0])
        return [] if one is None else [_hit(h, libs[0], labels[libs[0]])
                                       for h in one.search(q, k=k, mode=mode)]

    per = {}
    for lib in libs:
        try:
            k_ = _kb_for(lib)
            per[lib] = k_.search(q, k=max(k, 10), mode=mode) if k_ is not None else []
        except Exception:  # noqa: BLE001  (a broken library must not kill the search)
            per[lib] = []

    # scores from different stores/models are not comparable — fuse by rank
    merged = {}
    for lib, hits in per.items():
        for rank, h in enumerate(hits):
            key = (lib, h["chunk_id"])
            entry = merged.get(key)
            if entry is None:
                entry = merged[key] = {**h, "library": lib, "_rrf": 0.0}
            entry["_rrf"] += 1.0 / (60 + rank + 1)
    out = sorted(merged.values(), key=lambda x: x["_rrf"], reverse=True)[:k]
    for h in out:
        h["score"] = h.pop("_rrf", 0.0)
    return [_hit(h, h.get("library"), labels.get(h.get("library"))) for h in out]


def _save_bytes(filename, data: bytes) -> Path:
    src_dir = Path(prefs.ensure_storage_dirs()["sources_dir"])
    name = Path(filename or "upload.pdf").name
    if not name.lower().endswith(".pdf"):
        raise ValueError(f"not a PDF: {name}")
    if not data:
        raise ValueError("empty upload")
    dest = src_dir / name
    if dest.exists() and dest.read_bytes() != data:
        dest = src_dir / f"{Path(name).stem}-{hashlib.sha1(data).hexdigest()[:8]}.pdf"
    dest.write_bytes(data)
    return dest


def _pdf_row(doc_id):
    return kb().conn.execute(
        "SELECT path,filename FROM documents WHERE doc_id=?", (doc_id,)
    ).fetchone()


def _env_info() -> dict:
    st_installed = True
    try:
        import sentence_transformers  # noqa: F401
    except Exception:  # noqa: BLE001
        st_installed = False
    models, ollama_error = [], None
    try:
        models = [{"name": m.get("name"), "size": m.get("size", 0),
                   "modified": m.get("modified_at", ""),
                   "embed": any(k in (m.get("name") or "").lower()
                                for k in ("embed", "bge", "gte", "minilm", "nomic"))}
                  for m in list_ollama_models()]
    except Exception as ex:  # noqa: BLE001
        ollama_error = f"{type(ex).__name__}: {ex}"
    return {"ollama": {"reachable": ollama_error is None, "error": ollama_error,
                       "exe": shutil.which("ollama") or "",
                       "models": models},
            "sentence_transformers": {"installed": st_installed,
                                      "install_cmd": "python3 -m pip install sentence-transformers",
                                      "default_model": config.ST_MODEL},
            "providers": prefs.PROVIDERS,
            "extractors": [{**e, "available": (e["id"] != "docling")
                            or docling_available() or bool(docling_service_url())}
                           for e in EXTRACTORS],
            "docling": {"installed": docling_available(), "version": docling_version(),
                        "install_cmd": "python3 -m pip install docling",
                        "service_url": docling_service_url(),
                        "service_ready": docling_service_ready()},
            "vector_backends": vector_backends(),
            "defaults": {"ollama_model": config.OLLAMA_EMBED_MODEL},
            "job": dict(_job)}


# ---------------------------------------------------------------- handlers
async def index(request: Request):
    return HTMLResponse((WEB_DIR / "index.html").read_text(encoding="utf-8"))


async def stats(request: Request):
    return JSONResponse(await run_in_threadpool(_stats))


async def documents(request: Request):
    return JSONResponse({"documents": await run_in_threadpool(kb().documents)})


async def ingest(request: Request):
    """Extract + embed every uploaded PDF, reporting progress the whole way.

    The work is done here on the server (so it survives the user browsing to
    another page); `_ingest` is what the header polls to show it still running.
    """
    form = await request.form()
    files = [f for f in form.getlist("files") if isinstance(f, UploadFile)]
    force = str(form.get("force", "")).lower() in ("true", "1", "on", "yes")
    results = []

    def progress(done, total, msg):
        _ingest.update(detail=msg,
                       pct=int(round(100 * min(1.0, max(0.0, done / total)))) if total else 0)

    _ingest.update(running=True, ok=None, started=time.time(), done=None, pct=0, index=0,
                   total=len(files), results=[], detail="starting",
                   filename=files[0].filename if files else None)
    try:
        for i, uf in enumerate(files, 1):
            _ingest.update(index=i, filename=uf.filename, pct=0)
            try:
                data = await uf.read()
                dest = await run_in_threadpool(_save_bytes, uf.filename, data)
                r = await run_in_threadpool(kb().add_document, str(dest), force, progress)
                r["size"] = len(data)
            except Exception as ex:  # noqa: BLE001
                r = {"status": "error", "filename": getattr(uf, "filename", None),
                     "message": f"{type(ex).__name__}: {ex}"}
            results.append(r)
    finally:
        _ingest.update(running=False, done=time.time(), pct=100, results=results,
                       detail="finished",
                       ok=bool(results) and all(x.get("status") != "error" for x in results))
    return JSONResponse({"results": results, "stats": await run_in_threadpool(_stats)})


async def search(request: Request):
    q = (request.query_params.get("q") or "").strip()
    mode = request.query_params.get("mode", "hybrid")
    try:
        k = int(request.query_params.get("k", 10))
    except (TypeError, ValueError):
        k = 10
    k = max(1, min(k, 50))
    asked = [x.strip() for x in (request.query_params.get("libraries") or "").split(",") if x.strip()]
    libs = await run_in_threadpool(_resolve_libs, asked)
    if not q:
        return JSONResponse({"query": q, "mode": mode, "libraries": libs, "results": []})
    hits = await run_in_threadpool(_search_multi, q, mode, k, libs)
    return JSONResponse({"query": q, "mode": mode, "libraries": libs, "results": hits})


async def context(request: Request):
    doc_id = request.query_params.get("doc_id", "")
    lib = request.query_params.get("library", "")
    try:
        page = int(request.query_params.get("page", 1))
    except (TypeError, ValueError):
        page = 1
    try:
        k = await run_in_threadpool(_kb_for, lib)
    except Exception:  # noqa: BLE001
        return JSONResponse({"page": page, "chunks": [], "error": f"unknown library: {lib}"})
    if k is None:
        return JSONResponse({"page": page, "chunks": [],
                             "error": f"nothing readable in library: {lib}"})
    chunks = await run_in_threadpool(k.page_context, doc_id, page)
    return JSONResponse({"page": page, "library": lib or prefs.active_library(), "chunks": chunks})


async def pdf(request: Request):
    doc_id = request.path_params["doc_id"]
    row = await run_in_threadpool(_pdf_row, doc_id)
    if not row:
        return JSONResponse({"detail": "document not found"}, status_code=404)
    p = Path(row["path"])
    if not p.exists():
        return JSONResponse({"detail": "source file missing"}, status_code=404)
    return FileResponse(str(p), media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="{row["filename"]}"'})


async def pdf_in_library(request: Request):
    """Serve a source PDF out of a specific library (cross-library results)."""
    lib = request.path_params["library"]
    doc_id = request.path_params["doc_id"]
    try:
        k = await run_in_threadpool(_kb_for, lib)
    except Exception:  # noqa: BLE001
        return JSONResponse({"detail": f"unknown library: {lib}"}, status_code=404)
    if k is None:
        return JSONResponse({"detail": f"nothing readable in library: {lib}"}, status_code=404)
    row = await run_in_threadpool(
        lambda: k.conn.execute("SELECT path,filename FROM documents WHERE doc_id=?", (doc_id,)).fetchone())
    if not row:
        return JSONResponse({"detail": "document not found"}, status_code=404)
    p = Path(row["path"])
    if not p.exists():
        return JSONResponse({"detail": "source file missing"}, status_code=404)
    return FileResponse(str(p), media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="{row["filename"]}"'})


async def delete(request: Request):
    """Delete a document and everything derived from it."""
    form = await request.form()
    doc_id = str(form.get("doc_id", ""))
    row = await run_in_threadpool(_pdf_row, doc_id)
    report = await run_in_threadpool(kb().delete_document, doc_id)
    source_removed = False
    if row:
        p = Path(row["path"])
        try:
            # resolve() both sides: /var and /private/var differ on macOS
            if p.parent.resolve() == Path(prefs.storage()["sources_dir"]).resolve() and p.exists():
                p.unlink()
                source_removed = True
        except OSError:
            pass
    report["source_removed"] = source_removed
    return JSONResponse({"ok": True, "removed": report,
                         "stats": await run_in_threadpool(_stats)})


async def reset(request: Request):
    form = await request.form()
    # destructive: require an explicit token so a stray request can't wipe the KB
    if str(form.get("confirm", "")) != "RESET":
        return JSONResponse({"ok": False, "error": "missing confirm=RESET"}, status_code=400)
    backup = await run_in_threadpool(prefs.backup_kb)
    await run_in_threadpool(kb().reset)

    def _wipe_sources():
        for f in Path(prefs.storage()["sources_dir"]).glob("*"):
            try:
                f.unlink()
            except OSError:
                pass

    await run_in_threadpool(_wipe_sources)
    return JSONResponse({"ok": True, "backup": backup,
                         "stats": await run_in_threadpool(_stats)})


async def export(request: Request):
    form = await request.form()
    path = await run_in_threadpool(kb().export_text, str(form.get("doc_id", "")))
    return JSONResponse({"path": path})


# -------------------------------------------------------------- settings
async def settings_get(request: Request):
    live, live_err = None, None
    try:
        live = await run_in_threadpool(lambda: kb().provider_info())
    except Exception as ex:  # noqa: BLE001
        live_err = f"{type(ex).__name__}: {ex}"
    return JSONResponse({
        "settings": await run_in_threadpool(prefs.load),
        "live_provider": live, "live_error": live_err,
        "stored_dim": await run_in_threadpool(_stored_dim),
        "storage": await run_in_threadpool(prefs.storage_status),
        "libraries": await run_in_threadpool(prefs.libraries_list),
        "active_library": prefs.active_library(),
        "env": await run_in_threadpool(_env_info),
    })


async def settings_save(request: Request):
    form = await request.form()
    # only touch the keys actually sent, so saving one group can't clobber another
    data = {k: str(form.get(k, "")) for k in (prefs.EMBED_KEYS + prefs.EXTRACT_KEYS) if k in form}
    prev = await run_in_threadpool(_stored_dim)
    await run_in_threadpool(prefs.save, data)
    reload_kb()
    out = {"ok": True, "settings": await run_in_threadpool(prefs.load)}
    if set(data) & set(prefs.EMBED_KEYS):
        try:
            pr = await run_in_threadpool(probe_embedder, data.get("provider", "auto"),
                                        data.get("model", ""), data.get("ollama_url", ""))
            out["probe"] = pr
            if prev and pr.get("dim") and prev != pr["dim"]:
                out["warning"] = (f"Embedding dimension changed {prev} → {pr['dim']}. Existing vectors "
                                  f"were built with the previous model — reset the knowledge base and "
                                  f"re-ingest so semantic search stays consistent.")
        except Exception as ex:  # noqa: BLE001
            out["ok"] = False
            out["error"] = f"{type(ex).__name__}: {ex}"
    return JSONResponse(out)


async def settings_test(request: Request):
    form = await request.form()
    try:
        pr = await run_in_threadpool(probe_embedder, str(form.get("provider", "auto")),
                                     str(form.get("model", "")), str(form.get("ollama_url", "")))
        return JSONResponse({"ok": True, **pr})
    except Exception as ex:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": f"{type(ex).__name__}: {ex}"})


async def settings_storage(request: Request):
    """Re-point the knowledge base, vector store and export/source folders."""
    form = await request.form()
    data = {k: str(form.get(k, "")) for k in prefs.STORAGE_KEYS}
    # keep a copy of the current knowledge base before changing where it lives
    backup = await run_in_threadpool(prefs.backup_kb)
    await run_in_threadpool(prefs.save, data)
    reload_kb()
    out = {"ok": True, "settings": await run_in_threadpool(prefs.load),
           "storage": await run_in_threadpool(prefs.storage_status),
           "backup": backup}
    try:
        out["stats"] = await run_in_threadpool(lambda: kb().stats())
    except Exception as ex:  # noqa: BLE001
        out["ok"] = False
        out["error"] = f"{type(ex).__name__}: {ex} — check the paths are writable"
    return JSONResponse(out)


# ---------------------------------------------------------------- install
def _run_job(kind: str, target: str, argv):
    with _lock:
        _job.update(kind=kind, target=target, lines=[], ok=None, running=True)
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1)
        for line in proc.stdout:  # type: ignore[union-attr]
            line = line.rstrip()
            if line:
                with _lock:
                    _job["lines"].append(line)
                    del _job["lines"][:-150]
        with _lock:
            _job["ok"] = (proc.wait() == 0)
    except Exception as ex:  # noqa: BLE001
        with _lock:
            _job["lines"].append(f"error: {type(ex).__name__}: {ex}")
            _job["ok"] = False
    finally:
        with _lock:
            _job["running"] = False


def _start_job(kind, target, argv):
    if _job["running"]:
        return False, f"already running: {_job['kind']} {_job['target']}"
    threading.Thread(target=_run_job, args=(kind, target, argv), daemon=True).start()
    return True, target


async def settings_pull(request: Request):
    """Install an Ollama model (ollama pull)."""
    form = await request.form()
    model = str(form.get("model", "")).strip()
    if not model:
        return JSONResponse({"ok": False, "error": "no model given"}, status_code=400)
    ok, msg = _start_job("ollama pull", model, [shutil.which("ollama") or "ollama", "pull", model])
    return JSONResponse({"ok": ok, "target": msg} if ok else {"ok": False, "error": msg})


async def settings_install(request: Request):
    """Install an optional extra (currently docling) with pip, streaming output."""
    form = await request.form()
    extra = str(form.get("extra", "docling")).strip() or "docling"
    if not extra.replace("-", "").replace("_", "").replace(".", "").isalnum():
        return JSONResponse({"ok": False, "error": "bad package name"}, status_code=400)
    ok, msg = _start_job("pip install", extra,
                         [sys.executable, "-m", "pip", "install", "--upgrade", extra])
    return JSONResponse({"ok": ok, "target": msg} if ok else {"ok": False, "error": msg})


async def settings_job_status(request: Request):
    """The install/pull job, plus whatever ingestion is doing right now."""
    return JSONResponse({**dict(_job), "ingest": dict(_ingest)})


async def settings_vector_test(request: Request):
    """Connect to the vector store described by the form and report what it holds."""
    form = await request.form()
    try:
        st = await run_in_threadpool(_merged_storage, dict(form))
        out = await run_in_threadpool(_vector_probe, st)
        return JSONResponse({"ok": True, **out})
    except Exception as ex:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": f"{type(ex).__name__}: {ex}"})


def _merged_storage(form: dict) -> dict:
    st = dict(prefs.storage())
    for k in prefs.STORAGE_KEYS:
        v = str(form.get(k, "") or "").strip()
        if v:
            st[k] = v
    return st


def _vector_probe(st: dict) -> dict:
    vs, fallback = make_vector_store(st, kb().conn)
    return {"backend": vs.name, "info": vs.info(), "count": vs.count(), "fallback": fallback,
            "url": st.get("vector_url") or "", "index": st.get("vector_index") or "",
            "api_key": "set" if st.get("vector_api_key") else ""}


async def settings_models(request: Request):
    return JSONResponse(await run_in_threadpool(_env_info))


# -------------------------------------------------------------- libraries
async def libraries_list(request: Request):
    return JSONResponse({"libraries": await run_in_threadpool(prefs.libraries_list),
                         "active": prefs.active_library()})


async def libraries_save(request: Request):
    """Create a knowledge base, or update one, from the submitted storage settings.

    Creation ignores the submitted paths on purpose: a new knowledge base must not
    inherit the container of the one it is created from (that is how two of them end
    up sharing a database and a vector collection). It gets its own.
    """
    form = await request.form()
    try:
        name = str(form.get("name", "")).strip()
        cfg = {k: str(form.get(k, "")) for k in prefs.STORAGE_KEYS if k in form}
        await run_in_threadpool(prefs.save_library, name, cfg or None)
        reload_kb()
        mine = next((l for l in await run_in_threadpool(prefs.libraries_list)
                     if l["name"] == name), {})
        return JSONResponse({"ok": True, "name": name, "active": prefs.active_library(),
                             "container": mine.get("container"),
                             "collection": mine.get("collection"),
                             "libraries": await run_in_threadpool(prefs.libraries_list)})
    except Exception as ex:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": f"{type(ex).__name__}: {ex}"})


async def libraries_use(request: Request):
    """Switch the active library (backs the current knowledge base up first)."""
    form = await request.form()
    name = str(form.get("name", "")).strip()
    try:
        backup = await run_in_threadpool(prefs.backup_kb)
        await run_in_threadpool(prefs.use_library, name)
        reload_kb()
        return JSONResponse({"ok": True, "backup": backup, "active": prefs.active_library(),
                             "storage": await run_in_threadpool(prefs.storage_status),
                             "stats": await run_in_threadpool(_stats)})
    except Exception as ex:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": f"{type(ex).__name__}: {ex}"})


async def libraries_delete(request: Request):
    """Delete a knowledge base and, by default, every file it holds on disk."""
    form = await request.form()
    name = str(form.get("name", "")).strip()
    purge = str(form.get("purge", "1")).lower() not in ("0", "false", "no")
    try:
        report = await run_in_threadpool(prefs.delete_library, name, purge)
    except Exception as ex:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": f"{type(ex).__name__}: {ex}"})
    # drop the cached handle before its files disappear, so nothing keeps them alive
    cached = _kbs.pop(name, None)
    conn = getattr(cached, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    return JSONResponse({"ok": True, "purge": report,
                         "libraries": await run_in_threadpool(prefs.libraries_list)})


# ---------------------------------------------------------------- sources
async def sources_list(request: Request):
    return JSONResponse({
        "sources": [{**sources.public(s), "writable": sources.can_put(s)}
                    for s in await run_in_threadpool(sources.load_sources)],
        "connectors": sources.connectors(),
        "providers": sources.providers(),
        "local_presets": await run_in_threadpool(sources.local_presets),
        "browse": await run_in_threadpool(sources.browse_info),
        "file": str(sources.INTEGRATIONS),
    })


async def sources_browse(request: Request):
    """Open the OS folder picker on the machine that runs the server."""
    form = await request.form()
    kind = str(form.get("kind", "dir"))
    start = str(form.get("start", "")).strip() or None
    dry = str(form.get("dry", "")).lower() in ("1", "true", "yes")
    try:
        return JSONResponse(await run_in_threadpool(sources.browse_path, kind, start, dry))
    except Exception as ex:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": f"{type(ex).__name__}: {ex}"})


async def sources_put(request: Request):
    """Save an indexed document back to a source (its PDF or its readable text)."""
    form = await request.form()
    try:
        cfg = _cfg_for(str(form.get("name", "")))
        if not sources.can_put(cfg):
            return JSONResponse({"ok": False, "error": f"'{cfg.get('type')}' cannot be written to"})
        doc_id = str(form.get("doc_id", ""))
        what = str(form.get("what", "pdf"))
        row = await run_in_threadpool(_pdf_row, doc_id)
        if not row:
            raise ValueError(f"unknown document: {doc_id}")
        if what == "text":
            local = await run_in_threadpool(kb().export_text, doc_id)
            if not local:
                raise ValueError("no text export could be written")
        else:
            local = row["path"]
        saved = await run_in_threadpool(sources.put, cfg, str(local), None)
        return JSONResponse({"ok": True, "saved": saved, "what": what,
                             "filename": Path(str(local)).name})
    except Exception as ex:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": f"{type(ex).__name__}: {ex}"})


async def sources_save(request: Request):
    form = await request.form()
    cfg = {k: v for k, v in form.items()}
    if cfg.get("password") == MASK:                     # keep the stored secret
        old = await run_in_threadpool(sources.get_source, cfg.get("name", ""))
        if old and old.get("password"):
            cfg["password"] = old["password"]
    try:
        saved = await run_in_threadpool(sources.save_source, cfg)
        return JSONResponse({"ok": True, "source": sources.public(saved)})
    except Exception as ex:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": f"{type(ex).__name__}: {ex}"})


async def sources_delete(request: Request):
    form = await request.form()
    await run_in_threadpool(sources.delete_source, str(form.get("name", "")))
    return JSONResponse({"ok": True})


def _cfg_for(name: str, inline: dict | None = None):
    if name:
        s = sources.get_source(name)
        if not s:
            raise ValueError(f"unknown source: {name}")
        return s
    if inline and inline.get("type"):
        return inline
    raise ValueError("no source given")


async def sources_test(request: Request):
    form = await request.form()
    try:
        cfg = _cfg_for(str(form.get("name", "")),
                       {k: v for k, v in form.items() if k != "name"})
        msg = await run_in_threadpool(sources.test, cfg)
        return JSONResponse({"ok": True, "message": msg})
    except Exception as ex:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": f"{type(ex).__name__}: {ex}"})


async def sources_scan(request: Request):
    form = await request.form()
    try:
        cfg = _cfg_for(str(form.get("name", "")))
        files = await run_in_threadpool(sources.list_pdfs, cfg)
        return JSONResponse({"ok": True, "files": files, "count": len(files)})
    except Exception as ex:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": f"{type(ex).__name__}: {ex}"})


async def sources_ingest(request: Request):
    form = await request.form()
    try:
        cfg = _cfg_for(str(form.get("name", "")))
        wanted = json.loads(str(form.get("files", "[]")))
        force = str(form.get("force", "")).lower() in ("true", "1", "on", "yes")
        src_dir = Path(prefs.ensure_storage_dirs()["sources_dir"])
        results = []
        for remote in wanted:
            try:
                dest = await run_in_threadpool(sources.fetch, cfg, remote, src_dir)
                r = await run_in_threadpool(kb().add_document, str(dest), force)
                r["remote"] = remote
            except Exception as ex:  # noqa: BLE001
                r = {"status": "error", "remote": remote, "filename": Path(remote).name,
                     "message": f"{type(ex).__name__}: {ex}"}
            results.append(r)
        return JSONResponse({"ok": True, "results": results,
                             "stats": await run_in_threadpool(_stats)})
    except Exception as ex:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": f"{type(ex).__name__}: {ex}"})


routes = [
    Route("/", index),
    Route("/api/stats", stats),
    Route("/api/documents", documents),
    Route("/api/ingest", ingest, methods=["POST"]),
    Route("/api/search", search),
    Route("/api/context", context),
    Route("/api/pdf/{library}/{doc_id}", pdf_in_library),
    Route("/api/pdf/{doc_id}", pdf),
    Route("/api/delete", delete, methods=["POST"]),
    Route("/api/reset", reset, methods=["POST"]),
    Route("/api/export", export, methods=["POST"]),
    Route("/api/settings", settings_get),
    Route("/api/settings/save", settings_save, methods=["POST"]),
    Route("/api/settings/test", settings_test, methods=["POST"]),
    Route("/api/settings/storage", settings_storage, methods=["POST"]),
    Route("/api/settings/vector", settings_vector_test, methods=["POST"]),
    Route("/api/settings/models", settings_models),
    Route("/api/libraries", libraries_list),
    Route("/api/libraries/save", libraries_save, methods=["POST"]),
    Route("/api/libraries/use", libraries_use, methods=["POST"]),
    Route("/api/libraries/delete", libraries_delete, methods=["POST"]),
    Route("/api/settings/pull", settings_pull, methods=["POST"]),
    Route("/api/settings/install", settings_install, methods=["POST"]),
    Route("/api/settings/job", settings_job_status),
    Route("/api/sources", sources_list),
    Route("/api/sources/save", sources_save, methods=["POST"]),
    Route("/api/sources/delete", sources_delete, methods=["POST"]),
    Route("/api/sources/test", sources_test, methods=["POST"]),
    Route("/api/sources/browse", sources_browse, methods=["POST"]),
    Route("/api/sources/scan", sources_scan, methods=["POST"]),
    Route("/api/sources/ingest", sources_ingest, methods=["POST"]),
    Route("/api/sources/put", sources_put, methods=["POST"]),
    Mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static"),
]

app = Starlette(routes=routes)
