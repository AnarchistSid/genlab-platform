"""Tests for PostgresBackend — Content_Memory + BanditArms tables (Phase 4).

These tests require a running PostgreSQL with the genlab database and
the content_memory + bandit_arms tables (migration c3d4e5f6a7b8).
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

_ALL_TABLES = ("content_memory", "bandit_arms")


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


# ── Content Memory CRUD ──────────────────────────────────────────────

class TestContentMemoryCRUD:

    def test_create_and_get(self, pg_backend):
        ch = f"hash_{uuid.uuid4().hex[:8]}"
        record_id = pg_backend.create("content_memory", {
            "niche_id": _TEST_NICHE,
            "content_hash": ch,
            "title": "Duplicate check story",
            "url": "https://example.com/story",
        })
        uuid.UUID(record_id)

        record = pg_backend.get("content_memory", record_id)
        assert record is not None
        assert record["fields"]["content_hash"] == ch
        assert record["fields"]["title"] == "Duplicate check story"

    def test_update_last_seen(self, pg_backend):
        ch = f"hash_{uuid.uuid4().hex[:8]}"
        record_id = pg_backend.create("content_memory", {
            "niche_id": _TEST_NICHE,
            "content_hash": ch,
            "title": "Update test",
        })
        pg_backend.update("content_memory", record_id, {
            "title": "Updated title",
        })
        record = pg_backend.get("content_memory", record_id)
        assert record["fields"]["title"] == "Updated title"

    def test_delete(self, pg_backend):
        ch = f"hash_{uuid.uuid4().hex[:8]}"
        record_id = pg_backend.create("content_memory", {
            "niche_id": _TEST_NICHE,
            "content_hash": ch,
        })
        pg_backend.delete("content_memory", record_id)
        assert pg_backend.get("content_memory", record_id) is None

    def test_find_by_hash(self, pg_backend):
        ch = f"hash_{uuid.uuid4().hex[:8]}"
        pg_backend.create("content_memory", {
            "niche_id": _TEST_NICHE,
            "content_hash": ch,
            "title": "Findable",
        })
        results = pg_backend.find(
            "content_memory",
            formula=f"{{content_hash}}='{ch}'",
            niche_id=_TEST_NICHE,
        )
        assert len(results) >= 1
        assert any(r["fields"]["content_hash"] == ch for r in results)


# ── Bandit Arms CRUD ─────────────────────────────────────────────────

class TestBanditArmsCRUD:

    def test_create_and_get(self, pg_backend):
        aid = f"arm_{uuid.uuid4().hex[:8]}"
        record_id = pg_backend.create("bandit_arms", {
            "niche_id": _TEST_NICHE,
            "arm_id": aid,
            "alpha": 2.0,
            "beta": 1.5,
            "n_plays": 10,
            "linucb_state": {"A": [[1, 0], [0, 1]], "b": [0.1, 0.2]},
        })
        uuid.UUID(record_id)

        record = pg_backend.get("bandit_arms", record_id)
        assert record is not None
        assert record["fields"]["arm_id"] == aid
        assert record["fields"]["alpha"] == 2.0
        assert record["fields"]["beta"] == 1.5
        assert record["fields"]["n_plays"] == 10

    def test_update_bandit_state(self, pg_backend):
        aid = f"arm_{uuid.uuid4().hex[:8]}"
        record_id = pg_backend.create("bandit_arms", {
            "niche_id": _TEST_NICHE,
            "arm_id": aid,
            "alpha": 1.0,
            "beta": 1.0,
            "n_plays": 0,
        })
        pg_backend.update("bandit_arms", record_id, {
            "alpha": 3.0,
            "beta": 2.0,
            "n_plays": 5,
        })
        record = pg_backend.get("bandit_arms", record_id)
        assert record["fields"]["alpha"] == 3.0
        assert record["fields"]["n_plays"] == 5

    def test_delete(self, pg_backend):
        aid = f"arm_{uuid.uuid4().hex[:8]}"
        record_id = pg_backend.create("bandit_arms", {
            "niche_id": _TEST_NICHE,
            "arm_id": aid,
            "alpha": 1.0,
            "beta": 1.0,
            "n_plays": 0,
        })
        pg_backend.delete("bandit_arms", record_id)
        assert pg_backend.get("bandit_arms", record_id) is None


# ── RLS for Phase 4 ─────────────────────────────────────────────────

class TestPhase4RLS:

    def test_content_memory_rls_isolates(self, pg_backend):
        niche_a = f"rls_test_{uuid.uuid4().hex[:6]}"
        niche_b = f"rls_test_{uuid.uuid4().hex[:6]}"

        pg_backend.create("content_memory", {
            "niche_id": niche_a,
            "content_hash": f"rls_cm_a_{uuid.uuid4().hex[:8]}",
        })
        pg_backend.create("content_memory", {
            "niche_id": niche_b,
            "content_hash": f"rls_cm_b_{uuid.uuid4().hex[:8]}",
        })

        results_a = pg_backend.find("content_memory", niche_id=niche_a)
        results_b = pg_backend.find("content_memory", niche_id=niche_b)

        assert all(r["fields"]["niche_id"] == niche_a for r in results_a)
        assert all(r["fields"]["niche_id"] == niche_b for r in results_b)

    def test_bandit_arms_rls_isolates(self, pg_backend):
        niche_a = f"rls_test_{uuid.uuid4().hex[:6]}"
        niche_b = f"rls_test_{uuid.uuid4().hex[:6]}"

        pg_backend.create("bandit_arms", {
            "niche_id": niche_a,
            "arm_id": f"rls_ba_a_{uuid.uuid4().hex[:8]}",
            "alpha": 1.0,
            "beta": 1.0,
            "n_plays": 0,
        })
        pg_backend.create("bandit_arms", {
            "niche_id": niche_b,
            "arm_id": f"rls_ba_b_{uuid.uuid4().hex[:8]}",
            "alpha": 1.0,
            "beta": 1.0,
            "n_plays": 0,
        })

        results_a = pg_backend.find("bandit_arms", niche_id=niche_a)
        results_b = pg_backend.find("bandit_arms", niche_id=niche_b)

        assert all(r["fields"]["niche_id"] == niche_a for r in results_a)
        assert all(r["fields"]["niche_id"] == niche_b for r in results_b)
