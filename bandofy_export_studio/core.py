# =============================================================
# FILE: bandofy_export_studio/core.py
# VERSION: v1.0.0-EXPORTSTUDIO-GODCORE
# NAME: BandofyExportStudioCore
# AUTHOR: Brandon "iambandobandz" Emery x Victor (Fractal Architect Mode)
# PURPOSE: Offline-first ingestion, normalization, search, and export pipeline
#          for official ChatGPT export ZIPs (conversations.json).
# LICENSE: Proprietary - Massive Magnetics / Ethica AI / BHeard Network
# =============================================================

from __future__ import annotations

import json
import os
import re
import sqlite3
import zipfile
import hashlib
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

# -------------------------------
# Utility
# -------------------------------

def now_ts() -> int:
    return int(time.time())

def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()

def safe_str(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    try:
        return str(x)
    except Exception:
        return repr(x)

# -------------------------------
# PII Redaction (heuristic)
# -------------------------------

_PII_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\b[\w\.-]+@[\w\.-]+\.\w+\b", re.IGNORECASE), "[REDACTED_EMAIL]"),
    (re.compile(r"\b(?:\+?1[\s\-\.]?)?(?:\(\s*\d{3}\s*\)|\d{3})[\s\-\.]?\d{3}[\s\-\.]?\d{4}\b"), "[REDACTED_PHONE]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN]"),
    (re.compile(r"\b(?:\d[ -]*?){13,19}\b"), "[REDACTED_PAN]"),  # loose CC-like
]

def redact_pii(text: str) -> str:
    out = text
    for pat, repl in _PII_PATTERNS:
        out = pat.sub(repl, out)
    return out

# -------------------------------
# ChatGPT Export Parsing
# -------------------------------

@dataclass
class ParsedMessage:
    message_id: str
    conversation_id: str
    role: str
    content_text: str
    created_at: int
    turn_index: int

@dataclass
class ParsedConversation:
    conversation_id: str
    title: str
    created_at: int
    updated_at: int
    message_count: int

def _extract_text_from_message_obj(msg: Dict[str, Any]) -> str:
    """
    Handles common ChatGPT export formats. Official exports typically store:
      msg["content"]["parts"] as list[str] (or list of mixed types).
    """
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content") or {}
    if isinstance(content, dict):
        parts = content.get("parts")
        if isinstance(parts, list):
            # parts can include strings and sometimes dicts; stringify safely
            return "\n".join([safe_str(p) for p in parts if safe_str(p).strip() != ""]).strip()
        # some exports use "text" or "result"
        for k in ("text", "result", "value"):
            v = content.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    # fallback
    v = msg.get("text")
    return safe_str(v).strip()

def parse_conversations_json(conversations: Any) -> Tuple[List[ParsedConversation], List[ParsedMessage]]:
    """
    Parse a loaded conversations.json (list of conversations).
    Returns (conversations, messages).
    """
    parsed_convs: List[ParsedConversation] = []
    parsed_msgs: List[ParsedMessage] = []

    if not isinstance(conversations, list):
        raise ValueError("conversations.json root must be a list")

    for conv in conversations:
        if not isinstance(conv, dict):
            continue

        conv_id = safe_str(conv.get("id") or conv.get("conversation_id") or conv.get("uuid") or "")
        if not conv_id:
            # stable fallback: hash of title+times
            conv_id = sha256_text(safe_str(conv.get("title")) + safe_str(conv.get("create_time")))

        title = safe_str(conv.get("title") or "Untitled").strip() or "Untitled"
        created_at = int(conv.get("create_time") or conv.get("created_at") or 0) or 0
        updated_at = int(conv.get("update_time") or conv.get("updated_at") or created_at) or created_at

        mapping = conv.get("mapping")
        messages: List[Tuple[int, str, str, str]] = []  # (created_at, msg_id, role, text)

        if isinstance(mapping, dict):
            for node_id, node in mapping.items():
                if not isinstance(node, dict):
                    continue
                msg = node.get("message")
                if not isinstance(msg, dict):
                    continue
                author = msg.get("author") or {}
                role = safe_str((author.get("role") if isinstance(author, dict) else "") or "unknown").strip().lower()
                if role in ("tool", "system") and not _extract_text_from_message_obj(msg):
                    # skip empty tool/system nodes
                    continue
                msg_id = safe_str(msg.get("id") or node_id or "")
                text = _extract_text_from_message_obj(msg)
                if not text:
                    continue
                m_created = msg.get("create_time") or node.get("create_time") or created_at
                try:
                    m_created_i = int(m_created or 0)
                except Exception:
                    m_created_i = 0
                messages.append((m_created_i, msg_id, role or "unknown", text))

        # Some formats store messages as a list directly
        elif isinstance(conv.get("messages"), list):
            for msg in conv["messages"]:
                if not isinstance(msg, dict):
                    continue
                role = safe_str(msg.get("role") or msg.get("author") or "unknown").strip().lower()
                msg_id = safe_str(msg.get("id") or "")
                text = _extract_text_from_message_obj(msg) or safe_str(msg.get("content") or "")
                if not text:
                    continue
                m_created = msg.get("create_time") or msg.get("created_at") or created_at
                try:
                    m_created_i = int(m_created or 0)
                except Exception:
                    m_created_i = 0
                messages.append((m_created_i, msg_id or sha256_text(conv_id + text)[:16], role, text))

        messages.sort(key=lambda x: (x[0], x[1]))
        for i, (m_created_i, msg_id, role, text) in enumerate(messages):
            parsed_msgs.append(
                ParsedMessage(
                    message_id=msg_id,
                    conversation_id=conv_id,
                    role=role,
                    content_text=text,
                    created_at=m_created_i,
                    turn_index=i,
                )
            )

        parsed_convs.append(
            ParsedConversation(
                conversation_id=conv_id,
                title=title,
                created_at=created_at,
                updated_at=updated_at,
                message_count=len(messages),
            )
        )

    return parsed_convs, parsed_msgs

def load_conversations_from_export_zip(zip_path: str) -> Any:
    """
    Reads an official ChatGPT export ZIP and returns the parsed JSON object
    from conversations.json.
    """
    if not os.path.exists(zip_path):
        raise FileNotFoundError(zip_path)

    with zipfile.ZipFile(zip_path, "r") as z:
        # common paths
        candidates = [n for n in z.namelist() if n.endswith("conversations.json")]
        if not candidates:
            raise ValueError("No conversations.json found in ZIP")
        # prefer shortest path
        candidates.sort(key=lambda s: (len(s), s))
        with z.open(candidates[0]) as f:
            raw = f.read().decode("utf-8", errors="ignore")
            return json.loads(raw)

# -------------------------------
# SQLite Database + FTS
# -------------------------------

SCHEMA_SQL = r"""
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY,
  title TEXT,
  created_at INTEGER,
  updated_at INTEGER,
  message_count INTEGER,
  source_hash TEXT
);

CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  conversation_id TEXT,
  role TEXT,
  content_text TEXT,
  created_at INTEGER,
  turn_index INTEGER,
  FOREIGN KEY(conversation_id) REFERENCES conversations(id)
);

CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);

-- chunks are optional: derived artifacts to enable larger-grain retrieval / dataset generation
CREATE TABLE IF NOT EXISTS chunks (
  id TEXT PRIMARY KEY,
  conversation_id TEXT,
  chunk_index INTEGER,
  content_text TEXT,
  created_at INTEGER,
  FOREIGN KEY(conversation_id) REFERENCES conversations(id)
);

CREATE INDEX IF NOT EXISTS idx_chunks_conv ON chunks(conversation_id);

-- FTS5 for message content
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
USING fts5(content_text, role, conversation_id, content='messages', content_rowid='rowid');

-- Keep FTS in sync
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
  INSERT INTO messages_fts(rowid, content_text, role, conversation_id) VALUES (new.rowid, new.content_text, new.role, new.conversation_id);
END;

CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, content_text, role, conversation_id) VALUES ('delete', old.rowid, old.content_text, old.role, old.conversation_id);
END;

CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, content_text, role, conversation_id) VALUES ('delete', old.rowid, old.content_text, old.role, old.conversation_id);
  INSERT INTO messages_fts(rowid, content_text, role, conversation_id) VALUES (new.rowid, new.content_text, new.role, new.conversation_id);
END;
"""

class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        cur = self.conn.cursor()
        cur.executescript(SCHEMA_SQL)
        self.conn.commit()

    def close(self) -> None:
        try:
            self.conn.commit()
        finally:
            self.conn.close()

    # -------------
    # Upserts
    # -------------

    def upsert_conversations(self, convs: List[ParsedConversation], source_hash: str) -> int:
        cur = self.conn.cursor()
        n = 0
        for c in convs:
            cur.execute(
                """
                INSERT INTO conversations(id,title,created_at,updated_at,message_count,source_hash)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                  title=excluded.title,
                  created_at=excluded.created_at,
                  updated_at=excluded.updated_at,
                  message_count=excluded.message_count,
                  source_hash=excluded.source_hash
                """,
                (c.conversation_id, c.title, c.created_at, c.updated_at, c.message_count, source_hash),
            )
            n += 1
        self.conn.commit()
        return n

    def upsert_messages(self, msgs: List[ParsedMessage]) -> int:
        cur = self.conn.cursor()
        n = 0
        for m in msgs:
            cur.execute(
                """
                INSERT INTO messages(id,conversation_id,role,content_text,created_at,turn_index)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                  conversation_id=excluded.conversation_id,
                  role=excluded.role,
                  content_text=excluded.content_text,
                  created_at=excluded.created_at,
                  turn_index=excluded.turn_index
                """,
                (m.message_id, m.conversation_id, m.role, m.content_text, m.created_at, m.turn_index),
            )
            n += 1
        self.conn.commit()
        return n

    # -------------
    # Queries
    # -------------

    def list_conversations(self, limit: int = 200, offset: int = 0, search: str = "") -> List[Dict[str, Any]]:
        cur = self.conn.cursor()
        if search.strip():
            like = f"%{search.strip()}%"
            cur.execute(
                """
                SELECT id,title,created_at,updated_at,message_count
                FROM conversations
                WHERE title LIKE ?
                ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
                """,
                (like, limit, offset),
            )
        else:
            cur.execute(
                """
                SELECT id,title,created_at,updated_at,message_count
                FROM conversations
                ORDER BY updated_at DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            )
        return [dict(r) for r in cur.fetchall()]

    def get_messages_for_conversation(self, conversation_id: str) -> List[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT id,role,content_text,created_at,turn_index
            FROM messages
            WHERE conversation_id=?
            ORDER BY turn_index ASC
            """,
            (conversation_id,),
        )
        return [dict(r) for r in cur.fetchall()]

    def search_messages(self, query: str, limit: int = 50) -> List[Dict[str, Any]]:
        q = query.strip()
        if not q:
            return []
        cur = self.conn.cursor()
        # FTS5 query: escape quotes; allow simple terms
        safe = q.replace('"', '""')
        try:
            cur.execute(
                """
                SELECT m.id, m.conversation_id, m.role, m.content_text, m.created_at,
                       bm25(messages_fts) AS rank
                FROM messages_fts
                JOIN messages m ON messages_fts.rowid = m.rowid
                WHERE messages_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (safe, limit),
            )
            return [dict(r) for r in cur.fetchall()]
        except sqlite3.OperationalError:
            # fallback to LIKE
            like = f"%{q}%"
            cur.execute(
                """
                SELECT id, conversation_id, role, content_text, created_at, 0.0 AS rank
                FROM messages
                WHERE content_text LIKE ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (like, limit),
            )
            return [dict(r) for r in cur.fetchall()]

    def stats(self) -> Dict[str, int]:
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM conversations")
        convs = int(cur.fetchone()["n"])
        cur.execute("SELECT COUNT(*) AS n FROM messages")
        msgs = int(cur.fetchone()["n"])
        cur.execute("SELECT COUNT(*) AS n FROM chunks")
        chunks = int(cur.fetchone()["n"])
        return {"conversations": convs, "messages": msgs, "chunks": chunks}

# -------------------------------
# Chunking Engine
# -------------------------------

class Chunker:
    """
    Creates larger-grain chunks per conversation (useful for retrieval and dataset builds).
    Simple and deterministic: concatenates consecutive messages until max_chars.
    """
    def __init__(self, db: Database, max_chars: int = 2500, overlap_chars: int = 250):
        self.db = db
        self.max_chars = max(500, int(max_chars))
        self.overlap_chars = max(0, int(overlap_chars))

    def chunk_conversation(self, conversation_id: str) -> int:
        msgs = self.db.get_messages_for_conversation(conversation_id)
        if not msgs:
            return 0

        parts: List[str] = []
        created_at = msgs[0]["created_at"] or 0
        chunks: List[str] = []

        def flush():
            if not parts:
                return
            text = "\n\n".join(parts).strip()
            if text:
                chunks.append(text)

        for m in msgs:
            block = f"{m['role'].upper()}:\n{m['content_text']}".strip()
            candidate = ("\n\n".join(parts + [block])).strip()
            if len(candidate) > self.max_chars and parts:
                flush()
                # overlap
                if self.overlap_chars > 0:
                    tail = ("\n\n".join(parts)).strip()[-self.overlap_chars:]
                    parts.clear()
                    if tail.strip():
                        parts.append(tail.strip())
                else:
                    parts.clear()
            parts.append(block)

        flush()

        cur = self.db.conn.cursor()
        # delete old chunks
        cur.execute("DELETE FROM chunks WHERE conversation_id=?", (conversation_id,))
        for i, text in enumerate(chunks):
            cid = sha256_text(f"{conversation_id}:{i}:{text}")[:32]
            cur.execute(
                "INSERT OR REPLACE INTO chunks(id,conversation_id,chunk_index,content_text,created_at) VALUES(?,?,?,?,?)",
                (cid, conversation_id, i, text, created_at),
            )
        self.db.conn.commit()
        return len(chunks)

    def chunk_all(self) -> Dict[str, int]:
        convs = self.db.list_conversations(limit=10_000)
        total = 0
        for c in convs:
            total += self.chunk_conversation(c["id"])
        return {"conversations": len(convs), "chunks": total}

# -------------------------------
# Import / Export Pipelines
# -------------------------------

def import_export_zip(db: Database, zip_path: str) -> Dict[str, Any]:
    raw = load_conversations_from_export_zip(zip_path)
    # source hash ties DB state to artifact deterministically
    source_hash = sha256_text(json.dumps(raw, sort_keys=True)[:2_000_000])
    convs, msgs = parse_conversations_json(raw)
    c_n = db.upsert_conversations(convs, source_hash=source_hash)
    m_n = db.upsert_messages(msgs)
    return {"conversations": c_n, "messages": m_n, "source_hash": source_hash}

def export_conversation_markdown(db: Database, conversation_id: str, redact: bool = False) -> str:
    convs = db.list_conversations(limit=1, search="")
    # fetch title directly
    cur = db.conn.cursor()
    cur.execute("SELECT title,created_at,updated_at FROM conversations WHERE id=?", (conversation_id,))
    row = cur.fetchone()
    title = row["title"] if row else "Untitled"
    md = [f"# {title}", ""]
    msgs = db.get_messages_for_conversation(conversation_id)
    for m in msgs:
        role = (m["role"] or "unknown").strip().lower()
        body = m["content_text"] or ""
        if redact:
            body = redact_pii(body)
        md.append(f"## {role}")
        md.append("")
        md.append(body)
        md.append("")
    return "\n".join(md).strip() + "\n"

def export_messages_jsonl(db: Database, out_path: str, redact: bool = False) -> int:
    cur = db.conn.cursor()
    cur.execute("SELECT * FROM messages ORDER BY conversation_id, turn_index")
    n = 0
    with open(out_path, "w", encoding="utf-8") as f:
        for r in cur.fetchall():
            obj = dict(r)
            if redact:
                obj["content_text"] = redact_pii(obj.get("content_text", ""))
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
            n += 1
    return n

def export_training_pairs_jsonl(db: Database, out_path: str, redact: bool = False) -> int:
    """
    Emits adjacent USER -> ASSISTANT pairs as JSONL:
      {"prompt": "...", "completion": "...", "conversation_id": "...", "turn_index": ...}
    """
    cur = db.conn.cursor()
    cur.execute("SELECT conversation_id, role, content_text, turn_index FROM messages ORDER BY conversation_id, turn_index")
    rows = cur.fetchall()

    n = 0
    prev = None
    with open(out_path, "w", encoding="utf-8") as f:
        for r in rows:
            role = (r["role"] or "").lower()
            text = r["content_text"] or ""
            if redact:
                text = redact_pii(text)
            if prev and prev["conversation_id"] == r["conversation_id"]:
                if prev["role"] == "user" and role in ("assistant", "model"):
                    obj = {
                        "prompt": prev["content_text_redacted"] if redact else prev["content_text"],
                        "completion": text,
                        "conversation_id": r["conversation_id"],
                        "turn_index": int(r["turn_index"]),
                    }
                    f.write(json.dumps(obj, ensure_ascii=False) + "\n")
                    n += 1
            prev = {
                "conversation_id": r["conversation_id"],
                "role": role,
                "content_text": r["content_text"],
                "content_text_redacted": text,
            }
    return n

def export_obsidian_vault(db: Database, out_dir: str, redact: bool = False) -> Dict[str, Any]:
    os.makedirs(out_dir, exist_ok=True)
    convs = db.list_conversations(limit=50_000)
    index_lines = ["# Bandofy-AI Export Studio Vault", "", f"- Conversations: {len(convs)}", ""]
    written = 0
    for c in convs:
        cid = c["id"]
        title = (c["title"] or "Untitled").strip()
        safe_name = re.sub(r"[^a-zA-Z0-9 _\-]+", "", title).strip().replace(" ", "_")[:80] or cid[:8]
        fn = f"{safe_name}__{cid[:8]}.md"
        md = export_conversation_markdown(db, cid, redact=redact)
        with open(os.path.join(out_dir, fn), "w", encoding="utf-8") as f:
            f.write(md)
        index_lines.append(f"- [[{fn}]]")
        written += 1
    with open(os.path.join(out_dir, "INDEX.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(index_lines).strip() + "\n")
    return {"conversations": len(convs), "files_written": written}
