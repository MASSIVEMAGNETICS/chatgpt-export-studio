#!/usr/bin/env python3
"""
Basic tests for ChatGPT Export Studio
Run with: python3 test_basic.py
"""

import os
import sys
import tempfile
import shutil
import json
import zipfile
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from export_studio import (
    DatabaseManager,
    ImportPipeline,
    MetadataExtractor,
    ChunkingEngine,
    SearchEngine,
    ModelFoundry,
    PIIRedactor
)

def test_metadata_extraction():
    """Test deterministic metadata extraction"""
    print("Testing metadata extraction...")
    
    # Test question detection
    flags, intent, topics = MetadataExtractor.extract_metadata("How do I use Python?", "user")
    assert flags['is_question'], "Should detect question"
    assert intent == "question", "Should classify as question"
    
    # Test code detection
    flags, intent, topics = MetadataExtractor.extract_metadata("```python\ndef hello():\n    pass\n```", "assistant")
    assert flags['is_code'], "Should detect code"
    
    # Test list detection
    flags, intent, topics = MetadataExtractor.extract_metadata("- Item 1\n- Item 2\n- Item 3", "assistant")
    assert flags['is_list'], "Should detect list"
    
    # Test steps detection
    flags, intent, topics = MetadataExtractor.extract_metadata("1. First\n2. Second\n3. Third", "assistant")
    assert flags['has_steps'], "Should detect steps"
    
    print("✓ Metadata extraction tests passed")

def test_pii_redaction():
    """Test PII redaction"""
    print("Testing PII redaction...")
    
    text = "Contact me at john@example.com or 555-123-4567"
    redacted, redaction_map = PIIRedactor.redact(text)
    
    assert "john@example.com" not in redacted, "Email should be redacted"
    assert "555-123-4567" not in redacted, "Phone should be redacted"
    assert "[REDACTED_" in redacted, "Should contain redaction tokens"
    
    print("✓ PII redaction tests passed")

def test_import_and_search():
    """Test import and search functionality"""
    print("Testing import and search...")
    
    # Create temporary workspace
    workspace = tempfile.mkdtemp()
    db_path = os.path.join(workspace, "test.db")
    
    try:
        # Create sample data
        sample_data = [
            {
                "id": "conv-test-001",
                "title": "Test Conversation",
                "create_time": 1704067200.0,
                "update_time": 1704067200.0,
                "mapping": {
                    "msg-test-001": {
                        "id": "msg-test-001",
                        "parent": None,
                        "message": {
                            "id": "msg-test-001",
                            "author": {"role": "user"},
                            "create_time": 1704067200.0,
                            "content": {
                                "parts": ["Tell me about machine learning"]
                            }
                        }
                    },
                    "msg-test-002": {
                        "id": "msg-test-002",
                        "parent": "msg-test-001",
                        "message": {
                            "id": "msg-test-002",
                            "author": {"role": "assistant"},
                            "create_time": 1704067300.0,
                            "content": {
                                "parts": ["Machine learning is a subset of AI that enables systems to learn from data."]
                            }
                        }
                    }
                }
            }
        ]
        
        # Create ZIP
        json_path = os.path.join(workspace, "conversations.json")
        with open(json_path, 'w') as f:
            json.dump(sample_data, f)
        
        zip_path = os.path.join(workspace, "test_export.zip")
        with zipfile.ZipFile(zip_path, 'w') as zf:
            zf.write(json_path, "conversations.json")
        
        # Test import
        db = DatabaseManager(db_path)
        pipeline = ImportPipeline(db)
        stats = pipeline.import_zip(zip_path)
        
        assert stats['conversations'] == 1, "Should import 1 conversation"
        assert stats['messages'] == 2, "Should import 2 messages"
        
        # Test search
        search = SearchEngine(db)
        results = search.search_fts("machine", limit=10)
        assert len(results) > 0, "Should find search results"
        
        # Test chunking
        chunker = ChunkingEngine(db)
        chunks_stats = chunker.chunk_all_conversations()
        assert chunks_stats['chunks'] > 0, "Should create chunks"
        
        db.close()
        
        print("✓ Import and search tests passed")
        
    finally:
        shutil.rmtree(workspace)

def test_exports():
    """Test export functionality"""
    print("Testing exports...")
    
    workspace = tempfile.mkdtemp()
    db_path = os.path.join(workspace, "test.db")
    
    try:
        # Create sample data
        db = DatabaseManager(db_path)
        
        # Insert test conversation
        db.conn.execute("""
            INSERT INTO conversations (id, title, created_at, updated_at, raw_hash, meta_json)
            VALUES ('conv-1', 'Test', 1704067200.0, 1704067200.0, 'hash1', '{}')
        """)
        
        # Insert test messages
        db.conn.execute("""
            INSERT INTO messages (id, conversation_id, parent_id, role, created_at, turn_index, content_text, content_json, meta_json)
            VALUES ('msg-1', 'conv-1', NULL, 'user', 1704067200.0, 0, 'Question?', '{}', '{"intent": "question", "flags": {}, "topics": []}')
        """)
        
        db.conn.execute("""
            INSERT INTO messages (id, conversation_id, parent_id, role, created_at, turn_index, content_text, content_json, meta_json)
            VALUES ('msg-2', 'conv-1', 'msg-1', 'assistant', 1704067300.0, 1, 'Answer.', '{}', '{"intent": "other", "flags": {}, "topics": []}')
        """)
        
        db.conn.commit()
        
        # Test corpus export
        foundry = ModelFoundry(db)
        corpus_dir = os.path.join(workspace, "corpus")
        manifest = foundry.export_corpus(corpus_dir)
        assert os.path.exists(os.path.join(corpus_dir, "corpus.jsonl")), "Should create corpus.jsonl"
        assert manifest['record_count'] == 2, "Should export 2 records"
        
        # Test SSR export
        ssr_dir = os.path.join(workspace, "ssr")
        manifest = foundry.export_ssr(ssr_dir)
        assert os.path.exists(os.path.join(ssr_dir, "ssr.jsonl")), "Should create ssr.jsonl"
        
        # Test pairs export
        pairs_dir = os.path.join(workspace, "pairs")
        manifest = foundry.export_pairs(pairs_dir)
        assert os.path.exists(os.path.join(pairs_dir, "pairs.jsonl")), "Should create pairs.jsonl"
        
        db.close()
        
        print("✓ Export tests passed")
        
    finally:
        shutil.rmtree(workspace)

def main():
    """Run all tests"""
    print("=" * 60)
    print("ChatGPT Export Studio - Basic Tests")
    print("=" * 60)
    print()
    
    try:
        test_metadata_extraction()
        test_pii_redaction()
        test_import_and_search()
        test_exports()
        
        print()
        print("=" * 60)
        print("✓ All tests passed!")
        print("=" * 60)
        return 0
        
    except AssertionError as e:
        print()
        print("=" * 60)
        print(f"✗ Test failed: {e}")
        print("=" * 60)
        return 1
    except Exception as e:
        print()
        print("=" * 60)
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        print("=" * 60)
        return 1

if __name__ == '__main__':
    sys.exit(main())
