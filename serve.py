#!/usr/bin/env python3
"""Run the PDF Knowledge Base web app.

    python3 serve.py [--host 127.0.0.1] [--port 8765] [--reload]
"""

import argparse

import uvicorn


def main():
    ap = argparse.ArgumentParser(description="PDF Knowledge Base web app")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--reload", action="store_true")
    a = ap.parse_args()
    uvicorn.run("pdfkb.web:app", host=a.host, port=a.port, reload=a.reload)


if __name__ == "__main__":
    main()
