"""Configuration for the Knowledgebases tool."""

import os
from pathlib import Path

# Repo root (folder that contains the pdfkb package).
BASE_DIR = Path(__file__).resolve().parent.parent

# Everything the tool writes lives here (override with PDFKB_DATA_DIR).
DATA_DIR = Path(os.environ.get("PDFKB_DATA_DIR", BASE_DIR / "kb_data"))
SQLITE_PATH = DATA_DIR / "kb.sqlite"          # knowledge base: text + metadata
CHROMA_DIR = DATA_DIR / "chroma"              # vector store: embeddings
TEXT_DIR = DATA_DIR / "text"                  # human-readable text exports
SOURCES_DIR = DATA_DIR / "sources"            # uploaded PDFs (web app keeps a copy)
COLLECTION = "pdf_chunks"                     # Chroma collection name

# Chunking (character-based, per page — keeps page attribution exact).
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200

# Embedding backend. Ollama is preferred because it is local and fast.
OLLAMA_URL = os.environ.get("PDFKB_OLLAMA_URL", "http://localhost:11434")
OLLAMA_EMBED_MODEL = os.environ.get("PDFKB_EMBED_MODEL", "qwen3-embedding:0.6b")
ST_MODEL = os.environ.get("PDFKB_ST_MODEL", "all-MiniLM-L6-v2")
# Extraction can be delegated to a docling-serve container (see docker-compose.yml):
# blank = use the in-process Docling, or PyMuPDF, exactly as before.
DOCLING_URL = os.environ.get("PDFKB_DOCLING_URL", "").strip()
# The vector store can be chosen entirely from the environment, so a compose stack is
# declarative: PDFKB_VECTOR_BACKEND=qdrant PDFKB_VECTOR_URL=http://qdrant:6333
VECTOR_BACKEND = os.environ.get("PDFKB_VECTOR_BACKEND", "").strip()
VECTOR_URL = os.environ.get("PDFKB_VECTOR_URL", "").strip()
VECTOR_COLLECTION = os.environ.get("PDFKB_VECTOR_COLLECTION", "").strip()
HASH_DIM = 512                                # dimension of the offline fallback

# Chroma add() batches.
CHROMA_BATCH = 500


def ensure_dirs():
    for d in (DATA_DIR, CHROMA_DIR, TEXT_DIR, SOURCES_DIR):
        Path(d).mkdir(parents=True, exist_ok=True)
