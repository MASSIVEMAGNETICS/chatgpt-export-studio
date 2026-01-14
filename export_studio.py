#!/usr/bin/env python3
import sqlite3, json, hashlib, uuid, zipfile, os, re, sys, logging, tempfile, shutil
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, asdict, field
from collections import defaultdict, Counter
import traceback

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@dataclass
class SSR:
    id: str
    conversation_id: str
    source: str = "chatgpt_export"
    role: str = "user"
    created_at: float = 0.0
    turn_index: int = 0
    text: str = ""
    text_hash: str = ""
    intent: str = "other"
    flags: Dict[str, bool] = field(default_factory=dict)
    topics: List[str] = field(default_factory=list)
    links: Dict[str, Optional[str]] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)
    def to_dict(self) -> Dict:
        return asdict(self)

class DatabaseManager:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = None
        self.init_database()
    def init_database(self):
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, title TEXT, created_at REAL, updated_at REAL, raw_hash TEXT UNIQUE, meta_json TEXT);
            CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, parent_id TEXT, role TEXT NOT NULL, created_at REAL, turn_index INTEGER, content_text TEXT, content_json TEXT, meta_json TEXT, FOREIGN KEY (conversation_id) REFERENCES conversations(id));
            CREATE TABLE IF NOT EXISTS chunks (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, start_msg_id TEXT, end_msg_id TEXT, created_at REAL, token_estimate INTEGER, content_text TEXT, meta_json TEXT, FOREIGN KEY (conversation_id) REFERENCES conversations(id));
            CREATE TABLE IF NOT EXISTS tags (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL);
            CREATE TABLE IF NOT EXISTS conversation_tags (conversation_id TEXT NOT NULL, tag_id INTEGER NOT NULL, PRIMARY KEY (conversation_id, tag_id));
            CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at REAL, meta_json TEXT);
            CREATE TABLE IF NOT EXISTS project_items (project_id TEXT NOT NULL, conversation_id TEXT NOT NULL, PRIMARY KEY (project_id, conversation_id));
            CREATE TABLE IF NOT EXISTS artifacts (id TEXT PRIMARY KEY, project_id TEXT, created_at REAL, type TEXT NOT NULL, output_path TEXT, config_json TEXT, input_hash TEXT, output_hash TEXT);
            CREATE TABLE IF NOT EXISTS embeddings (id TEXT PRIMARY KEY, obj_type TEXT NOT NULL, obj_id TEXT NOT NULL, model_id TEXT NOT NULL, dim INTEGER NOT NULL, vector_blob BLOB NOT NULL, norm REAL, created_at REAL);
            CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);
            CREATE INDEX IF NOT EXISTS idx_chunks_conv ON chunks(conversation_id);
            CREATE INDEX IF NOT EXISTS idx_embeddings_obj ON embeddings(obj_type, obj_id);
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content_text, message_id UNINDEXED, conversation_id UNINDEXED);
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(content_text, chunk_id UNINDEXED, conversation_id UNINDEXED);
        """)
        self.conn.commit()
        logger.info(f"Database initialized: {self.db_path}")
    def close(self):
        if self.conn:
            self.conn.close()

class MetadataExtractor:
    INTERROGATIVES = {'what', 'why', 'how', 'when', 'where', 'who', 'which', 'can', 'could', 'should', 'would'}
    CODE_KEYWORDS = {'def', 'class', 'import', 'function', 'var', 'let', 'const', 'SELECT', 'return'}
    IMPERATIVE_VERBS = {'build', 'create', 'generate', 'make', 'write', 'implement', 'add', 'fix'}
    STOPWORDS = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'from'}
    @staticmethod
    def extract_metadata(text: str, role: str) -> Tuple[Dict[str, bool], str, List[str]]:
        text_lower = text.lower()
        flags = {
            'is_question': '?' in text or any(text_lower.startswith(w) for w in MetadataExtractor.INTERROGATIVES),
            'is_code': '```' in text or any(k in text_lower for k in MetadataExtractor.CODE_KEYWORDS),
            'is_list': len(re.findall(r'^\s*[-*•]\s', text, re.M)) >= 2,
            'has_steps': len(re.findall(r'^\s*\d+\.\s', text, re.M)) >= 2
        }
        if flags['is_question']:
            intent = 'question'
        elif any(v in text_lower.split()[:5] for v in MetadataExtractor.IMPERATIVE_VERBS):
            intent = 'instruction'
        elif any(w in text_lower for w in ['because', 'therefore', 'means']):
            intent = 'explanation'
        elif 'plan' in text_lower or 'roadmap' in text_lower:
            intent = 'plan'
        else:
            intent = 'other'
        words = [w for w in re.findall(r'\w+', text_lower) if w not in MetadataExtractor.STOPWORDS and len(w) > 2]
        topics = list(dict.fromkeys(words[:10]))
        return flags, intent, topics

class ImportPipeline:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.workspace = tempfile.mkdtemp(prefix='export_studio_')
    def cleanup(self):
        if os.path.exists(self.workspace):
            shutil.rmtree(self.workspace)
    def import_zip(self, zip_path: str, force_reimport: bool = False) -> Dict[str, Any]:
        logger.info(f"Importing from {zip_path}")
        stats = {'conversations': 0, 'messages': 0, 'skipped': 0, 'errors': []}
        try:
            extract_path = os.path.join(self.workspace, 'extract')
            os.makedirs(extract_path, exist_ok=True)
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_path)
            conversations_json = None
            for root, dirs, files in os.walk(extract_path):
                if 'conversations.json' in files:
                    conversations_json = os.path.join(root, 'conversations.json')
                    break
            if not conversations_json:
                raise FileNotFoundError("conversations.json not found in ZIP")
            with open(conversations_json, 'rb') as f:
                raw_bytes = f.read()
                raw_hash = hashlib.sha256(raw_bytes).hexdigest()
            cursor = self.db.conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM conversations WHERE raw_hash = ?", (raw_hash,))
            if cursor.fetchone()[0] > 0 and not force_reimport:
                logger.info("Already imported. Use force_reimport=True to re-import.")
                return stats
            data = json.loads(raw_bytes.decode('utf-8'))
            conversations = data if isinstance(data, list) else [data]
            for conv_data in conversations:
                try:
                    self._import_conversation(conv_data, raw_hash, stats)
                except Exception as e:
                    logger.warning(f"Failed to import conversation: {e}")
                    stats['skipped'] += 1
            self.db.conn.commit()
            logger.info(f"Import complete: {stats['conversations']} conversations, {stats['messages']} messages")
        except Exception as e:
            logger.error(f"Import failed: {e}")
            stats['errors'].append(str(e))
        finally:
            self.cleanup()
        return stats
    def _import_conversation(self, conv_data: Dict, raw_hash: str, stats: Dict):
        conv_id = conv_data.get('id') or str(uuid.uuid4())
        title = conv_data.get('title', 'Untitled')
        created_at = conv_data.get('create_time', datetime.now().timestamp())
        updated_at = conv_data.get('update_time', created_at)
        meta_json = json.dumps({k: v for k, v in conv_data.items() if k not in ['id', 'title', 'create_time', 'update_time', 'mapping']})
        self.db.conn.execute("INSERT OR REPLACE INTO conversations (id, title, created_at, updated_at, raw_hash, meta_json) VALUES (?, ?, ?, ?, ?, ?)", (conv_id, title, created_at, updated_at, raw_hash, meta_json))
        stats['conversations'] += 1
        mapping = conv_data.get('mapping', {})
        if mapping:
            messages = self._process_mapping(mapping, conv_id)
            stats['messages'] += len(messages)
    def _process_mapping(self, mapping: Dict, conv_id: str) -> List[Dict]:
        messages = []
        msg_lookup = {}
        for node_id, node_data in mapping.items():
            message = node_data.get('message')
            if not message:
                continue
            msg_id = message.get('id') or str(uuid.uuid4())
            parent_id = node_data.get('parent')
            content_parts = message.get('content', {})
            if isinstance(content_parts, dict):
                parts = content_parts.get('parts', [])
            elif isinstance(content_parts, list):
                parts = content_parts
            else:
                parts = [str(content_parts)]
            content_text = '\n'.join(str(part) for part in parts if part)
            role = message.get('author', {}).get('role', 'user') if isinstance(message.get('author'), dict) else 'user'
            created_at = message.get('create_time', 0)
            flags, intent, topics = MetadataExtractor.extract_metadata(content_text, role)
            text_hash = hashlib.sha256(content_text.encode('utf-8')).hexdigest()
            meta = {'node_id': node_id, 'intent': intent, 'flags': flags, 'topics': topics, 'text_hash': text_hash}
            msg_lookup[msg_id] = {'id': msg_id, 'parent_id': parent_id, 'role': role, 'created_at': created_at, 'content_text': content_text, 'content_json': json.dumps(message), 'meta': meta, 'turn_index': 0}
            messages.append(msg_lookup[msg_id])
        turn_index = 0
        for msg in sorted(messages, key=lambda x: x['created_at']):
            msg['turn_index'] = turn_index
            turn_index += 1
        for msg in messages:
            self.db.conn.execute("INSERT OR REPLACE INTO messages (id, conversation_id, parent_id, role, created_at, turn_index, content_text, content_json, meta_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (msg['id'], conv_id, msg['parent_id'], msg['role'], msg['created_at'], msg['turn_index'], msg['content_text'], msg['content_json'], json.dumps(msg['meta'])))
            self.db.conn.execute("INSERT INTO messages_fts(content_text, message_id, conversation_id) VALUES (?, ?, ?)", (msg['content_text'], msg['id'], conv_id))
        return messages

class ChunkingEngine:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
    def chunk_conversation(self, conversation_id: str, target_size: int = 1000) -> List[Dict]:
        cursor = self.db.conn.cursor()
        cursor.execute("SELECT id, role, content_text, turn_index, created_at FROM messages WHERE conversation_id = ? AND role IN ('user', 'assistant') ORDER BY turn_index", (conversation_id,))
        messages = [dict(row) for row in cursor.fetchall()]
        if not messages:
            return []
        chunks = []
        current_chunk = []
        current_size = 0
        for msg in messages:
            msg_size = len(msg['content_text']) // 4
            if current_size + msg_size > target_size and current_chunk:
                chunk_data = self._create_chunk(conversation_id, current_chunk)
                chunks.append(chunk_data)
                current_chunk = [current_chunk[-1]]
                current_size = len(current_chunk[0]['content_text']) // 4
            current_chunk.append(msg)
            current_size += msg_size
        if current_chunk:
            chunk_data = self._create_chunk(conversation_id, current_chunk)
            chunks.append(chunk_data)
        return chunks
    def _create_chunk(self, conversation_id: str, messages: List[Dict]) -> Dict:
        content_text = '\n\n'.join(f"[{msg['role']}]: {msg['content_text']}" for msg in messages)
        token_estimate = len(content_text) // 4
        start_msg_id = messages[0]['id']
        end_msg_id = messages[-1]['id']
        created_at = messages[0]['created_at']
        config_hash = hashlib.sha256(f"{conversation_id}_{start_msg_id}_{end_msg_id}".encode()).hexdigest()[:16]
        chunk_id = f"chunk_{config_hash}"
        meta = {'message_count': len(messages)}
        self.db.conn.execute("INSERT OR REPLACE INTO chunks (id, conversation_id, start_msg_id, end_msg_id, created_at, token_estimate, content_text, meta_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (chunk_id, conversation_id, start_msg_id, end_msg_id, created_at, token_estimate, content_text, json.dumps(meta)))
        self.db.conn.execute("INSERT INTO chunks_fts(content_text, chunk_id, conversation_id) VALUES (?, ?, ?)", (content_text, chunk_id, conversation_id))
        return {'id': chunk_id, 'conversation_id': conversation_id, 'content_text': content_text}
    def chunk_all_conversations(self) -> Dict[str, int]:
        cursor = self.db.conn.cursor()
        cursor.execute("SELECT id FROM conversations")
        conversation_ids = [row['id'] for row in cursor.fetchall()]
        stats = {'conversations': 0, 'chunks': 0}
        for conv_id in conversation_ids:
            chunks = self.chunk_conversation(conv_id)
            stats['conversations'] += 1
            stats['chunks'] += len(chunks)
        self.db.conn.commit()
        logger.info(f"Chunked: {stats['chunks']} chunks from {stats['conversations']} conversations")
        return stats

class SearchEngine:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
    def search_fts(self, query: str, limit: int = 50, search_type: str = 'messages') -> List[Dict]:
        cursor = self.db.conn.cursor()
        if search_type == 'messages':
            cursor.execute("SELECT m.id, m.conversation_id, m.role, m.content_text, m.created_at, c.title as conversation_title FROM messages_fts JOIN messages m ON messages_fts.message_id = m.id JOIN conversations c ON m.conversation_id = c.id WHERE messages_fts MATCH ? ORDER BY rank LIMIT ?", (query, limit))
        else:
            cursor.execute("SELECT ch.id, ch.conversation_id, ch.content_text, ch.token_estimate, c.title as conversation_title FROM chunks_fts JOIN chunks ch ON chunks_fts.chunk_id = ch.id JOIN conversations c ON ch.conversation_id = c.id WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?", (query, limit))
        return [dict(row) for row in cursor.fetchall()]

class ModelFoundry:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
    def export_corpus(self, output_dir: str) -> Dict[str, Any]:
        os.makedirs(output_dir, exist_ok=True)
        cursor = self.db.conn.cursor()
        cursor.execute("SELECT m.id, m.conversation_id, m.role, m.content_text, m.created_at, m.meta_json, c.title as conversation_title FROM messages m JOIN conversations c ON m.conversation_id = c.id ORDER BY m.created_at")
        records = []
        for row in cursor.fetchall():
            meta = json.loads(row['meta_json'] or '{}')
            record = {'id': row['id'], 'text': row['content_text'], 'role': row['role'], 'intent': meta.get('intent', 'other'), 'topics': meta.get('topics', []), 'time': row['created_at'], 'convo_id': row['conversation_id']}
            records.append(record)
        with open(os.path.join(output_dir, 'corpus.jsonl'), 'w', encoding='utf-8') as f:
            for record in records:
                f.write(json.dumps(record) + '\n')
        with open(os.path.join(output_dir, 'corpus.txt'), 'w', encoding='utf-8') as f:
            for record in records:
                f.write(f"=== [{record['role']}] ===\n{record['text']}\n\n---\n\n")
        manifest = {'type': 'corpus', 'created_at': datetime.now().isoformat(), 'record_count': len(records), 'output_files': ['corpus.jsonl', 'corpus.txt']}
        with open(os.path.join(output_dir, 'manifest.json'), 'w') as f:
            json.dump(manifest, f, indent=2)
        logger.info(f"Corpus exported: {len(records)} records")
        return manifest
    def export_ssr(self, output_dir: str) -> Dict[str, Any]:
        os.makedirs(output_dir, exist_ok=True)
        cursor = self.db.conn.cursor()
        cursor.execute("SELECT m.id, m.conversation_id, m.role, m.content_text, m.created_at, m.turn_index, m.parent_id, m.meta_json FROM messages m ORDER BY m.conversation_id, m.turn_index")
        records = []
        for row in cursor.fetchall():
            meta = json.loads(row['meta_json'] or '{}')
            ssr = SSR(id=row['id'], conversation_id=row['conversation_id'], role=row['role'], created_at=row['created_at'], turn_index=row['turn_index'], text=row['content_text'], text_hash=meta.get('text_hash', ''), intent=meta.get('intent', 'other'), flags=meta.get('flags', {}), topics=meta.get('topics', []), links={'responds_to': row['parent_id']}, meta=meta)
            records.append(ssr.to_dict())
        with open(os.path.join(output_dir, 'ssr.jsonl'), 'w', encoding='utf-8') as f:
            for record in records:
                f.write(json.dumps(record) + '\n')
        manifest = {'type': 'ssr', 'version': '1.0', 'created_at': datetime.now().isoformat(), 'record_count': len(records)}
        with open(os.path.join(output_dir, 'manifest.json'), 'w') as f:
            json.dump(manifest, f, indent=2)
        logger.info(f"SSR exported: {len(records)} records")
        return manifest
    def export_pairs(self, output_dir: str, max_pairs: int = 10000) -> Dict[str, Any]:
        os.makedirs(output_dir, exist_ok=True)
        cursor = self.db.conn.cursor()
        cursor.execute("SELECT m1.id as q_id, m1.content_text as question, m2.id as a_id, m2.content_text as answer, m1.conversation_id, m1.meta_json as q_meta FROM messages m1 JOIN messages m2 ON m2.parent_id = m1.id WHERE m1.role = 'user' AND m2.role = 'assistant' AND length(m1.content_text) > 10 AND length(m2.content_text) > 10 LIMIT ?", (max_pairs,))
        pairs = []
        for row in cursor.fetchall():
            q_meta = json.loads(row['q_meta'] or '{}')
            pair = {'id': f"pair_{row['q_id']}_{row['a_id']}", 'a': row['question'], 'b': row['answer'], 'label': 1, 'type': 'qa', 'meta': {'conversation_id': row['conversation_id'], 'intent': q_meta.get('intent')}}
            pairs.append(pair)
        with open(os.path.join(output_dir, 'pairs.jsonl'), 'w', encoding='utf-8') as f:
            for pair in pairs:
                f.write(json.dumps(pair) + '\n')
        manifest = {'type': 'pairs', 'created_at': datetime.now().isoformat(), 'pair_count': len(pairs)}
        with open(os.path.join(output_dir, 'manifest.json'), 'w') as f:
            json.dump(manifest, f, indent=2)
        logger.info(f"Pairs exported: {len(pairs)} pairs")
        return manifest
    def export_triples(self, output_dir: str, max_triples: int = 5000) -> Dict[str, Any]:
        os.makedirs(output_dir, exist_ok=True)
        cursor = self.db.conn.cursor()
        cursor.execute("SELECT m1.id as anchor_id, m1.content_text as anchor, m2.id as pos_id, m2.content_text as positive, m1.conversation_id FROM messages m1 JOIN messages m2 ON m2.parent_id = m1.id WHERE m1.role = 'user' AND m2.role = 'assistant' AND length(m1.content_text) > 10 AND length(m2.content_text) > 10 LIMIT ?", (max_triples,))
        anchors = cursor.fetchall()
        cursor.execute("SELECT id, content_text, conversation_id FROM messages WHERE role = 'assistant' AND length(content_text) > 10 ORDER BY RANDOM() LIMIT ?", (max_triples,))
        negatives = cursor.fetchall()
        triples = []
        for i, anchor_row in enumerate(anchors):
            if i >= len(negatives):
                break
            neg_row = negatives[i]
            if neg_row['conversation_id'] == anchor_row['conversation_id']:
                continue
            triple = {'anchor': anchor_row['anchor'], 'positive': anchor_row['positive'], 'negative': neg_row['content_text'], 'meta': {'anchor_id': anchor_row['anchor_id'], 'pos_id': anchor_row['pos_id']}}
            triples.append(triple)
        with open(os.path.join(output_dir, 'triples.jsonl'), 'w', encoding='utf-8') as f:
            for triple in triples:
                f.write(json.dumps(triple) + '\n')
        manifest = {'type': 'triples', 'created_at': datetime.now().isoformat(), 'triple_count': len(triples)}
        with open(os.path.join(output_dir, 'manifest.json'), 'w') as f:
            json.dump(manifest, f, indent=2)
        logger.info(f"Triples exported: {len(triples)} triples")
        return manifest

class PIIRedactor:
    EMAIL_PATTERN = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')
    PHONE_PATTERN = re.compile(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b')
    SSN_PATTERN = re.compile(r'\b\d{3}-\d{2}-\d{4}\b')
    @staticmethod
    def redact(text: str) -> Tuple[str, Dict]:
        redaction_map = {}
        def redact_email(match):
            email = match.group(0)
            token = f"[REDACTED_EMAIL_{len(redaction_map)}]"
            redaction_map[token] = email
            return token
        text = PIIRedactor.EMAIL_PATTERN.sub(redact_email, text)
        text = PIIRedactor.PHONE_PATTERN.sub('[REDACTED_PHONE]', text)
        text = PIIRedactor.SSN_PATTERN.sub('[REDACTED_SSN]', text)
        return text, redaction_map

def main():
    import argparse
    parser = argparse.ArgumentParser(description='ChatGPT Export Studio')
    parser.add_argument('--db', default='export_studio.db', help='Database path')
    subparsers = parser.add_subparsers(dest='command')
    import_parser = subparsers.add_parser('import', help='Import ZIP')
    import_parser.add_argument('zip_path')
    import_parser.add_argument('--force', action='store_true')
    search_parser = subparsers.add_parser('search', help='Search')
    search_parser.add_argument('query')
    search_parser.add_argument('--limit', type=int, default=10)
    chunk_parser = subparsers.add_parser('chunk', help='Chunk conversations')
    export_parser = subparsers.add_parser('export', help='Export datasets')
    export_parser.add_argument('type', choices=['corpus', 'ssr', 'pairs', 'triples'])
    export_parser.add_argument('output_dir')
    list_parser = subparsers.add_parser('list', help='List conversations')
    list_parser.add_argument('--limit', type=int, default=20)
    gui_parser = subparsers.add_parser('gui', help='Launch GUI')
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return
    db = DatabaseManager(args.db)
    try:
        if args.command == 'import':
            pipeline = ImportPipeline(db)
            stats = pipeline.import_zip(args.zip_path, force_reimport=args.force)
            print(f"Import: {stats['conversations']} conversations, {stats['messages']} messages")
        elif args.command == 'search':
            search = SearchEngine(db)
            results = search.search_fts(args.query, limit=args.limit)
            print(f"Found {len(results)} results:")
            for i, r in enumerate(results, 1):
                print(f"{i}. [{r.get('role')}] {r['content_text'][:200]}...")
        elif args.command == 'chunk':
            chunker = ChunkingEngine(db)
            stats = chunker.chunk_all_conversations()
            print(f"Chunked: {stats['chunks']} chunks")
        elif args.command == 'export':
            foundry = ModelFoundry(db)
            if args.type == 'corpus':
                manifest = foundry.export_corpus(args.output_dir)
            elif args.type == 'ssr':
                manifest = foundry.export_ssr(args.output_dir)
            elif args.type == 'pairs':
                manifest = foundry.export_pairs(args.output_dir)
            elif args.type == 'triples':
                manifest = foundry.export_triples(args.output_dir)
            print(f"Exported to {args.output_dir}")
        elif args.command == 'list':
            cursor = db.conn.cursor()
            cursor.execute("SELECT id, title, created_at, (SELECT COUNT(*) FROM messages WHERE conversation_id = conversations.id) as msg_count FROM conversations ORDER BY created_at DESC LIMIT ?", (args.limit,))
            print("Conversations:")
            for row in cursor.fetchall():
                dt = datetime.fromtimestamp(row['created_at']).strftime('%Y-%m-%d %H:%M')
                print(f"  {row['id'][:8]}... - {row['title'][:60]} ({row['msg_count']} msgs) - {dt}")
        elif args.command == 'gui':
            print("GUI mode - launching Tkinter...")
            launch_gui(db)
    finally:
        db.close()

def launch_gui(db: DatabaseManager):
    try:
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox, scrolledtext
    except ImportError:
        print("Tkinter not available")
        return
    class ExportStudioGUI:
        def __init__(self, root, db):
            self.root = root
            self.db = db
            self.root.title("ChatGPT Export Studio")
            self.root.geometry("1200x800")
            self.setup_ui()
            self.load_conversations()
        def setup_ui(self):
            pane = tk.PanedWindow(self.root, orient=tk.HORIZONTAL)
            pane.pack(fill=tk.BOTH, expand=True)
            left = ttk.Frame(pane, width=300)
            pane.add(left)
            ttk.Label(left, text="Conversations", font=('Arial', 12, 'bold')).pack(pady=5)
            search_frame = ttk.Frame(left)
            search_frame.pack(fill=tk.X, padx=5, pady=5)
            ttk.Label(search_frame, text="Search:").pack(side=tk.LEFT)
            self.search_entry = ttk.Entry(search_frame)
            self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
            ttk.Button(search_frame, text="Go", command=self.do_search).pack(side=tk.RIGHT)
            list_frame = ttk.Frame(left)
            list_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
            scrollbar = ttk.Scrollbar(list_frame)
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            self.conv_listbox = tk.Listbox(list_frame, yscrollcommand=scrollbar.set)
            self.conv_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            scrollbar.config(command=self.conv_listbox.yview)
            self.conv_listbox.bind('<<ListboxSelect>>', self.on_select)
            ttk.Button(left, text="Import ZIP", command=self.import_zip).pack(pady=5)
            center = ttk.Frame(pane)
            pane.add(center)
            ttk.Label(center, text="Messages", font=('Arial', 12, 'bold')).pack(pady=5)
            self.message_text = scrolledtext.ScrolledText(center, wrap=tk.WORD, width=60, height=40)
            self.message_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
            right = ttk.Frame(pane, width=300)
            pane.add(right)
            ttk.Label(right, text="Model Foundry", font=('Arial', 12, 'bold')).pack(pady=5)
            export_frame = ttk.LabelFrame(right, text="Export", padding=10)
            export_frame.pack(fill=tk.X, padx=5, pady=5)
            ttk.Button(export_frame, text="Export Corpus", command=lambda: self.export('corpus')).pack(fill=tk.X, pady=2)
            ttk.Button(export_frame, text="Export SSR", command=lambda: self.export('ssr')).pack(fill=tk.X, pady=2)
            ttk.Button(export_frame, text="Export Pairs", command=lambda: self.export('pairs')).pack(fill=tk.X, pady=2)
            ttk.Button(export_frame, text="Export Triples", command=lambda: self.export('triples')).pack(fill=tk.X, pady=2)
            chunk_frame = ttk.LabelFrame(right, text="Chunking", padding=10)
            chunk_frame.pack(fill=tk.X, padx=5, pady=5)
            ttk.Button(chunk_frame, text="Chunk All", command=self.chunk_all).pack(fill=tk.X, pady=2)
            stats_frame = ttk.LabelFrame(right, text="Stats", padding=10)
            stats_frame.pack(fill=tk.X, padx=5, pady=5)
            self.stats_text = tk.Text(stats_frame, height=10, width=30)
            self.stats_text.pack(fill=tk.BOTH, expand=True)
            self.update_stats()
        def load_conversations(self):
            self.conv_listbox.delete(0, tk.END)
            cursor = self.db.conn.cursor()
            cursor.execute("SELECT id, title FROM conversations ORDER BY created_at DESC LIMIT 100")
            self.conversations = [dict(row) for row in cursor.fetchall()]
            for conv in self.conversations:
                title = conv['title'][:60] if conv['title'] else 'Untitled'
                self.conv_listbox.insert(tk.END, title)
        def on_select(self, event):
            sel = self.conv_listbox.curselection()
            if not sel:
                return
            conv = self.conversations[sel[0]]
            self.display_conversation(conv['id'])
        def display_conversation(self, conv_id):
            self.message_text.delete(1.0, tk.END)
            cursor = self.db.conn.cursor()
            cursor.execute("SELECT role, content_text, created_at FROM messages WHERE conversation_id = ? ORDER BY turn_index", (conv_id,))
            for row in cursor.fetchall():
                dt = datetime.fromtimestamp(row['created_at']).strftime('%Y-%m-%d %H:%M:%S')
                self.message_text.insert(tk.END, f"\n[{row['role'].upper()}] - {dt}\n", 'header')
                self.message_text.insert(tk.END, f"{row['content_text']}\n")
                self.message_text.insert(tk.END, "\n" + "="*80 + "\n")
            self.message_text.tag_config('header', foreground='blue', font=('Arial', 10, 'bold'))
        def do_search(self):
            query = self.search_entry.get()
            if not query:
                return
            search = SearchEngine(self.db)
            results = search.search_fts(query, limit=50)
            self.message_text.delete(1.0, tk.END)
            self.message_text.insert(tk.END, f"Search: {query}\n\n", 'header')
            for i, r in enumerate(results, 1):
                self.message_text.insert(tk.END, f"{i}. [{r.get('role')}]\n", 'header')
                self.message_text.insert(tk.END, f"{r['content_text'][:300]}...\n\n")
        def import_zip(self):
            zip_path = filedialog.askopenfilename(title="Select ZIP", filetypes=[("ZIP", "*.zip")])
            if not zip_path:
                return
            try:
                pipeline = ImportPipeline(self.db)
                stats = pipeline.import_zip(zip_path)
                messagebox.showinfo("Import", f"Imported {stats['conversations']} conversations")
                self.load_conversations()
                self.update_stats()
            except Exception as e:
                messagebox.showerror("Error", str(e))
        def export(self, export_type):
            output_dir = filedialog.askdirectory(title="Output Directory")
            if not output_dir:
                return
            try:
                foundry = ModelFoundry(self.db)
                if export_type == 'corpus':
                    foundry.export_corpus(output_dir)
                elif export_type == 'ssr':
                    foundry.export_ssr(output_dir)
                elif export_type == 'pairs':
                    foundry.export_pairs(output_dir)
                elif export_type == 'triples':
                    foundry.export_triples(output_dir)
                messagebox.showinfo("Export", f"Exported to {output_dir}")
            except Exception as e:
                messagebox.showerror("Error", str(e))
        def chunk_all(self):
            try:
                chunker = ChunkingEngine(self.db)
                stats = chunker.chunk_all_conversations()
                messagebox.showinfo("Chunking", f"Created {stats['chunks']} chunks")
                self.update_stats()
            except Exception as e:
                messagebox.showerror("Error", str(e))
        def update_stats(self):
            self.stats_text.delete(1.0, tk.END)
            cursor = self.db.conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM conversations")
            conv_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM messages")
            msg_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM chunks")
            chunk_count = cursor.fetchone()[0]
            stats = f"Conversations: {conv_count}\nMessages: {msg_count}\nChunks: {chunk_count}"
            self.stats_text.insert(1.0, stats)
    root = tk.Tk()
    app = ExportStudioGUI(root, db)
    root.mainloop()

if __name__ == '__main__':
    main()
