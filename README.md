# ChatGPT Export Studio

A local-only ChatGPT Export Explorer and Model Foundry that transforms raw chat history into richly structured semantic data.

## Features

- **Local-Only Operation**: No network calls, no telemetry, fully offline
- **Import ChatGPT Exports**: Import official export ZIPs with `conversations.json`
- **Structured Semantic Records (SSR)**: Normalize conversations into metadata-dense records
- **Hybrid Search**: SQLite FTS5 full-text search + local embeddings (future)
- **Model Foundry**: Generate clean corpora, training pairs, triples, and distillation datasets
- **Privacy-Preserving**: Optional PII redaction for safe exports
- **Deterministic**: Reproducible pipelines with hashed artifacts

## Installation

### Requirements

- Python 3.11 or higher
- No external dependencies required (uses Python stdlib only)

### Quick Start

```bash
# Clone the repository
git clone https://github.com/MASSIVEMAGNETICS/chatgpt-export-studio.git
cd chatgpt-export-studio

# Make executable
chmod +x export_studio.py

# Run the CLI
python3 export_studio.py --help

# Or launch the GUI
python3 export_studio.py gui
```

## Usage

### CLI Commands

#### Import a ChatGPT Export ZIP

```bash
python3 export_studio.py import /path/to/export.zip
```

#### Search Conversations

```bash
python3 export_studio.py search "machine learning" --limit 20
```

#### List Conversations

```bash
python3 export_studio.py list --limit 10
```

#### Chunk Conversations for RAG

```bash
python3 export_studio.py chunk
```

#### Export Datasets

```bash
# Export clean corpus
python3 export_studio.py export corpus ./output/corpus

# Export SSR (Structured Semantic Records)
python3 export_studio.py export ssr ./output/ssr

# Export training pairs (Q&A)
python3 export_studio.py export pairs ./output/pairs

# Export contrastive triples
python3 export_studio.py export triples ./output/triples
```

### GUI Mode

Launch the graphical interface:

```bash
python3 export_studio.py gui
```

The GUI provides:
- **Left Panel**: Conversations list with search
- **Center Panel**: Message viewer
- **Right Panel**: Model Foundry with export controls

## SSR (Structured Semantic Records)

Every message is normalized into an SSR v1 record with:

- `id`: Stable UUID
- `conversation_id`: Parent conversation
- `source`: Data source (chatgpt_export)
- `role`: user|assistant|system|tool
- `created_at`: Unix timestamp
- `turn_index`: Message order in conversation
- `text`: Flattened message content
- `text_hash`: SHA256 of content
- `intent`: question|instruction|explanation|plan|debug|other
- `flags`: is_question, is_code, is_list, has_steps
- `topics`: Extracted keywords
- `links`: Parent message references
- `meta`: Additional metadata (never discarded)

## Model Foundry Exports

### 1. Corpus Export

- `corpus.jsonl`: Structured records with metadata
- `corpus.txt`: Plain text with separators
- `manifest.json`: Export metadata

### 2. SSR Dataset

- `ssr.jsonl`: Full SSR objects with all fields
- Schema versioned for reproducibility

### 3. Training Pairs

- `pairs.jsonl`: Q&A pairs mined from conversations
- Positive pairs (user question → assistant answer)
- Includes metadata and conversation context

### 4. Contrastive Triples

- `triples.jsonl`: Anchor, positive, negative triplets
- For contrastive learning and embedding training
- Hard negatives from different conversations

## Database Schema

SQLite database with:

- **conversations**: Conversation metadata
- **messages**: Individual messages with FTS5 index
- **chunks**: Semantic chunks for RAG (800-1200 tokens)
- **tags**: Custom tags for organization
- **projects**: Export project management
- **artifacts**: Generated dataset tracking
- **embeddings**: Future: local embedding vectors

## Architecture

- **Single-file Python monolith**: `export_studio.py`
- **SQLite FTS5**: Fast full-text search
- **Tkinter GUI**: Zero web dependencies
- **Deterministic metadata**: Heuristic-based extraction
- **Reproducible exports**: Hashed inputs/outputs

## Building Windows Executable

```bash
# Install PyInstaller
pip install pyinstaller

# Build one-file executable
pyinstaller export_studio.spec

# Output: dist/ExportStudio.exe
```

## Privacy & Security

- **No network calls**: Fully offline operation
- **PII redaction**: Automatic detection and redaction of emails, phones, SSNs
- **Local storage**: All data stays on your machine
- **No telemetry**: Zero data collection

## Deterministic Pipeline

All operations are deterministic:

- Metadata extraction uses fixed heuristics
- Chunk IDs derived from content hashes
- Export artifacts include input/output hashes
- Reproducible across runs with same inputs

## Future Enhancements

- [ ] Local ONNX embeddings for semantic search
- [ ] Hybrid search (FTS5 + vector similarity)
- [ ] Additional export formats (Markdown, HTML)
- [ ] Advanced negative mining strategies
- [ ] Custom tagging and filtering
- [ ] Multi-export project management

## License

MIT License - See LICENSE file

## Contributing

Contributions welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make minimal, focused changes
4. Test thoroughly (CLI and GUI)
5. Submit a pull request

## Support

- **Issues**: [GitHub Issues](https://github.com/MASSIVEMAGNETICS/chatgpt-export-studio/issues)
- **Discussions**: [GitHub Discussions](https://github.com/MASSIVEMAGNETICS/chatgpt-export-studio/discussions)

---

**Built with privacy and reproducibility in mind. No cloud, no API keys, just local data processing.**
