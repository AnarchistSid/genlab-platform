"""Tests for PostgresBackend — PendingEngagement + PendingFeedback tables (Phase 5).

These tests require a running PostgreSQL with the genlab database and
the pending_engagement + pending_feedback tables (migration d4e5f6a7b8c9).
Set POSTGRES_PASSWORD env var to enable these tests.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("POSTGRES_PASSWORD"),
    reason="POSTGRES_PASSWORD not set — skip PostgreSQL integration tests",
)

_TEST_NICHE = f"test_{uuid.uuid4().hex[:8]}"

_ALL_TABLES = ("pending_engagement", "pending_feedback")


@pytest.fixture
def pg_backend():
    from genlab_core.storage.postgres import PostgresBackend

    backend = PostgresBackend(
        host="localhost",
        port=5432,
        database="genlab",
        user="genlab",
        password=os.environ.get("POSTGRES_PASSWORD", ""),
        min_size=1,
        max_size=2,
    )
    yield backend

    async def cleanup():
        pool = backend._get_pool()
        async with pool.acquire() as conn:
            for tbl in _ALL_TABLES:
                await conn.execute(
                    f"DELETE FROM {tbl} WHERE niche_id = $1", _TEST_NICHE
                )
                await conn.execute(
                    f"DELETE FROM {tbl} WHERE niche_id LIKE 'rls_test_%'"
                )
        await pool.close()

    backend._ensure_pool()
    assert backend._loop is not None
    backend._loop.run_until_complete(cleanup())


# ── Pending Engagement CRUD ──────────────────────────────────────────

class TestPendingEngagementCRUD:

    def test_create_and_get(self, pg_backend):
        record_id = pg_backend.create("pending_engagement", {
            "niche_id": _TEST_NICHE,
            "post_id": "pe_post_1",
            "platform": "youtube",
            "status": "PENDING",
            "attempts": 0,
        })
        uuid.UUID(record_id)

        record = pg_backend.get("pending_engagement", record_id)
        assert record is not None
        assert record["fields"]["post_id"] == "pe_post_1"
        assert record["fields"]["platform"] == "youtube"
        assert record["fields"]["attempts"] == 0

    def test_update_attempts(self, pg_backend):
        record_id = pg_backend.create("pending_engagement", {
            "niche_id": _TEST_NICHE,
            "post_id": "pe_upd",
            "platform": "instagram",
            "status": "PENDING",
            "attempts": 0,
        })
        pg_backend.update("pending_engagement", record_id, {
            "attempts": 2,
            "status": "RETRYING",
        })
        record = pg_backend.get("pending_engagement", record_id)
        assert record["fields"]["attempts"] == 2
        assert record["fields"]["status"] == "RETRYING"

    def test_delete(self, pg_backend):
        record_id = pg_backend.create("pending_engagement", {
            "niche_id": _TEST_NICHE,
            "post_id": "pe_del",
            "platform": "x",
            "status": "PENDING",
            "attempts": 0,
        })
        pg_backend.delete("pending_engagement", record_id)
        assert pg_backend.get("pending_engagement", record_id) is None

    def test_find_pending(self, pg_backend):
        pid = f"pe_{uuid.uuid4().hex[:8]}"
        pg_backend.create("pending_engagement", {
            "niche_id": _TEST_NICHE,
            "post_id": pid,
            "platform": "facebook",
            "status": "PENDING",
            "attempts": 0,
        })
        results = pg_backend.find(
            "pending_engagement",
            formula=f"{{post_id}}='{pid}'",
            niche_id=_TEST_NICHE,
        )
        assert len(results) >= 1


# ── Pending Feedback CRUD ────────────────────────────────────────────

class TestPendingFeedbackCRUD:

    def test_create_and_get(self, pg_backend):
        tid = f"task_{uuid.uuid4().hex[:8]}"
        record_id = pg_backend.create("pending_feedback", {
            "niche_id": _TEST_NICHE,
            "task_id": tid,
            "post_id": "pf_post_1",
            "platform": "youtube",
            "arm_id": "arm_yt_gaming",
            "bandit_context": {"day": 1, "hour": 12, "source": "youtube"},
            "collection_status": "PENDING",
            "reward_48h": None,
        })
        uuid.UUID(record_id)

        record = pg_backend.get("pending_feedback", record_id)
        assert record is not None
        assert record["fields"]["task_id"] == tid
        assert record["fields"]["collection_status"] == "PENDING"

    def test_update_reward(self, pg_backend):
        tid = f"task_{uuid.uuid4().hex[:8]}"
        record_id = pg_backend.create("pending_feedback", {
            "niche_id": _TEST_NICHE,
            "task_id": tid,
            "post_id": "pf_upd",
            "platform": "instagram",
            "collection_status": "PENDING",
        })
        pg_backend.update("pending_feedback", record_id, {
            "collection_status": "COLLECTED_48H",
            "reward_48h": 0.75,
        })
        record = pg_backend.get("pending_feedback", record_id)
        assert record["fields"]["collection_status"] == "COLLECTED_48H"
        assert record["fields"]["reward_48h"] == 0.75

    def test_delete(self, pg_backend):
        tid = f"task_{uuid.uuid4().hex[:8]}"
        record_id = pg_backend.create("pending_feedback", {
            "niche_id": _TEST_NICHE,
            "task_id": tid,
            "post_id": "pf_del",
            "platform": "x",
            "collection_status": "PENDING",
        })
        pg_backend.delete("pending_feedback", record_id)
        assert pg_backend.get("pending_feedback", record_id) is None

    def test_bandit_context_jsonb(self, pg_backend):
        """bandit_context should be stored as JSONB and retrieved correctly."""
        tid = f"task_{uuid.uuid4().hex[:8]}"
        ctx = {"day": 3, "hour": 18, "source": "youtube", "velocity": 0.92}
        record_id = pg_backend.create("pending_feedback", {
            "niche_id": _TEST_NICHE,
            "task_id": tid,
            "post_id": "pf_ctx",
            "platform": "youtube",
            "bandit_context": ctx,
            "collection_status": "PENDING",
        })
        record = pg_backend.get("pending_feedback", record_id)
        # bandit_context is a promoted JSONB column — should be a dict
        bc = record["fields"]["bandit_context"]
        if isinstance(bc, str):
            import json
            bc = json.loads(bc)
        assert bc["day"] == 3
        assert bc["velocity"] == 0.92


# ── RLS for Phase 5 ─────────────────────────────────────────────────

class TestPhase5RLS:

    def test_pending_engagement_rls_isolates(self, pg_backend):
        niche_a = f"rls_test_{uuid.uuid4().hex[:6]}"
        niche_b = f"rls_test_{uuid.uuid4().hex[:6]}"

        pg_backend.create("pending_engagement", {
            "niche_id": niche_a,
            "post_id": "rls_pe_a",
            "platform": "instagram",
            "status": "PENDING",
            "attempts": 0,
        })
        pg_backend.create("pending_engagement", {
            "niche_id": niche_b,
            "post_id": "rls_pe_b",
            "platform": "youtube",
            "status": "PENDING",
            "attempts": 0,
        })

        results_a = pg_backend.find("pending_engagement", niche_id=niche_a)
        results_b = pg_backend.find("pending_engagement", niche_id=niche_b)

        assert all(r["fields"]["niche_id"] == niche_a for r in results_a)
        assert all(r["fields"]["niche_id"] == niche_b for r in results_b)

    def test_pending_feedback_rls_isolates(self, pg_backend):
        niche_a = f"rls_test_{uuid.uuid4().hex[:6]}"
        niche_b = f"rls_test_{uuid.uuid4().hex[:6]}"

        pg_backend.create("pending_feedback", {
            "niche_id": niche_a,
            "task_id": f"rls_pf_a_{uuid.uuid4().hex[:8]}",
            "post_id": "rls_pf_a",
            "platform": "instagram",
            "collection_status": "PENDING",
        })
        pg_backend.create("pending_feedback", {
            "niche_id": niche_b,
            "task_id": f"rls_pf_b_{uuid.uuid4().hex[:8]}",
            "post_id": "rls_pf_b",
            "platform": "youtube",
            "collection_status": "PENDING",
        })

        results_a = pg_backend.find("pending_feedback", niche_id=niche_a)
        results_b = pg_backend.find("pending_feedback", niche_id=niche_b)

        assert all(r["fields"]["niche_id"] == niche_a for r in results_a)
        assert all(r["fields"]["niche_id"] == niche_b for r in results_b)
