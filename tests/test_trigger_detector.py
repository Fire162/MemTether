"""Tests for Proactive Memory Recall trigger_detector."""
import os
import sqlite3
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trigger_detector import detect_triggers, auto_context


@pytest.fixture
def entity_db(tmp_path):
    """Create a test DB with known entities + facts table for auto_context."""
    db = str(tmp_path / "test_trigger.db")
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE fact_entities (id INTEGER PRIMARY KEY, entity_name TEXT, attr TEXT, value TEXT, fact_uid TEXT, source TEXT, ts TEXT, valid_from TEXT, valid_to TEXT);
        CREATE TABLE tool_assets (id INTEGER PRIMARY KEY, asset_name TEXT, asset_type TEXT, path TEXT, description TEXT, tags TEXT);
        CREATE TABLE facts (id INTEGER PRIMARY KEY, uid TEXT, type TEXT, subject TEXT, content TEXT, status TEXT DEFAULT 'active', source TEXT, created_at TEXT);
    """)
    for entity in ["packy", "clash", "comfyui", "memtether"]:
        conn.execute("INSERT INTO fact_entities (entity_name) VALUES (?)", (entity,))
    for tool in ["gateway.py", "memsearch.py"]:
        conn.execute("INSERT INTO tool_assets (asset_name) VALUES (?)", (tool,))
    for content in ["packy is a proxy service", "clash proxy on port 7890", "comfyui is a diffusion UI"]:
        conn.execute("INSERT INTO facts (uid, content, status, source) VALUES (?,?,?,?)",
                     ("test-" + content[:10], content, "active", "test"))
    conn.commit()
    conn.close()
    return db


class TestDetectTriggers:
    def test_true_positive_packy(self, entity_db):
        triggers = detect_triggers("what is packy status?", entity_db)
        entities = [e for t in triggers for e in t.get("matched_entities", [])]
        assert "packy" in [e.lower() for e in entities]

    def test_true_positive_clash(self, entity_db):
        triggers = detect_triggers("clash proxy settings", entity_db)
        entities = [e for t in triggers for e in t.get("matched_entities", [])]
        assert "clash" in [e.lower() for e in entities]

    def test_false_positive_france(self, entity_db):
        triggers = detect_triggers("I went to France last week", entity_db)
        entities = [e for t in triggers for e in t.get("matched_entities", [])]
        assert "france" not in [e.lower() for e in entities]

    def test_false_positive_joke(self, entity_db):
        triggers = detect_triggers("tell me a joke", entity_db)
        assert len(triggers) == 0

    def test_empty_message(self, entity_db):
        assert detect_triggers("", entity_db) == []


class TestAutoContext:
    def test_context_contains_relevant_fact(self, entity_db):
        triggers = detect_triggers("what about packy?", entity_db)
        assert triggers, "should have triggered"
        ctx = auto_context(triggers, entity_db)
        assert isinstance(ctx, str)
        assert len(ctx) > 0
        assert "packy" in ctx.lower()

    def test_context_type_string(self, entity_db):
        triggers = detect_triggers("clash settings", entity_db)
        ctx = auto_context(triggers, entity_db)
        assert isinstance(ctx, str)


class TestIntegration:
    def test_latency_under_5ms(self, entity_db):
        import time
        t0 = time.perf_counter()
        detect_triggers("what is packy and clash and comfyui?", entity_db)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        assert elapsed_ms < 5, f"Trigger detection took {elapsed_ms:.1f}ms, expected <5ms"
