#!/bin/bash
# Double-clickable launcher for macOS: starts the PDF Knowledge Base GUI.
cd "$(dirname "$0")" || exit 1
exec python3 app.py
