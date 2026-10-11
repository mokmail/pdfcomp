"""kmail.at design system for the Tkinter GUI.

Tokens are lifted verbatim from the live site's CSS custom properties
(RGB-triplet variables, *true monochrome* — no hue anywhere) and applied to
ttk widget styles, so the tool reads as part of kmail.at: Inter for display,
JetBrains Mono for micro-labels, hairline borders, off-white/off-black panels.

    :root     --bg-void 255 255 255  --text-ghost 12 12 13  ...
    .dark     --bg-void  10  10  10  --text-ghost 245 245 245 ...
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

# --- tokens (RGB triplets from kmail.at, as hex for Tk) ---
LIGHT = {
    "void": "#ffffff",       # --bg-void      255 255 255
    "panel": "#fafafa",      # --bg-panel     250 250 250
    "panel_hi": "#f3f3f4",   # --bg-panel-hi  243 243 244
    "ghost": "#0c0c0d",      # --text-ghost    12  12  13
    "dim": "#52525b",        # --text-dim      82  82  91
    "faint": "#8c8c94",      # --text-faint   140 140 148
    "line": "#e4e4e7",       # --line-dim     228 228 231
    "line_hi": "#babac0",    # --line-hi      186 186 192
}
DARK = {
    "void": "#0a0a0a",       # --bg-void       10  10  10
    "panel": "#111111",      # --bg-panel      17  17  17
    "panel_hi": "#1a1a1a",   # --bg-panel-hi   26  26  26
    "ghost": "#f5f5f5",      # --text-ghost   245 245 245
    "dim": "#a8a8ad",        # --text-dim     168 168 173
    "faint": "#74747a",      # --text-faint   116 116 122
    "line": "#242426",       # --line-dim      36  36  38
    "line_hi": "#4a4a4e",    # --line-hi       74  74  78
}

# Font stacks mirroring --font-display / --font-mono, with local fallbacks.
DISPLAY_PREFS = ["Inter", "Inter Display", "Helvetica Neue", "Helvetica",
                 "SF Pro Text", "Arial"]
MONO_PREFS = ["JetBrains Mono", "SF Mono", "Menlo", "Monaco", "PT Mono",
              "Andale Mono", "Courier New"]


def _pick(families, prefs, default):
    for p in prefs:
        if p in families:
            return p
    return default


class Theme:
    def __init__(self, root: tk.Tk, mode="light"):
        self.root = root
        self.mode = mode
        fams = set(tkfont.families(root))
        self.display = _pick(fams, DISPLAY_PREFS, "Helvetica")
        self.mono = _pick(fams, MONO_PREFS, "Courier New")
        self.t = dict(DARK if mode == "dark" else LIGHT)
        self.style = ttk.Style(root)
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass
        self.text_widgets = []      # (widget, font-kind)
        self.bg_widgets = []        # widgets needing a raw bg (lines/frames)

    # ------------------------------------------------------------ type scale
    def font(self, kind):
        d, m = self.display, self.mono
        return {
            "h1": (d, 24, "bold"),
            "h2": (d, 16, "bold"),
            "body": (d, 12),
            "small": (d, 11),
            "micro": (m, 9),
            "microb": (m, 9, "bold"),
            "mono": (m, 11),
            "mono_s": (m, 10),
        }[kind]

    # --------------------------------------------------------------- toggling
    def toggle(self):
        self.set_mode("dark" if self.mode == "light" else "light")

    def set_mode(self, mode):
        self.mode = mode
        self.t = dict(DARK if mode == "dark" else LIGHT)
        self.apply()
        for widget, kind in self.text_widgets:
            self.style_text(widget, kind)
        for widget, key in self.bg_widgets:
            try:
                widget.configure(background=self.t[key])
            except tk.TclError:
                pass

    def register_text(self, widget, kind="mono"):
        self.text_widgets.append((widget, kind))
        self.style_text(widget, kind)

    def register_bg(self, widget, key="line"):
        self.bg_widgets.append((widget, key))
        widget.configure(background=self.t[key])

    def style_text(self, widget, kind="mono"):
        t = self.t
        fg = t["ghost"] if kind == "mono" else t["dim"]
        widget.configure(background=t["panel"], foreground=fg,
                         insertbackground=t["ghost"], selectbackground=t["line_hi"],
                         selectforeground=t["ghost"], font=self.font(kind),
                         relief="flat", borderwidth=0, padx=10, pady=8,
                         highlightthickness=1, highlightbackground=t["line"],
                         highlightcolor=t["line"])

    # ----------------------------------------------------------------- styles
    def apply(self):
        t, s = self.t, self.style

        s.configure(".", background=t["void"], foreground=t["ghost"], font=self.font("body"))
        s.configure("TFrame", background=t["void"])
        s.configure("Panel.TFrame", background=t["panel"])
        s.configure("Line.TFrame", background=t["line"])

        s.configure("TLabel", background=t["void"], foreground=t["ghost"], font=self.font("body"))
        s.configure("H1.TLabel", background=t["void"], foreground=t["ghost"], font=self.font("h1"))
        s.configure("H2.TLabel", background=t["void"], foreground=t["ghost"], font=self.font("h2"))
        s.configure("Dim.TLabel", background=t["void"], foreground=t["dim"], font=self.font("small"))
        s.configure("Micro.TLabel", background=t["void"], foreground=t["faint"], font=self.font("micro"))

        # buttons — flat, hairline border, uppercase-mono
        s.configure("TButton", background=t["void"], foreground=t["ghost"],
                    font=self.font("micro"), borderwidth=1, relief="solid",
                    bordercolor=t["line_hi"], focusthickness=0, padding=(14, 8))
        s.map("TButton",
              background=[("active", t["panel_hi"]), ("pressed", t["panel_hi"]),
                          ("disabled", t["void"])],
              foreground=[("disabled", t["faint"])],
              bordercolor=[("active", t["ghost"]), ("disabled", t["line"])])
        s.configure("Accent.TButton", background=t["ghost"], foreground=t["void"],
                    bordercolor=t["ghost"], font=self.font("microb"))
        s.map("Accent.TButton",
              background=[("active", t["dim"]), ("pressed", t["dim"])],
              foreground=[("active", t["void"])], bordercolor=[("active", t["dim"])])
        s.configure("Ghost.TButton", background=t["void"], foreground=t["faint"],
                    bordercolor=t["line"], font=self.font("micro"), padding=(10, 6))
        s.map("Ghost.TButton", foreground=[("active", t["ghost"])],
              bordercolor=[("active", t["line_hi"])], background=[("active", t["void"])])

        s.configure("TEntry", fieldbackground=t["void"], foreground=t["ghost"],
                    bordercolor=t["line_hi"], insertcolor=t["ghost"], padding=10,
                    relief="flat", borderwidth=1)
        s.map("TEntry", bordercolor=[("focus", t["ghost"])])

        s.configure("TNotebook", background=t["void"], borderwidth=0, tabmargins=(0, 6, 0, 0))
        s.configure("TNotebook.Tab", background=t["void"], foreground=t["faint"],
                    font=self.font("micro"), padding=(18, 11), borderwidth=0)
        s.map("TNotebook.Tab", background=[("selected", t["void"])],
              foreground=[("selected", t["ghost"])])

        s.configure("TSeparator", background=t["line"])
        s.configure("Line.Horizontal.TSeparator", background=t["line"])

        s.configure("Treeview", background=t["void"], fieldbackground=t["void"],
                    foreground=t["ghost"], font=self.font("small"), rowheight=27,
                    borderwidth=0, relief="flat")
        s.map("Treeview", background=[("selected", t["panel_hi"])],
              foreground=[("selected", t["ghost"])])
        s.configure("Treeview.Heading", background=t["void"], foreground=t["faint"],
                    font=self.font("micro"), relief="flat", borderwidth=0, padding=(8, 9))
        s.map("Treeview.Heading", background=[("active", t["panel_hi"])],
              foreground=[("active", t["ghost"])])

        s.configure("K.Horizontal.TProgressbar", troughcolor=t["panel_hi"],
                    background=t["ghost"], bordercolor=t["panel_hi"],
                    lightcolor=t["ghost"], darkcolor=t["ghost"], thickness=5)

        for w in ("TRadiobutton", "TCheckbutton"):
            s.configure(w, background=t["void"], foreground=t["dim"],
                        font=self.font("small"), focuscolor=t["void"])
            s.map(w, background=[("active", t["void"])], foreground=[("active", t["ghost"])])

        s.configure("TLabelframe", background=t["void"], borderwidth=1, relief="solid",
                    bordercolor=t["line"])
        s.configure("TLabelframe.Label", background=t["void"], foreground=t["faint"],
                    font=self.font("micro"))

        s.configure("TSpinbox", fieldbackground=t["void"], foreground=t["ghost"],
                    bordercolor=t["line_hi"], arrowcolor=t["faint"], padding=4)
        s.configure("TScrollbar", background=t["panel"], troughcolor=t["void"],
                    bordercolor=t["void"], arrowcolor=t["faint"], relief="flat")
        s.map("TScrollbar", background=[("active", t["line_hi"])])
