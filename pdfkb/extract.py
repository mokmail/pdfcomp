"""PDF text extraction and chunking (PyMuPDF based)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pymupdf

from . import config


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
    text = re.sub(r"[ \t]+\n", "\n", text)      # trailing spaces
    text = re.sub(r"\n{3,}", "\n\n", text)       # collapse blank runs
    return text.strip()


def extract_pages(path):
    """Return (doc_meta, pages) for a PDF.

    pages is a list of {"page": 1-based int, "text": str}.
    doc_meta carries the sha1-based doc_id used everywhere else.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(str(p))

    doc = pymupdf.open(str(p))
    try:
        pages = []
        for i in range(doc.page_count):
            page = doc.load_page(i)
            pages.append({"page": i + 1, "text": _clean(page.get_text("text"))})
        md = doc.metadata or {}
        sha1 = _sha1_file(p)
        meta = {
            "path": str(p.resolve()),
            "filename": p.name,
            "sha1": sha1,
            "npages": doc.page_count,
            "title": (md.get("title") or "").strip(),
            "doc_id": sha1[:20],
        }
    finally:
        doc.close()
    return meta, pages


def chunk_pages(pages, size=None, overlap=None):
    """Split each page's text into overlapping chunks.

    Chunking never crosses a page boundary, so every chunk maps to exactly
    one page — which is what makes source references reliable.
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
                "page": pg["page"],
                "chunk_index": idx,
                "start": start,
                "end": end,
                "text": text[start:end],
            })
            idx += 1
            if end >= n:
                break
            start = end - overlap
    return chunks
