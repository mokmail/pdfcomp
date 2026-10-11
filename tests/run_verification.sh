#!/usr/bin/env bash
# Verification for Knowledgebases: an isolated instance, the web probe, and the
# library-level read-only checks. The real knowledge base is never touched — every run
# gets its own data directory and port.
#
#   bash tests/run_verification.sh              (VERIFY_PORT=8899 by default)
#   PDFKB_PYTHON=/path/to/python3 bash tests/run_verification.sh   # pin the interpreter
set -euo pipefail
cd "$(dirname "$0")/.."

PORT="${VERIFY_PORT:-8899}"
ISO="$(mktemp -d -t knowledgebases-verify-)"
SRV=""
cleanup() { [ -n "$SRV" ] && kill "$SRV" 2>/dev/null || true; rm -rf "$ISO"; }
trap cleanup EXIT

# The app must run in an interpreter that can import its own dependencies — a wrapper
# (`hermes verify`, an IDE task, a CI image) may well have a different one first on PATH,
# and then the backend checks would fail for a reason that has nothing to do with the code.
PY=""
for c in "${PDFKB_PYTHON:-}" "$(command -v python3)" "${HOME}/.venv/bin/python3" /usr/local/bin/python3; do
  [ -n "$c" ] || continue
  if "$c" -c "import starlette, chromadb, pymupdf" 2>/dev/null; then PY="$c"; break; fi
done
[ -n "$PY" ] || { echo "no interpreter here can import the app (starlette, chromadb, pymupdf)"; \
                  echo "  python3 -m pip install -r requirements.txt"; exit 1; }
echo "interpreter: $PY ($("$PY" -V 2>&1))"
for m in hnswlib qdrant_client; do
  "$PY" -c "import $m" 2>/dev/null || echo "  note: $m is missing from that interpreter — the $m checks will fail." \
    " Install it with: $PY -m pip install -r tests/requirements-dev.txt"
done

echo
echo "== syntax =="
"$PY" -m compileall -q pdfkb serve.py app.py tests && echo "  python OK"
command -v node >/dev/null && { node --check web/app.js && echo "  javascript OK"; } || echo "  (node not installed — skipping the javascript check)"

echo
echo "== a read-only knowledge base refuses every write (library level, temp store) =="
PDFKB_DATA_DIR="$ISO/unit" "$PY" tests/verify_library_readonly.py

echo
echo "== the web app, on an isolated instance (port $PORT) =="
PDFKB_DATA_DIR="$ISO/app" "$PY" serve.py --port "$PORT" > "$ISO/server.log" 2>&1 &
SRV=$!
for _ in $(seq 1 60); do curl -sf -o /dev/null "http://127.0.0.1:$PORT/api/stats" && break; sleep 1; done
curl -sf -o /dev/null "http://127.0.0.1:$PORT/api/stats" || { echo "the app never came up:"; tail -20 "$ISO/server.log"; exit 1; }

PDFKB_BASE="http://127.0.0.1:$PORT" "$PY" tests/verify_web.py | tee "$ISO/probe.txt"
grep -q "^RESULT: ALL PASS" "$ISO/probe.txt" || { echo; echo "the web probe reported failures"; exit 1; }

echo
echo "verification: ALL PASS (the app was never pointed at the real knowledge base)"
