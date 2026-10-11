"""Command line interface for pdfkb (also handy for scripting / testing).

    python -m pdfkb.cli ingest FILE.pdf [FILE2.pdf ...] [--force]
    python -m pdfkb.cli search "your question" [-k 10] [--mode hybrid]
    python -m pdfkb.cli list
    python -m pdfkb.cli show <chunk_id>
    python -m pdfkb.cli export <doc_id>
    python -m pdfkb.cli delete <doc_id>
    python -m pdfkb.cli reset --yes
"""

from __future__ import annotations

import argparse
import sys

from .store import KB


def _kb():
    kb = KB()
    return kb


def cmd_ingest(args):
    kb = _kb()
    for path in args.files:
        def prog(done, total, msg):
            print(f"  [{done}/{total}] {msg}")
        try:
            res = kb.add_document(path, force=args.force, progress=prog)
            print(f"=> {res['status']}: {res['filename']} "
                  f"({res.get('npages', 0)} pages, {res['nchunks']} chunks)")
        except Exception as ex:  # noqa: BLE001
            print(f"!! {path}: {type(ex).__name__}: {ex}")
    print(kb.stats())


def cmd_search(args):
    kb = _kb()
    hits = kb.search(args.query, k=args.k, mode=args.mode)
    if not hits:
        print("no results")
        return
    for i, h in enumerate(hits, 1):
        if h.get("sim") is not None:
            score = f"sim={h['sim']:.3f}"
        elif h.get("kw") is not None:
            score = f"kw={h['kw']:.3f}"
        else:
            score = f"score={h['score']:.4f}"
        snippet = " ".join(h["text"].split())[:160]
        print(f"{i:2d}. [{score}] {h['citation']}")
        print(f"    {snippet}")
        print()


def cmd_list(args):
    kb = _kb()
    for d in kb.documents():
        print(f"{d['doc_id']}  {d['filename']}  pages={d['npages']} chunks={d['nchunks']}  "
              f"{d['embed_provider']}:{d['embed_model']}")
    print(kb.stats())


def cmd_show(args):
    kb = _kb()
    row = kb.conn.execute("SELECT * FROM chunks WHERE chunk_id=?", (args.chunk_id,)).fetchone()
    if not row:
        print("not found")
        return
    print(f"chunk {row['chunk_id']}  doc={row['doc_id']}  page={row['page']}  "
          f"offsets={row['start']}-{row['end']}")
    print("-" * 60)
    print(row["text"])


def cmd_export(args):
    kb = _kb()
    print(kb.export_text(args.doc_id))


def cmd_delete(args):
    kb = _kb()
    kb.delete_document(args.doc_id)
    print("deleted", args.doc_id)


def cmd_reset(args):
    if not args.yes:
        print("refusing without --yes")
        return
    KB().reset()
    print("knowledge base reset")


def build_parser():
    p = argparse.ArgumentParser(prog="knowledgebases",
                                description="Knowledgebases — PDF -> knowledge base -> semantic search")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("ingest", help="extract PDF(s) into the knowledge base")
    a.add_argument("files", nargs="+")
    a.add_argument("--force", action="store_true", help="re-index even if already present")
    a.set_defaults(func=cmd_ingest)

    s = sub.add_parser("search", help="search the knowledge base")
    s.add_argument("query")
    s.add_argument("-k", type=int, default=10)
    s.add_argument("--mode", choices=["hybrid", "semantic", "keyword"], default="hybrid")
    s.set_defaults(func=cmd_search)

    l = sub.add_parser("list", help="list indexed documents")
    l.set_defaults(func=cmd_list)

    sh = sub.add_parser("show", help="print one chunk")
    sh.add_argument("chunk_id")
    sh.set_defaults(func=cmd_show)

    e = sub.add_parser("export", help="write readable text export for a document")
    e.add_argument("doc_id")
    e.set_defaults(func=cmd_export)

    d = sub.add_parser("delete", help="remove a document")
    d.add_argument("doc_id")
    d.set_defaults(func=cmd_delete)

    r = sub.add_parser("reset", help="wipe the knowledge base")
    r.add_argument("--yes", action="store_true")
    r.set_defaults(func=cmd_reset)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
