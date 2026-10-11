"""Headless GUI smoke test: builds all widgets (hidden window), waits for the
knowledge base to initialise, runs a real search through the UI widgets and
verifies the result list + detail panel are populated. No window is shown."""

import os
import sys
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import App  # noqa: E402


def pump(root, seconds=0.1):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        root.update_idletasks()
        time.sleep(0.02)


def main():
    root = tk.Tk()
    root.withdraw()                      # keep it off-screen
    app = App(root)

    for _ in range(100):
        pump(root, 0.1)
        if app.kb is not None:
            break
    assert app.kb is not None, "knowledge base did not initialise"
    print("KB ready:", app.kb.provider_info())

    # exercise tabs exist
    print("tabs:", [app.nb.tab(i, "text").strip() for i in app.nb.tabs()])

    # run a search through the widgets
    app.query_var.set("hybrid retrieval with embeddings and cosine similarity")
    app.mode_var.set("hybrid")
    app.do_search()
    for _ in range(100):
        pump(root, 0.1)
        if app.tree.get_children():
            break

    rows = app.tree.get_children()
    print("result rows:", len(rows))
    for r in rows[:3]:
        print("  ", app.tree.item(r, "values"))

    if rows:
        app.tree.selection_set(rows[0])
        pump(root, 0.2)
        app._on_select_result()
        pump(root, 0.2)
        detail = app.detail.get("1.0", "end")
        print("detail chars:", len(detail))
        assert "SOURCE" in detail and app._cur_result is not None
        print("citation:", app.cite_label.cget("text"))

    print("library rows:", len(app.dtree.get_children()))
    print("status:", app.status.cget("text"))
    root.destroy()
    print("SMOKE TEST OK")


if __name__ == "__main__":
    main()
