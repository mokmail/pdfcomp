"""PDF text extraction: per-page text, keeping 1-based page numbers.

Two interchangeable extractors (chosen in Settings):

  pymupdf  — fast, text-layer only (default)
  docling  — optional extra: layout/table-aware conversion, can OCR scans.
             Slower and a large install (`pip install docling`).

Both return ([doc_meta, ...], [{"page": n, "text": ...}]) so page attribution —
which every source reference depends on — is preserved either way.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pymupdf

from . import config

EXTRACTORS = [
    {"id": "pymupdf", "label": "PyMuPDF", "install": "",
     "note": "fast, page-accurate text layer"},
    {"id": "docling", "label": "Docling", "install": "python3 -m pip install docling",
     "note": "layout/table aware, optional OCR — slower, large install"},
]


# ---------------------------------------------------------------- helpers
def _sha1_file(path, buf=1 << 20):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            b = f.read(buf)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _clean(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def docling_available():
    """True only if Docling is genuinely usable.

    A bare `import docling` is not enough: a leftover/partial install can leave a
    namespace package that imports fine but has no modules, so probe the
    converter we actually use.
    """
    try:
        from docling.document_converter import DocumentConverter  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def docling_version():
    try:
        from importlib.metadata import version
        return version("docling")
    except Exception:  # noqa: BLE001
        return ""


# --------------------------------------------------------------- extractors
def _extract_pymupdf(path):
    doc = pymupdf.open(str(path))
    try:
        pages = []
        for i in range(doc.page_count):
            pages.append({"page": i + 1, "text": _clean(doc.load_page(i).get_text("text"))})
        md = doc.metadata or {}
        n = doc.page_count
    finally:
        doc.close()
    return {"title": (md.get("title") or "").strip()}, pages, n


def _extract_docling(path, ocr=False):
    """Page-by-page text via Docling, from each item's provenance."""
    from docling.document_converter import DocumentConverter
    res = DocumentConverter().convert(str(path))
    doc = res.document

    by_page: dict[int, list[str]] = {}
    for entry in doc.iterate_items():
        item = entry[0] if isinstance(entry, tuple) else entry
        text = (getattr(item, "text", "") or "").strip()
        if not text:
            continue
        page_no = 1
        for prov in (getattr(item, "prov", None) or []):
            page_no = getattr(prov, "page_no", None) or page_no
            break
        by_page.setdefault(int(page_no), []).append(text)

    if not by_page:                      # no provenance — fall back to markdown
        md = doc.export_to_markdown()
        return {}, [{"page": 1, "text": _clean(md)}], 1

    pages = [{"page": p, "text": _clean("\n\n".join(by_page[p]))} for p in sorted(by_page)]
    return {}, pages, len(pages)


def docling_service_url():
    """The docling-serve base URL, or "" when none is configured.

    `PDFKB_DOCLING_URL` wins — that is what the container sets — and the saved setting is
    the fallback, so the app can be pointed at a service without a restart.
    """
    if config.DOCLING_URL:
        return config.DOCLING_URL.rstrip("/")
    try:
        from . import prefs
        return (prefs.load().get("docling_url") or "").strip().rstrip("/")
    except Exception:                                     # noqa: BLE001
        return ""


def docling_service_ready(url=None, timeout=2.5):
    """True when a docling-serve answers its health endpoint (`/health`)."""
    url = url or docling_service_url()
    if not url:
        return False
    try:
        import requests
        return requests.get(url.rstrip("/") + "/health", timeout=timeout).status_code < 500
    except Exception:                                     # noqa: BLE001
        return False


def _extract_docling_service(path, url, ocr=False):
    """Extract through a docling-serve container — the same pages, no local install.

    POST /v1/convert/file (multipart) asking for JSON, then group the document's text
    items by the page in their provenance: exactly what the in-process path does, so a
    hit keeps its page number and its citation.
    """
    import requests
    with open(path, "rb") as fh:
        r = requests.post(
            url.rstrip("/") + "/v1/convert/file",
            files={"files": (Path(path).name, fh, "application/octet-stream")},
            data={"to_formats": "json", "do_ocr": "true" if ocr else "false"},
            timeout=1800)
    r.raise_for_status()
    payload = r.json() or {}
    doc = payload.get("document") or {}
    js = doc.get("json_content") or {}

    by_page: dict[int, list[str]] = {}
    for item in (js.get("texts") or []):
        text = (item.get("text") or "").strip()
        if not text:
            continue
        page_no = 1
        for prov in (item.get("prov") or []):
            page_no = prov.get("page_no") or page_no
            break
        by_page.setdefault(int(page_no), []).append(text)

    if by_page:
        pages = [{"page": p, "text": _clean("\n\n".join(by_page[p]))} for p in sorted(by_page)]
        return {}, pages, len(pages)

    md = doc.get("md_content") or doc.get("text_content") or ""      # no provenance
    if not str(md).strip():
        raise ValueError(f"the docling service returned status={payload.get('status')} "
                         f"with no text")
    return {}, [{"page": 1, "text": _clean(str(md))}], 1


def extract_pages(path, extractor=None, ocr=False):
    """Return (doc_meta, pages) for a PDF.

    pages is a list of {"page": 1-based int, "text": str}; doc_meta carries the
    sha1-based doc_id used everywhere else.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(str(p))

    ext = (extractor or "pymupdf").lower()
    note = ""
    if ext == "docling":
        used = None
        url = docling_service_url()
        if url:
            try:
                meta_extra, pages, npages = _extract_docling_service(p, url, ocr=ocr)
                used = "docling-serve"
            except Exception as ex:                       # noqa: BLE001
                # a configured service that is down must not block ingestion
                note = (f"the docling service at {url} could not be used "
                        f"({type(ex).__name__}: {ex}) — ")
        if used is None:
            if docling_available():
                meta_extra, pages, npages = _extract_docling(p, ocr=ocr)
                used = "docling"
            else:
                # Docling is the preferred extractor, but it is an optional extra:
                # never block ingestion on a missing package — use PyMuPDF and say so.
                meta_extra, pages, npages = _extract_pymupdf(p)
                used = "pymupdf"
                note += ("docling is the preferred extractor but is not installed and no "
                         "docling service is configured — used PyMuPDF (pip install docling, "
                         "or set the docling service URL)")
    else:
        ext = used = "pymupdf"
        meta_extra, pages, npages = _extract_pymupdf(p)

    sha1 = _sha1_file(p)
    meta = {
        "path": str(p.resolve()),
        "filename": p.name,
        "sha1": sha1,
        "npages": npages,
        "title": meta_extra.get("title", ""),
        "doc_id": sha1[:20],
        "extractor": used,
        "extractor_note": note,
    }
    return meta, pages


# ------------------------------------------------------------------ chunking
def chunk_pages(pages, size=None, overlap=None):
    """Split each page's text into overlapping chunks.

    Chunking never crosses a page boundary, so every chunk maps to exactly one
    page — which is what makes source references reliable.
    """
    size = size or config.CHUNK_SIZE
    overlap = overlap or config.CHUNK_OVERLAP
    if overlap >= size:
        overlap = size // 5

    chunks = []
    idx = 0
    for pg in pages:
        text = pg["text"]
        if not text:
            continue
        n = len(text)
        start = 0
        while start < n:
            end = min(start + size, n)
            chunks.append({
                "page": pg["page"], "chunk_index": idx,
                "start": start, "end": end, "text": text[start:end],
            })
            idx += 1
            if end >= n:
                break
            start = end - overlap
    return chunks
