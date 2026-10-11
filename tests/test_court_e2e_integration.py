"""End-to-end integration tests for Memory Court.
Proves all components work together as a system, not just individually.
"""
import json
import os
import sqlite3
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory_court import maybe_anchor, verify_chain


@pytest.fixture
def court_db(tmp_path):
    """Fresh DB with court tables ready."""
    db_path = str(tmp_path / "court_e2e.db")
    os.environ["MEM_DB"] = db_path
    import importlib
    import gateway as gw
    importlib.reload(gw)
    gw.init_db()
    return db_path, gw


def _db_status(db_path, uid):
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT status FROM facts WHERE uid=?", (uid,)).fetchone()
    conn.close()
    return row[0] if row else None


def _verify(db_path):
    """Open DB, run verify_chain, close, return result."""
    from memory_court import verify_chain as vc
    conn = sqlite3.connect(db_path)
    result = vc(conn)
    conn.close()
    return result


def _anchor(gw):
    """Force anchor on current DB."""
    conn = gw.get_conn()
    maybe_anchor(conn, force=True)
    conn.close()


class TestGateToCourtE2E:
    """Test 1: Correct -> supersede -> hash chain -> verify -> audit trail."""

    def test_gate_supersede_verify_export(self, court_db):
        db_path, gw = court_db

        # Step 1: Write original fact
        r1 = gw.remember("Clash proxy port is 7890", source="agent-a")
        assert r1["ok"]
        old_uid = r1["uid"]

        # Step 2: Anchor to lock current state
        _anchor(gw)
        ok_before, detail_before = _verify(db_path)
        assert ok_before, f"Chain should verify before correction"

        # Step 3: Correct (supersede)
        r2 = gw.correct(old_uid, "Clash proxy port is 7897", reason="port changed", by_agent="agent-b")
        assert r2["ok"]
        assert r2["op"] == "supersede"
        new_uid = r2["new_uid"]

        # Step 4: Anchor and verify chain after supersession
        _anchor(gw)
        ok_after, detail_after = _verify(db_path)
        assert ok_after, f"Chain should verify after supersession"

        # Step 5: Old fact is superseded, new is active
        assert _db_status(db_path, old_uid) == "superseded"
        assert _db_status(db_path, new_uid) == "active"

        # Step 6: Audit trail has entries
        conn = sqlite3.connect(db_path)
        audit_count = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        assert audit_count >= 2, f"Audit log should have entries, got {audit_count}"
        conn.close()


class TestCrossAgentReconciliation:
    """Test 2: Two agents write contradictions -> adjudication -> downstream sees only truth."""

    def test_two_agents_contradiction_resolved(self, court_db):
        db_path, gw = court_db

        # Step 1: Agent A: "Service Alpha is available"
        r1 = gw.remember("Service Alpha is available and running on port 3000", source="agent-alpha")
        assert r1["ok"]
        uid_a = r1["uid"]

        # Step 2: Agent B: contradictory "Service Alpha is deprecated"
        r2 = gw.remember("Service Alpha is deprecated and no longer available", source="agent-beta")
        assert r2["ok"]
        uid_b = r2["uid"]

        # Step 3: Both active (contradiction exists in storage)
        assert _db_status(db_path, uid_a) == "active"
        assert _db_status(db_path, uid_b) == "active"

        # Step 4: Adjudicate — supersede agent B's wrong info
        r3 = gw.correct(uid_b, "Service Alpha is available on port 3000 (confirmed)",
                        reason="Cross-agent reconciliation: agent-alpha is correct",
                        by_agent="adjudicator")
        assert r3["ok"]
        assert r3["op"] == "supersede"
        uid_c = r3["new_uid"]

        # Step 5: After adjudication
        assert _db_status(db_path, uid_b) == "superseded", "Contradictory fact superseded"
        assert _db_status(db_path, uid_c) == "active", "Corrected fact active"

        # Step 6: Hash chain verifies after full workflow
        _anchor(gw)
        ok, detail = _verify(db_path)
        assert ok, f"Chain should verify after adjudication"

        # Step 7: Complete audit trail
        conn = sqlite3.connect(db_path)
        audit_count = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        assert audit_count >= 3
        sup_count = conn.execute("SELECT COUNT(*) FROM supersessions").fetchone()[0]
        assert sup_count >= 1
        conn.close()

    def test_gate_tracks_conflicting_writes(self, court_db):
        """Conflicting writes are tracked, not silently overwritten."""
        db_path, gw = court_db

        r1 = gw.remember("Database on port 5432", source="agent-x")
        r2 = gw.remember("Database on port 3306", source="agent-y")

        assert _db_status(db_path, r1["uid"]) == "active"
        assert _db_status(db_path, r2["uid"]) == "active"
        # Both tracked — advisory gate, not blocking. Memory Court provides resolution tools.
