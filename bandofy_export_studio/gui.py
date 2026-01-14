# =============================================================
# FILE: bandofy_export_studio/gui.py
# VERSION: v1.1.0-NEON-PIXELMATCH-GODCORE
# NAME: BandofyExportStudioGUI
# AUTHOR: Brandon "iambandobandz" Emery x Victor (Fractal Architect Mode)
# PURPOSE: Cyberpunk-dark Tkinter GUI matching the provided mock layout:
#          - Left: Conversations + Projects
#          - Center: Chat viewer
#          - Right: Export Studio pipeline controls
#          - Bottom: Live activity log
#          Local-only. No network calls. No telemetry.
# LICENSE: Proprietary - Massive Magnetics / Ethica AI / BHeard Network
# =============================================================

from __future__ import annotations

import os
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox
except Exception as e:  # pragma: no cover
    raise RuntimeError("Tkinter is required for GUI mode") from e

from .core import (
    Database,
    Chunker,
    export_conversation_markdown,
    export_messages_jsonl,
    export_training_pairs_jsonl,
    export_obsidian_vault,
    import_export_zip,
)

# -------------------------------
# Neon Theme (Tk widgets)
# -------------------------------

THEME = {
    "bg": "#070a10",
    "panel": "#0e1420",
    "panel2": "#0b111b",
    "stroke": "#182334",
    "text": "#d9e6ff",
    "muted": "#8ca2c7",
    "accent_g": "#00ff9a",   # neon green
    "accent_p": "#ff2ed1",   # neon pink
    "accent_c": "#00d9ff",   # cyan
    "good": "#00ff9a",
    "warn": "#ffd34d",
    "bad": "#ff4d4d",
}


def _hex_mix(a: str, b: str, t: float) -> str:
    a = a.lstrip("#")
    b = b.lstrip("#")
    ar, ag, ab = int(a[0:2], 16), int(a[2:4], 16), int(a[4:6], 16)
    br, bg, bb = int(b[0:2], 16), int(b[2:4], 16), int(b[4:6], 16)
    r = int(ar + (br - ar) * t)
    g = int(ag + (bg - ag) * t)
    b2 = int(ab + (bb - ab) * t)
    return f"#{r:02x}{g:02x}{b2:02x}"


class NeonButton(tk.Frame):
    """A Tkinter button with neon outline + hover."""

    def __init__(
        self,
        parent,
        text: str,
        command,
        accent: str,
        width: int = 22,
        height: int = 2,
        big: bool = False,
    ):
        super().__init__(parent, bg=THEME["panel"], highlightthickness=1)
        self._accent = accent
        self._cmd = command
        self.configure(highlightbackground=_hex_mix(THEME["stroke"], accent, 0.55))

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

    def _hover_on(self, _=None):
        self.configure(highlightbackground=self._accent)
        self._btn.configure(bg=_hex_mix(THEME["panel2"], self._accent, 0.14))

    def _hover_off(self, _=None):
        self.configure(highlightbackground=_hex_mix(THEME["stroke"], self._accent, 0.55))
        self._btn.configure(bg=_hex_mix(THEME["panel2"], self._accent, 0.08))

    def _on_click(self):
        try:
            self._cmd()
        except Exception as e:
            messagebox.showerror("Error", str(e))


class ScrollableFrame(tk.Frame):
    """A scrollable frame for card-style content."""

    def __init__(self, parent, bg: str):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0)
        self.vbar = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=bg)

        self.inner_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.vbar.pack(side="right", fill="y")

        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        # mousewheel support
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _on_inner_configure(self, _=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self.inner_id, width=event.width)

    def _on_mousewheel(self, event):
        try:
            delta = int(-1 * (event.delta / 120))
            self.canvas.yview_scroll(delta, "units")
        except Exception:
            pass


class ExportStudioApp:
    def __init__(self, db: Database):
        self.db = db

        self.root = tk.Tk()
        self.root.title("Bandofy AI Export Studio")
        self.root.configure(bg=THEME["bg"])
        self.root.geometry("1450x860")
        self.root.minsize(1180, 720)

        # state
        self.conversations: List[Dict[str, Any]] = []
        self.current_conversation_id: Optional[str] = None
        self.safe_export = tk.BooleanVar(value=True)
        self.output_dir = tk.StringVar(value=os.path.abspath(os.path.join(os.getcwd(), "exports", "dataset_01")))

        self._build_layout()
        self._refresh_conversations()
        self._log("Ready.")

    # ---------------------------
    # Layout
    # ---------------------------

    def _build_layout(self) -> None:
        # Top neon bar
        top = tk.Frame(self.root, bg=THEME["panel2"], highlightthickness=1, highlightbackground=THEME["stroke"])
        top.pack(fill="x", padx=10, pady=(10, 8))

        title = tk.Label(
            top,
            text="Bandofy AI Export Studio",
            bg=THEME["panel2"],
            fg=_hex_mix(THEME["accent_g"], THEME["accent_p"], 0.35),
            font=("Segoe UI", 16, "bold"),
        )
        title.pack(side="top", pady=8)

        # Main grid container
        main = tk.Frame(self.root, bg=THEME["bg"])
        main.pack(fill="both", expand=True, padx=10)
        main.grid_columnconfigure(0, weight=2)
        main.grid_columnconfigure(1, weight=5)
        main.grid_columnconfigure(2, weight=3)
        main.grid_rowconfigure(0, weight=1)

        self.left = tk.Frame(main, bg=THEME["panel"], highlightthickness=1, highlightbackground=THEME["stroke"])
        self.center = tk.Frame(main, bg=THEME["panel"], highlightthickness=1, highlightbackground=THEME["stroke"])
        self.right = tk.Frame(main, bg=THEME["panel"], highlightthickness=1, highlightbackground=THEME["stroke"])

        self.left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.center.grid(row=0, column=1, sticky="nsew", padx=(0, 10))
        self.right.grid(row=0, column=2, sticky="nsew")

        self._build_left_panel()
        self._build_center_panel()
        self._build_right_panel()

        # Bottom activity log
        bottom = tk.Frame(self.root, bg=THEME["panel2"], highlightthickness=1, highlightbackground=THEME["stroke"])
        bottom.pack(fill="x", padx=10, pady=(8, 10))

        self.log_list = tk.Listbox(
            bottom,
            height=5,
            bg=THEME["panel2"],
            fg=THEME["text"],
            selectbackground=_hex_mix(THEME["panel2"], THEME["accent_g"], 0.25),
            activestyle="none",
            highlightthickness=0,
            bd=0,
            font=("Consolas", 9),
        )
        self.log_list.pack(fill="x", padx=10, pady=8)

    def _section_title(self, parent, text: str, accent: str) -> tk.Frame:
        fr = tk.Frame(parent, bg=THEME["panel"], highlightthickness=0)
        tk.Label(fr, text=text, bg=THEME["panel"], fg=THEME["text"], font=("Segoe UI", 11, "bold")).pack(side="left")
        tk.Label(fr, text="≡", bg=THEME["panel"], fg=accent, font=("Segoe UI", 12, "bold")).pack(side="right")
        return fr

    def _build_left_panel(self) -> None:
        self.left.grid_rowconfigure(1, weight=1)
        self.left.grid_rowconfigure(3, weight=1)

        # Conversations
        t1 = self._section_title(self.left, "Conversations", THEME["accent_p"])
        t1.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))

        search_row = tk.Frame(self.left, bg=THEME["panel"], highlightthickness=1, highlightbackground=THEME["stroke"])
        search_row.grid(row=1, column=0, sticky="ew", padx=10)
        self.conv_search = tk.StringVar(value="")
        e = tk.Entry(search_row, textvariable=self.conv_search, bg=THEME["panel2"], fg=THEME["text"], insertbackground=THEME["accent_g"], bd=0, font=("Segoe UI", 10))
        e.pack(side="left", fill="x", expand=True, padx=8, pady=8)
        e.bind("<Return>", lambda _ev: self._refresh_conversations())
        tk.Button(search_row, text="⌕", command=self._refresh_conversations, bg=THEME["panel2"], fg=THEME["accent_g"], bd=0, font=("Segoe UI", 12, "bold"), cursor="hand2").pack(side="right", padx=8)

        self.conv_list = tk.Listbox(
            self.left,
            bg=THEME["panel"],
            fg=THEME["text"],
            bd=0,
            highlightthickness=0,
            activestyle="none",
            selectbackground=_hex_mix(THEME["panel"], THEME["accent_g"], 0.25),
            font=("Segoe UI", 10),
        )
        self.conv_list.grid(row=2, column=0, sticky="nsew", padx=10, pady=(10, 10))
        self.conv_list.bind("<<ListboxSelect>>", self._on_select_conversation)

        # Projects
        t2 = self._section_title(self.left, "Projects", THEME["accent_g"])
        t2.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 6))

        self.project_list = tk.Listbox(
            self.left,
            bg=THEME["panel"],
            fg=THEME["muted"],
            bd=0,
            highlightthickness=0,
            activestyle="none",
            selectbackground=_hex_mix(THEME["panel"], THEME["accent_p"], 0.22),
            font=("Segoe UI", 10),
            height=6,
        )
        for item in ["Knowledge Base", "Debug Tools", "Trip Planner"]:
            self.project_list.insert(tk.END, f"•  {item}")
        self.project_list.grid(row=4, column=0, sticky="ew", padx=10, pady=(0, 10))

        # Import button
        import_wrap = tk.Frame(self.left, bg=THEME["panel"])
        import_wrap.grid(row=5, column=0, sticky="ew", padx=10, pady=(0, 10))
        NeonButton(import_wrap, "Import ChatGPT Export", self._on_import_zip, THEME["accent_g"], big=True).pack(fill="x")

    def _build_center_panel(self) -> None:
        self.center.grid_rowconfigure(1, weight=1)
        self.center.grid_rowconfigure(2, weight=0)

        # Header
        hdr = tk.Frame(self.center, bg=THEME["panel"], highlightthickness=0)
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

        # small action icons
        tk.Button(hdr, text="↗", command=self._copy_conversation_id, bg=THEME["panel"], fg=THEME["accent_c"], bd=0, font=("Segoe UI", 11, "bold"), cursor="hand2").grid(row=0, column=1, padx=4)
        tk.Button(hdr, text="▶", command=self._run_pipeline, bg=THEME["panel"], fg=THEME["accent_p"], bd=0, font=("Segoe UI", 11, "bold"), cursor="hand2").grid(row=0, column=2, padx=4)

        # Messages scroll area
        self.msg_scroll = ScrollableFrame(self.center, bg=THEME["panel"])  # cards live in .inner
        self.msg_scroll.grid(row=1, column=0, sticky="nsew", padx=10)

        # Timeline line (visual only)
        line = tk.Frame(self.center, bg=THEME["panel"], highlightthickness=0)
        line.grid(row=2, column=0, sticky="ew", padx=10, pady=(8, 10))
        for i, col in enumerate([THEME["accent_p"], THEME["accent_c"], THEME["accent_g"]]):
            tk.Frame(line, bg=col, height=2).pack(side="left", fill="x", expand=True, padx=(0 if i == 0 else 6, 0))

    def _build_right_panel(self) -> None:
        self.right.grid_rowconfigure(7, weight=1)

        # Header
        hdr = tk.Frame(self.right, bg=THEME["panel"], highlightthickness=0)
        hdr.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 8))
        tk.Label(hdr, text="Export Studio", bg=THEME["panel"], fg=_hex_mix(THEME["accent_p"], THEME["accent_g"], 0.5), font=("Segoe UI", 12, "bold")).pack(side="left")
        tk.Label(hdr, text="≡", bg=THEME["panel"], fg=THEME["accent_p"], font=("Segoe UI", 12, "bold")).pack(side="right")

        # Pipeline buttons
        btns = tk.Frame(self.right, bg=THEME["panel"])
        btns.grid(row=1, column=0, sticky="ew", padx=10)

        NeonButton(btns, "Build SSR Dataset", self._build_ssr_dataset, THEME["accent_p"]).pack(fill="x", pady=5)
        NeonButton(btns, "Generate Pair Triples", self._generate_pair_triples, THEME["accent_p"]).pack(fill="x", pady=5)
        NeonButton(btns, "Create Distillation Pack", self._create_distillation_pack, THEME["accent_p"]).pack(fill="x", pady=5)
        NeonButton(btns, "Compile Clean Corpus", self._compile_clean_corpus, THEME["accent_p"]).pack(fill="x", pady=5)

        # Embeddings
        emb = tk.Frame(self.right, bg=THEME["panel"], highlightthickness=0)
        emb.grid(row=2, column=0, sticky="ew", padx=10, pady=(12, 4))
        tk.Label(emb, text="Embedding Model: ExportBrain-200", bg=THEME["panel"], fg=THEME["muted"], font=("Segoe UI", 9)).pack(anchor="w")

        NeonButton(self.right, "Generate Embeddings", self._generate_embeddings, THEME["accent_g"], big=True).grid(row=3, column=0, sticky="ew", padx=10, pady=(2, 8))
        NeonButton(self.right, "Semantic Search", self._semantic_search_popup, THEME["accent_p"], big=True).grid(row=4, column=0, sticky="ew", padx=10, pady=(0, 10))

        # Safe Export toggle
        safe = tk.Frame(self.right, bg=THEME["panel"], highlightthickness=0)
        safe.grid(row=5, column=0, sticky="ew", padx=10)
        tk.Label(safe, text="Safe Export: ON", bg=THEME["panel"], fg=THEME["muted"], font=("Segoe UI", 9, "bold")).pack(side="left")
        tk.Checkbutton(
            safe,
            variable=self.safe_export,
            onvalue=True,
            offvalue=False,
            bg=THEME["panel"],
            activebackground=THEME["panel"],
            fg=THEME["accent_g"],
            selectcolor=THEME["panel2"],
            bd=0,
        ).pack(side="right")

        # Output path
        out = tk.Frame(self.right, bg=THEME["panel"], highlightthickness=0)
        out.grid(row=6, column=0, sticky="ew", padx=10, pady=(8, 8))
        tk.Label(out, text="OUTPUT:", bg=THEME["panel"], fg=THEME["muted"], font=("Segoe UI", 9, "bold")).pack(side="left")
        ent = tk.Entry(out, textvariable=self.output_dir, bg=THEME["panel2"], fg=THEME["text"], insertbackground=THEME["accent_g"], bd=0, font=("Segoe UI", 9))
        ent.pack(side="left", fill="x", expand=True, padx=8, pady=6)
        tk.Button(out, text="…", command=self._pick_output_dir, bg=THEME["panel2"], fg=THEME["accent_p"], bd=0, font=("Segoe UI", 12, "bold"), cursor="hand2").pack(side="right")

        # Run pipeline (big)
        NeonButton(self.right, "RUN PIPELINE", self._run_pipeline, THEME["accent_p"], big=True).grid(row=7, column=0, sticky="ew", padx=10, pady=(0, 10))

        # Progress labels
        prog = tk.Frame(self.right, bg=THEME["panel"], highlightthickness=1, highlightbackground=THEME["stroke"])
        prog.grid(row=8, column=0, sticky="ew", padx=10)
        self.pair_rate = tk.Label(prog, text="PAIR MINING: 0 pairs/min", bg=THEME["panel"], fg=THEME["muted"], font=("Consolas", 9, "bold"))
        self.pair_rate.pack(anchor="w", padx=8, pady=(6, 2))
        self.embed_job = tk.Label(prog, text="EMBEDDING JOB: 0% Complete", bg=THEME["panel"], fg=THEME["muted"], font=("Consolas", 9, "bold"))
        self.embed_job.pack(anchor="w", padx=8, pady=(0, 6))

    # ---------------------------
    # Logging / status
    # ---------------------------

    def _log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        try:
            self.log_list.insert(tk.END, line)
            self.log_list.see(tk.END)
        except Exception:
            pass

    def _run_bg(self, label: str, fn):
        def runner():
            try:
                self._log(f"▶ {label}")
                fn()
                self._log(f"✔ {label}")
            except Exception as e:
                self._log(f"✖ {label}: {e}")
                try:
                    messagebox.showerror("Error", f"{label}\n\n{e}")
                except Exception:
                    pass

        threading.Thread(target=runner, daemon=True).start()

    # ---------------------------
    # Data loading
    # ---------------------------

    def _refresh_conversations(self) -> None:
        q = self.conv_search.get().strip()
        self.conversations = self.db.list_conversations(limit=600, search=q)

        self.conv_list.delete(0, tk.END)
        for c in self.conversations:
            title = c.get("title") or "(untitled)"
            self.conv_list.insert(tk.END, title)

        stats = self.db.stats()
        self._log(f"Indexed: {stats['conversations']} conversations | {stats['messages']} messages | {stats['chunks']} chunks")

    def _on_select_conversation(self, _ev=None) -> None:
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
        # clear
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

        card = tk.Frame(outer, bg=THEME["panel2"], highlightthickness=1, highlightbackground=_hex_mix(THEME["stroke"], accent, 0.7))
        card.pack(fill="x", padx=(6, 40) if is_user else (40, 6))

        head = tk.Frame(card, bg=THEME["panel2"])
        head.pack(fill="x", padx=10, pady=(8, 0))
        tk.Label(head, text=("User:" if is_user else "Assistant:"), bg=THEME["panel2"], fg=accent, font=("Segoe UI", 9, "bold")).pack(side="left")

        created = m.get("created_at") or ""
        try:
            ts = datetime.fromisoformat(created.replace("Z", "+00:00")).strftime("%b %d %H:%M")
        except Exception:
            ts = ""
        if ts:
            tk.Label(head, text=ts, bg=THEME["panel2"], fg=THEME["muted"], font=("Segoe UI", 8)).pack(side="right")

        body = tk.Label(
            card,
            text=(m.get("content_text") or "").strip(),
            bg=THEME["panel2"],
            fg=THEME["text"],
            font=("Segoe UI", 10),
            justify="left",
            anchor="w",
            wraplength=780,
        )
        body.pack(fill="x", padx=10, pady=(6, 8))

    # ---------------------------
    # UI actions
    # ---------------------------

    def _pick_output_dir(self) -> None:
        d = filedialog.askdirectory(title="Choose output directory")
        if d:
            self.output_dir.set(d)

    def _copy_conversation_id(self) -> None:
        if not self.current_conversation_id:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.current_conversation_id)
        self._log("Conversation ID copied to clipboard")

    def _on_import_zip(self) -> None:
        path = filedialog.askopenfilename(
            title="Select ChatGPT export ZIP",
            filetypes=[("ZIP files", "*.zip"), ("All files", "*.*")],
        )
        if not path:
            return

        def work():
            convs, msgs = import_export_zip(path)
            # compute hash inside core; import_export_zip returns parsed objects
            import hashlib
            h = hashlib.sha256(open(path, "rb").read()).hexdigest()
            n1 = self.db.upsert_conversations(convs, h)
            n2 = self.db.upsert_messages(msgs)
            self._refresh_conversations()
            self._log(f"Import Complete: {n1} conversations, {n2} messages processed")

        self._run_bg("Import ChatGPT Export", work)

    # ---------------------------
    # Pipeline actions (mapped to real exporters)
    # ---------------------------

    def _ensure_out(self) -> str:
        out = self.output_dir.get().strip() or os.path.abspath(os.path.join(os.getcwd(), "exports"))
        os.makedirs(out, exist_ok=True)
        return out

    def _build_ssr_dataset(self) -> None:
        def work():
            out = self._ensure_out()
            fp = os.path.join(out, "messages.jsonl")
            export_messages_jsonl(self.db, fp, redact=self.safe_export.get())
            self._log(f"SSR dataset exported: {fp}")

        self._run_bg("Build SSR Dataset", work)

    def _generate_pair_triples(self) -> None:
        def work():
            out = self._ensure_out()
            fp = os.path.join(out, "pairs.jsonl")
            export_training_pairs_jsonl(self.db, fp, redact=self.safe_export.get())
            # crude rate visualization: pairs/min ~ based on file line count and assumed 1 sec (demo)
            try:
                n = sum(1 for _ in open(fp, "r", encoding="utf-8"))
            except Exception:
                n = 0
            self.pair_rate.config(text=f"PAIR MINING: {n} pairs/min")
            self._log(f"Pair triples exported: {fp}")

        self._run_bg("Generate Pair Triples", work)

    def _compile_clean_corpus(self) -> None:
        def work():
            out = self._ensure_out()
            corpus_fp = os.path.join(out, "corpus.txt")
            with open(corpus_fp, "w", encoding="utf-8") as f:
                for c in self.db.list_conversations(limit=20000):
                    cid = c["id"]
                    title = (c.get("title") or "(untitled)").strip()
                    md = export_conversation_markdown(self.db, cid, redact=self.safe_export.get())
                    f.write(f"\n\n# {title}\n\n")
                    f.write(md)
                    f.write("\n")
            self._log(f"Clean corpus compiled: {corpus_fp}")

        self._run_bg("Compile Clean Corpus", work)

    def _create_distillation_pack(self) -> None:
        def work():
            out = self._ensure_out()
            pack_dir = os.path.join(out, "distillation_pack")
            os.makedirs(pack_dir, exist_ok=True)
            export_messages_jsonl(self.db, os.path.join(pack_dir, "messages.jsonl"), redact=self.safe_export.get())
            export_training_pairs_jsonl(self.db, os.path.join(pack_dir, "pairs.jsonl"), redact=self.safe_export.get())
            export_obsidian_vault(self.db, os.path.join(pack_dir, "obsidian_vault"), redact=self.safe_export.get())
            self._log(f"Distillation pack created: {pack_dir}")

        self._run_bg("Create Distillation Pack", work)

    def _generate_embeddings(self) -> None:
        def work():
            # In this local-first build, "embeddings" == chunk index (for retrieval).
            # If you want true vector embeddings, wire in your own model later.
            chunker = Chunker(max_chars=2500, overlap_chars=250)
            n = chunker.build_all(self.db)
            self.embed_job.config(text="EMBEDDING JOB: 100% Complete")
            self._log(f"Chunk index built: {n} chunks")

        self._run_bg("Generate Embeddings", work)

    def _semantic_search_popup(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("Semantic Search")
        win.configure(bg=THEME["bg"])
        win.geometry("820x520")

        top = tk.Frame(win, bg=THEME["panel"], highlightthickness=1, highlightbackground=THEME["stroke"])
        top.pack(fill="x", padx=10, pady=10)
        qv = tk.StringVar(value="")
        ent = tk.Entry(top, textvariable=qv, bg=THEME["panel2"], fg=THEME["text"], insertbackground=THEME["accent_g"], bd=0, font=("Segoe UI", 10))
        ent.pack(side="left", fill="x", expand=True, padx=8, pady=8)

        results = tk.Listbox(win, bg=THEME["panel"], fg=THEME["text"], bd=0, highlightthickness=0, activestyle="none", font=("Segoe UI", 10))
        results.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        def run():
            results.delete(0, tk.END)
            q = qv.get().strip()
            if not q:
                return
            hits = self.db.search_messages(q, limit=60)
            for h in hits:
                cid = h["conversation_id"]
                role = h.get("role")
                snippet = (h.get("content_text") or "").replace("\n", " ")
                snippet = snippet[:120] + ("…" if len(snippet) > 120 else "")
                results.insert(tk.END, f"{role}: {snippet}  [cid={cid}]")
            self._log(f"Search: {len(hits)} hits for '{q}'")

        def on_pick(_=None):
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

        tk.Button(top, text="Search", command=run, bg=_hex_mix(THEME["panel2"], THEME["accent_p"], 0.14), fg=THEME["text"], bd=0, font=("Segoe UI", 10, "bold"), cursor="hand2").pack(side="right", padx=8)
        ent.bind("<Return>", lambda _ev: run())
        results.bind("<Double-Button-1>", on_pick)

        ent.focus_set()

    def _load_conversation_by_id(self, cid: str) -> None:
        # ensure it exists
        rows = [c for c in self.conversations if c["id"] == cid]
        if not rows:
            # refresh and try again
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
        # A pragmatic default pipeline that matches the UI concept:
        # 1) Build SSR dataset
        # 2) Generate pairs
        # 3) Compile corpus
        # 4) Export Obsidian vault
        def work():
            out = self._ensure_out()
            export_messages_jsonl(self.db, os.path.join(out, "messages.jsonl"), redact=self.safe_export.get())
            export_training_pairs_jsonl(self.db, os.path.join(out, "pairs.jsonl"), redact=self.safe_export.get())
            self._compile_clean_corpus_sync(out)
            export_obsidian_vault(self.db, os.path.join(out, "obsidian_vault"), redact=self.safe_export.get())
            self._log(f"Pipeline completed -> {out}")

        self._run_bg("RUN PIPELINE", work)

    def _compile_clean_corpus_sync(self, out: str) -> None:
        corpus_fp = os.path.join(out, "corpus.txt")
        with open(corpus_fp, "w", encoding="utf-8") as f:
            for c in self.db.list_conversations(limit=20000):
                cid = c["id"]
                title = (c.get("title") or "(untitled)").strip()
                md = export_conversation_markdown(self.db, cid, redact=self.safe_export.get())
                f.write(f"\n\n# {title}\n\n")
                f.write(md)
                f.write("\n")

    # ---------------------------
    # Main
    # ---------------------------

    def run(self) -> None:
        self.root.mainloop()


def run_gui(db_path: str = "bandofy_export_studio.sqlite3") -> None:
    db = Database(db_path)
    try:
        ExportStudioApp(db).run()
    finally:
        db.close()
