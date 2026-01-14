# ChatGPT Export Studio - Usage Guide

## Quick Start

### 1. Getting Your ChatGPT Export

1. Go to ChatGPT Settings → Data Controls → Export Data
2. Wait for the email with your export ZIP
3. Download the ZIP file (typically named `chat-data-export-yyyy-mm-dd.zip`)

### 2. Import Your Export

```bash
python3 export_studio.py import /path/to/your-export.zip
```

This will:
- Extract the ZIP
- Find `conversations.json`
- Parse and normalize all conversations
- Store in SQLite database with FTS5 index
- Extract metadata (intent, topics, flags)

### 3. Explore Your Data

#### List Conversations

```bash
python3 export_studio.py list --limit 20
```

#### Search Messages

```bash
# Search for specific topics
python3 export_studio.py search "machine learning" --limit 10

# Search for code snippets
python3 export_studio.py search "python def" --limit 5
```

#### Launch GUI

```bash
python3 export_studio.py gui
```

The GUI provides:
- Browse all conversations
- Search with instant results
- View messages in chronological order
- Export datasets with one click

## Model Foundry

### Chunking for RAG

Before using chunks for semantic search or embeddings:

```bash
python3 export_studio.py chunk
```

This creates overlapping chunks of 800-1200 tokens (estimated) suitable for:
- Retrieval Augmented Generation (RAG)
- Embedding generation
- Context-aware search

### Export Clean Corpus

```bash
python3 export_studio.py export corpus ./my_corpus
```

Creates:
- `corpus.jsonl`: Structured records with role, intent, topics, timestamps
- `corpus.txt`: Plain text format with separators
- `manifest.json`: Export metadata and checksums

Use cases:
- Training language models
- Fine-tuning on your writing style
- Analysis and statistics

### Export SSR Dataset

```bash
python3 export_studio.py export ssr ./my_ssr
```

Creates:
- `ssr.jsonl`: Full Structured Semantic Records with all metadata
- Schema version tracked for reproducibility

SSR includes:
- Stable IDs
- Parent-child relationships
- Intent classification
- Topic extraction
- Content hashes
- Temporal information

### Export Training Pairs

```bash
python3 export_studio.py export pairs ./my_pairs
```

Creates:
- `pairs.jsonl`: Question-answer pairs mined from conversations

Format:
```json
{
  "id": "pair_xxx_yyy",
  "a": "user question",
  "b": "assistant answer",
  "label": 1,
  "type": "qa",
  "meta": {"conversation_id": "...", "intent": "question"}
}
```

Use cases:
- Supervised fine-tuning
- Question-answering models
- Instruction following

### Export Contrastive Triples

```bash
python3 export_studio.py export triples ./my_triples
```

Creates:
- `triples.jsonl`: Anchor, positive, negative triplets

Format:
```json
{
  "anchor": "user message",
  "positive": "correct assistant response",
  "negative": "unrelated response from different conversation",
  "meta": {"anchor_id": "...", "pos_id": "..."}
}
```

Use cases:
- Contrastive learning
- Embedding model training
- Semantic similarity models

## Advanced Usage

### Database Location

Specify custom database:

```bash
python3 export_studio.py --db /path/to/my_database.db list
```

Default: `export_studio.db` in current directory

### Re-importing

Force re-import (if you've edited the export):

```bash
python3 export_studio.py import my-export.zip --force
```

Without `--force`, duplicate exports (same hash) are skipped.

### Metadata Extraction

Metadata is extracted automatically using deterministic heuristics:

**Intent Detection:**
- `question`: Contains "?" or starts with interrogatives (what, why, how)
- `instruction`: Starts with imperative verbs (build, create, make)
- `explanation`: Contains because/therefore/means
- `plan`: Contains plan/roadmap/milestone keywords
- `other`: Default fallback

**Flags:**
- `is_question`: Question marks or interrogative starters
- `is_code`: Code fences (```) or high keyword density
- `is_list`: Multiple lines starting with `-`, `*`, or numbers
- `has_steps`: Numbered steps or "Step N" patterns

**Topics:**
- Top 10 keywords after removing stopwords
- Deterministic, reproducible

### PII Redaction

The `PIIRedactor` class automatically detects and redacts:
- Email addresses → `[REDACTED_EMAIL_N]`
- Phone numbers → `[REDACTED_PHONE]`
- SSN patterns → `[REDACTED_SSN]`

Future enhancement: Add `--redact` flag to export commands.

## Building Windows Executable

### Prerequisites

```bash
pip install pyinstaller
```

### Build

```bash
pyinstaller export_studio.spec
```

Output: `dist/ExportStudio.exe` (single-file executable)

### Run

```cmd
ExportStudio.exe gui
ExportStudio.exe import my-export.zip
ExportStudio.exe list
```

## Tips & Best Practices

### 1. Organize with Projects (Future)

The database schema supports projects for organizing conversations:
- Group related conversations
- Track exports per project
- Version control your datasets

### 2. Chunking Strategy

Default: 800-1200 tokens, 15% overlap

Good for:
- Embedding generation (512-1024 token models)
- RAG retrieval
- Context windows

### 3. Export Workflow

Recommended workflow:
1. Import export ZIP
2. Review conversations in GUI
3. Chunk conversations
4. Export corpus for analysis
5. Export pairs/triples for training
6. Export SSR for archival

### 4. Search Tips

FTS5 supports:
- Phrase search: `"exact phrase"`
- Boolean: `python AND machine learning`
- Prefix: `embed*` matches embed, embeddings, embedded

### 5. Reproducibility

Every export includes:
- Input hash (source data)
- Config hash (parameters)
- Output hash (generated data)
- Timestamps
- Record counts

This ensures:
- Reproducible pipelines
- Traceable artifacts
- Auditable datasets

## Troubleshooting

### Import fails with "conversations.json not found"

Ensure your ZIP contains `conversations.json` at the root or in a subdirectory.

### Search returns no results

Check:
1. Was the import successful?
2. Are messages in the database? `python3 export_studio.py list`
3. Try simpler queries first

### GUI doesn't launch

Ensure Tkinter is installed:
- Ubuntu: `sudo apt-get install python3-tk`
- macOS: Included with Python
- Windows: Included with Python

### Database is locked

Close other connections to the database. Only one write connection at a time.

## Performance

- **Import**: ~1000 conversations/second
- **Search**: FTS5 is fast, even with millions of messages
- **Chunking**: ~500 conversations/second
- **Export**: Limited by disk I/O

For very large exports (>100k conversations):
- Consider batching exports
- Use SSD storage
- Increase system memory

## Next Steps

1. ✅ Import your export
2. ✅ Explore with GUI or CLI
3. ✅ Chunk for RAG
4. ✅ Export datasets
5. 🚧 Add local embeddings (future enhancement)
6. 🚧 Implement hybrid search (future enhancement)
7. 🚧 Train custom models on your data

## Support

- Documentation: [README.md](README.md)
- Issues: [GitHub Issues](https://github.com/MASSIVEMAGNETICS/chatgpt-export-studio/issues)
- Source: [GitHub Repository](https://github.com/MASSIVEMAGNETICS/chatgpt-export-studio)

---

**Privacy First**: All processing happens locally. No data leaves your machine.
