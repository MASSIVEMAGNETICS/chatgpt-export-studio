# Bandofy-AI :: Export Studio

An offline, local-only **ChatGPT export explorer + transformer**.

You point it at an official ChatGPT export ZIP (must include `conversations.json`), and it gives you:

- A cyberpunk-dark GUI for browsing and searching your history
- SQLite storage + FTS search (fast)
- Exports:
  - Markdown (per conversation)
  - Messages JSONL (all messages)
  - Training pairs JSONL (user → assistant)
  - Obsidian vault export (all conversations)
- Optional PII redaction on exports

## Install

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

## Run (GUI)

```bash
python -m bandofy_export_studio gui
```

## Run (CLI)

Import:

```bash
python -m bandofy_export_studio import "path\to\chatgpt_export.zip"
```

List:

```bash
python -m bandofy_export_studio list --limit 50
```

Export Markdown:

```bash
python -m bandofy_export_studio export-md <conversation_id> --out out.md --redact
```

Export messages JSONL:

```bash
python -m bandofy_export_studio export-messages-jsonl --out messages.jsonl
```

Export training pairs JSONL:

```bash
python -m bandofy_export_studio export-pairs-jsonl --out pairs.jsonl
```

Export Obsidian vault:

```bash
python -m bandofy_export_studio export-obsidian --out-dir vault_dir
```

Chunk:

```bash
python -m bandofy_export_studio chunk --max-chars 2500 --overlap-chars 250
```

## Notes

- **No network calls**: This is intended to run fully offline.
- The parser supports the typical official export structure; if your export differs, open an issue and attach the schema (not your private content).
