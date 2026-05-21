"""Tests for PostgresBackend — Publishing_Analytics + Analytics tables (Phase 3).

These tests require a running PostgreSQL with the genlab database and
the publishing_analytics + analytics tables (migration b2c3d4e5f6a7).
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

_ALL_TABLES = ("publishing_analytics", "analytics")


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
                await conn.execute(f"DELETE FROM {tbl} WHERE niche_id = $1", _TEST_NICHE)
                await conn.execute(f"DELETE FROM {tbl} WHERE niche_id LIKE 'rls_test_%'")
        await pool.close()

    backend._ensure_pool()
    assert backend._loop is not None
    backend._loop.run_until_complete(cleanup())


# ── Publishing Analytics CRUD ────────────────────────────────────────


class TestPublishingAnalyticsCRUD:
    def test_create_and_get(self, pg_backend):
        record_id = pg_backend.create(
            "publishing_analytics",
            {
                "niche_id": _TEST_NICHE,
                "post_id": "post_123",
                "platform": "instagram",
                "status": "PUBLISHED",
                "views": 1500,
                "likes": 200,
                "comments": 30,
                "shares": 10,
                "saves": 5,
                "metrics_fetched": True,
            },
        )
        uuid.UUID(record_id)

        record = pg_backend.get("publishing_analytics", record_id)
        assert record is not None
        assert record["fields"]["platform"] == "instagram"
        assert record["fields"]["views"] == 1500
        assert record["fields"]["metrics_fetched"] is True

    def test_update(self, pg_backend):
        record_id = pg_backend.create(
            "publishing_analytics",
            {
                "niche_id": _TEST_NICHE,
                "post_id": "post_upd",
                "platform": "youtube",
                "status": "PUBLISHED",
            },
        )
        pg_backend.update(
            "publishing_analytics",
            record_id,
            {
                "views": 5000,
                "likes": 400,
                "metrics_fetched": True,
            },
        )
        record = pg_backend.get("publishing_analytics", record_id)
        assert record["fields"]["views"] == 5000
        assert record["fields"]["metrics_fetched"] is True

    def test_delete(self, pg_backend):
        record_id = pg_backend.create(
            "publishing_analytics",
            {
                "niche_id": _TEST_NICHE,
                "post_id": "post_del",
                "platform": "x",
                "status": "PUBLISHED",
            },
        )
        pg_backend.delete("publishing_analytics", record_id)
        assert pg_backend.get("publishing_analytics", record_id) is None

    def test_find_by_platform(self, pg_backend):
        pid = f"post_{uuid.uuid4().hex[:8]}"
        pg_backend.create(
            "publishing_analytics",
            {
                "niche_id": _TEST_NICHE,
                "post_id": pid,
                "platform": "facebook",
                "status": "PUBLISHED",
            },
        )
        results = pg_backend.find(
            "publishing_analytics",
            formula=f"{{post_id}}='{pid}'",
            niche_id=_TEST_NICHE,
        )
        assert len(results) >= 1


# ── Analytics CRUD ───────────────────────────────────────────────────


class TestAnalyticsCRUD:
    def test_create_and_get(self, pg_backend):
        record_id = pg_backend.create(
            "analytics",
            {
                "niche_id": _TEST_NICHE,
                "post_id": "an_post_1",
                "platform": "instagram",
                "metric_type": "views",
                "value": 2500.0,
                "window": "24h",
            },
        )
        uuid.UUID(record_id)

        record = pg_backend.get("analytics", record_id)
        assert record is not None
        assert record["fields"]["metric_type"] == "views"
        assert record["fields"]["value"] == 2500.0
        assert record["fields"]["window"] == "24h"

    def test_update(self, pg_backend):
        record_id = pg_backend.create(
            "analytics",
            {
                "niche_id": _TEST_NICHE,
                "post_id": "an_post_upd",
                "platform": "youtube",
                "metric_type": "likes",
                "value": 100.0,
                "window": "6h",
            },
        )
        pg_backend.update("analytics", record_id, {"value": 250.0})
        record = pg_backend.get("analytics", record_id)
        assert record["fields"]["value"] == 250.0

    def test_delete(self, pg_backend):
        record_id = pg_backend.create(
            "analytics",
            {
                "niche_id": _TEST_NICHE,
                "post_id": "an_del",
                "platform": "x",
                "metric_type": "shares",
                "value": 10.0,
                "window": "48h",
            },
        )
        pg_backend.delete("analytics", record_id)
        assert pg_backend.get("analytics", record_id) is None


# ── RLS for Phase 3 ─────────────────────────────────────────────────


class TestPhase3RLS:
    def test_pub_analytics_rls_isolates(self, pg_backend):
        niche_a = f"rls_test_{uuid.uuid4().hex[:6]}"
        niche_b = f"rls_test_{uuid.uuid4().hex[:6]}"

        pg_backend.create(
            "publishing_analytics",
            {
                "niche_id": niche_a,
                "post_id": "rls_pa_a",
                "platform": "instagram",
                "status": "PUBLISHED",
            },
        )
        pg_backend.create(
            "publishing_analytics",
            {
                "niche_id": niche_b,
                "post_id": "rls_pa_b",
                "platform": "youtube",
                "status": "PUBLISHED",
            },
        )

        results_a = pg_backend.find("publishing_analytics", niche_id=niche_a)
        results_b = pg_backend.find("publishing_analytics", niche_id=niche_b)

        assert all(r["fields"]["niche_id"] == niche_a for r in results_a)
        assert all(r["fields"]["niche_id"] == niche_b for r in results_b)

    def test_analytics_rls_isolates(self, pg_backend):
        niche_a = f"rls_test_{uuid.uuid4().hex[:6]}"
        niche_b = f"rls_test_{uuid.uuid4().hex[:6]}"

        pg_backend.create(
            "analytics",
            {
                "niche_id": niche_a,
                "post_id": "rls_an_a",
                "platform": "instagram",
                "metric_type": "views",
                "value": 100.0,
                "window": "6h",
            },
        )
        pg_backend.create(
            "analytics",
            {
                "niche_id": niche_b,
                "post_id": "rls_an_b",
                "platform": "youtube",
                "metric_type": "views",
                "value": 200.0,
                "window": "6h",
            },
        )

        results_a = pg_backend.find("analytics", niche_id=niche_a)
        results_b = pg_backend.find("analytics", niche_id=niche_b)

        assert all(r["fields"]["niche_id"] == niche_a for r in results_a)
        assert all(r["fields"]["niche_id"] == niche_b for r in results_b)
