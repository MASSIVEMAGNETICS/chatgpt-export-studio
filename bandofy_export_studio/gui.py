# =============================================================
# FILE: bandofy_export_studio/gui.py
# VERSION: v2.0.0-ENTERPRISE
# NAME: ChatGPT Export Studio GUI
# PURPOSE: Enterprise-grade Tkinter GUI.
#          Tabs:
#            1. Conversations — import, browse, search, export
#            2. Neural Net Studio — 32×32 hue/intensity grid,
#               real-time training animation, symbol overlays,
#               model export
#          Local-only. No network calls. No telemetry.
# LICENSE: Proprietary - Massive Magnetics / Ethica AI / BHeard Network
# =============================================================

from __future__ import annotations

import json
import math
import os
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except Exception as exc:  # pragma: no cover
    raise RuntimeError("Tkinter is required for GUI mode") from exc

from .core import (
    Database,
    Chunker,
    export_conversation_markdown,
    export_messages_jsonl,
    export_training_pairs_jsonl,
    export_obsidian_vault,
    import_export_zip,
)
from .neural_net import (
    NeuralNetEngine,
    Vocabulary,
    GRID_SIDE,
    GRID_NEURONS,
    DEFAULT_EPOCHS,
    DEFAULT_LR,
    DEFAULT_VOCAB_SIZE,
)

# ── Theme ────────────────────────────────────────────────────────────────────

THEME: Dict[str, str] = {
    "bg":       "#070a10",
    "panel":    "#0e1420",
    "panel2":   "#0b111b",
    "stroke":   "#182334",
    "text":     "#d9e6ff",
    "muted":    "#8ca2c7",
    "accent_g": "#00ff9a",   # neon green
    "accent_p": "#ff2ed1",   # neon pink
    "accent_c": "#00d9ff",   # cyan
    "good":     "#00ff9a",
    "warn":     "#ffd34d",
    "bad":      "#ff4d4d",
}

_NN_GRID_PX: int = 512   # canvas pixel size for the neural-net grid
_CELL_PX:    int = _NN_GRID_PX // GRID_SIDE   # pixels per cell (16)


def _hex_mix(a: str, b: str, t: float) -> str:
    """Linear-interpolate between two hex colours.  t=0→a, t=1→b."""
    a, b = a.lstrip("#"), b.lstrip("#")
    ar, ag, ab_ = int(a[0:2], 16), int(a[2:4], 16), int(a[4:6], 16)
    br, bg, bb  = int(b[0:2], 16), int(b[2:4], 16), int(b[4:6], 16)
    r  = int(ar + (br - ar) * t)
    g  = int(ag + (bg - ag) * t)
    b2 = int(ab_ + (bb - ab_) * t)
    return f"#{r:02x}{g:02x}{b2:02x}"


# ── Reusable widgets ─────────────────────────────────────────────────────────

class NeonButton(tk.Frame):
    """Button with neon outline that brightens on hover."""

    def __init__(
        self,
        parent: Any,
        text: str,
        command: Any,
        accent: str,
        width: int = 22,
        height: int = 2,
        big: bool = False,
    ) -> None:
        super().__init__(parent, bg=THEME["panel"], highlightthickness=1)
        self._accent = accent
        self._cmd = command
        self.configure(
            highlightbackground=_hex_mix(THEME["stroke"], accent, 0.55)
        )
        font = ("Segoe UI", 11, "bold") if big else ("Segoe UI", 10, "bold")
        pad_y = 10 if big else 6
        self._btn = tk.Button(
            self,
            text=text,
            command=self._on_click,
            bd=0,
            relief="flat",
            bg=_hex_mix(THEME["panel2"], accent, 0.08),
            fg=THEME["text"],
            activebackground=_hex_mix(THEME["panel2"], accent, 0.18),
            activeforeground=THEME["text"],
            font=font,
            padx=10,
            pady=pad_y,
            cursor="hand2",
            anchor="w",
        )
        self._btn.pack(fill="x", expand=True)
        self._btn.bind("<Enter>", self._hover_on)
        self._btn.bind("<Leave>", self._hover_off)

    def _hover_on(self, _: Any = None) -> None:
        self.configure(highlightbackground=self._accent)
        self._btn.configure(bg=_hex_mix(THEME["panel2"], self._accent, 0.14))

    def _hover_off(self, _: Any = None) -> None:
        self.configure(
            highlightbackground=_hex_mix(THEME["stroke"], self._accent, 0.55)
        )
        self._btn.configure(bg=_hex_mix(THEME["panel2"], self._accent, 0.08))

    def _on_click(self) -> None:
        try:
            self._cmd()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def set_state(self, enabled: bool) -> None:
        self._btn.configure(state="normal" if enabled else "disabled")


class ScrollableFrame(tk.Frame):
    """Vertically scrollable frame."""

    def __init__(self, parent: Any, bg: str) -> None:
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0)
        self.vbar = tk.Scrollbar(
            self, orient="vertical", command=self.canvas.yview
        )
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._iid = self.canvas.create_window(
            (0, 0), window=self.inner, anchor="nw"
        )
        self.canvas.configure(yscrollcommand=self.vbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.vbar.pack(side="right", fill="y")
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _on_inner(self, _: Any = None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, ev: Any) -> None:
        self.canvas.itemconfigure(self._iid, width=ev.width)

    def _on_wheel(self, ev: Any) -> None:
        try:
            self.canvas.yview_scroll(int(-1 * (ev.delta / 120)), "units")
        except Exception:
            pass


# ── Main application ─────────────────────────────────────────────────────────

class ExportStudioApp:
    """Top-level GUI controller."""

    APP_TITLE   = "ChatGPT Export Studio"
    APP_VERSION = "v2.0 Enterprise"

    def __init__(self, db: Database) -> None:
        self.db = db

        self.root = tk.Tk()
        self.root.title(f"{self.APP_TITLE}  ·  {self.APP_VERSION}")
        self.root.configure(bg=THEME["bg"])
        self.root.geometry("1500x900")
        self.root.minsize(1200, 740)

        # ── Conversation state ────────────────────────────────────────
        self.conversations: List[Dict[str, Any]] = []
        self.current_conversation_id: Optional[str] = None
        self.safe_export = tk.BooleanVar(value=True)
        self.output_dir = tk.StringVar(
            value=os.path.abspath(
                os.path.join(os.getcwd(), "exports", "dataset_01")
            )
        )

        # ── Neural Net state ──────────────────────────────────────────
        self._nn_engine: Optional[NeuralNetEngine] = None
        self._nn_vocab:   Vocabulary = Vocabulary()
        self._nn_samples: List[List[float]] = []
        self._nn_thread:  Optional[threading.Thread] = None
        self._nn_target_epochs: int = DEFAULT_EPOCHS

        # tkinter variables for NN controls
        self._nn_var_vocab   = tk.IntVar(value=DEFAULT_VOCAB_SIZE)
        self._nn_var_epochs  = tk.IntVar(value=DEFAULT_EPOCHS)
        self._nn_var_lr      = tk.StringVar(value=str(DEFAULT_LR))

        # canvas item ID lists (filled in _nn_build_canvas)
        self._nn_rect_ids: List[int] = []
        self._nn_text_ids: List[int] = []
        self._nn_canvas:   Optional[tk.Canvas] = None

        self._build_layout()
        self._refresh_conversations()
        self._log("ChatGPT Export Studio ready.")

    # =========================================================================
    # Layout
    # =========================================================================

    def _build_layout(self) -> None:
        # ── Top header ────────────────────────────────────────────────
        header = tk.Frame(
            self.root, bg=THEME["panel2"],
            highlightthickness=1,
            highlightbackground=THEME["stroke"],
        )
        header.pack(fill="x", padx=10, pady=(10, 0))

        tk.Label(
            header,
            text=self.APP_TITLE,
            bg=THEME["panel2"],
            fg=_hex_mix(THEME["accent_g"], THEME["accent_p"], 0.35),
            font=("Segoe UI", 15, "bold"),
        ).pack(side="left", padx=14, pady=8)

        tk.Label(
            header,
            text=self.APP_VERSION,
            bg=THEME["panel2"],
            fg=THEME["muted"],
            font=("Segoe UI", 9),
        ).pack(side="left", padx=0, pady=8)

        # live stats shown in header
        self._header_stats = tk.Label(
            header, text="",
            bg=THEME["panel2"],
            fg=THEME["accent_c"],
            font=("Consolas", 9),
        )
        self._header_stats.pack(side="right", padx=14, pady=8)

        # ── Notebook (styled dark) ─────────────────────────────────────
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(
            "Dark.TNotebook",
            background=THEME["bg"],
            borderwidth=0,
            tabmargins=0,
        )
        style.configure(
            "Dark.TNotebook.Tab",
            background=THEME["panel2"],
            foreground=THEME["muted"],
            padding=[16, 7],
            font=("Segoe UI", 10, "bold"),
            borderwidth=0,
        )
        style.map(
            "Dark.TNotebook.Tab",
            background=[("selected", THEME["panel"]),
                        ("active",   THEME["stroke"])],
            foreground=[("selected", THEME["accent_g"]),
                        ("active",   THEME["text"])],
        )
        style.configure(
            "Dark.TFrame", background=THEME["bg"]
        )

        self._notebook = ttk.Notebook(
            self.root, style="Dark.TNotebook"
        )
        self._notebook.pack(
            fill="both", expand=True, padx=10, pady=(4, 0)
        )

        # Tab 1 — Conversations
        conv_tab = tk.Frame(self._notebook, bg=THEME["bg"])
        self._notebook.add(conv_tab, text="  Conversations  ")
        self._build_conversations_tab(conv_tab)

        # Tab 2 — Neural Net Studio
        nn_tab = tk.Frame(self._notebook, bg=THEME["bg"])
        self._notebook.add(nn_tab, text="  Neural Net Studio  ")
        self._build_nn_studio_tab(nn_tab)

        # ── Activity log (shared, sits below the notebook) ────────────
        bottom = tk.Frame(
            self.root, bg=THEME["panel2"],
            highlightthickness=1,
            highlightbackground=THEME["stroke"],
        )
        bottom.pack(fill="x", padx=10, pady=(4, 10))

        tk.Label(
            bottom, text="ACTIVITY LOG",
            bg=THEME["panel2"], fg=THEME["muted"],
            font=("Consolas", 8, "bold"),
        ).pack(side="left", padx=10, pady=(6, 0))

        self.log_list = tk.Listbox(
            bottom,
            height=4,
            bg=THEME["panel2"],
            fg=THEME["text"],
            selectbackground=_hex_mix(
                THEME["panel2"], THEME["accent_g"], 0.25
            ),
            activestyle="none",
            highlightthickness=0,
            bd=0,
            font=("Consolas", 9),
        )
        self.log_list.pack(fill="x", padx=10, pady=6)

    # =========================================================================
    # Tab 1 — Conversations
    # =========================================================================

    def _build_conversations_tab(self, parent: tk.Frame) -> None:
        """3-column layout: Left=browser, Center=viewer, Right=export."""
        main = tk.Frame(parent, bg=THEME["bg"])
        main.pack(fill="both", expand=True)
        main.grid_columnconfigure(0, weight=2)
        main.grid_columnconfigure(1, weight=5)
        main.grid_columnconfigure(2, weight=3)
        main.grid_rowconfigure(0, weight=1)

        self.left = tk.Frame(
            main, bg=THEME["panel"],
            highlightthickness=1,
            highlightbackground=THEME["stroke"],
        )
        self.center = tk.Frame(
            main, bg=THEME["panel"],
            highlightthickness=1,
            highlightbackground=THEME["stroke"],
        )
        self.right = tk.Frame(
            main, bg=THEME["panel"],
            highlightthickness=1,
            highlightbackground=THEME["stroke"],
        )
        self.left.grid(  row=0, column=0, sticky="nsew", padx=(0, 6))
        self.center.grid(row=0, column=1, sticky="nsew", padx=(0, 6))
        self.right.grid( row=0, column=2, sticky="nsew")

        self._build_left_panel()
        self._build_center_panel()
        self._build_right_panel()

    def _section_title(
        self, parent: Any, text: str, accent: str
    ) -> tk.Frame:
        fr = tk.Frame(parent, bg=THEME["panel"])
        tk.Label(
            fr, text=text,
            bg=THEME["panel"], fg=THEME["text"],
            font=("Segoe UI", 11, "bold"),
        ).pack(side="left")
        tk.Label(
            fr, text="≡",
            bg=THEME["panel"], fg=accent,
            font=("Segoe UI", 12, "bold"),
        ).pack(side="right")
        return fr

    def _build_left_panel(self) -> None:
        p = self.left
        p.grid_rowconfigure(2, weight=2)
        p.grid_rowconfigure(4, weight=1)
        p.grid_columnconfigure(0, weight=1)

        # Section title
        self._section_title(p, "Conversations", THEME["accent_p"]).grid(
            row=0, column=0, sticky="ew", padx=10, pady=(10, 6)
        )

        # Search row
        sr = tk.Frame(
            p, bg=THEME["panel"],
            highlightthickness=1,
            highlightbackground=THEME["stroke"],
        )
        sr.grid(row=1, column=0, sticky="ew", padx=10)
        self.conv_search = tk.StringVar(value="")
        ent = tk.Entry(
            sr, textvariable=self.conv_search,
            bg=THEME["panel2"], fg=THEME["text"],
            insertbackground=THEME["accent_g"],
            bd=0, font=("Segoe UI", 10),
        )
        ent.pack(side="left", fill="x", expand=True, padx=8, pady=8)
        ent.bind("<Return>", lambda _: self._refresh_conversations())
        tk.Button(
            sr, text="⌕",
            command=self._refresh_conversations,
            bg=THEME["panel2"], fg=THEME["accent_g"],
            bd=0, font=("Segoe UI", 12, "bold"), cursor="hand2",
        ).pack(side="right", padx=8)

        # Conversation list
        self.conv_list = tk.Listbox(
            p,
            bg=THEME["panel"], fg=THEME["text"],
            bd=0, highlightthickness=0,
            activestyle="none",
            selectbackground=_hex_mix(
                THEME["panel"], THEME["accent_g"], 0.25
            ),
            font=("Segoe UI", 10),
        )
        self.conv_list.grid(
            row=2, column=0, sticky="nsew", padx=10, pady=(6, 6)
        )
        self.conv_list.bind("<<ListboxSelect>>",
                            self._on_select_conversation)

        # Projects section
        self._section_title(p, "Projects", THEME["accent_g"]).grid(
            row=3, column=0, sticky="ew", padx=10, pady=(0, 6)
        )
        self.project_list = tk.Listbox(
            p,
            bg=THEME["panel"], fg=THEME["muted"],
            bd=0, highlightthickness=0,
            activestyle="none",
            selectbackground=_hex_mix(
                THEME["panel"], THEME["accent_p"], 0.22
            ),
            font=("Segoe UI", 10),
            height=5,
        )
        for item in ["Knowledge Base", "Debug Tools", "Trip Planner"]:
            self.project_list.insert(tk.END, f"•  {item}")
        self.project_list.grid(
            row=4, column=0, sticky="ew", padx=10, pady=(0, 6)
        )

        # Import button
        wrap = tk.Frame(p, bg=THEME["panel"])
        wrap.grid(row=5, column=0, sticky="ew", padx=10, pady=(0, 10))
        NeonButton(
            wrap, "  Import ChatGPT Export ZIP",
            self._on_import_zip, THEME["accent_g"], big=True,
        ).pack(fill="x")

    def _build_center_panel(self) -> None:
        p = self.center
        p.grid_rowconfigure(1, weight=1)
        p.grid_columnconfigure(0, weight=1)

        # Header
        hdr = tk.Frame(p, bg=THEME["panel"])
        hdr.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 8))
        hdr.grid_columnconfigure(0, weight=1)

        self.conv_title_label = tk.Label(
            hdr,
            text="Select a conversation",
            bg=THEME["panel"],
            fg=_hex_mix(THEME["accent_p"], THEME["accent_g"], 0.35),
            font=("Segoe UI", 12, "bold"),
            anchor="w",
        )
        self.conv_title_label.grid(row=0, column=0, sticky="w")

        tk.Button(
            hdr, text="↗",
            command=self._copy_conversation_id,
            bg=THEME["panel"], fg=THEME["accent_c"],
            bd=0, font=("Segoe UI", 11, "bold"), cursor="hand2",
        ).grid(row=0, column=1, padx=4)
        tk.Button(
            hdr, text="▶",
            command=self._run_pipeline,
            bg=THEME["panel"], fg=THEME["accent_p"],
            bd=0, font=("Segoe UI", 11, "bold"), cursor="hand2",
        ).grid(row=0, column=2, padx=4)

        # Message scroll area
        self.msg_scroll = ScrollableFrame(p, bg=THEME["panel"])
        self.msg_scroll.grid(row=1, column=0, sticky="nsew", padx=10)

        # Colour-band footer
        band = tk.Frame(p, bg=THEME["panel"])
        band.grid(row=2, column=0, sticky="ew", padx=10, pady=(8, 10))
        for i, col in enumerate(
            [THEME["accent_p"], THEME["accent_c"], THEME["accent_g"]]
        ):
            tk.Frame(band, bg=col, height=2).pack(
                side="left", fill="x", expand=True,
                padx=(0 if i == 0 else 6, 0),
            )

    def _build_right_panel(self) -> None:
        p = self.right
        p.grid_rowconfigure(7, weight=1)
        p.grid_columnconfigure(0, weight=1)

        # Header
        hdr = tk.Frame(p, bg=THEME["panel"])
        hdr.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 8))
        tk.Label(
            hdr, text="Export Studio",
            bg=THEME["panel"],
            fg=_hex_mix(THEME["accent_p"], THEME["accent_g"], 0.5),
            font=("Segoe UI", 12, "bold"),
        ).pack(side="left")
        tk.Label(
            hdr, text="≡",
            bg=THEME["panel"], fg=THEME["accent_p"],
            font=("Segoe UI", 12, "bold"),
        ).pack(side="right")

        # Pipeline buttons
        btns = tk.Frame(p, bg=THEME["panel"])
        btns.grid(row=1, column=0, sticky="ew", padx=10)
        for label, cmd in [
            ("  Build SSR Dataset",       self._build_ssr_dataset),
            ("  Generate Pair Triples",   self._generate_pair_triples),
            ("  Create Distillation Pack",self._create_distillation_pack),
            ("  Compile Clean Corpus",    self._compile_clean_corpus),
        ]:
            NeonButton(btns, label, cmd, THEME["accent_p"]).pack(
                fill="x", pady=4
            )

        # Embeddings
        emb = tk.Frame(p, bg=THEME["panel"])
        emb.grid(row=2, column=0, sticky="ew", padx=10, pady=(10, 4))
        tk.Label(
            emb, text="Chunk Index: ExportBrain-200",
            bg=THEME["panel"], fg=THEME["muted"],
            font=("Segoe UI", 9),
        ).pack(anchor="w")

        NeonButton(
            p, "  Generate Chunk Index",
            self._generate_embeddings, THEME["accent_g"], big=True,
        ).grid(row=3, column=0, sticky="ew", padx=10, pady=(2, 4))
        NeonButton(
            p, "  Semantic Search",
            self._semantic_search_popup, THEME["accent_p"], big=True,
        ).grid(row=4, column=0, sticky="ew", padx=10, pady=(0, 8))

        # Safe-export toggle
        safe = tk.Frame(p, bg=THEME["panel"])
        safe.grid(row=5, column=0, sticky="ew", padx=10)
        tk.Label(
            safe, text="PII Safe Export",
            bg=THEME["panel"], fg=THEME["muted"],
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left")
        tk.Checkbutton(
            safe,
            variable=self.safe_export,
            onvalue=True, offvalue=False,
            bg=THEME["panel"],
            activebackground=THEME["panel"],
            fg=THEME["accent_g"],
            selectcolor=THEME["panel2"],
            bd=0,
        ).pack(side="right")

        # Output path
        out = tk.Frame(p, bg=THEME["panel"])
        out.grid(row=6, column=0, sticky="ew", padx=10, pady=(8, 8))
        tk.Label(
            out, text="OUTPUT:",
            bg=THEME["panel"], fg=THEME["muted"],
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left")
        tk.Entry(
            out, textvariable=self.output_dir,
            bg=THEME["panel2"], fg=THEME["text"],
            insertbackground=THEME["accent_g"],
            bd=0, font=("Segoe UI", 9),
        ).pack(side="left", fill="x", expand=True, padx=8, pady=6)
        tk.Button(
            out, text="…",
            command=self._pick_output_dir,
            bg=THEME["panel2"], fg=THEME["accent_p"],
            bd=0, font=("Segoe UI", 12, "bold"), cursor="hand2",
        ).pack(side="right")

        # Run all button
        NeonButton(
            p, "  RUN PIPELINE",
            self._run_pipeline, THEME["accent_p"], big=True,
        ).grid(row=7, column=0, sticky="ew", padx=10, pady=(0, 8))

        # Progress labels
        prog = tk.Frame(
            p, bg=THEME["panel"],
            highlightthickness=1,
            highlightbackground=THEME["stroke"],
        )
        prog.grid(row=8, column=0, sticky="ew", padx=10, pady=(0, 10))
        self.pair_rate = tk.Label(
            prog, text="PAIR MINING: 0 pairs",
            bg=THEME["panel"], fg=THEME["muted"],
            font=("Consolas", 9, "bold"),
        )
        self.pair_rate.pack(anchor="w", padx=8, pady=(6, 2))
        self.embed_job = tk.Label(
            prog, text="CHUNK INDEX: idle",
            bg=THEME["panel"], fg=THEME["muted"],
            font=("Consolas", 9, "bold"),
        )
        self.embed_job.pack(anchor="w", padx=8, pady=(0, 6))

    # =========================================================================
    # Tab 2 — Neural Net Studio
    # =========================================================================

    def _build_nn_studio_tab(self, parent: tk.Frame) -> None:
        """Enterprise neural-net visualisation panel."""
        parent.grid_columnconfigure(0, weight=3)
        parent.grid_columnconfigure(1, weight=2)
        parent.grid_rowconfigure(0, weight=1)

        # ── Left: grid canvas + status strip ──────────────────────────
        left = tk.Frame(
            parent, bg=THEME["panel"],
            highlightthickness=1,
            highlightbackground=THEME["stroke"],
        )
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)

        # Panel title
        lt = tk.Frame(left, bg=THEME["panel"])
        lt.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 6))
        tk.Label(
            lt,
            text="Neural Net  ·  32 × 32  ·  1 024 neurons",
            bg=THEME["panel"],
            fg=_hex_mix(THEME["accent_c"], THEME["accent_g"], 0.4),
            font=("Segoe UI", 11, "bold"),
        ).pack(side="left")
        self._nn_epoch_badge = tk.Label(
            lt, text="epoch —",
            bg=THEME["panel"], fg=THEME["muted"],
            font=("Consolas", 9),
        )
        self._nn_epoch_badge.pack(side="right")

        # Canvas container (centred)
        canvas_wrap = tk.Frame(left, bg=THEME["bg"])
        canvas_wrap.grid(row=1, column=0, sticky="nsew")
        canvas_wrap.grid_rowconfigure(0, weight=1)
        canvas_wrap.grid_columnconfigure(0, weight=1)

        self._nn_canvas = tk.Canvas(
            canvas_wrap,
            width=_NN_GRID_PX,
            height=_NN_GRID_PX,
            bg="#060b12",
            highlightthickness=1,
            highlightbackground=THEME["stroke"],
        )
        self._nn_canvas.grid(row=0, column=0)
        self._nn_build_canvas_items()

        # Legend bar
        legend = tk.Frame(left, bg=THEME["panel"])
        legend.grid(row=2, column=0, sticky="ew", padx=12, pady=(6, 10))
        tk.Label(
            legend, text="Hue = column layer position  ·  "
                         "Brightness = activation  ·  "
                         "Symbol = top-activated neuron",
            bg=THEME["panel"], fg=THEME["muted"],
            font=("Segoe UI", 8),
        ).pack(side="left")

        # Loss mini-display
        self._nn_loss_label = tk.Label(
            legend, text="loss —",
            bg=THEME["panel"],
            fg=THEME["accent_g"],
            font=("Consolas", 9, "bold"),
        )
        self._nn_loss_label.pack(side="right")

        # ── Right: controls ────────────────────────────────────────────
        right = tk.Frame(
            parent, bg=THEME["panel"],
            highlightthickness=1,
            highlightbackground=THEME["stroke"],
        )
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)

        scroll_r = ScrollableFrame(right, bg=THEME["panel"])
        scroll_r.pack(fill="both", expand=True)
        rp = scroll_r.inner  # shorthand

        def _sep() -> None:
            tk.Frame(rp, bg=THEME["stroke"], height=1).pack(
                fill="x", padx=8, pady=8
            )

        def _sec(title: str) -> None:
            tk.Label(
                rp, text=title,
                bg=THEME["panel"],
                fg=THEME["accent_c"],
                font=("Segoe UI", 10, "bold"),
            ).pack(anchor="w", padx=12, pady=(10, 4))

        def _lbl(text: str, fg: str = THEME["muted"]) -> None:
            tk.Label(
                rp, text=text,
                bg=THEME["panel"], fg=fg,
                font=("Segoe UI", 9),
                wraplength=300, justify="left",
            ).pack(anchor="w", padx=14, pady=1)

        # ── How it works ──────────────────────────────────────────────
        _sec("HOW IT WORKS")
        _lbl(
            "This studio trains a 1 024-neuron neural network on your "
            "conversation history.  Each cell in the grid represents one "
            "hidden neuron.  Colour encodes activation intensity; symbols "
            "mark the most active neurons.  When training completes you can "
            "export the model as a portable JSON file."
        )

        _sep()

        # ── Step 1: data ───────────────────────────────────────────────
        _sec("STEP 1 — LOAD TRAINING DATA")
        _lbl("Encodes all conversation messages as bag-of-words vectors.")

        self._nn_data_status = tk.Label(
            rp, text="No data loaded yet.",
            bg=THEME["panel"], fg=THEME["warn"],
            font=("Consolas", 9),
        )
        self._nn_data_status.pack(anchor="w", padx=14, pady=(4, 2))

        btn_load = tk.Frame(rp, bg=THEME["panel"])
        btn_load.pack(fill="x", padx=10, pady=(4, 8))
        NeonButton(
            btn_load, "  Load from Conversations Database",
            self._nn_load_data, THEME["accent_c"],
        ).pack(fill="x")

        _sep()

        # ── Step 2: config ─────────────────────────────────────────────
        _sec("STEP 2 — CONFIGURE TRAINING")

        def _spin_row(label: str, var: tk.Variable,
                      lo: int, hi: int, step: int = 1) -> None:
            row = tk.Frame(rp, bg=THEME["panel"])
            row.pack(fill="x", padx=12, pady=3)
            tk.Label(
                row, text=label, width=16, anchor="w",
                bg=THEME["panel"], fg=THEME["text"],
                font=("Segoe UI", 9),
            ).pack(side="left")
            tk.Spinbox(
                row,
                from_=lo, to=hi, increment=step,
                textvariable=var,
                bg=THEME["panel2"], fg=THEME["accent_g"],
                insertbackground=THEME["accent_g"],
                buttonbackground=THEME["panel2"],
                bd=1, relief="flat",
                font=("Consolas", 10),
                width=7,
            ).pack(side="left", padx=6)

        _spin_row("Vocabulary size",  self._nn_var_vocab,  10, 200, 10)
        _spin_row("Training epochs",  self._nn_var_epochs, 5,  500, 5)

        # LR row
        lr_row = tk.Frame(rp, bg=THEME["panel"])
        lr_row.pack(fill="x", padx=12, pady=3)
        tk.Label(
            lr_row, text="Learning rate", width=16, anchor="w",
            bg=THEME["panel"], fg=THEME["text"],
            font=("Segoe UI", 9),
        ).pack(side="left")
        tk.Entry(
            lr_row,
            textvariable=self._nn_var_lr,
            bg=THEME["panel2"], fg=THEME["accent_g"],
            insertbackground=THEME["accent_g"],
            bd=1, relief="flat",
            font=("Consolas", 10), width=9,
        ).pack(side="left", padx=6)

        _sep()

        # ── Step 3: train ──────────────────────────────────────────────
        _sec("STEP 3 — AUTO-TRAIN")
        _lbl(
            "Runs the configured number of epochs, updating the grid in "
            "real time.  You can stop at any time."
        )

        btn_row = tk.Frame(rp, bg=THEME["panel"])
        btn_row.pack(fill="x", padx=10, pady=(6, 4))
        self._nn_btn_start = NeonButton(
            btn_row, "  AUTO-TRAIN",
            self._nn_start_training, THEME["accent_g"], big=True,
        )
        self._nn_btn_start.pack(fill="x", pady=(0, 6))

        self._nn_btn_stop = NeonButton(
            btn_row, "  STOP",
            self._nn_stop_training, THEME["bad"],
        )
        self._nn_btn_stop.pack(fill="x")
        self._nn_btn_stop.set_state(False)

        # Metrics display
        met = tk.Frame(
            rp, bg=THEME["panel2"],
            highlightthickness=1,
            highlightbackground=THEME["stroke"],
        )
        met.pack(fill="x", padx=10, pady=(8, 4))
        self._nn_metric_epoch = tk.Label(
            met, text="Epoch   :  — / —",
            bg=THEME["panel2"], fg=THEME["text"],
            font=("Consolas", 9, "bold"),
        )
        self._nn_metric_epoch.pack(anchor="w", padx=10, pady=(6, 2))
        self._nn_metric_loss = tk.Label(
            met, text="Loss    :  —",
            bg=THEME["panel2"], fg=THEME["text"],
            font=("Consolas", 9, "bold"),
        )
        self._nn_metric_loss.pack(anchor="w", padx=10, pady=2)
        self._nn_metric_steps = tk.Label(
            met, text="Steps   :  0",
            bg=THEME["panel2"], fg=THEME["text"],
            font=("Consolas", 9, "bold"),
        )
        self._nn_metric_steps.pack(anchor="w", padx=10, pady=(2, 6))

        # Progress bar (simple tk canvas)
        pb_wrap = tk.Frame(rp, bg=THEME["panel"])
        pb_wrap.pack(fill="x", padx=10, pady=(0, 4))
        self._nn_pb_canvas = tk.Canvas(
            pb_wrap, height=8, bg=THEME["panel2"],
            highlightthickness=0, bd=0,
        )
        self._nn_pb_canvas.pack(fill="x")
        self._nn_pb_fill = self._nn_pb_canvas.create_rectangle(
            0, 0, 0, 8, fill=THEME["accent_g"], outline="",
        )

        _sep()

        # ── Step 4: export ─────────────────────────────────────────────
        _sec("STEP 4 — EXPORT MODEL")
        _lbl(
            "Saves the trained model as a portable JSON file containing "
            "the architecture, weights, vocabulary, and training history."
        )

        btn_exp = tk.Frame(rp, bg=THEME["panel"])
        btn_exp.pack(fill="x", padx=10, pady=(6, 4))
        NeonButton(
            btn_exp, "  Export Model  (.json)",
            self._nn_export_model, THEME["accent_p"], big=True,
        ).pack(fill="x")

        self._nn_export_status = tk.Label(
            rp, text="",
            bg=THEME["panel"], fg=THEME["good"],
            font=("Consolas", 9),
        )
        self._nn_export_status.pack(anchor="w", padx=14, pady=(2, 10))

        # ── Reset ──────────────────────────────────────────────────────
        _sep()
        rst_row = tk.Frame(rp, bg=THEME["panel"])
        rst_row.pack(fill="x", padx=10, pady=(0, 10))
        NeonButton(
            rst_row, "  Reset Weights",
            self._nn_reset, THEME["warn"],
        ).pack(fill="x")

    # ── Canvas construction ──────────────────────────────────────────────────

    def _nn_build_canvas_items(self) -> None:
        """Pre-create all 1 024 cell rectangles and text items."""
        c = self._nn_canvas
        if c is None:
            return
        px = _CELL_PX
        gs = GRID_SIDE
        self._nn_rect_ids = []
        self._nn_text_ids = []

        for row in range(gs):
            for col in range(gs):
                x0 = col * px
                y0 = row * px
                x1 = x0 + px
                y1 = y0 + px
                rid = c.create_rectangle(
                    x0, y0, x1, y1,
                    fill="#080d14",
                    outline="#0d1520",
                    width=1,
                )
                tid = c.create_text(
                    x0 + px // 2,
                    y0 + px // 2,
                    text="",
                    fill="#ffffff",
                    font=("Segoe UI", 6),
                )
                self._nn_rect_ids.append(rid)
                self._nn_text_ids.append(tid)

    # =========================================================================
    # NN — data loading
    # =========================================================================

    def _nn_load_data(self) -> None:
        """Encode all conversation messages and build the vocabulary."""
        def work() -> None:
            texts = []
            for conv in self.db.list_conversations(limit=50_000):
                msgs = self.db.get_messages_for_conversation(conv["id"])
                for m in msgs:
                    t = (m.get("content_text") or "").strip()
                    if t:
                        texts.append(t)
            if not texts:
                self._log("Neural Net: no messages found in database.")
                self._nn_data_status.config(
                    text="No messages found.  Import a ZIP first.",
                    fg=THEME["bad"],
                )
                return
            v_size = self._nn_var_vocab.get()
            self._nn_vocab = Vocabulary(max_vocab=v_size)
            actual = self._nn_vocab.build(texts)
            self._nn_samples = [
                self._nn_vocab.encode(t) for t in texts
            ]
            # Filter out zero vectors
            self._nn_samples = [
                s for s in self._nn_samples if any(v != 0.0 for v in s)
            ]
            n = len(self._nn_samples)
            self._nn_data_status.config(
                text=f"Loaded {n:,} samples  ·  vocab {actual} words",
                fg=THEME["good"],
            )
            self._log(
                f"Neural Net: {n} training samples built "
                f"(vocab={actual} words)"
            )

        self._run_bg("Load NN Training Data", work)

    # =========================================================================
    # NN — training
    # =========================================================================

    def _nn_start_training(self) -> None:
        if not self._nn_samples:
            messagebox.showwarning(
                "No Data",
                "Load training data first (Step 1).",
            )
            return
        if self._nn_engine and self._nn_engine.running:
            return

        try:
            lr = float(self._nn_var_lr.get())
        except ValueError:
            lr = DEFAULT_LR

        vocab_size = len(self._nn_vocab.words) or self._nn_var_vocab.get()
        self._nn_target_epochs = self._nn_var_epochs.get()

        # (Re-)create engine if vocab size changed or none exists
        if (
            self._nn_engine is None
            or self._nn_engine.n_in != vocab_size
        ):
            self._nn_engine = NeuralNetEngine(
                vocab_size=vocab_size,
                learning_rate=lr,
            )

        self._nn_engine.running = True
        self._nn_engine.lr = lr
        self._nn_btn_start.set_state(False)
        self._nn_btn_stop.set_state(True)
        self._log("Neural Net: training started.")

        def training_loop() -> None:
            eng = self._nn_engine
            samples = self._nn_samples
            target = self._nn_target_epochs
            while eng.running and eng.epoch < target:
                eng.run_epoch(samples)
            eng.running = False

        self._nn_thread = threading.Thread(
            target=training_loop, daemon=True
        )
        self._nn_thread.start()

        # Start periodic UI tick
        self._nn_tick()

    def _nn_stop_training(self) -> None:
        if self._nn_engine:
            self._nn_engine.running = False
        self._nn_btn_start.set_state(True)
        self._nn_btn_stop.set_state(False)
        self._log("Neural Net: training stopped.")

    # =========================================================================
    # NN — canvas tick (called via after())
    # =========================================================================

    def _nn_tick(self) -> None:
        """Refresh canvas from engine state at ~10 FPS."""
        eng = self._nn_engine
        if eng is None:
            return

        # ── Update canvas colours and symbols ──────────────────────────
        c = self._nn_canvas
        if c is not None:
            colors  = eng.colors    # list of 1024 hex strings
            symbols = eng.symbols   # list of 1024 strings
            for i, (rid, tid) in enumerate(
                zip(self._nn_rect_ids, self._nn_text_ids)
            ):
                col = colors[i]
                c.itemconfig(rid, fill=col)
                sym = symbols[i]
                if sym:
                    # text is white when activated
                    c.itemconfig(tid, text=sym, fill="#ffffff")
                else:
                    c.itemconfig(tid, text="")

        # ── Update metric labels ────────────────────────────────────────
        tgt = self._nn_target_epochs
        self._nn_metric_epoch.config(
            text=f"Epoch   :  {eng.epoch} / {tgt}"
        )
        loss_str = f"{eng.loss:.6f}" if eng.loss else "—"
        self._nn_metric_loss.config(
            text=f"Loss    :  {loss_str}"
        )
        self._nn_metric_steps.config(
            text=f"Steps   :  {eng.step:,}"
        )
        self._nn_epoch_badge.config(
            text=f"epoch {eng.epoch}/{tgt}  ·  step {eng.step:,}"
        )
        self._nn_loss_label.config(
            text=f"loss {loss_str}"
        )

        # ── Progress bar ────────────────────────────────────────────────
        try:
            pct = eng.epoch / max(1, tgt)
            w = self._nn_pb_canvas.winfo_width()
            self._nn_pb_canvas.coords(
                self._nn_pb_fill, 0, 0, int(w * pct), 8
            )
        except Exception:
            pass

        # ── Schedule next tick (or stop when done) ──────────────────────
        if eng.running:
            self.root.after(100, self._nn_tick)
        else:
            # Training finished or stopped
            self._nn_btn_start.set_state(True)
            self._nn_btn_stop.set_state(False)
            if eng.epoch >= tgt and tgt > 0:
                self._log(
                    f"Neural Net: training complete  "
                    f"({eng.epoch} epochs, final loss={eng.loss:.6f})"
                )

    # =========================================================================
    # NN — export & reset
    # =========================================================================

    def _nn_export_model(self) -> None:
        if self._nn_engine is None or self._nn_engine.step == 0:
            messagebox.showwarning(
                "Nothing to export",
                "Train the model first (Step 3).",
            )
            return

        path = filedialog.asksaveasfilename(
            title="Save neural model",
            defaultextension=".json",
            filetypes=[("JSON model", "*.json"), ("All files", "*.*")],
            initialfile="synthetic_model.json",
        )
        if not path:
            return

        def work() -> None:
            model_dict = self._nn_engine.export_model(self._nn_vocab)
            # Also write a tiny inference helper alongside
            helper_path = os.path.splitext(path)[0] + "_inference.py"
            _write_inference_helper(helper_path)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(model_dict, f, indent=2)
            self._nn_export_status.config(
                text=f"Saved: {os.path.basename(path)}",
                fg=THEME["good"],
            )
            self._log(f"Neural Net: model exported → {path}")

        self._run_bg("Export Neural Model", work)

    def _nn_reset(self) -> None:
        if self._nn_engine and self._nn_engine.running:
            self._nn_stop_training()
        self._nn_engine = None
        self._nn_samples = []
        self._nn_vocab = Vocabulary()
        self._nn_data_status.config(
            text="No data loaded yet.", fg=THEME["warn"]
        )
        self._nn_export_status.config(text="")
        self._nn_metric_epoch.config(text="Epoch   :  — / —")
        self._nn_metric_loss.config(text="Loss    :  —")
        self._nn_metric_steps.config(text="Steps   :  0")
        self._nn_epoch_badge.config(text="epoch —")
        self._nn_loss_label.config(text="loss —")
        # Reset canvas to dark
        if self._nn_canvas:
            for rid in self._nn_rect_ids:
                self._nn_canvas.itemconfig(rid, fill="#080d14")
            for tid in self._nn_text_ids:
                self._nn_canvas.itemconfig(tid, text="")
        self._log("Neural Net: weights reset.")

    # =========================================================================
    # Logging / background execution
    # =========================================================================

    def _log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}]  {msg}"
        try:
            self.log_list.insert(tk.END, line)
            self.log_list.see(tk.END)
        except Exception:
            pass

    def _run_bg(self, label: str, fn: Any) -> None:
        def runner() -> None:
            try:
                self._log(f"▶ {label}")
                fn()
                self._log(f"✔ {label}")
            except Exception as exc:
                self._log(f"✖ {label}: {exc}")
                try:
                    messagebox.showerror("Error", f"{label}\n\n{exc}")
                except Exception:
                    pass
        threading.Thread(target=runner, daemon=True).start()

    # =========================================================================
    # Conversations — data loading
    # =========================================================================

    def _refresh_conversations(self) -> None:
        q = self.conv_search.get().strip()
        self.conversations = self.db.list_conversations(
            limit=600, search=q
        )
        self.conv_list.delete(0, tk.END)
        for c in self.conversations:
            title = c.get("title") or "(untitled)"
            self.conv_list.insert(tk.END, title)
        stats = self.db.stats()
        info = (
            f"conversations: {stats['conversations']}  ·  "
            f"messages: {stats['messages']}  ·  "
            f"chunks: {stats['chunks']}"
        )
        self._log(f"Indexed: {info}")
        try:
            self._header_stats.config(text=info)
        except Exception:
            pass

    def _on_select_conversation(self, _ev: Any = None) -> None:
        sel = self.conv_list.curselection()
        if not sel:
            return
        idx = int(sel[0])
        if idx < 0 or idx >= len(self.conversations):
            return
        conv = self.conversations[idx]
        self.current_conversation_id = conv["id"]
        self.conv_title_label.config(text=conv.get("title") or "(untitled)")
        self._render_conversation(conv["id"])

    def _render_conversation(self, conversation_id: str) -> None:
        for w in list(self.msg_scroll.inner.winfo_children()):
            w.destroy()
        msgs = self.db.get_messages_for_conversation(conversation_id)
        for m in msgs:
            self._add_message_card(m)
        self._log(f"Loaded conversation: {len(msgs)} messages")

    def _add_message_card(self, m: Dict[str, Any]) -> None:
        role = (m.get("role") or "").lower()
        is_user = role == "user"
        accent = THEME["accent_g"] if is_user else THEME["accent_p"]

        outer = tk.Frame(self.msg_scroll.inner, bg=THEME["panel"], pady=6)
        outer.pack(fill="x", anchor="n")

        card = tk.Frame(
            outer, bg=THEME["panel2"],
            highlightthickness=1,
            highlightbackground=_hex_mix(THEME["stroke"], accent, 0.7),
        )
        card.pack(
            fill="x",
            padx=(6, 40) if is_user else (40, 6),
        )

        head = tk.Frame(card, bg=THEME["panel2"])
        head.pack(fill="x", padx=10, pady=(8, 0))
        tk.Label(
            head,
            text="You:" if is_user else "Assistant:",
            bg=THEME["panel2"], fg=accent,
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left")

        created = m.get("created_at") or ""
        try:
            ts = datetime.fromisoformat(
                created.replace("Z", "+00:00")
            ).strftime("%b %d  %H:%M")
        except Exception:
            ts = ""
        if ts:
            tk.Label(
                head, text=ts,
                bg=THEME["panel2"], fg=THEME["muted"],
                font=("Segoe UI", 8),
            ).pack(side="right")

        tk.Label(
            card,
            text=(m.get("content_text") or "").strip(),
            bg=THEME["panel2"], fg=THEME["text"],
            font=("Segoe UI", 10),
            justify="left", anchor="w",
            wraplength=780,
        ).pack(fill="x", padx=10, pady=(6, 8))

    # =========================================================================
    # Conversations — UI actions
    # =========================================================================

    def _pick_output_dir(self) -> None:
        d = filedialog.askdirectory(title="Choose output directory")
        if d:
            self.output_dir.set(d)

    def _copy_conversation_id(self) -> None:
        if not self.current_conversation_id:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.current_conversation_id)
        self._log("Conversation ID copied to clipboard.")

    def _on_import_zip(self) -> None:
        path = filedialog.askopenfilename(
            title="Select ChatGPT export ZIP",
            filetypes=[
                ("ZIP files", "*.zip"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return

        def work() -> None:
            stats = import_export_zip(self.db, path)
            self._refresh_conversations()
            self._log(
                f"Import complete: "
                f"{stats['conversations']} conversations, "
                f"{stats['messages']} messages"
            )

        self._run_bg("Import ChatGPT Export", work)

    # =========================================================================
    # Conversations — pipeline actions
    # =========================================================================

    def _ensure_out(self) -> str:
        out = self.output_dir.get().strip() or os.path.abspath(
            os.path.join(os.getcwd(), "exports")
        )
        os.makedirs(out, exist_ok=True)
        return out

    def _build_ssr_dataset(self) -> None:
        def work() -> None:
            out = self._ensure_out()
            fp = os.path.join(out, "messages.jsonl")
            export_messages_jsonl(self.db, fp, redact=self.safe_export.get())
            self._log(f"SSR dataset exported: {fp}")
        self._run_bg("Build SSR Dataset", work)

    def _generate_pair_triples(self) -> None:
        def work() -> None:
            out = self._ensure_out()
            fp = os.path.join(out, "pairs.jsonl")
            export_training_pairs_jsonl(
                self.db, fp, redact=self.safe_export.get()
            )
            try:
                n = sum(1 for _ in open(fp, "r", encoding="utf-8"))
            except Exception:
                n = 0
            self.pair_rate.config(text=f"PAIR MINING: {n} pairs")
            self._log(f"Pair triples exported: {fp}")
        self._run_bg("Generate Pair Triples", work)

    def _compile_clean_corpus(self) -> None:
        def work() -> None:
            out = self._ensure_out()
            fp = os.path.join(out, "corpus.txt")
            with open(fp, "w", encoding="utf-8") as f:
                for conv in self.db.list_conversations(limit=20_000):
                    cid = conv["id"]
                    title = (conv.get("title") or "(untitled)").strip()
                    md = export_conversation_markdown(
                        self.db, cid, redact=self.safe_export.get()
                    )
                    f.write(f"\n\n# {title}\n\n{md}\n")
            self._log(f"Clean corpus compiled: {fp}")
        self._run_bg("Compile Clean Corpus", work)

    def _create_distillation_pack(self) -> None:
        def work() -> None:
            out = self._ensure_out()
            pack_dir = os.path.join(out, "distillation_pack")
            os.makedirs(pack_dir, exist_ok=True)
            export_messages_jsonl(
                self.db,
                os.path.join(pack_dir, "messages.jsonl"),
                redact=self.safe_export.get(),
            )
            export_training_pairs_jsonl(
                self.db,
                os.path.join(pack_dir, "pairs.jsonl"),
                redact=self.safe_export.get(),
            )
            export_obsidian_vault(
                self.db,
                os.path.join(pack_dir, "obsidian_vault"),
                redact=self.safe_export.get(),
            )
            self._log(f"Distillation pack created: {pack_dir}")
        self._run_bg("Create Distillation Pack", work)

    def _generate_embeddings(self) -> None:
        def work() -> None:
            chunker = Chunker(self.db, max_chars=2500, overlap_chars=250)
            stats = chunker.chunk_all()
            self.embed_job.config(
                text=f"CHUNK INDEX: {stats['chunks']} chunks"
            )
            self._log(
                f"Chunk index built: {stats['chunks']} chunks "
                f"across {stats['conversations']} conversations"
            )
        self._run_bg("Generate Chunk Index", work)

    def _semantic_search_popup(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("Search Messages")
        win.configure(bg=THEME["bg"])
        win.geometry("860x540")

        top = tk.Frame(
            win, bg=THEME["panel"],
            highlightthickness=1,
            highlightbackground=THEME["stroke"],
        )
        top.pack(fill="x", padx=10, pady=10)
        qv = tk.StringVar(value="")
        ent = tk.Entry(
            top, textvariable=qv,
            bg=THEME["panel2"], fg=THEME["text"],
            insertbackground=THEME["accent_g"],
            bd=0, font=("Segoe UI", 10),
        )
        ent.pack(side="left", fill="x", expand=True, padx=8, pady=8)

        results = tk.Listbox(
            win,
            bg=THEME["panel"], fg=THEME["text"],
            bd=0, highlightthickness=0,
            activestyle="none",
            font=("Segoe UI", 10),
        )
        results.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        def run_search() -> None:
            results.delete(0, tk.END)
            q = qv.get().strip()
            if not q:
                return
            hits = self.db.search_messages(q, limit=60)
            for h in hits:
                cid = h["conversation_id"]
                role = h.get("role", "")
                snippet = (h.get("content_text") or "").replace("\n", " ")
                snippet = snippet[:120] + ("…" if len(snippet) > 120 else "")
                results.insert(tk.END, f"{role}: {snippet}  [cid={cid}]")
            self._log(f"Search: {len(hits)} hits for '{q}'")

        def on_pick(_: Any = None) -> None:
            sel = results.curselection()
            if not sel:
                return
            line = results.get(sel[0])
            if "[cid=" not in line:
                return
            cid = line.split("[cid=", 1)[1].split("]", 1)[0]
            self._load_conversation_by_id(cid)
            try:
                win.destroy()
            except Exception:
                pass

        tk.Button(
            top, text="Search",
            command=run_search,
            bg=_hex_mix(THEME["panel2"], THEME["accent_p"], 0.14),
            fg=THEME["text"], bd=0,
            font=("Segoe UI", 10, "bold"), cursor="hand2",
        ).pack(side="right", padx=8)
        ent.bind("<Return>", lambda _: run_search())
        results.bind("<Double-Button-1>", on_pick)
        ent.focus_set()

    def _load_conversation_by_id(self, cid: str) -> None:
        rows = [c for c in self.conversations if c["id"] == cid]
        if not rows:
            self._refresh_conversations()
            rows = [c for c in self.conversations if c["id"] == cid]
        if not rows:
            self._log(f"Conversation not found: {cid}")
            return
        conv = rows[0]
        idx = self.conversations.index(conv)
        self.conv_list.selection_clear(0, tk.END)
        self.conv_list.selection_set(idx)
        self.conv_list.see(idx)
        self.current_conversation_id = cid
        self.conv_title_label.config(text=conv.get("title") or "(untitled)")
        self._render_conversation(cid)

    def _run_pipeline(self) -> None:
        def work() -> None:
            out = self._ensure_out()
            export_messages_jsonl(
                self.db,
                os.path.join(out, "messages.jsonl"),
                redact=self.safe_export.get(),
            )
            export_training_pairs_jsonl(
                self.db,
                os.path.join(out, "pairs.jsonl"),
                redact=self.safe_export.get(),
            )
            fp = os.path.join(out, "corpus.txt")
            with open(fp, "w", encoding="utf-8") as f:
                for conv in self.db.list_conversations(limit=20_000):
                    cid = conv["id"]
                    title = (conv.get("title") or "(untitled)").strip()
                    md = export_conversation_markdown(
                        self.db, cid, redact=self.safe_export.get()
                    )
                    f.write(f"\n\n# {title}\n\n{md}\n")
            export_obsidian_vault(
                self.db,
                os.path.join(out, "obsidian_vault"),
                redact=self.safe_export.get(),
            )
            self._log(f"Pipeline completed → {out}")
        self._run_bg("RUN PIPELINE", work)

    # =========================================================================
    # Main loop
    # =========================================================================

    def run(self) -> None:
        self.root.mainloop()


# ── Inference helper writer ───────────────────────────────────────────────────

_INFERENCE_TEMPLATE = '''\
"""
ChatGPT Export Studio — Synthetic Neural Model Inference Helper
Generated automatically.  Requires Python 3.10+, no extra packages.
"""
import json, math, re

def load_model(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def encode(text: str, vocab: list) -> list:
    w2i = {w: i for i, w in enumerate(vocab)}
    vec = [0.0] * len(vocab)
    for w in re.findall(r"\\b[a-zA-Z]{3,}\\b", text.lower()):
        if w in w2i:
            vec[w2i[w]] += 1.0
    n = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / n for v in vec]

def infer(model: dict, text: str) -> list:
    wts    = model["weights"]
    vocab  = model["vocabulary"]["words"]
    W1, b1 = wts["W1"], wts["b1"]
    W2, b2 = wts["W2"], wts["b2"]
    x = encode(text, vocab)
    n = len(x)
    h = [max(0.0, sum(W1[j][i] * x[i] for i in range(n)) + b1[j])
         for j in range(len(W1))]
    out = [1.0 / (1.0 + math.exp(-max(-15.0, min(15.0,
            sum(W2[k][j] * h[j] for j in range(len(h))) + b2[k]))))
           for k in range(len(W2))]
    return out

if __name__ == "__main__":
    import sys
    m = load_model(sys.argv[1] if len(sys.argv) > 1 else "synthetic_model.json")
    print("Architecture:", m["architecture"])
    print("Epochs trained:", m["training"]["epochs"])
    print("Final loss:", m["training"]["final_loss"])
    result = infer(m, "hello world test")
    print("Sample output (first 10 dims):", [round(v, 4) for v in result[:10]])
'''


def _write_inference_helper(path: str) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(_INFERENCE_TEMPLATE)
    except Exception:
        pass


# ── Entry point ───────────────────────────────────────────────────────────────

def run_gui(db_path: str = "bandofy_export_studio.sqlite3") -> None:
    db = Database(db_path)
    try:
        ExportStudioApp(db).run()
    finally:
        db.close()
