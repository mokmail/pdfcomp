"""Knowledgebases — turn PDFs into a searchable, source-referenced knowledge base.

Pipeline:  PDF -> per-page text (PyMuPDF) -> overlapping chunks ->
SQLite knowledge base (text + metadata) + Chroma vector store (embeddings).

Every stored chunk keeps its source document id, file name, 1-based page
number, chunk index and character offsets, so any search hit can be traced
back to the exact place it came from.
"""

from .config import DATA_DIR, SQLITE_PATH, CHROMA_DIR, TEXT_DIR  # noqa: F401
from .store import KB  # noqa: F401
from .embeddings import get_embedder  # noqa: F401
from .extract import extract_pages, chunk_pages  # noqa: F401

__version__ = "0.1.0"
