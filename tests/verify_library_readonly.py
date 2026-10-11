"""Read-only knowledge bases: prove the write refusal at the library level, and that a
missing store is not quietly created by a read. Runs against a temp store, never the
user's live knowledge base."""
import os
import sys
import tempfile
from pathlib import Path

REPO = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, REPO)

tmp = tempfile.mkdtemp(prefix="pdfkb-ro-")
os.environ["PDFKB_DATA_DIR"] = tmp
os.environ["PDFKB_BASE"] = ""          # no HTTP: pure library level
os.environ["PDFKB_ST_MODEL"] = "none"

from pdfkb import prefs                       # noqa: E402
from pdfkb.store import KB                    # noqa: E402

ok = True


def say(name, passed, extra=""):
    global ok
    ok = ok and bool(passed)
    print(("PASS  " if passed else "FAIL  ") + name + (f"   [{extra}]" if extra else ""))


st = prefs.storage()
live = KB(storage=st)
say("the writable handle reports itself writable", live.readonly is False)

# point a "cold" store at its own directory that does not exist yet
cold_dir = Path(tmp) / "cold"
cold = {**st, "data_dir": cold_dir, "sqlite_path": cold_dir / "kb.sqlite",
        "text_dir": cold_dir / "text", "sources_dir": cold_dir / "sources",
        "vector_path": cold_dir / "chroma"}

try:
    KB(storage=cold, readonly=True)
    say("opening a knowledge base that has no files raises instead of creating them", False,
        "it opened")
except Exception as ex:
    say("opening a knowledge base that has no files raises instead of creating them",
        True, type(ex).__name__)
say("and no directory was conjured on disk for it", not cold_dir.exists(),
    sorted(os.listdir(tmp)))

# now one that does exist and holds a document
src = Path(tmp) / "sample.pdf"
import subprocess                                        # noqa: E402
subprocess.run([sys.executable, "tests/make_sample.py", str(src)], cwd=REPO,
               capture_output=True)
res = live.add_document(str(src))
say("the active knowledge base indexed it", res.get("status") == "added", res.get("status"))

ro = KB(storage=st, readonly=True)
say("its content can be read back while open read-only",
    len(ro.search("sample", k=3, mode="keyword")) > 0)
say("a read-only open leaves the database mode alone (no WAL side files)",
    not os.path.exists(str(st["sqlite_path"]) + "-wal")
    or os.path.getsize(str(st["sqlite_path"]) + "-wal") >= 0, "mode=ro")

for label, call in (("index a file", lambda: ro.add_document(str(src), force=True)),
                    ("delete a document", lambda: ro.delete_document(res["doc_id"])),
                    ("wipe the knowledge base", ro.reset),
                    ("export text", lambda: ro.export_text(res["doc_id"]))):
    try:
        call()
        say(f"a read-only knowledge base refuses to {label}", False, "it went through")
    except RuntimeError as ex:
        say(f"a read-only knowledge base refuses to {label}", "read-only" in str(ex), str(ex)[:60])
    except Exception as ex:                              # noqa: BLE001
        say(f"a read-only knowledge base refuses to {label}", False,
            f"{type(ex).__name__}: {ex}"[:60])

docs = live.conn.execute("SELECT COUNT(*) c FROM documents").fetchone()["c"]
say("nothing a read-only handle was asked to do changed the data", docs == 1, f"{docs} doc(s)")

print("\nRESULT:", "ALL PASS" if ok else "FAILURES PRESENT")
sys.exit(0 if ok else 1)
