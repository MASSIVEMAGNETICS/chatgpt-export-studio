# =============================================================
# FILE: bandofy_export_studio/app.py
# VERSION: v1.0.0-EXPORTSTUDIOAPP-GODCORE
# NAME: BandofyExportStudioApp
# AUTHOR: Brandon "iambandobandz" Emery x Victor (Fractal Architect Mode)
# PURPOSE: CLI + GUI entrypoint for Bandofy-AI :: Export Studio.
# LICENSE: Proprietary - Massive Magnetics / Ethica AI / BHeard Network
# =============================================================

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .core import Database, import_export_zip, export_conversation_markdown, export_messages_jsonl, export_training_pairs_jsonl, export_obsidian_vault, Chunker

DEFAULT_DB = "bandofy_export_studio.sqlite3"

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bandofy-export-studio", description="Bandofy-AI :: Export Studio (offline ChatGPT export explorer + transformer)")
    p.add_argument("--db", default=DEFAULT_DB, help=f"SQLite DB path (default: {DEFAULT_DB})")

    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("gui", help="Launch cyberpunk GUI")

    imp = sub.add_parser("import", help="Import official ChatGPT export ZIP")
    imp.add_argument("zip", help="Path to ChatGPT export ZIP")

    ls = sub.add_parser("list", help="List conversations")
    ls.add_argument("--limit", type=int, default=50)
    ls.add_argument("--search", type=str, default="")

    md = sub.add_parser("export-md", help="Export a conversation to Markdown")
    md.add_argument("conversation_id", help="Conversation ID")
    md.add_argument("--out", required=True, help="Output markdown path")
    md.add_argument("--redact", action="store_true", help="Redact obvious PII in output")

    mj = sub.add_parser("export-messages-jsonl", help="Export all messages as JSONL")
    mj.add_argument("--out", required=True, help="Output jsonl path")
    mj.add_argument("--redact", action="store_true", help="Redact obvious PII in output")

    pj = sub.add_parser("export-pairs-jsonl", help="Export training pairs (user->assistant) as JSONL")
    pj.add_argument("--out", required=True, help="Output jsonl path")
    pj.add_argument("--redact", action="store_true", help="Redact obvious PII in output")

    ov = sub.add_parser("export-obsidian", help="Export all conversations as Obsidian vault")
    ov.add_argument("--out-dir", required=True, help="Output folder")
    ov.add_argument("--redact", action="store_true", help="Redact obvious PII in output")

    ch = sub.add_parser("chunk", help="Build chunks for all conversations")
    ch.add_argument("--max-chars", type=int, default=2500)
    ch.add_argument("--overlap-chars", type=int, default=250)

    return p

def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    db = Database(args.db)
    try:
        if args.cmd == "gui":
            from .gui import ExportStudioApp
            ExportStudioApp(db).run()
            return 0

        if args.cmd == "import":
            stats = import_export_zip(db, args.zip)
            print(f"Imported conversations: {stats['conversations']}, messages: {stats['messages']}")
            print(f"Source hash: {stats['source_hash']}")
            return 0

        if args.cmd == "list":
            convs = db.list_conversations(limit=args.limit, search=args.search)
            for c in convs:
                print(f"{c['id'][:8]}  {c.get('message_count',0):>4}  {c.get('title','Untitled')}")
            return 0

        if args.cmd == "export-md":
            md = export_conversation_markdown(db, args.conversation_id, redact=args.redact)
            Path(args.out).write_text(md, encoding="utf-8")
            print(f"Wrote {args.out}")
            return 0

        if args.cmd == "export-messages-jsonl":
            n = export_messages_jsonl(db, args.out, redact=args.redact)
            print(f"Wrote {n} rows -> {args.out}")
            return 0

        if args.cmd == "export-pairs-jsonl":
            n = export_training_pairs_jsonl(db, args.out, redact=args.redact)
            print(f"Wrote {n} pairs -> {args.out}")
            return 0

        if args.cmd == "export-obsidian":
            stats = export_obsidian_vault(db, args.out_dir, redact=args.redact)
            print(f"Wrote {stats['files_written']} files -> {args.out_dir}")
            return 0

        if args.cmd == "chunk":
            ch = Chunker(db, max_chars=args.max_chars, overlap_chars=args.overlap_chars)
            stats = ch.chunk_all()
            print(f"Chunked {stats['chunks']} chunks across {stats['conversations']} conversations")
            return 0

        raise RuntimeError("Unhandled command")
    finally:
        db.close()

if __name__ == "__main__":
    raise SystemExit(main())
