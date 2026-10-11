"""Generate sample PDFs for testing / demoing the knowledge base."""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf

PAGES = [
    ("Quarterly Infrastructure Report", [
        "The federal geodetic service operates a nationwide network of GNSS "
        "reference stations. This quarter 148 stations were upgraded to "
        "multi-frequency receivers with real-time kinematic correction streams.",
        "Data availability across the network reached 99.2 percent. Downtime was "
        "mainly caused by power outages at three remote alpine sites.",
        "The transformation parameters for the national coordinate reference "
        "frame were recomputed using the latest epoch. Horizontal shifts stay "
        "below two centimetres for the whole territory.",
    ]),
    ("Vector Search and Retrieval", [
        "A vector store keeps high dimensional embeddings and answers nearest "
        "neighbour queries. Cosine similarity is the usual metric for text, "
        "because it ignores magnitude and compares direction only.",
        "Hybrid retrieval combines dense semantic embeddings with sparse keyword "
        "matching such as BM25, then fuses the two ranked lists with reciprocal "
        "rank fusion. It improves recall for rare terms and acronyms.",
        "Chunking strategy matters: overlapping windows of roughly one thousand "
        "characters preserve context across page boundaries while keeping each "
        "chunk small enough for precise retrieval.",
    ]),
    ("Cadastral Data Quality", [
        "Quality control of the digital cadastre follows the ISO 19157 framework. "
        "Completeness, logical consistency and positional accuracy are measured "
        "against the register of parcel boundaries.",
        "Positional accuracy of the surveyed boundary points is within five "
        "centimetres at the 95 percent confidence level for urban parcels.",
    ]),
]


def make(path, title="Sample Knowledge Base Document"):
    doc = pymupdf.open()
    doc.set_metadata({"title": title, "author": "Knowledgebases sample"})
    for heading, paragraphs in PAGES:
        page = doc.new_page()
        y = 72
        page.insert_text((72, y), heading, fontsize=16)
        y += 28
        for para in paragraphs:
            words = para.split()
            line = ""
            for w in words:
                if len(line) + len(w) > 88:
                    page.insert_text((72, y), line, fontsize=11)
                    y += 16
                    line = w
                else:
                    line = (line + " " + w).strip()
            if line:
                page.insert_text((72, y), line, fontsize=11)
                y += 16
            y += 8
    doc.save(path)
    doc.close()
    return path


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "sample.pdf"
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    make(out)
    print("wrote", out)
