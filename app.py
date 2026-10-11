#!/usr/bin/env python3
"""PDF Knowledge Base — Tkinter GUI, styled with the kmail.at design system.

Three tabs:
  * Add PDFs  — pick PDF files; text + embeddings are extracted and stored.
  * Search    — semantic / keyword / hybrid search with source references
                (file name + page) and one-click "open source PDF".
  * Library   — the indexed documents: open, export readable text, delete.

The visual language (Inter + JetBrains Mono, hairline borders, off-white /
off-black panels, strict monochrome) is the kmail.at token set — see
pdfkb/theme.py. A theme toggle switches the light/dark pair.
"""

from __future__ import annotations

import os
import platform
import queue
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
from tkinter import END, DISABLED, NORMAL, filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pdfkb.store import KB          # noqa: E402
from pdfkb.theme import Theme       # noqa: E402


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.kb: KB | None = None
        self._busy = False
        self._results = []
        self._doc_id_by_item = {}
        self._cur_result = None

        self.theme = Theme(root, mode="light")

        root.title("PDF Knowledge Base — Mohammed Kmail")
        root.geometry("1080x760")
        root.minsize(860, 580)
        root.configure(background=self.theme.t["void"])

        self._build()
        self.theme.apply()
        self.root.after(80, self._poll)
        self._init_kb()

    # --------------------------------------------------------------- layout
    def _build(self):
        self._build_header()
        self._hairline()

        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=18, pady=(10, 4))
        self.nb = nb

        self.tab_add = ttk.Frame(nb, padding=4)
        self.tab_search = ttk.Frame(nb, padding=4)
        self.tab_lib = ttk.Frame(nb, padding=4)
        nb.add(self.tab_add, text="ADD PDFS")
        nb.add(self.tab_search, text="SEARCH")
        nb.add(self.tab_lib, text="LIBRARY")

        self._build_add()
        self._build_search()
        self._build_library()

        self._hairline()
        bar = ttk.Frame(self.root, padding=(18, 6, 18, 10))
        bar.pack(fill="x")
        self.status = ttk.Label(bar, text="Initialising…", style="Micro.TLabel")
        self.status.pack(side="left")

    def _hairline(self):
        line = ttk.Frame(self.root, style="Line.TFrame", height=1)
        line.pack(fill="x")

    def _build_header(self):
        hdr = ttk.Frame(self.root, padding=(18, 18, 18, 14))
        hdr.pack(fill="x")
        left = ttk.Frame(hdr)
        left.pack(side="left", anchor="w")
        ttk.Label(left, text="PDF Knowledge Base", style="H1.TLabel").pack(anchor="w")
        ttk.Label(left, text="Extract · embed · search — every hit traceable to its source page",
                  style="Dim.TLabel").pack(anchor="w", pady=(2, 0))

        right = ttk.Frame(hdr)
        right.pack(side="right", anchor="ne")
        self.provider_lbl = ttk.Label(right, text="", style="Micro.TLabel", anchor="e")
        self.provider_lbl.pack(anchor="e")
        self.theme_btn = ttk.Button(right, text="◐  DARK", style="Ghost.TButton",
                                    command=self.toggle_theme)
        self.theme_btn.pack(anchor="e", pady=(8, 0))

    # ----------------------------------------------------------- Add PDFs tab
    def _build_add(self):
        f = self.tab_add
        ttk.Label(f, text="STEP 01 — INGEST", style="Micro.TLabel").pack(anchor="w", pady=(6, 2))
        ttk.Label(f, text="Add PDFs to the knowledge base", style="H2.TLabel").pack(anchor="w")
        ttk.Label(
            f, justify="left", wraplength=980, style="Dim.TLabel",
            text="Each PDF is converted to text per page, split into overlapping chunks, "
                 "and stored twice: as text in a SQLite knowledge base and as embeddings "
                 "in a vector store. Source file + page are kept on every chunk so results "
                 "stay traceable.",
        ).pack(anchor="w", pady=(4, 14))

        row = ttk.Frame(f)
        row.pack(anchor="w", pady=(0, 12))
        self.btn_add_files = ttk.Button(row, text="ADD PDF FILES…", style="Accent.TButton",
                                        command=self.add_files)
        self.btn_add_files.pack(side="left")
        self.btn_add_folder = ttk.Button(row, text="ADD FOLDER…", command=self.add_folder)
        self.btn_add_folder.pack(side="left", padx=8)
        self.force_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="Re-index if already added",
                        variable=self.force_var).pack(side="left", padx=14)

        self.progress = ttk.Progressbar(f, orient="horizontal", mode="determinate",
                                        length=1000, style="K.Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(0, 14))

        ttk.Label(f, text="LOG", style="Micro.TLabel").pack(anchor="w", pady=(0, 4))
        lf = ttk.Frame(f)
        lf.pack(fill="both", expand=True)
        self.log_box = tk.Text(lf, height=14, wrap="word", state=DISABLED)
        self.log_box.pack(fill="both", expand=True, side="left")
        sb = ttk.Scrollbar(lf, command=self.log_box.yview)
        sb.pack(side="right", fill="y")
        self.log_box.configure(yscrollcommand=sb.set)
        self.theme.register_text(self.log_box, "mono")

    # ------------------------------------------------------------ Search tab
    def _build_search(self):
        f = self.tab_search
        ttk.Label(f, text="STEP 02 — RETRIEVE", style="Micro.TLabel").pack(anchor="w", pady=(6, 2))
        ttk.Label(f, text="Search the extracted content", style="H2.TLabel").pack(anchor="w")

        top = ttk.Frame(f)
        top.pack(fill="x", pady=(12, 8))
        self.query_var = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.query_var, font=self.theme.font("body"))
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda e: self.do_search())
        self.search_entry = entry
        self.btn_search = ttk.Button(top, text="SEARCH", style="Accent.TButton", command=self.do_search)
        self.btn_search.pack(side="left", padx=(8, 0))

        opts = ttk.Frame(f)
        opts.pack(fill="x", pady=(0, 10))
        self.mode_var = tk.StringVar(value="hybrid")
        for val, lab in (("hybrid", "Hybrid"), ("semantic", "Semantic"), ("keyword", "Keyword")):
            ttk.Radiobutton(opts, text=lab, value=val, variable=self.mode_var).pack(side="left")
        ttk.Label(opts, text="RESULTS", style="Micro.TLabel").pack(side="left", padx=(24, 6))
        self.k_var = tk.StringVar(value="10")
        ttk.Spinbox(opts, from_=1, to=50, width=4, textvariable=self.k_var).pack(side="left")

        panes = ttk.Panedwindow(f, orient="vertical")
        panes.pack(fill="both", expand=True)

        resf = ttk.Frame(panes)
        cols = ("rank", "score", "source", "page", "snippet")
        heads = ("#", "SCORE", "SOURCE", "PAGE", "SNIPPET")
        self.tree = ttk.Treeview(resf, columns=cols, show="headings", height=9)
        for c, h, w in zip(cols, heads, (46, 90, 220, 56, 560)):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="w", stretch=(c == "snippet"))
        self.tree.pack(fill="both", expand=True, side="left")
        tsb = ttk.Scrollbar(resf, command=self.tree.yview)
        tsb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=tsb.set)
        self.tree.bind("<<TreeviewSelect>>", self._on_select_result)
        self.tree.bind("<Double-1>", lambda e: self.open_selected_source())
        panes.add(resf, weight=3)

        detf = ttk.Frame(panes)
        ttk.Label(detf, text="SELECTED RESULT — FULL CHUNK + PAGE CONTEXT",
                  style="Micro.TLabel").pack(anchor="w", pady=(8, 4))
        self.cite_label = ttk.Label(detf, text="", style="Dim.TLabel")
        self.cite_label.pack(anchor="w", pady=(0, 6))
        self.detail = tk.Text(detf, height=11, wrap="word")
        self.detail.pack(fill="both", expand=True, side="left")
        dsb = ttk.Scrollbar(detf, command=self.detail.yview)
        dsb.pack(side="right", fill="y")
        self.detail.configure(yscrollcommand=dsb.set, state=DISABLED)
        self.theme.register_text(self.detail, "mono")
        btns = ttk.Frame(detf)
        btns.pack(side="bottom", fill="x", pady=(8, 0))
        self.btn_open = ttk.Button(btns, text="OPEN SOURCE PDF", command=self.open_selected_source)
        self.btn_open.pack(side="left")
        ttk.Button(btns, text="COPY CITATION", command=self.copy_citation).pack(side="left", padx=8)
        ttk.Button(btns, text="COPY TEXT", command=self.copy_text).pack(side="left")
        panes.add(detf, weight=2)

    # ----------------------------------------------------------- Library tab
    def _build_library(self):
        f = self.tab_lib
        ttk.Label(f, text="STEP 03 — MANAGE", style="Micro.TLabel").pack(anchor="w", pady=(6, 2))
        ttk.Label(f, text="Indexed documents", style="H2.TLabel").pack(anchor="w", pady=(0, 10))

        cols = ("file", "pages", "chunks", "model", "added", "path")
        heads = ("FILE", "PAGES", "CHUNKS", "EMBEDDING", "ADDED", "PATH")
        self.dtree = ttk.Treeview(f, columns=cols, show="headings", height=15)
        for c, h, w in zip(cols, heads, (240, 60, 60, 240, 150, 300)):
            self.dtree.heading(c, text=h)
            self.dtree.column(c, width=w, anchor="w", stretch=(c == "path"))
        self.dtree.pack(fill="both", expand=True, side="left")
        sb = ttk.Scrollbar(f, command=self.dtree.yview)
        sb.pack(side="right", fill="y")
        self.dtree.configure(yscrollcommand=sb.set)
        self.dtree.bind("<Double-1>", lambda e: self.open_library_pdf())

        row = ttk.Frame(f)
        row.pack(side="bottom", fill="x", pady=(10, 0))
        ttk.Button(row, text="REFRESH", command=self.refresh_library).pack(side="left")
        ttk.Button(row, text="OPEN PDF", command=self.open_library_pdf).pack(side="left", padx=8)
        ttk.Button(row, text="EXPORT TEXT", command=self.export_selected).pack(side="left")
        ttk.Button(row, text="DELETE", command=self.delete_selected).pack(side="left", padx=8)
        ttk.Button(row, text="RESET KNOWLEDGE BASE…", style="Ghost.TButton",
                   command=self.reset_kb).pack(side="right")

    # --------------------------------------------------------------- theming
    def toggle_theme(self):
        self.theme.toggle()
        self.theme_btn.configure(text="◐  LIGHT" if self.theme.mode == "dark" else "◐  DARK")
        self.root.configure(background=self.theme.t["void"])

    # ----------------------------------------------------------- KB lifecycle
    def _init_kb(self):
        def work():
            return KB()
        def done(kb):
            self.kb = kb
            self._refresh_status()
            self.refresh_library()
            self.log(f"Knowledge base ready. Embedding provider: {kb.provider_info()}")
            self.log(f"Data dir: {kb.sqlite_path}")
        self._run(work, on_done=done, err_title="Knowledge base")

    def _refresh_status(self):
        if not self.kb:
            return
        s = self.kb.stats()
        self.provider_lbl.configure(text=s["provider"])
        self.status.configure(
            text=f"DOCUMENTS {s['documents']}   ·   CHUNKS {s['chunks']}   ·   "
                 f"VECTORS {s['vectors']}   ·   {self.kb.sqlite_path}")

    # ---------------------------------------------------------------- actions
    def add_files(self):
        if self._guard():
            return
        paths = filedialog.askopenfilenames(
            title="Select PDF files",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")])
        if paths:
            self._ingest(list(paths))

    def add_folder(self):
        if self._guard():
            return
        d = filedialog.askdirectory(title="Select a folder with PDFs")
        if not d:
            return
        paths = sorted(os.path.join(d, n) for n in os.listdir(d) if n.lower().endswith(".pdf"))
        if not paths:
            messagebox.showinfo("Add folder", "No PDF files found in that folder.")
            return
        self._ingest(paths)

    def _ingest(self, paths):
        total = len(paths)
        self._clear_log()
        self.log(f"Ingesting {total} file(s) …")
        force = self.force_var.get()
        self.progress.configure(mode="determinate", maximum=total, value=0)

        def work():
            summary = []
            for i, p in enumerate(paths):
                self.q.put(("progress", {"done": i, "total": total,
                                         "msg": f"({i + 1}/{total}) {os.path.basename(p)}"}))
                try:
                    r = self.kb.add_document(p, force=force,
                                             progress=lambda d, t, m, i=i:
                                             self.q.put(("progress", {"done": i, "total": total,
                                                                      "msg": m})))
                except Exception as ex:  # noqa: BLE001
                    self.q.put(("log", {"msg": f"  ERROR {os.path.basename(p)}: {ex}"}))
                    summary.append({"status": "error", "filename": os.path.basename(p)})
                    continue
                self.q.put(("log", {"msg": f"  {r['status']}: {r['filename']} "
                                           f"({r.get('npages', 0)} pages, {r['nchunks']} chunks)"}))
                summary.append(r)
            return summary

        def done(res):
            self.progress.configure(value=self.progress["maximum"])
            added = sum(1 for r in res if r["status"] == "added")
            skipped = sum(1 for r in res if r["status"] == "skipped")
            self.log(f"Done: {added} added, {skipped} skipped, "
                     f"{len(res) - added - skipped} other.")
            self._refresh_status()
            self.refresh_library()

        self._start(work, done)

    def do_search(self):
        if self._guard():
            return
        query = self.query_var.get().strip()
        if not query:
            return
        mode = self.mode_var.get()
        try:
            k = max(1, int(self.k_var.get()))
        except ValueError:
            k = 10
        self._clear_tree()

        def work():
            return self.kb.search(query, k=k, mode=mode)

        def done(hits):
            self._results = hits or []
            for i, h in enumerate(self._results, 1):
                if h.get("sim") is not None:
                    score = f"{h['sim']:.3f}"
                elif h.get("kw") is not None:
                    score = f"kw {h['kw']:.2f}"
                else:
                    score = f"{h['score']:.3f}"
                snippet = " ".join(h["text"].split())[:180]
                self.tree.insert("", "end", iid=h["chunk_id"],
                                 values=(i, score, h["filename"], h["page"], snippet))
            if not self._results:
                self._set_detail("No results. Add PDFs first, or try another query/mode.")
            self.log(f"{len(self._results)} result(s) for: {query} [{mode}]")

        self._start(work, done)

    def _on_select_result(self, _evt=None):
        sel = self.tree.selection()
        if not sel:
            return
        cid = sel[0]
        res = next((r for r in self._results if r["chunk_id"] == cid), None)
        if not res:
            return
        self._cur_result = res
        self.cite_label.configure(text=f"Citation:  {res['citation']}")
        parts = [f"SOURCE  {res['filename']}   (page {res['page']})",
                 f"PATH    {res['path']}",
                 f"CHUNK   {res['chunk_id']}",
                 "-" * 76,
                 res["text"]]
        try:
            ctx = self.kb.page_context(res["doc_id"], res["page"])
            parts.append("")
            parts.append(f"—— page {res['page']} context ——")
            for c in ctx:
                parts.append(f"[p.{c['page']}] {' '.join(c['text'].split())}")
        except Exception:
            pass
        self._set_detail("\n".join(parts))

    def open_selected_source(self):
        if self._cur_result:
            self._open_path(self._cur_result["path"])

    def copy_citation(self):
        if self._cur_result:
            self._clipboard(self._cur_result["citation"])

    def copy_text(self):
        if self._cur_result:
            self._clipboard(self._cur_result["text"])

    # -------------------------------------------------------------- library
    def refresh_library(self):
        if not self.kb:
            return
        for i in self.dtree.get_children():
            self.dtree.delete(i)
        self._doc_id_by_item.clear()
        for d in self.kb.documents():
            added = time.strftime("%Y-%m-%d %H:%M", time.localtime(d["added_at"])) if d["added_at"] else ""
            model = f"{d['embed_provider']}:{d['embed_model']} ({d['embed_dim']}d)"
            iid = self.dtree.insert("", "end",
                                    values=(d["filename"], d["npages"], d["nchunks"],
                                            model, added, d["path"]))
            self._doc_id_by_item[iid] = d["doc_id"]
        self._refresh_status()

    def _selected_doc(self):
        sel = self.dtree.selection()
        if not sel:
            return None, None
        iid = sel[0]
        return self._doc_id_by_item.get(iid), self.dtree.item(iid, "values")

    def open_library_pdf(self):
        _, vals = self._selected_doc()
        if vals:
            self._open_path(vals[5])

    def export_selected(self):
        if self._guard():
            return
        doc_id, _ = self._selected_doc()
        if not doc_id:
            return
        try:
            path = self.kb.export_text(doc_id)
            self.log(f"Exported text -> {path}")
            messagebox.showinfo("Export text", f"Written:\n{path}")
        except Exception as ex:  # noqa: BLE001
            messagebox.showerror("Export text", str(ex))

    def delete_selected(self):
        if self._guard():
            return
        doc_id, vals = self._selected_doc()
        if not doc_id:
            return
        if not messagebox.askyesno("Delete", f"Remove '{vals[0]}' from the knowledge base?"):
            return

        def work():
            self.kb.delete_document(doc_id)
            return True

        def done(_):
            self.log(f"Deleted {vals[0]}")
            self.refresh_library()

        self._start(work, done)

    def reset_kb(self):
        if self._guard():
            return
        if not messagebox.askyesno("Reset", "Delete ALL documents, chunks and vectors?"):
            return

        def work():
            self.kb.reset()
            return True

        def done(_):
            self.log("Knowledge base reset.")
            self._clear_tree()
            self.refresh_library()

        self._start(work, done)

    # ------------------------------------------------------------- plumbing
    def _guard(self):
        if self.kb is None:
            messagebox.showinfo("Please wait", "The knowledge base is still initialising.")
            return True
        if self._busy:
            return True
        return False

    def _start(self, work, on_done):
        self._busy = True
        self._set_buttons(DISABLED)

        def wrapped():
            try:
                res = work()
            except Exception as ex:  # noqa: BLE001
                self.q.put(("result", {"cb": None, "res": None, "err": ex,
                                       "tb": traceback.format_exc()}))
                return
            self.q.put(("result", {"cb": on_done, "res": res, "err": None, "tb": None}))

        threading.Thread(target=wrapped, daemon=True).start()

    def _run(self, fn, on_done=None, err_title="Error"):
        def work():
            try:
                res = fn()
            except Exception as ex:  # noqa: BLE001
                self.q.put(("result", {"cb": None, "res": None, "err": ex,
                                       "tb": traceback.format_exc(), "title": err_title}))
                return
            self.q.put(("result", {"cb": on_done, "res": res, "err": None, "tb": None}))

        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind, kw = self.q.get_nowait()
                if kind == "progress":
                    self.progress.configure(maximum=max(1, kw["total"]),
                                            value=min(kw["done"], kw["total"]))
                    if kw.get("msg"):
                        self.log(kw["msg"])
                elif kind == "log":
                    self.log(kw["msg"])
                elif kind == "busy":
                    self._busy = kw["state"]
                    self._set_buttons(NORMAL)
                elif kind == "result":
                    if kw["err"] is not None:
                        self.log(f"ERROR: {kw['err']}")
                        self._busy = False
                        self._set_buttons(NORMAL)
                        messagebox.showerror(kw.get("title", "Error"),
                                             f"{type(kw['err']).__name__}: {kw['err']}\n\n{kw['tb']}")
                    else:
                        try:
                            if kw["cb"]:
                                kw["cb"](kw["res"])
                        finally:
                            self._busy = False
                            self._set_buttons(NORMAL)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _set_buttons(self, state):
        for b in (getattr(self, "btn_add_files", None), getattr(self, "btn_add_folder", None),
                  getattr(self, "btn_search", None), getattr(self, "btn_open", None)):
            if b is not None:
                try:
                    b.configure(state=state)
                except tk.TclError:
                    pass

    def _set_detail(self, text):
        self.detail.configure(state=NORMAL)
        self.detail.delete("1.0", END)
        self.detail.insert("1.0", text)
        self.detail.configure(state=DISABLED)

    def _clear_tree(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        self._results = []
        self._cur_result = None
        self.cite_label.configure(text="")

    def log(self, msg):
        self.log_box.configure(state=NORMAL)
        self.log_box.insert(END, msg + "\n")
        self.log_box.see(END)
        self.log_box.configure(state=DISABLED)

    def _clear_log(self):
        self.log_box.configure(state=NORMAL)
        self.log_box.delete("1.0", END)
        self.log_box.configure(state=DISABLED)

    def _clipboard(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update_idletasks()

    def _open_path(self, path):
        if not path or not os.path.exists(path):
            messagebox.showwarning("Open PDF", f"File not found:\n{path}")
            return
        try:
            if platform.system() == "Darwin":
                subprocess.Popen(["open", path])
            elif platform.system() == "Windows":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as ex:  # noqa: BLE001
            messagebox.showerror("Open PDF", str(ex))


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
