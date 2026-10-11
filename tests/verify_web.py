#!/usr/bin/env python3
"""Verification of the Knowledgebases web app: HTTP API + the rendered page.

Run against an ISOLATED instance (see tests/run_verification.sh) — this probe deletes
knowledges bases and resets them on purpose, so it must never point at the real one.

  A. core API      stats -> multipart ingest -> semantic/keyword/hybrid search ->
                   source references (filename + page + #page url) -> source PDF
                   served inline -> page context -> static assets -> delete cleanup
  B. settings      provider/model endpoints, connection test, ollama model list
  C. storage       REVERSIBLE: point storage at a temp dir with the sqlite vector
                   backend, ingest + search there, then restore the original
                   settings and confirm the real knowledge base is intact
  D. sources       connector registry, save a local source, test, scan, ingest from it
  E. rendered DOM  headless Chrome: 5 tabs, kmail.at brand marks, design tokens,
                   search driven through the widgets, light + dark screenshots

Run: PDFKB_BASE=http://127.0.0.1:8799 python3 tests/verify_web.py
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

import requests

BASE = os.environ.get("PDFKB_BASE", "http://127.0.0.1:8765")
TMP = tempfile.mkdtemp(prefix="hermes-verify-pdfkb-")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 9419
STORAGE_KEYS = ("data_dir", "sqlite_path", "vector_backend", "vector_path",
                "vector_collection", "text_dir", "sources_dir")
EMBED_KEYS = ("provider", "model", "ollama_url")

FAILS = []


def check(name, ok, extra=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"   [{extra}]" if extra else ""))
    if not ok:
        FAILS.append(name)


def fixture(path, lines):
    import pymupdf
    d = pymupdf.open()
    p = d.new_page()
    for i, line in enumerate(lines):
        p.insert_text((72, 72 + i * 20), line, fontsize=12)
    d.save(path)
    d.close()
    return path


# ============================================================== A. core API
def api_checks():
    print("\n--- A. core API ---")
    s = requests.get(f"{BASE}/api/stats", timeout=30).json()
    check("stats: provider + counts + vector store",
          all(k in s for k in ("provider", "documents", "chunks", "vectors", "vector_store")), s["vector_store"])

    # self-contained baseline (this probe runs against an ISOLATED knowledge base)
    for i, lines in enumerate((["Baseline One", "The harbour crane was overhauled in spring."],
                               ["Baseline Two", "Sediment cores were sampled along the estuary."]), 1):
        b = fixture(os.path.join(TMP, f"hermes-verify-base{i}.pdf"), lines)
        with open(b, "rb") as fh:
            requests.post(f"{BASE}/api/ingest",
                          files={"files": (os.path.basename(b), fh, "application/pdf")},
                          data={"force": "true"}, timeout=240)
    docs = requests.get(f"{BASE}/api/documents", timeout=30).json()["documents"]
    check("baseline documents ingested", len(docs) >= 2, f"{len(docs)} docs")

    fx = fixture(os.path.join(TMP, "hermes-verify-sample.pdf"),
                 ["Verification Fixture", "The zebra crossing protocol requires reflective paint.",
                  "Ultraviolet curing lamps emit at 395 nanometres."])
    with open(fx, "rb") as fh:
        up = requests.post(f"{BASE}/api/ingest",
                           files={"files": ("hermes-verify-sample.pdf", fh, "application/pdf")},
                           data={"force": "true"}, timeout=240).json()
    r0 = up["results"][0]
    check("ingest via multipart upload", r0.get("status") == "added", r0.get("status"))
    fixture_doc = r0.get("doc_id")
    check("extraction: ingest reports the extractor it used",
          r0.get("extractor") in ("docling", "pymupdf"), r0.get("extractor"))
    check("extraction: a missing docling falls back to PyMuPDF with a note",
          r0.get("extractor") == "docling" or "docling" in (r0.get("extractor_note") or ""),
          f"extractor={r0.get('extractor')} note={r0.get('extractor_note')}")

    sem = requests.get(f"{BASE}/api/search", params={"q": "ultraviolet curing lamps nanometres", "mode": "semantic", "k": 5}, timeout=90).json()["results"]
    kw = requests.get(f"{BASE}/api/search", params={"q": "zebra crossing reflective paint", "mode": "keyword", "k": 5}, timeout=90).json()["results"]
    hyb = requests.get(f"{BASE}/api/search", params={"q": "zebra crossing reflective paint", "mode": "hybrid", "k": 5}, timeout=90).json()["results"]
    check("semantic search finds fixture", any("hermes-verify" in h["filename"] for h in sem), [h["filename"] for h in sem][:2])
    check("keyword search finds fixture", any("hermes-verify" in h["filename"] for h in kw), [h["filename"] for h in kw][:2])
    check("hybrid search returns ranked hits", len(hyb) > 0, f"{len(hyb)} hits")

    top = hyb[0] if hyb else {}
    check("hit carries SOURCE reference (filename + page)",
          bool(top.get("filename")) and isinstance(top.get("page"), int), f"{top.get('filename')} p.{top.get('page')}")
    check("hit URL targets the source page",
          top.get("url", "").startswith("/api/pdf/") and "#page=" in top.get("url", ""), top.get("url"))
    check("hit carries chunk_id + doc_id", bool(top.get("chunk_id")) and bool(top.get("doc_id")))
    check("hit does not leak the server path", "path" not in top)

    pr = requests.get(f"{BASE}/api/pdf/{top.get('doc_id')}", timeout=60)
    check("source PDF served inline",
          pr.status_code == 200 and pr.headers.get("content-type") == "application/pdf"
          and "inline" in pr.headers.get("content-disposition", ""), f"{pr.status_code} {pr.headers.get('content-type')}")
    check("served bytes are a real PDF", pr.content[:5] == b"%PDF-", pr.content[:5])

    ctx = requests.get(f"{BASE}/api/context", params={"doc_id": top.get("doc_id"), "page": top.get("page", 1)}, timeout=60).json()
    check("page context returns chunks", len(ctx["chunks"]) >= 1, f"{len(ctx['chunks'])} chunks")
    check("/static/style.css served", requests.get(f"{BASE}/static/style.css", timeout=30).status_code == 200)
    check("/static/app.js served", requests.get(f"{BASE}/static/app.js", timeout=30).status_code == 200)
    check("brand logo mark served", requests.get(f"{BASE}/static/logo-mark.png", timeout=30).status_code == 200)
    htm = requests.get(f"{BASE}/", timeout=30).text
    check("index links both static assets", "/static/style.css" in htm and "/static/app.js" in htm)
    check("index carries the kmail.at brand",
          'class="brand-name">kmail.at' in htm and "tools" in htm, "wordmark + crumb")

    # sources dir comes from the server's own resolved storage
    src_dir = requests.get(f"{BASE}/api/settings", timeout=30).json()["storage"]["paths"]["sources_dir"]
    st_before = requests.get(f"{BASE}/api/stats", timeout=30).json()
    exp = requests.post(f"{BASE}/api/export", data={"doc_id": fixture_doc}, timeout=60).json().get("path")
    kw_before = requests.get(f"{BASE}/api/search", params={"q": "ultraviolet curing lamps", "mode": "keyword", "k": 5}, timeout=60).json()["results"]
    check("delete: the fixture is findable and exported before deletion",
          any(x["doc_id"] == fixture_doc for x in kw_before) and bool(exp) and os.path.exists(exp),
          f"{len(kw_before)} hits · {exp}")

    rm = requests.post(f"{BASE}/api/delete", data={"doc_id": fixture_doc}, timeout=60).json()
    rep = rm.get("removed", {})
    check("delete: the response reports what was purged",
          rep.get("chunks_removed", 0) > 0 and rep.get("chunks_left") == 0,
          f"chunks removed={rep.get('chunks_removed')} left={rep.get('chunks_left')}")
    check("delete: the embeddings went with it",
          rep.get("vector_error") is None
          and rep.get("vectors_removed") == rep.get("chunks_removed")
          and rep.get("vectors_left") == st_before["vectors"] - rep.get("vectors_removed", 0),
          f"removed={rep.get('vectors_removed')} left={rep.get('vectors_left')} err={rep.get('vector_error')}")
    st_after = requests.get(f"{BASE}/api/stats", timeout=30).json()
    check("delete: documents, chunks and vectors all drop in the stats",
          st_after["documents"] == st_before["documents"] - 1
          and st_after["chunks"] == st_before["chunks"] - rep.get("chunks_removed", 0)
          and st_after["vectors"] == st_before["vectors"] - rep.get("vectors_removed", 0),
          f"docs {st_before['documents']}->{st_after['documents']} · "
          f"chunks {st_before['chunks']}->{st_after['chunks']} · "
          f"vectors {st_before['vectors']}->{st_after['vectors']}")
    left = requests.get(f"{BASE}/api/documents", timeout=30).json()["documents"]
    check("delete removed the fixture", all(x["doc_id"] != fixture_doc for x in left), f"{len(left)} docs remain")
    kw_after = requests.get(f"{BASE}/api/search", params={"q": "ultraviolet curing lamps", "mode": "keyword", "k": 5}, timeout=60).json()["results"]
    check("delete: search can no longer reach it",
          all(x["doc_id"] != fixture_doc for x in kw_after), f"{len(kw_after)} hits")
    check("delete: the exported text file is gone", not exp or not os.path.exists(exp), exp)
    check("delete also removed the stored source copy",
          not os.path.exists(os.path.join(src_dir, r0.get("filename") or "")),
          os.path.join(src_dir, r0.get("filename") or ""))


# ============================================================= B. settings
def settings_checks():
    print("\n--- B. settings (provider) ---")
    r = requests.get(f"{BASE}/api/settings", timeout=30).json()
    check("settings: provider + model + url present",
          all(k in r["settings"] for k in EMBED_KEYS), r["settings"]["provider"])
    check("settings: Docling is the default PDF text extractor",
          r["settings"].get("extractor") == "docling", r["settings"].get("extractor"))
    check("settings: env lists providers", len(r["env"].get("providers", [])) >= 4,
          [p["id"] for p in r["env"].get("providers", [])])
    check("settings: ollama reachable + models listed",
          r["env"]["ollama"]["reachable"] and len(r["env"]["ollama"]["models"]) > 0,
          f"{len(r['env']['ollama']['models'])} models")
    check("settings: storage + vector backends reported",
          "storage" in r and len(r["env"].get("vector_backends", [])) >= 2,
          [b["id"] for b in r["env"].get("vector_backends", [])])

    t = requests.post(f"{BASE}/api/settings/test",
                      data={"provider": "ollama", "model": "", "ollama_url": r["settings"]["ollama_url"]},
                      timeout=120).json()
    check("settings: test connection succeeds", t.get("ok") is True and t.get("dim"), t)

    bad = requests.post(f"{BASE}/api/settings/test",
                        data={"provider": "ollama", "model": "definitely-not-a-model", "ollama_url": r["settings"]["ollama_url"]},
                        timeout=120).json()
    check("settings: test fails loudly on a bad model", bad.get("ok") is False and bad.get("error"), bad.get("error", "")[:80])

    same = requests.post(f"{BASE}/api/settings/save",
                         data={k: r["settings"].get(k, "") for k in EMBED_KEYS}, timeout=180).json()
    check("settings: saving the current provider is accepted", same.get("ok") is True, same.get("probe"))

    # --- extraction / optional extras (docling) ---
    exs = r["env"].get("extractors", [])
    check("settings: extractors listed with availability",
          len(exs) >= 2 and any(e["id"] == "docling" for e in exs),
          [(e["id"], "on" if e["available"] else "off") for e in exs])
    dl = r["env"].get("docling", {})
    check("settings: docling reported with an install command",
          bool(dl.get("install_cmd")) and "installed" in dl,
          f"installed={dl.get('installed')} cmd={dl.get('install_cmd')}")
    # docling is the default: prove it really extracts, or say it is not installed
    if r["env"].get("docling", {}).get("installed"):
        fx = fixture(os.path.join(TMP, "hermes-verify-docling.pdf"),
                     ["Docling Fixture", "The turbine hall was recommissioned in autumn."])
        requests.post(f"{BASE}/api/settings/save", data={"extractor": "docling"}, timeout=60)
        with open(fx, "rb") as fh:
            up = requests.post(f"{BASE}/api/ingest",
                               files={"files": ("hermes-verify-docling.pdf", fh, "application/pdf")},
                               data={"force": "true"}, timeout=900).json()["results"][0]
        check("extraction: docling really extracts a PDF end to end",
              up.get("status") == "added" and up.get("extractor") == "docling"
              and (up.get("nchunks") or 0) > 0,
              f"status={up.get('status')} extractor={up.get('extractor')} "
              f"chunks={up.get('nchunks')} note={up.get('extractor_note')}")
        requests.post(f"{BASE}/api/delete", data={"doc_id": up.get("doc_id", "")}, timeout=60)
    else:
        print("SKIP  docling is not installed on this machine — the PyMuPDF fallback covers it")

    ex = requests.post(f"{BASE}/api/settings/save",
                       data={"extractor": "pymupdf", "docling_ocr": "false"}, timeout=60).json()
    check("settings: extraction saved without clobbering the embedding choice",
          ex.get("ok") is True and ex["settings"]["extractor"] == "pymupdf"
          and ex["settings"]["provider"] == r["settings"]["provider"], ex["settings"]["extractor"])
    bad = requests.post(f"{BASE}/api/settings/install", data={"extra": "not a package!"}, timeout=30)
    check("install endpoint rejects a bad package name", bad.status_code == 400, bad.status_code)
    np = requests.post(f"{BASE}/api/settings/pull", data={"model": ""}, timeout=30)
    check("model-install endpoint requires a model", np.status_code == 400, np.status_code)
    job = requests.get(f"{BASE}/api/settings/job", timeout=30).json()
    check("job status endpoint responds", "running" in job and "lines" in job, job.get("kind"))

    # --- vector-store registry ---
    bk = r["env"].get("vector_backends", [])
    check("settings: vector-backend registry is broad", len(bk) >= 15, f"{len(bk)} backends")
    ids = [b["id"] for b in bk]
    check("settings: registry covers the named targets + the field",
          {"chroma", "sqlite", "qdrant", "opensearch", "elasticsearch", "weaviate",
           "milvus", "pgvector", "pinecone"} <= set(ids), ids[:10])
    check("settings: backends carry status + availability + category + install hints",
          all({"status", "available", "category", "install", "note"} <= set(b) for b in bk)
          and any(b.get("install") for b in bk),
          [(b["id"], b["status"], "on" if b["available"] else "off") for b in bk[:6]])
    check("settings: every 'verified' backend is available here",
          all(b["available"] for b in bk if b["status"] == "verified"),
          [b["id"] for b in bk if b["status"] == "verified"])


# ============================================================== C. storage
def storage_checks():
    print("\n--- C. storage + vector backends (reversible) ---")
    before = requests.get(f"{BASE}/api/documents", timeout=30).json()["documents"]
    orig = requests.get(f"{BASE}/api/settings", timeout=30).json()["settings"]

    # exercise every backend that can run in-process here
    targets = [("sqlite", {}), ("hnswlib", {}), ("chroma", {}),
               ("qdrant", {"vector_url": ":memory:"})]
    for backend, extra in targets:
        alt = os.path.join(TMP, f"alt-{backend}")
        data = {"data_dir": alt, "sqlite_path": "", "vector_backend": backend,
                "vector_path": "", "vector_collection": "pdf_chunks",
                "vector_url": "", "vector_api_key": "", "vector_index": "",
                "text_dir": "", "sources_dir": ""}
        data.update(extra)
        ap = requests.post(f"{BASE}/api/settings/storage", data=data, timeout=120).json()
        fallback = (ap.get("stats") or {}).get("vector_error")
        ok = ap.get("ok") and ap["storage"]["vector_backend"] == backend and not fallback
        check(f"storage: switch to the {backend} vector backend", ok,
              fallback or ap["storage"]["paths"]["sqlite_path"])
        if not ok:
            continue

        fx = fixture(os.path.join(TMP, f"hermes-verify-{backend}.pdf"),
                     ["Backend Fixture",
                      f"Walrus haul-out counts recorded for the {backend} backend."])
        with open(fx, "rb") as fh:
            up = requests.post(f"{BASE}/api/ingest",
                               files={"files": (os.path.basename(fx), fh, "application/pdf")},
                               data={"force": "true"}, timeout=240).json()["results"][0]
        check(f"storage: ingest works on {backend}", up.get("status") == "added", up.get("status"))

        hit = requests.get(f"{BASE}/api/search",
                           params={"q": "walrus haul-out counts", "mode": "semantic", "k": 3},
                           timeout=90).json()["results"]
        check(f"storage: semantic search works on {backend}",
              bool(hit) and f"hermes-verify-{backend}" in hit[0]["filename"],
              hit[0]["filename"] if hit else "no hits")

        vt = requests.post(f"{BASE}/api/settings/vector", data=data, timeout=120).json()
        check(f"storage: vector-store test reports {backend}",
              vt.get("ok") and vt.get("backend") == backend, vt.get("info") or vt.get("error"))

    # ---- restore the original layout ----
    requests.post(f"{BASE}/api/settings/storage",
                  data={k: orig.get(k, "") for k in STORAGE_KEYS}, timeout=120)
    requests.post(f"{BASE}/api/settings/save",
                  data={k: orig.get(k, "") for k in EMBED_KEYS}, timeout=180)
    after = requests.get(f"{BASE}/api/documents", timeout=30).json()["documents"]
    now = requests.get(f"{BASE}/api/settings", timeout=30).json()["storage"]
    check("storage: original settings restored",
          now["vector_backend"] == (orig.get("vector_backend") or "chroma"),
          f"{now['vector_backend']} @ {now['paths']['data_dir']}")
    check("storage: original knowledge base intact after restore",
          len(after) == len(before), f"{len(before)} -> {len(after)} docs")
    back = requests.get(f"{BASE}/api/search",
                        params={"q": "harbour crane estuary", "mode": "semantic", "k": 3},
                        timeout=90).json()["results"]
    check("storage: semantic search works again on the restored backend", len(back) > 0, f"{len(back)} hits")


# ============================================================== D. sources
def source_checks():
    print("\n--- D. sources (file server / other) ---")
    r = requests.get(f"{BASE}/api/sources", timeout=30).json()
    ids = [c["id"] for c in r["connectors"]]
    check("sources: connector registry exposed", {"local", "http", "webdav", "ssh"} <= set(ids), ids)
    check("sources: availability + install hints reported",
          all("available" in c for c in r["connectors"])
          and any(c.get("install") for c in r["connectors"]),
          [f"{c['id']}={'on' if c['available'] else 'off'}" for c in r["connectors"]])
    bi = r.get("browse") or {}
    check("sources: a native folder picker is available on this machine",
          bi.get("available") is True and bool(bi.get("backend")), bi)
    dry = requests.post(f"{BASE}/api/sources/browse",
                        data={"kind": "dir", "dry": "1"}, timeout=30).json()
    check("sources: Browse is wired to a real folder chooser",
          dry.get("ok") is True
          and any(("choose folder" in str(x)) or ("--directory" in str(x))
                  for x in dry.get("command", [])),
          dry.get("command"))
    dry2 = requests.post(f"{BASE}/api/sources/browse",
                         data={"kind": "dir", "start": "/tmp", "dry": "1"}, timeout=30).json()
    check("sources: the picker opens at the path already typed",
          "/tmp" in " ".join(str(x) for x in dry2.get("command", [])), dry2.get("command"))
    local = next((c for c in r["connectors"] if c["id"] == "local"), {})
    check("sources: the local connection marks its path field as browsable",
          any(f.get("browse") == "dir" for f in local.get("fields", [])),
          str([f.get("browse") for f in local.get("fields", [])]))
    check("sources: connections file is in the KB dir", "integrations.json" in r["file"], r["file"])

    share = os.path.join(TMP, "share")
    os.makedirs(share, exist_ok=True)
    fixture(os.path.join(share, "hermes-verify-share.pdf"),
            ["Shared Source", "Peregrine falcon nesting boxes on the cathedral tower."])

    name = "hermes-verify-local"
    sv = requests.post(f"{BASE}/api/sources/save",
                       data={"name": name, "type": "local", "path": share}, timeout=60).json()
    check("sources: save a local connection", sv.get("ok") is True, sv.get("source", {}).get("path"))

    t = requests.post(f"{BASE}/api/sources/test", data={"name": name}, timeout=90).json()
    check("sources: test the connection", t.get("ok") is True, t.get("message"))

    sc = requests.post(f"{BASE}/api/sources/scan", data={"name": name}, timeout=90).json()
    names = [f["name"] for f in sc.get("files", [])]
    check("sources: scan finds the PDF", sc.get("ok") is True and "hermes-verify-share.pdf" in names, names)

    ig = requests.post(f"{BASE}/api/sources/ingest", data={
        "name": name, "files": json.dumps([f["remote"] for f in sc.get("files", [])]),
        "force": "true"}, timeout=300).json()
    res = (ig.get("results") or [{}])[0]
    check("sources: ingest from the source", ig.get("ok") is True and res.get("status") == "added", res.get("status"))
    doc = res.get("doc_id")
    hit = requests.get(f"{BASE}/api/search", params={"q": "peregrine falcon nesting cathedral", "mode": "hybrid", "k": 3}, timeout=90).json()["results"]
    check("sources: ingested document is searchable with a source reference",
          bool(hit) and "hermes-verify-share" in hit[0]["filename"] and hit[0]["page"] >= 1,
          hit[0]["citation"] if hit else "no hits")

    requests.post(f"{BASE}/api/delete", data={"doc_id": doc}, timeout=60)
    dl = requests.post(f"{BASE}/api/sources/delete", data={"name": name}, timeout=30).json()
    left = [s["name"] for s in requests.get(f"{BASE}/api/sources", timeout=30).json()["sources"]]
    check("sources: delete the connection", dl.get("ok") is True and name not in left, left)


# =========================================================== E. rendered DOM
EVAL_JS = r"""
(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const st = el => { const c = getComputedStyle(el); return el ? {bg:c.backgroundColor, fg:c.color, font:c.fontFamily} : null; };
  const out = {
    title: document.title,
    h1: document.querySelector('h1')?.textContent.trim(),
    tabs: [...document.querySelectorAll('.tab')].map(t => t.textContent.trim()),
    panels: [...document.querySelectorAll('.panel')].map(p => p.id + (p.classList.contains('is-active') ? '*' : '')),
    brand: document.querySelector('.brand-name')?.textContent.trim(),
    brandHref: document.querySelector('.wordmark')?.getAttribute('href'),
    markSrc: document.querySelector('.wordmark .mark')?.getAttribute('src'),
    markFilter: getComputedStyle(document.querySelector('.wordmark .mark')).filter,
    crumb: document.querySelector('.crumb')?.textContent.replace(/\s+/g,' ').trim(),
    modalOnLoad: { open: document.querySelector('#rmodal').open,
                   display: getComputedStyle(document.querySelector('#rmodal')).display },
    badgeOnLoad: getComputedStyle(document.querySelector('#job-badge')).display,
    lsSetup: localStorage.getItem('pdfkb-setup'),
    bar: (() => {
      const h = document.querySelector('.site-header');
      const f = document.querySelector('.site-foot');
      const hr = h.getBoundingClientRect(), fr = f.getBoundingClientRect(), cs = getComputedStyle(f);
      return { headerPosition: getComputedStyle(h).position, headerTop: Math.round(hr.top),
               position: cs.position, bottom: Math.round(fr.bottom),
               viewport: window.innerHeight, height: Math.round(fr.height),
               pageRoom: getComputedStyle(document.body).paddingBottom };
    })(),
    foot: (() => {
      const f = document.querySelector('.site-foot');
      if (!f) return null;
      const link = f.querySelector('.foot-link');
      const facts = [...f.querySelectorAll('.fact')].map(e => [e.querySelector('.fk').textContent.trim(),
                                                              e.querySelector('.fv').textContent.trim()]);
      return { exists: true, text: f.textContent.replace(/\s+/g, ' ').trim(),
               developer: /Mohammed Kmail/.test(f.textContent),
               linkHref: link && link.getAttribute('href'),
               linkText: link && link.textContent.trim(),
               linkRel: link && link.getAttribute('rel'), newTab: link && link.target === '_blank',
               facts, version: (document.querySelector('#foot-version') || {}).textContent,
               year: (document.querySelector('#foot-year') || {}).textContent };
    })(),
    setupFold: { tag: document.querySelector('#setupbar')?.tagName,
                 open: !!document.querySelector('#setupbar')?.open },
    design: { body: st(document.body), h1: st(document.querySelector('h1')),
              micro: st(document.querySelector('.micro')), primaryBtn: st(document.querySelector('.btn.primary')) },
    icons: (() => {
      const ic = [...document.querySelectorAll('svg.ic')];
      const buttons = [...document.querySelectorAll('.btn, .btn.primary, .tab, a.btn')];
      const glyphs = buttons.map(b => (b.textContent || '').trim())
        .filter(t => /[◐◑✦✕✓•▾▸⚠]/.test(t));
      return { count: ic.length,
               strokes: [...new Set(ic.map(s => getComputedStyle(s).strokeWidth))],
               fills: [...new Set(ic.map(s => getComputedStyle(s).fill))],
               wrong: [...document.querySelectorAll('[data-ic]')]
                 .filter(e => e.querySelectorAll('svg.ic').length !== 1).map(e => e.id || e.dataset.ic),
               theme: document.querySelectorAll('#theme svg.ic').length, glyphs };
    })(),
  };
  // the landing search field
  const qEl = document.querySelector('#q');
  const qBox = qEl.getBoundingClientRect();
  const heroEl = document.querySelector('.search-hero h1');
  out.searchfield = {
    exists: !!document.querySelector('#searchfield'),
    width: Math.round(qBox.width),
    centerOff: Math.round(Math.abs((qBox.left + qBox.right) / 2 - window.innerWidth / 2)),
    h1px: heroEl ? parseFloat(getComputedStyle(heroEl).fontSize) : 0,
    glowFilter: getComputedStyle(document.querySelector('.searchfield-glow')).filter,
    ringAnim: getComputedStyle(document.querySelector('.searchfield-ring')).animationName,
    focused: document.activeElement === qEl,
    hint: !!document.querySelector('#search-hint'),
  };
  out.rtableHeads = [...document.querySelectorAll('#rtable thead th')].map(th => th.textContent.trim());
  out.scope = {
    exists: !!document.querySelector('#scope'),
    boxes: document.querySelectorAll('#scope-list input[type=checkbox]').length,
    count: document.querySelector('#scope-count')?.textContent.trim(),
  };

  // widen the search scope to every knowledge base, then search via the widgets
  [...document.querySelectorAll('#scope-list input[type=checkbox]')].forEach(c => {
    if (!c.checked) { c.checked = true; c.dispatchEvent(new Event('change')); }
  });
  out.scopeAfterAll = document.querySelector('#scope-count')?.textContent.trim();

  document.querySelector('#q').value = 'harbour crane overhauled estuary sediment cores';
  document.querySelector('#go').click();
  let sawSearching = false;
  for (let i = 0; i < 40; i++) {
    if (document.querySelector('#searchfield').classList.contains('is-searching')) { sawSearching = true; break; }
    await sleep(20);
  }
  out.searchingClass = sawSearching;
  await sleep(3500);
  out.searchingOff = !document.querySelector('#searchfield').classList.contains('is-searching');
  out.rows = [...document.querySelectorAll('#rtable tbody tr')].map(tr => [...tr.children].map(td => td.textContent.trim()));
  out.citation = document.querySelector('#citation')?.textContent.trim();
  out.openHref = document.querySelector('#open')?.getAttribute('href');
  out.detailLen = (document.querySelector('#detail')?.textContent || '').length;
  out.stalePane = ['#detail', '#citation', '#open', '#copy-cite', '#copy-text']
    .some(s => !!document.querySelector(s));
  out.menubar = (() => {
    const tabs = [...document.querySelectorAll('.tab')];
    const active = document.querySelector('.tab.is-active');
    const byPanel = (p) => tabs.find(t => t.dataset.panel === p);
    return {
      active: active ? active.dataset.panel : '',
      indicator: active ? getComputedStyle(active, '::after').backgroundImage : '',
      searchN: byPanel('search') ? byPanel('search').dataset.n : undefined,
      searchBadge: byPanel('search') ? getComputedStyle(byPanel('search'), '::before').content : '',
      libraryN: byPanel('library') ? byPanel('library').dataset.n : undefined,
      dangerColor: getComputedStyle(document.querySelector('#del')).color,
      radius: getComputedStyle(document.querySelector('#src-save')).borderRadius,
      tabBg: active ? getComputedStyle(active).backgroundColor : '',
      tabBorder: active ? getComputedStyle(active).borderTopStyle + " " + getComputedStyle(active).borderTopWidth : '',
    };
  })();
  out.menubarSize = (() => {
    const t = document.querySelector('.tab');
    if (!t) return {};
    const r = t.getBoundingClientRect();
    return { h: Math.round(r.height), fs: parseFloat(getComputedStyle(t).fontSize),
             weight: getComputedStyle(t).fontWeight };
  })();

  // click a result → modal with the document at the found position
  out.modal = {};
  const row = document.querySelector('#rtable tbody tr');
  if (row) {
    row.click();
    await sleep(800);
    const dlg = document.querySelector('#rmodal');
    out.modal.open = dlg.open === true;
    out.modal.title = document.querySelector('#rmodal-title')?.textContent.trim();
    out.modal.crumb = document.querySelector('#rmodal-crumb')?.textContent.trim();
    out.modal.note = document.querySelector('#rmodal-note')?.textContent.trim();
    out.modal.textLen = (document.querySelector('#rmodal-text')?.textContent || '').length;
    out.modal.contextLen = (document.querySelector('#rmodal-context')?.textContent || '').length;
    out.modal.showLabel = document.querySelector('#rmodal-show')?.textContent.trim();
    out.modal.openHref = document.querySelector('#rmodal-open')?.getAttribute('href') || '';
    out.modal.frameHiddenOnOpen = document.querySelector('#rmodal-frame')?.hidden === true;
    out.modal.frameSrcOnOpen = document.querySelector('#rmodal-frame')?.getAttribute('src') || '';
    document.querySelector('#rmodal-show').click();
    await sleep(700);
    out.modal.frameSrcAfterReload = document.querySelector('#rmodal-frame')?.getAttribute('src') || '';
    document.querySelector('#rmodal-close').click();
    await sleep(300);
    out.modal.closed = document.querySelector('#rmodal').open === false;
    out.modal.hiddenAfterClose = getComputedStyle(document.querySelector('#rmodal')).display === 'none';
  }

  // nothing that needs a result may be visible before one is opened
  out.modalHiddenNow = ['#rmodal', '#job-badge']
    .every(s => getComputedStyle(document.querySelector(s)).display === 'none');

  // changing the search type re-runs the query by itself
  let searchCalls = 0;
  const realFetch = window.fetch;
  window.fetch = (...a) => {
    if (String(a[0]).includes('/api/search')) searchCalls++;
    return realFetch(...a);
  };
  document.querySelector('#q').value = 'harbour crane overhauled estuary sediment cores';
  document.querySelector('#go').click();
  await sleep(3000);
  const callsBefore = searchCalls;
  document.querySelector('input[name=mode][value=keyword]').click();
  await sleep(3000);
  out.autoRerun = { calls: searchCalls - callsBefore,
                    mode: document.querySelector('input[name=mode]:checked').value,
                    rows: document.querySelectorAll('#rtable tbody tr').length,
                    q: document.querySelector('#q').value };
  window.fetch = realFetch;

  // the header reports indexing wherever the user is (rendered from a synthetic state)
  jobBadge({ running: false, ok: null, lines: [],
             ingest: { running: true, filename: 'big-report.pdf', index: 2, total: 3, pct: 42 } });
  out.jobBadgeIngest = document.querySelector('#job-badge').textContent.replace(/\s+/g, ' ').trim();
  out.jobBarOn = document.querySelector('#jobbar').classList.contains('on');
  liveIngest({ running: true, filename: 'big-report.pdf', index: 2, total: 3, pct: 42,
               detail: 'Embedding chunks 64/128 via ollama' });
  out.ingestLive = document.querySelector('#ingest-live').textContent.replace(/\s+/g, ' ').trim();
  jobBadge({ running: false, ok: null, lines: [], ingest: { running: false, done: null } });
  liveIngest({ running: false, done: null });

  // the result modal must scroll when its content is taller than the window
  const rowEl2 = document.querySelector('#rtable tbody tr');
  if (rowEl2) {
    rowEl2.click();
    await sleep(700);
    const mBody = document.querySelector('.rmodal-body');
    const mDlg = document.querySelector('.rmodal');
    mBody.scrollTop = 0;
    const fitsShort = mDlg.getBoundingClientRect().height <= window.innerHeight * 0.9;
    document.querySelector('#rmodal-text').textContent =
      Array.from({ length: 400 }, (_, i) => 'probe line ' + i).join('\n');
    await sleep(200);
    const overflowY = getComputedStyle(mBody).overflowY;
    const canScroll = mBody.scrollHeight > mBody.clientHeight + 2;
    mBody.scrollTop = 999999;
    await sleep(150);
    out.modalScroll = { overflowY, canScroll, fitsShort, scrolled: Math.round(mBody.scrollTop),
                        clipped: mDlg.scrollHeight > mDlg.clientHeight + 2,
                        modalH: Math.round(mDlg.getBoundingClientRect().height),
                        win: window.innerHeight };
    document.querySelector('#rmodal-close').click();
    await sleep(250);
    out.modalScroll.closes = document.querySelector('#rmodal').open === false;
  }

  // delete a document through the button — the path the user reported broken
  [...document.querySelectorAll('.tab')].find(t => t.dataset.panel === 'library').click();
  await sleep(1500);
  window.__realConfirm = window.confirm;
  window.confirm = () => true;
  const delBefore = document.querySelectorAll('#dtable tbody tr').length;
  const delName = document.querySelector('#dtable tbody tr td')?.textContent.trim() || '';
  document.querySelector('#del').click();
  await sleep(4000);
  window.confirm = window.__realConfirm;
  out.docDelete = {
    before: delBefore,
    after: document.querySelectorAll('#dtable tbody tr').length,
    name: delName,
    result: (document.querySelector('#lib-result')?.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 130),
    delDisabled: document.querySelector('#del').disabled,
  };

  // Enter clears the field
  const qi = document.querySelector('#q');
  qi.value = 'clear me';
  qi.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  await sleep(500);
  out.enterClears = qi.value === '';

  // sources panel controls
  [...document.querySelectorAll('.tab')].find(t => t.dataset.panel === 'sources').click();
  await sleep(900);
  out.sourceTypes = [...document.querySelectorAll('#src-type option')].map(o => o.value + (o.disabled ? ':off' : ':on'));
  out.sourceFields = [...document.querySelectorAll('#src-fields input')].map(i => i.dataset.key);
  out.sourceList = !!document.querySelector('#src-list');
  out.browse = (() => {
    const inp = document.querySelector('#sf-path');
    const btn = inp && inp.closest('.field') && inp.closest('.field').querySelector('button.browse');
    return {
      input: !!inp,
      button: !!btn,
      label: btn ? btn.textContent.trim() : '',
      kind: btn ? (btn.dataset.kind || '') : '',
      target: btn ? btn.dataset.target : '',
      wired: btn ? typeof btn.onclick === 'function' : false,
      storageButtons: document.querySelectorAll('#panel-settings button.browse').length,
    };
  })();

  // settings panel: provider + storage controls
  [...document.querySelectorAll('.tab')].find(t => t.dataset.panel === 'settings').click();
  await sleep(1200);
  out.providerOptions = [...document.querySelectorAll('#s-provider option')].map(o => o.value);
  // the header's setup strip + the merged pages
  out.setup = document.querySelector('#setup')?.textContent.replace(/\s+/g, ' ').trim() || '';
  out.setupParts = [...document.querySelectorAll('#setup .setup-item .setup-k')].map(e => e.textContent.trim());
  out.footerGone = !document.querySelector('.statusbar') && !document.querySelector('.foot-brand');
  out.localInSources = !!document.querySelector('#panel-sources #pick-files');
  out.addPanelGone = !document.querySelector('#panel-add');
  // the info strip folds: closed unless it was left open, and it opens on click
  out.setupSum = (document.querySelector('#setup-sum')?.textContent || '').trim().slice(0, 150);
  const sbEl = document.querySelector('#setupbar');
  if (sbEl) {
    document.querySelector('#setupbar > summary').click();
    await sleep(220);
    out.setupOpens = sbEl.open === true;
    sbEl.open = false;
    await sleep(150);
    out.setupCloses = sbEl.open === false;
  }
  out.backendOptions = [...document.querySelectorAll('#st-backend option')].map(o => o.value);
  out.storageFields = ['#st-data','#st-sqlite','#st-vpath','#st-vcoll','#st-text','#st-sources','#st-backend']
      .every(s => !!document.querySelector(s));
  out.storageResolved = document.querySelector('#st-current')?.textContent.replace(/\s+/g,' ').slice(0,220);
  out.extractorOptions = [...document.querySelectorAll('#x-extractor option')].map(o => o.value + (o.disabled ? ':off' : ':on'));
  // the extraction note must match what is really installed here
  const xsel = document.querySelector('#x-extractor');
  const xprev = xsel.value;
  xsel.value = 'docling';
  xsel.dispatchEvent(new Event('change'));
  await sleep(220);
  const docNote = document.querySelector('#x-note');
  out.xNote = (docNote && !docNote.hidden)
    ? docNote.textContent.replace(/\s+/g, ' ').trim().slice(0, 240) : '';
  out.xNoteAvailable = [...xsel.options].some(o => o.value === 'docling' && o.dataset.ok === '1');
  out.extractorSticks = xsel.value === 'docling';   // the change above must not be undone
  xsel.value = xprev;
  xsel.dispatchEvent(new Event('change'));
  await sleep(120);
  out.extractorRestored = xsel.value === xprev;
  out.extractionControls = ['#x-ocr','#x-save','#i-extra','#i-install','#job-log','#x-extras']
      .every(s => !!document.querySelector(s));
  out.extrasReadout = document.querySelector('#x-extras')?.textContent.replace(/\s+/g,' ').trim().slice(0,140);
  out.vectorFields = ['#st-vurl','#st-vkey','#st-vindex','#vt-test','#st-note']
      .every(s => !!document.querySelector(s));
  out.backendOptions = [...document.querySelectorAll('#st-backend option')].map(o => o.value);
  out.libraryOptions = [...document.querySelectorAll('#lib-select option')].map(o => o.value);
  out.libraryTableInLibrary = !!document.querySelector('#panel-library #libtable');
  out.libraryTableInSettings = !!document.querySelector('#panel-settings #libtable');
  out.libCurrent = !!document.querySelector('#lib-current');
  out.backendNote = document.querySelector('#st-note')?.textContent.replace(/\s+/g,' ').trim().slice(0,160);

  // back to search, then a colour audit in both themes
  [...document.querySelectorAll('.tab')].find(t => t.dataset.panel === 'search').click();
  await sleep(300);
  const hueOf = (r, g, b) => {
    const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
    if (!d) return null;
    let h;
    if (mx === r) h = ((g - b) / d) % 6;
    else if (mx === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h *= 60; if (h < 0) h += 360;
    return { h, chroma: d };
  };
  const BRAND_HUES = [82, 142, 188, 45, 26, 0];   // lime, green, cyan, amber, amber, red
  const nearHue = (h, t) => Math.min(Math.abs(h - t), 360 - Math.abs(h - t)) <= 34;
  const colourScan = () => {
    const strong = [], offBrand = [];
    document.querySelectorAll('*').forEach(el => {
      const c = getComputedStyle(el);
      ['color','backgroundColor','borderTopColor','borderBottomColor','accentColor','backgroundImage']
        .forEach(prop => {
          const v = c[prop] || '';
          const re = /rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)/g;
          let m;
          while ((m = re.exec(v))) {
            if (m[4] !== undefined && parseFloat(m[4]) < 0.08) continue;
            const rgb = [+m[1], +m[2], +m[3]];
            const info = hueOf(rgb[0], rgb[1], rgb[2]);
            if (!info || info.chroma <= 24) continue;
            const item = { tag: el.tagName, cls: String(el.className).slice(0, 34),
                           prop, val: `rgb(${rgb.join(',')})`, hue: Math.round(info.h) };
            strong.push(item);
            if (!BRAND_HUES.some(h => nearHue(info.h, h))) offBrand.push(item);
          }
        });
    });
    const buckets = [...new Set(strong.map(s => Math.round(s.hue / 30) * 30))].sort((a, b) => a - b);
    return { count: strong.length, buckets, offBrand: offBrand.slice(0, 40), sample: strong.slice(0, 6) };
  };
  out.colourLight = colourScan();
  document.querySelector('#theme').click();
  await sleep(500);
  out.dark = { on: document.documentElement.classList.contains('dark'),
               bg: getComputedStyle(document.body).backgroundColor,
               label: document.querySelector('#theme').textContent.trim() };
  out.colourDark = colourScan();
  return JSON.stringify(out);
})()
"""


async def dom_checks():
    print("\n--- E. rendered DOM (headless Chrome) ---")
    if not os.path.exists(CHROME):
        print("SKIP  Chrome not found")
        return None
    try:
        import websockets  # noqa: F401
    except ImportError:
        print("SKIP  websockets not installed")
        return None

    import websockets
    proc = subprocess.Popen(
        [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
         f"--remote-debugging-port={PORT}", "--window-size=1440,1100", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    shots = []
    try:
        for _ in range(160):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=1).read()
                break
            except Exception:
                time.sleep(0.25)
        else:
            print("SKIP  Chrome debug port never came up")
            return None

        targets = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list").read())
        target = next(t for t in targets if t["type"] == "page")
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=90_000_000) as ws:
            ctr = 0

            async def send(method, params=None):
                nonlocal ctr
                ctr += 1
                mid = ctr
                await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
                while True:
                    rep = json.loads(await ws.recv())
                    if rep.get("id") == mid:
                        return rep.get("result", {})

            await send("Page.enable")
            await send("Runtime.enable")
            await send("Emulation.setDeviceMetricsOverride",
                       {"width": 1440, "height": 1100, "deviceScaleFactor": 2, "mobile": False})
            await send("Page.navigate", {"url": BASE + "/?v=" + str(int(time.time()))})
            await asyncio.sleep(6)

            r = await send("Runtime.evaluate", {"expression": EVAL_JS, "returnByValue": True, "awaitPromise": True})
            val = r.get("result", {}).get("value")
            if not val:
                print("FAIL  DOM evaluation returned nothing:", json.dumps(r)[:300])
                FAILS.append("dom evaluation")
                return None
            d = json.loads(val)

            check("page title carries the tool name",
                  d["title"] == "Knowledgebases — kmail.at", d["title"])
            check("four tabs, search first",
                  d["tabs"] == ["Search", "Sources", "Settings", "Library"], d["tabs"])
            check("four panels, search active by default",
                  len(d["panels"]) == 4 and "panel-search*" in d["panels"]
                  and sum(p.endswith("*") for p in d["panels"]) == 1, d["panels"])
            check("kmail.at wordmark in the header", d["brand"] == "kmail.at", d["brand"])
            check("wordmark links to kmail.at", d["brandHref"] == "https://kmail.at", d["brandHref"])
            check("brand logo mark loaded",
                  "logo-mark.png" in (d["markSrc"] or ""), f"{d['markSrc']} filter={d['markFilter']}")
            check("breadcrumb names the tool", "knowledgebases" in (d["crumb"] or "").lower(), d["crumb"])
            check("the old status strip is gone (the footer is a colophon now)",
                  d.get("footerGone") is True)
            bar = d.get("bar") or {}
            htop = bar.get("headerTop")          # 0 is a valid top — `or` would turn it into 99
            check("the header is still sticky at the top of the window",
                  bar.get("headerPosition") == "sticky" and htop is not None and abs(htop) <= 2,
                  f"{bar.get('headerPosition')} at {htop}px")
            check("the footer is pinned to the bottom of the window",
                  bar.get("position") == "fixed"
                  and abs((bar.get("bottom") or 0) - (bar.get("viewport") or 0)) <= 2,
                  f"position={bar.get('position')} bottom={bar.get('bottom')} viewport={bar.get('viewport')} h={bar.get('height')}")
            check("the page leaves room for the pinned footer, so nothing hides under it",
                  (float((bar.get("pageRoom") or "0px").rstrip("px")) or 0) >= (bar.get("height") or 0),
                  f"{bar.get('pageRoom')} reserved for a {bar.get('height')}px footer")
            ft = d.get("foot") or {}
            check("the footer credits the developer",
                  ft.get("developer") is True and "Designed and built by" in (ft.get("text") or ""),
                  (ft.get("text") or "")[:80])
            check("the footer links to kmail.at, opening in a new tab",
                  ft.get("linkHref") == "https://kmail.at" and ft.get("linkText") == "kmail.at"
                  and ft.get("newTab") is True and "noopener" in (ft.get("linkRel") or ""),
                  f"{ft.get('linkText')} -> {ft.get('linkHref')} rel={ft.get('linkRel')}")
            check("the footer carries facts about this installation",
                  len(ft.get("facts") or []) >= 8
                  and all(k and v for k, v in (ft.get("facts") or []))
                  and (ft.get("version") or "").startswith("v")
                  and (ft.get("year") or "").isdigit(),
                  f"{len(ft.get('facts') or [])} facts · version {ft.get('version')} · © {ft.get('year')}")
            names = [k for k, _ in (ft.get("facts") or [])]
            check("those facts include the counts and the build",
                  {"documents", "chunks", "vectors", "embedding", "vector store"} <= set(names)
                  and "runs on" in names and "up for" in names,
                  names)
            sf2 = d.get("setupFold") or {}
            check("the info strip is foldable, and folded unless it was left open",
                  sf2.get("tag") == "DETAILS" and sf2.get("open") == (d.get("lsSetup") == "open")
                  and d.get("setupOpens") is True and d.get("setupCloses") is True,
                  f"{sf2} stored={d.get('lsSetup')!r} opens={d.get('setupOpens')} closes={d.get('setupCloses')}")
            check("the folded strip still says what matters",
                  "setup" in (d.get("setupSum") or "") and "·" in (d.get("setupSum") or ""),
                  d.get("setupSum"))
            sp = d.get("setup") or ""
            parts = d.get("setupParts") or []
            check("the header shows the current setup",
                  all(k in parts for k in ("embedding", "knowledge base", "extractor",
                                           "vector store", "collection", "saving in")), parts)
            check("the header setup names the real values, not placeholders",
                  "qwen3-embedding" in sp and "chroma" in sp and "pdf_chunks" in sp
                  and ("docling" in sp or "pymupdf" in sp), sp[:220])
            data_dir = requests.get(f"{BASE}/api/stats", timeout=30).json().get("data_dir", "")
            leaf = str(data_dir).rstrip("/").split("/")[-1]
            check("the header setup says where it saves",
                  bool(leaf) and leaf in sp, f"{leaf} ⊂ …{sp[-110:]}")
            check("local files moved onto the sources page",
                  d.get("localInSources") is True and d.get("addPanelGone") is True,
                  f"sources={d.get('localInSources')} add-panel-gone={d.get('addPanelGone')}")

            design = d["design"]
            check("body bg == --bg-void #ffffff", design["body"]["bg"] == "rgb(255, 255, 255)", design["body"]["bg"])
            check("h1 uses Inter", "Inter" in design["h1"]["font"], design["h1"]["font"])
            check("micro labels use JetBrains Mono", "JetBrains Mono" in design["micro"]["font"], design["micro"]["font"])
            check("primary button wears the brand green",
                  design["primaryBtn"]["bg"] == "rgb(22, 163, 74)", design["primaryBtn"]["bg"])
            cl, cdk = d.get("colourLight", {}), d.get("colourDark", {})
            check("the design carries colour, not just black and white",
                  cl.get("count", 0) >= 8 and cdk.get("count", 0) >= 8,
                  f"light={cl.get('count')} dark={cdk.get('count')} coloured declarations")
            check("the palette spans several brand hues",
                  len(cl.get("buckets", [])) >= 3 and len(cdk.get("buckets", [])) >= 3,
                  f"light={cl.get('buckets')} dark={cdk.get('buckets')}")
            check("every strong colour is a brand colour (no stray browser hues)",
                  not cl.get("offBrand") and not cdk.get("offBrand"),
                  f"light={(cl.get('offBrand') or [])[:2]} dark={(cdk.get('offBrand') or [])[:2]}")
            ic = d.get("icons") or {}
            check("icons: every control carries a glyph, exactly one each",
                  ic.get("count", 0) >= 15 and ic.get("wrong") == [] and ic.get("theme") == 1,
                  f"{ic.get('count')} icons · theme={ic.get('theme')} · wrong={ic.get('wrong')}")
            check("icons: one thin stroke weight, never filled",
                  set(ic.get("strokes") or []) <= {"1.25px", "1.4px"}
                  and set(ic.get("fills") or []) == {"none"},
                  f"strokes={ic.get('strokes')} fills={ic.get('fills')}")
            check("icons: no character is left standing in for an icon",
                  not ic.get("glyphs"), f"{ic.get('glyphs')}")

            check("search through the UI returned rows", len(d["rows"]) > 0, f"{len(d['rows'])} rows")
            if d["rows"]:
                hdr = d["rows"][0]
                check("result row shows knowledge base + source file + page",
                      len(hdr) == 6 and hdr[2] != "" and hdr[3].endswith(".pdf") and hdr[4].isdigit(), hdr[:5])
            dd = d.get("docDelete") or {}
            check("deleting a document through the button works, end to end",
                  dd.get("after") == (dd.get("before") or 0) - 1 and dd.get("delDisabled") is False,
                  f"{dd.get('before')} -> {dd.get('after')} rows · {dd.get('name')} · "
                  f"button disabled={dd.get('delDisabled')}")
            check("the page reports what the delete purged",
                  "chunks" in (dd.get("result") or "") and "embeddings" in (dd.get("result") or ""),
                  dd.get("result"))
            ar = d.get("autoRerun") or {}
            check("changing the search type re-runs the query by itself",
                  ar.get("calls", 0) >= 1 and ar.get("mode") == "keyword" and ar.get("rows", 0) > 0,
                  f"calls={ar.get('calls')} mode={ar.get('mode')} rows={ar.get('rows')}")
            check("the automatic re-run shows the query it is re-running",
                  (ar.get("q") or "").startswith("harbour crane"), ar.get("q"))
            check("the header shows indexing while it runs, on any page",
                  "indexing" in (d.get("jobBadgeIngest") or "")
                  and "42%" in (d.get("jobBadgeIngest") or "")
                  and d.get("jobBarOn") is True, d.get("jobBadgeIngest"))
            check("the add page shows the live embedding line",
                  "Embedding chunks 64/128" in (d.get("ingestLive") or ""), d.get("ingestLive"))
            ms = d.get("modalScroll") or {}
            check("the result modal scrolls when its content is taller than the window",
                  ms.get("overflowY") in ("auto", "scroll") and ms.get("canScroll") is True
                  and ms.get("scrolled", 0) > 0 and ms.get("clipped") is False
                  and ms.get("closes") is True,
                  f"{ms.get('overflowY')} canScroll={ms.get('canScroll')} scrolled={ms.get('scrolled')} "
                  f"clipped={ms.get('clipped')} modal={ms.get('modalH')}/{ms.get('win')}")
            mb = d.get("menubar") or {}
            check("the menubar marks the active tab with the brand gradient",
                  "linear-gradient" in str(mb.get("indicator")) and mb.get("active") == "search",
                  f"{mb.get('active')} ::after={str(mb.get('indicator'))[:70]}")
            check("the menubar carries live counts",
                  str(mb.get("searchN") or "").isdigit() and str(mb.get("libraryN") or "").isdigit()
                  and '"' in str(mb.get("searchBadge") or ""),
                  f"search={mb.get('searchN')} badge={mb.get('searchBadge')} library={mb.get('libraryN')}")
            check("destructive buttons read as destructive",
                  mb.get("dangerColor") == "rgb(220, 38, 38)", mb.get("dangerColor"))
            check("buttons keep the hairline radius",
                  float(str(mb.get("radius") or "0").rstrip("px")) >= 3, mb.get("radius"))
            check("the menubar is flat: no background and no border of its own",
                  mb.get("tabBg") in ("rgba(0, 0, 0, 0)", "transparent")
                  and str(mb.get("tabBorder") or "").startswith("none"),
                  f"bg={mb.get('tabBg')} border={mb.get('tabBorder')}")
            check("the menubar is a tall, prominent strip",
                  d.get("menubarSize", {}).get("h", 0) >= 38
                  and d.get("menubarSize", {}).get("fs", 0) >= 11,
                  d.get("menubarSize"))
            check("results table names the knowledge base column",
                  "Library" in (d["rtableHeads"] or []), d["rtableHeads"])
            sf = d["searchfield"]
            check("search is the landing page (first tab, search panel active)",
                  d["tabs"][0] == "Search" and "panel-search*" in d["panels"], f"{d['tabs'][0]} / {d['panels']}")
            check("search field is dominant and centred",
                  sf["exists"] and sf["width"] >= 600 and sf["centerOff"] < 120, f"w={sf['width']} off={sf['centerOff']}")
            check("search hero headline is large", sf["h1px"] >= 30, f"{sf['h1px']}px")
            check("search field carries the AI halo (blurred glow)", "blur" in (sf["glowFilter"] or ""), sf["glowFilter"])
            check("search field ring animates", sf["ringAnim"] == "sweep", sf["ringAnim"])
            check("search input is focused on load", sf["focused"] is True)
            check("search empty-state hint present", sf["hint"] is True)
            check("search shows the in-flight AI state",
                  d["searchingClass"] is True and d["searchingOff"] is True)
            check("scope control lists the knowledge bases",
                  d["scope"]["exists"] and d["scope"]["boxes"] >= 1, f"{d['scope']['boxes']} bases")
            check("scope 'all' covers every knowledge base",
                  str(d["scopeAfterAll"]).startswith("all"), d["scopeAfterAll"])
            m = d.get("modal") or {}
            check("clicking a result opens the modal", m.get("open") is True, m.get("title"))
            check("modal names the knowledge base it came from",
                  "·" in (m.get("note") or "") and m.get("crumb"), f"{m.get('crumb')} | {m.get('note')}")
            check("modal shows the matched chunk + page context",
                  (m.get("textLen") or 0) > 20 and (m.get("contextLen") or 0) > 10,
                  f"text={m.get('textLen')} ctx={m.get('contextLen')}")
            check("modal previews the original at the found page, without a click",
                  m.get("frameHiddenOnOpen") is False
                  and "/api/pdf/" in (m.get("frameSrcOnOpen") or "")
                  and "#page=" in (m.get("frameSrcOnOpen") or ""), m.get("frameSrcOnOpen"))
            check("modal can reload the original at that page",
                  "#page=" in (m.get("frameSrcAfterReload") or ""), m.get("frameSrcAfterReload"))
            check("modal offers the found position",
                  "p." in (m.get("showLabel") or ""), m.get("showLabel"))
            check("nothing is showing on a fresh load that needs a result",
                  d["modalOnLoad"]["open"] is False and d["modalOnLoad"]["display"] == "none"
                  and d.get("badgeOnLoad") == "none",
                  f"{d['modalOnLoad']} badge={d.get('badgeOnLoad')}")
            check("neither is anything mid-session", d.get("modalHiddenNow") is True)
            check("the modal is really gone after it closes, not just un-flagged",
                  m.get("hiddenAfterClose") is True, f"display={m.get('hiddenAfterClose')}")
            check("modal closes", m.get("closed") is True)
            check("Enter clears the search field", d.get("enterClears") is True)
            check("the duplicated in-page result pane is gone (the modal is the only view)",
                  d.get("stalePane") is False and not (d.get("detailLen") or 0),
                  f"stale elements present={d.get('stalePane')} detailLen={d.get('detailLen')}")
            check("the modal's own link targets the source page",
                  (m.get("openHref") or "").startswith("/api/pdf/") and "#page=" in (m.get("openHref") or ""),
                  m.get("openHref"))
            check("the modal names library · knowledge base · source page",
                  "p." in (m.get("note") or "") and ".sqlite" in (m.get("note") or ""), m.get("note"))
            check("settings tab: the extraction note matches what is really installed",
                  bool(d.get("xNote")) and (("not installed" in d["xNote"]) != d["xNoteAvailable"]),
                  f"docling available={d.get('xNoteAvailable')} · {d.get('xNote')}")
            check("settings tab: the extractor choice sticks (it does not snap back)",
                  d.get("extractorSticks") is True, d.get("extractorSticks"))

            check("sources tab: connector types listed",
                  len(d["sourceTypes"]) >= 4 and any(t.endswith(":on") for t in d["sourceTypes"]), d["sourceTypes"])
            check("sources tab: dynamic fields for the chosen type",
                  "path" in d["sourceFields"] or "url" in d["sourceFields"], d["sourceFields"])
            check("sources tab: saved-source list present", d["sourceList"] is True)
            check("settings tab: provider options", len(d["providerOptions"]) >= 4, d["providerOptions"])
            check("settings tab: vector backend options", len(d["backendOptions"]) >= 2, d["backendOptions"])
            check("settings tab: storage fields present", d["storageFields"] is True)
            check("settings tab: resolved storage readout",
                  "data dir" in (d["storageResolved"] or "").lower(), (d["storageResolved"] or "")[:90])
            check("settings tab: extractor options with availability",
                  len(d["extractorOptions"]) >= 2 and any(o.endswith(":on") for o in d["extractorOptions"]),
                  d["extractorOptions"])
            check("settings tab: extraction + install controls present", d["extractionControls"] is True)
            check("settings tab: extras readout names docling",
                  "docling" in (d["extrasReadout"] or "").lower(), d["extrasReadout"])
            check("settings tab: vector server fields + test present", d["vectorFields"] is True)
            check("settings tab: broad backend list in the select",
                  len(d["backendOptions"]) >= 15, f"{len(d['backendOptions'])} options")
            check("settings tab: backend note explains the current choice",
                  len(d["backendNote"] or "") > 0, d["backendNote"])
            check("local connection offers a Browse button instead of a typed path",
                  d["browse"]["input"] is True and d["browse"]["button"] is True
                  and "Browse" in (d["browse"]["label"] or "")
                  and d["browse"]["wired"] is True
                  and d["browse"]["target"] == "sf-path" and d["browse"]["kind"] == "dir",
                  f"{d['browse']['label']} -> {d['browse']['target']} ({d['browse']['kind']}) wired={d['browse']['wired']}")
            check("storage path fields offer Browse too",
                  d["browse"]["storageButtons"] >= 3, d["browse"]["storageButtons"])
            check("header: library selector populated", len(d["libraryOptions"]) >= 1, d["libraryOptions"])
            check("knowledge bases live on the library page",
                  d["libraryTableInLibrary"] is True and d["libraryTableInSettings"] is False,
                  f"library={d['libraryTableInLibrary']} settings={d['libraryTableInSettings']}")
            check("library page names the active knowledge base", d["libCurrent"] is True)
            check("dark toggle switches tokens",
                  d["dark"]["on"] is True and d["dark"]["bg"] == "rgb(10, 10, 10)", d["dark"]["bg"])

            for label, tab, wait in (("light", None, 2.5), ("dark", None, 0.8)):
                p = os.path.join(TMP, f"hermes-verify-{label}.png")
                shot = await send("Page.captureScreenshot", {"format": "png"})
                open(p, "wb").write(base64.b64decode(shot["data"]))
                shots.append(p)
                if label == "light":
                    await send("Runtime.evaluate", {"expression": "document.querySelector('#theme').click()"})
                    await asyncio.sleep(1.0)
            for s in shots:
                check("screenshot written " + os.path.basename(s), os.path.getsize(s) > 20_000,
                      f"{os.path.getsize(s)} bytes")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
    return shots


# ========================================================== F. safeguards
def safeguard_checks():
    print("\n--- F. safeguards ---")
    r = requests.post(f"{BASE}/api/reset", data={}, timeout=30)
    check("reset without the confirm token is refused", r.status_code == 400, r.status_code)
    docs = requests.get(f"{BASE}/api/documents", timeout=30).json()["documents"]
    check("refused reset left documents untouched", len(docs) >= 1, f"{len(docs)} docs")

    st = requests.get(f"{BASE}/api/settings", timeout=30).json()["storage"]["paths"]
    ap = requests.post(f"{BASE}/api/settings/storage", data={
        "data_dir": st["data_dir"], "sqlite_path": "", "vector_backend": "chroma",
        "vector_path": "", "vector_collection": "pdf_chunks",
        "text_dir": "", "sources_dir": ""}, timeout=120).json()
    check("storage change reports a knowledge-base backup",
          bool(ap.get("backup")), ap.get("backup"))
    check("the reported backup file exists", bool(ap.get("backup")) and os.path.exists(ap["backup"]), ap.get("backup"))

    # deleting a document must not leave an orphaned text export behind
    before = requests.get(f"{BASE}/api/documents", timeout=30).json()["documents"]
    if before:
        d0 = before[0]
        requests.post(f"{BASE}/api/delete", data={"doc_id": d0["doc_id"]}, timeout=60)
        export = os.path.join(st["text_dir"], f"{os.path.splitext(d0['filename'])[0]}.{d0['doc_id'][:8]}.md")
        check("delete removed the readable export too", not os.path.exists(export), export)

    rz = requests.post(f"{BASE}/api/reset", data={"confirm": "RESET"}, timeout=120).json()
    check("reset with the confirm token works", rz.get("ok") is True, rz.get("stats", {}).get("documents"))
    after = requests.get(f"{BASE}/api/documents", timeout=30).json()["documents"]
    check("reset emptied the isolated knowledge base", len(after) == 0, len(after))


# ============================== C2. multi-library search (the search scope)
def multilib_checks():
    print("\n--- C2. searching across several knowledge bases ---")
    base = requests.get(f"{BASE}/api/libraries", timeout=30).json()
    active = base["active"]
    lib2 = "hermes-verify-scope"
    alt = os.path.join(TMP, "scope-store")

    r = requests.post(f"{BASE}/api/libraries/save", data={
        "name": lib2, "data_dir": alt, "sqlite_path": "", "vector_backend": "sqlite",
        "vector_path": "", "vector_url": "", "vector_api_key": "", "vector_index": "",
        "vector_collection": "pdf_chunks", "text_dir": "", "sources_dir": ""}, timeout=120).json()
    check("scope: a second knowledge base exists", r.get("ok") is True,
          [l["name"] for l in r.get("libraries", [])])

    # put a distinctive document in the second library (ingest writes to the active one)
    fx = fixture(os.path.join(TMP, "hermes-verify-scopedoc.pdf"),
                 ["Scope Fixture", "Narwhal tusk measurements from the drift station."])
    requests.post(f"{BASE}/api/libraries/use", data={"name": lib2}, timeout=120)
    with open(fx, "rb") as fh:
        up = requests.post(f"{BASE}/api/ingest",
                           files={"files": ("hermes-verify-scopedoc.pdf", fh, "application/pdf")},
                           data={"force": "true"}, timeout=240).json()["results"][0]
    check("scope: document ingested inside the second knowledge base", up.get("status") == "added", up.get("status"))
    back = requests.post(f"{BASE}/api/libraries/use", data={"name": active}, timeout=120).json()
    check("scope: back on the first knowledge base", back.get("active") == active, back.get("active"))

    # one knowledge base only
    one = requests.get(f"{BASE}/api/search",
                       params={"q": "narwhal tusk measurements", "mode": "hybrid",
                               "k": 5, "libraries": active}, timeout=90).json()
    check("scope: a single-library search stays inside that library",
          one.get("libraries") == [active]
          and all(h["library"] == active for h in one["results"]),
          [h["library"] for h in one["results"]][:3])

    # both knowledge bases at once
    two = requests.get(f"{BASE}/api/search",
                       params={"q": "narwhal tusk measurements", "mode": "hybrid",
                               "k": 8, "libraries": f"{active},{lib2}"}, timeout=120).json()
    libs = {h["library"] for h in two["results"]}
    check("scope: searching two knowledge bases returns hits from both",
          two.get("libraries") == [active, lib2] and {active, lib2} <= libs,
          sorted(libs))
    check("scope: every hit names the knowledge base it was saved in",
          all(h.get("library") for h in two["results"]), [h["library"] for h in two["results"]][:3])
    hit2 = next((h for h in two["results"] if h["library"] == lib2), None)
    check("scope: the second library's hit links into that library",
          bool(hit2) and hit2["url"].startswith(f"/api/pdf/{lib2}/") and "#page=" in hit2["url"],
          hit2["url"] if hit2 else "no hit from the second library")

    if hit2:
        pr = requests.get(f"{BASE}{hit2['url'].split('#')[0]}", timeout=60)
        check("scope: library-qualified source PDF is served",
              pr.status_code == 200 and pr.content[:5] == b"%PDF-",
              f"{pr.status_code} {pr.headers.get('content-type')}")
    ctx = requests.get(f"{BASE}/api/context",
                       params={"doc_id": (hit2 or {}).get("doc_id", ""),
                               "page": (hit2 or {}).get("page", 1), "library": lib2}, timeout=60).json()
    check("scope: page context resolves in the named library",
          ctx.get("library") == lib2 and len(ctx.get("chunks", [])) >= 1, ctx.get("library"))

    unknown = requests.get(f"{BASE}/api/search",
                           params={"q": "narwhal", "mode": "hybrid", "k": 3,
                                   "libraries": "no-such-library"}, timeout=60).json()
    check("scope: an unknown library falls back to the active one",
          unknown.get("libraries") == [active], unknown.get("libraries"))


# ========================================================== G. libraries
def library_checks(scope_lib="hermes-verify-scope"):
    print("\n--- G. libraries (multiple target knowledge bases) ---")
    base = requests.get(f"{BASE}/api/libraries", timeout=30).json()
    check("libraries: endpoint lists every knowledge base",
          len(base["libraries"]) >= 2 and any(l["active"] for l in base["libraries"]),
          [(l["name"], l["documents"]) for l in base["libraries"]])
    active = base["active"]
    before = requests.get(f"{BASE}/api/documents", timeout=30).json()["documents"]

    fx0 = fixture(os.path.join(TMP, "hermes-verify-libbase.pdf"),
                  ["Library Baseline", "The harbour crane was overhauled in spring."])
    with open(fx0, "rb") as fh:
        requests.post(f"{BASE}/api/ingest",
                      files={"files": ("hermes-verify-libbase.pdf", fh, "application/pdf")},
                      data={"force": "true"}, timeout=240)
    before = requests.get(f"{BASE}/api/documents", timeout=30).json()["documents"]
    check("libraries: the first library has content to compare against", len(before) >= 1, f"{len(before)} docs")

    hit = requests.get(f"{BASE}/api/search",
                       params={"q": "aurora borealis narwhal", "mode": "semantic", "k": 5,
                               "libraries": active}, timeout=90).json()["results"]
    check("libraries: the other knowledge base is isolated from this one",
          all("scopedoc" not in h["filename"].lower() for h in hit),
          [h["filename"] for h in hit][:3])

    u = requests.post(f"{BASE}/api/libraries/use", data={"name": scope_lib}, timeout=120).json()
    check("libraries: switch to the second knowledge base",
          u.get("ok") is True and u.get("active") == scope_lib, u.get("active"))
    check("libraries: switching reports a knowledge-base backup", bool(u.get("backup")), u.get("backup"))
    docs2 = requests.get(f"{BASE}/api/documents", timeout=30).json()["documents"]
    check("libraries: the second knowledge base shows only its own document", len(docs2) == 1, len(docs2))

    u2 = requests.post(f"{BASE}/api/libraries/use", data={"name": active}, timeout=120).json()
    check("libraries: switch back to the first", u2.get("ok") is True and u2.get("active") == active, u2.get("active"))
    after = requests.get(f"{BASE}/api/documents", timeout=30).json()["documents"]
    check("libraries: first library intact after the round trip",
          len(after) == len(before) and all(any(a["doc_id"] == b["doc_id"] for a in after) for b in before),
          f"{len(before)} -> {len(after)}")
    hit2 = requests.get(f"{BASE}/api/search",
                        params={"q": "harbour crane estuary", "mode": "semantic", "k": 3}, timeout=90).json()["results"]
    check("libraries: search works again in the first library", len(hit2) > 0, f"{len(hit2)} hits")

    # ---- deleting a knowledge base must take every one of its files with it ----
    scope_dir = str(next((l.get("container") for l in
                          requests.get(f"{BASE}/api/libraries", timeout=30).json()["libraries"]
                          if l["name"] == scope_lib), ""))
    check("libraries: the second knowledge base has its own container on disk",
          bool(scope_dir) and os.path.exists(os.path.join(scope_dir, "kb.sqlite")), scope_dir)
    dd = requests.post(f"{BASE}/api/libraries/delete",
                       data={"name": scope_lib, "purge": "1"}, timeout=60).json()
    p = dd.get("purge", {})
    check("libraries: delete reports the purge",
          dd.get("ok") is True and bool(p.get("purged"))
          and os.path.realpath(p["purged"]) == os.path.realpath(scope_dir),
          f"purged={p.get('purged')} want {scope_dir}")
    check("libraries: deleting reports the documents it removed",
          p.get("documents") == 1 and (p.get("freed_bytes") or 0) > 0,
          f"documents={p.get('documents')} freed={p.get('freed_bytes')} bytes")
    check("libraries: its database, vector store, exports and sources are gone from disk",
          not os.path.exists(scope_dir), scope_dir)
    check("libraries: the deleted knowledge base is no longer listed",
          scope_lib not in [l["name"] for l in dd.get("libraries", [])], scope_lib)

    # ---- guards: a purge must never take out live data ----
    live = requests.get(f"{BASE}/api/settings", timeout=30).json()["storage"]["paths"]
    live_dir, live_db = live["data_dir"], live["sqlite_path"]
    cfg_fields = {"sqlite_path": "", "vector_backend": "chroma", "vector_path": "",
                  "vector_url": "", "vector_api_key": "", "vector_index": "",
                  "vector_collection": "pdf_chunks", "text_dir": "", "sources_dir": ""}
    requests.post(f"{BASE}/api/libraries/save", data={"name": "hermes-verify-shared"}, timeout=60)
    moved = requests.post(f"{BASE}/api/libraries/save",
                          data={"name": "hermes-verify-shared", "data_dir": live_dir,
                                **cfg_fields}, timeout=60).json()
    check("libraries: a knowledge base cannot be moved onto another one's container",
          moved.get("ok") is False and "container" in (moved.get("error") or ""),
          (moved.get("error") or "")[:90])
    g1 = requests.post(f"{BASE}/api/libraries/delete",
                       data={"name": "hermes-verify-shared", "purge": "1"}, timeout=60).json()
    check("libraries: the live knowledge base's own files are never touched",
          g1.get("ok") is True and os.path.exists(live_db)
          and os.path.realpath(str(g1["purge"].get("purged") or "")) != os.path.realpath(live_dir),
          f"purged={g1['purge'].get('purged')} live={live_db}")

    junk = os.path.join(TMP, "not-a-kb")
    os.makedirs(junk, exist_ok=True)
    with open(os.path.join(junk, "holiday-photos.txt"), "w", encoding="utf-8") as fh:
        fh.write("definitely not a knowledge base\n")
    requests.post(f"{BASE}/api/libraries/save", data={"name": "hermes-verify-junk"}, timeout=60)
    requests.post(f"{BASE}/api/libraries/save",
                  data={"name": "hermes-verify-junk", "data_dir": junk, **cfg_fields}, timeout=60)
    g2 = requests.post(f"{BASE}/api/libraries/delete",
                       data={"name": "hermes-verify-junk", "purge": "1"}, timeout=60).json()
    check("libraries: a directory that is not a knowledge base is left alone",
          g2.get("ok") is True and g2["purge"].get("purged") is None
          and os.path.exists(os.path.join(junk, "holiday-photos.txt")), g2["purge"].get("skipped"))

    bad = requests.post(f"{BASE}/api/libraries/delete", data={"name": active}, timeout=30).json()
    check("libraries: the active knowledge base cannot be deleted", bad.get("ok") is False,
          (bad.get("error") or "")[:80])


# ================================================ C3. live ingest activity
def activity_checks():
    """The indexing/embedding indicator: visible while it runs, from any page."""
    import threading
    import time as _time

    print("\n--- C3. the indexing / embedding indicator ---")
    j0 = requests.get(f"{BASE}/api/settings/job", timeout=30).json()
    check("activity: the job endpoint reports ingestion too",
          "ingest" in j0 and "running" in j0["ingest"], sorted(j0.keys()))

    fx = os.path.join(TMP, "hermes-verify-activity.pdf")
    fixtures = []
    for i in range(5):        # five files: enough work that the indicator MUST be observable
        p = os.path.join(TMP, "hermes-verify-activity-%d.pdf" % i)
        fixtures.append(fixture(p, ["Activity Fixture %d" % i,
                                    "The quay wall was surveyed after the spring tide.",
                                    "Second page, so there is real work to report."]))
    out = {}

    def post():
        files = [("files", (os.path.basename(p), open(p, "rb"), "application/pdf"))
                 for p in fixtures]
        out["r"] = requests.post(f"{BASE}/api/ingest", files=files,
                                 data={"force": "true"}, timeout=900).json()

    th = threading.Thread(target=post)
    th.start()
    seen_running, detail, pcts, stages, totals = False, "", [], 0, set()
    end = _time.time() + 60
    while th.is_alive() and _time.time() < end:
        try:
            ing = (requests.get(f"{BASE}/api/settings/job", timeout=10).json() or {}).get("ingest") or {}
        except Exception:  # noqa: BLE001
            ing = {}
        if ing.get("running"):
            seen_running = True
            stages += 1
            if ing.get("total"):
                totals.add(ing["total"])
            if ing.get("detail") and ing["detail"] != detail:
                detail = ing["detail"]
            pcts.append(ing.get("pct"))
        if seen_running and not ing.get("running"):
            break
        _time.sleep(0.02)
    th.join(timeout=180)
    up = (out.get("r", {}).get("results") or [{}])[0]

    check("activity: an ingest is visible while it runs", seen_running is True,
          f"{stages} samples caught")
    check("activity: it says what it is doing and how far it has got",
          bool(detail) and any(isinstance(p, int) and 0 <= p <= 100 for p in pcts),
          f"detail={detail!r} pcts={sorted({p for p in pcts if p is not None})[:6]}")
    check("activity: it counts the files in the batch", totals == {len(fixtures)},
          f"totals seen={sorted(totals)} want={len(fixtures)}")
    fin = requests.get(f"{BASE}/api/settings/job", timeout=30).json()["ingest"]
    check("activity: it reports done, with the results, when it finishes",
          fin.get("running") is False and bool(fin.get("done"))
          and (fin.get("results") or [{}])[0].get("nchunks", 0) > 0,
          f"running={fin.get('running')} chunks={(fin.get('results') or [{}])[0].get('nchunks')}")
    check("activity: the ingest itself still returns its results",
          up.get("status") == "added" and up.get("nchunks", 0) > 0, up.get("status"))


# ============================================ C4. knowledge base isolation
def isolation_checks():
    """A new knowledge base is its own container: own files, own collection."""
    print("\n--- C4. knowledge base isolation ---")
    before = requests.get(f"{BASE}/api/libraries", timeout=30).json()
    active = before["active"]
    base = next(l for l in before["libraries"] if l["active"])
    iso_name = "hermes-verify-iso"

    r = requests.post(f"{BASE}/api/libraries/save", data={"name": iso_name}, timeout=60).json()
    check("isolation: a new knowledge base is created", r.get("ok") is True, r.get("error"))
    mine = next((l for l in r.get("libraries", []) if l["name"] == iso_name), {})
    check("isolation: it gets its own container, not the current one's",
          bool(mine.get("container")) and mine["container"] != base["container"],
          f"{mine.get('container')}  vs  {base.get('container')}")
    check("isolation: it gets its own embedding collection",
          bool(mine.get("collection")) and mine["collection"] != base["collection"]
          and "iso" in str(mine["collection"]),
          f"{mine.get('collection')}  vs  {base.get('collection')}")
    check("isolation: its container holds nothing of anyone else's yet",
          not os.path.exists(os.path.join(str(mine.get("container")), "kb.sqlite")),
          mine.get("container"))

    # a new knowledge base is never pointed at someone else's container: the paths you
    # submit are ignored on creation, and an update onto a used container is refused
    cfg_fields = {"sqlite_path": "", "vector_backend": "chroma", "vector_path": "",
                  "vector_url": "", "vector_api_key": "", "vector_index": "",
                  "vector_collection": "pdf_chunks", "text_dir": "", "sources_dir": ""}
    clash = requests.post(f"{BASE}/api/libraries/save",
                          data={"name": "hermes-verify-clash", "data_dir": base["container"],
                                **cfg_fields}, timeout=60).json()
    made = next((l for l in clash.get("libraries", []) if l["name"] == "hermes-verify-clash"), {})
    check("isolation: creation ignores submitted paths and gives it its own container",
          clash.get("ok") is True and made.get("container") != base["container"],
          f"{made.get('container')}  (submitted {base['container']})")
    move = requests.post(f"{BASE}/api/libraries/save",
                         data={"name": "hermes-verify-clash", "data_dir": base["container"],
                               **cfg_fields}, timeout=60).json()
    check("isolation: moving one onto another's container is refused",
          move.get("ok") is False and "container" in (move.get("error") or ""),
          (move.get("error") or "")[:90])

    requests.post(f"{BASE}/api/libraries/use", data={"name": iso_name}, timeout=120)
    fx = fixture(os.path.join(TMP, "hermes-verify-iso.pdf"),
                 ["Isolation Fixture", "The weir gate was replaced after the flood."])
    with open(fx, "rb") as fh:
        up = requests.post(f"{BASE}/api/ingest",
                           files={"files": ("hermes-verify-iso.pdf", fh, "application/pdf")},
                           data={"force": "true"}, timeout=300).json()["results"][0]
    check("isolation: it ingests into its own container", up.get("status") == "added", up.get("status"))
    cont = str(mine.get("container"))
    check("isolation: its database, vectors, exports and source copies are its own",
          all(os.path.exists(os.path.join(cont, n)) for n in ("kb.sqlite", "chroma", "text", "sources")),
          sorted(os.listdir(cont)) if os.path.isdir(cont) else "container missing")
    iso = requests.get(f"{BASE}/api/stats", timeout=30).json()
    check("isolation: the index is aware which collection holds its embeddings",
          iso.get("vector_collection") == mine.get("collection")
          and iso.get("collection_stamp") == mine.get("collection")
          and iso.get("collection_match") is True,
          f"collection={iso.get('vector_collection')} stamp={iso.get('collection_stamp')}")
    hits = requests.get(f"{BASE}/api/search", params={"q": "weir gate flood", "mode": "hybrid", "k": 5},
                        timeout=120).json()["results"]
    check("isolation: only its own content is searchable inside it",
          bool(hits) and all(h["library"] == iso_name for h in hits),
          [(h["library"], h["filename"]) for h in hits][:3])
    check("isolation: it cannot see the other knowledge base's files",
          all("BEV" not in (h["filename"] or "") and "base" not in (h["filename"] or "") for h in hits),
          [h["filename"] for h in hits][:3])

    requests.post(f"{BASE}/api/libraries/use", data={"name": active}, timeout=120)
    back = requests.get(f"{BASE}/api/stats", timeout=30).json()
    check("isolation: the knowledge base you started in is untouched",
          os.path.realpath(str(back.get("data_dir"))) == os.path.realpath(str(base["container"]))
          and back["documents"] == base["documents"],
          f"{base['documents']} -> {back['documents']} docs in {back.get('data_dir')}")
    other = requests.get(f"{BASE}/api/search", params={"q": "weir gate flood", "mode": "hybrid", "k": 5},
                         timeout=120).json()["results"]
    check("isolation: and it cannot find the new one's document",
          all("hermes-verify-iso" not in (h["filename"] or "") for h in other),
          [h["filename"] for h in other][:3])

    dd = requests.post(f"{BASE}/api/libraries/delete",
                       data={"name": iso_name, "purge": "1"}, timeout=60).json()
    requests.post(f"{BASE}/api/libraries/delete",
                  data={"name": "hermes-verify-clash", "purge": "1"}, timeout=60)
    check("isolation: deleting it removes only its own container",
          dd.get("ok") is True and dd["purge"].get("purged")
          and not os.path.exists(cont) and os.path.exists(str(base["container"])),
          f"purged={dd['purge'].get('purged')}")


# ================================== C5. only the active knowledge base writes
def _dir_digest(root):
    """Every file under a container: path, size and mtime — for before/after comparison."""
    import hashlib
    out = {}
    for dp, _dn, fn in os.walk(root):
        for f in fn:
            p = os.path.join(dp, f)
            try:
                stt = os.stat(p)
            except OSError:
                continue
            key = os.path.relpath(p, root)
            out[key] = (stt.st_size, stt.st_mtime_ns,
                        hashlib.sha1(open(p, "rb").read(4096)).hexdigest()[:12])
    return out


def readonly_checks():
    """Reading another knowledge base must not save anything into it."""
    print("\n--- C5. only the active knowledge base writes ---")
    active = requests.get(f"{BASE}/api/libraries", timeout=30).json()["active"]
    other = "hermes-verify-cold"

    # a second knowledge base with real content in it
    requests.post(f"{BASE}/api/libraries/save", data={"name": other}, timeout=60)
    requests.post(f"{BASE}/api/libraries/use", data={"name": other}, timeout=60)
    fx = fixture(os.path.join(TMP, "hermes-verify-cold.pdf"),
                 ["Cold Base Fixture",
                  "The harbour is frozen in January and the ice is two metres thick."])
    up = requests.post(f"{BASE}/api/ingest", data={"force": ""},
                       files=[("files", (os.path.basename(fx), open(fx, "rb"), "application/pdf"))],
                       timeout=600).json()
    requests.post(f"{BASE}/api/libraries/use", data={"name": active}, timeout=60)
    libs = requests.get(f"{BASE}/api/libraries", timeout=30).json()["libraries"]
    cont = str(next((l.get("container") for l in libs if l["name"] == other), ""))
    r0 = (up.get("results") or [{}])[0]
    check("cold base: a second knowledge base has its own indexed content",
          bool(cont) and os.path.exists(os.path.join(cont, "kb.sqlite"))
          and r0.get("status") == "added" and r0.get("nchunks", 0) > 0,
          f"{r0.get('status')} {r0.get('nchunks')} chunks in {cont}")

    before = _dir_digest(cont)
    sqlite_before = before.get("kb.sqlite")

    hit = requests.get(f"{BASE}/api/search", params={"q": "frozen harbour ice thickness",
                                                     "mode": "semantic", "k": 5,
                                                     "libraries": f"{active},{other}"},
                       timeout=120).json()
    froms = {h["library"] for h in hit.get("results", [])}
    check("cold base: its content is still searchable across knowledge bases",
          other in froms, sorted(froms))

    after = _dir_digest(cont)
    created = sorted(set(after) - set(before))
    check("cold base: searching across it creates no file inside it",
          not created, created or "nothing created")
    check("cold base: its database is byte-for-byte untouched by a read",
          after.get("kb.sqlite") == sqlite_before and sqlite_before is not None,
          f"before={sqlite_before} after={after.get('kb.sqlite')}")

    # a knowledge base that was never used at all: a search must not conjure files for it
    empty = "hermes-verify-untouched"
    requests.post(f"{BASE}/api/libraries/save", data={"name": empty}, timeout=60)
    libs = requests.get(f"{BASE}/api/libraries", timeout=30).json()["libraries"]
    econt = str(next((l.get("container") for l in libs if l["name"] == empty), ""))
    edir = _dir_digest(econt)
    q = requests.get(f"{BASE}/api/search", params={"q": "anything at all", "k": 3,
                                                   "libraries": f"{active},{empty}"},
                     timeout=120)
    check("cold base: asking one that was never used still answers",
          q.status_code == 200, q.status_code)
    check("cold base: and it is not given a database just to be searched",
          _dir_digest(econt) == edir and not os.path.exists(os.path.join(econt, "kb.sqlite")),
          sorted(_dir_digest(econt).keys()) or "still empty")

    for name in (other, empty):
        requests.post(f"{BASE}/api/libraries/delete",
                      data={"name": name, "purge": "1"}, timeout=60)
    check("cold base: the leftovers are cleaned up",
          active == requests.get(f"{BASE}/api/libraries", timeout=30).json()["active"], active)


if __name__ == "__main__":
    for fn in (api_checks, settings_checks, storage_checks, activity_checks,
               multilib_checks, source_checks, isolation_checks, readonly_checks):
        try:
            fn()
        except Exception as ex:  # noqa: BLE001
            print(f"{fn.__name__} CRASHED:", type(ex).__name__, ex)
            FAILS.append(f"{fn.__name__} crash")
    try:
        shots = asyncio.run(dom_checks())
    except Exception as ex:  # noqa: BLE001
        print("DOM CHECKS CRASHED:", type(ex).__name__, ex)
        FAILS.append("dom crash")
        shots = None
    try:
        safeguard_checks()
    except Exception as ex:  # noqa: BLE001
        print("SAFEGUARD CHECKS CRASHED:", type(ex).__name__, ex)
        FAILS.append("safeguard crash")
    try:
        library_checks()
    except Exception as ex:  # noqa: BLE001
        print("LIBRARY CHECKS CRASHED:", type(ex).__name__, ex)
        FAILS.append("library crash")
    print("\n" + "=" * 60)
    print(f"probe dir: {TMP}")
    if shots:
        print("screenshots: " + "  ".join(shots))
    print("RESULT:", "ALL PASS" if not FAILS else f"{len(FAILS)} FAILURES: {FAILS}")
    sys.exit(1 if FAILS else 0)
