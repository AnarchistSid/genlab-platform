"""Tests for PostgresBackend — Stories + Assets tables (Phase 2).

These tests require a running PostgreSQL with the genlab database and
the stories + assets tables (created by Alembic migration a1b2c3d4e5f6).
Set POSTGRES_PASSWORD env var to enable these tests.
"""

from __future__ import annotations

import os
import uuid

import pytest

# Skip entire module if no PostgreSQL available
pytestmark = pytest.mark.skipif(
    not os.environ.get("POSTGRES_PASSWORD"),
    reason="POSTGRES_PASSWORD not set — skip PostgreSQL integration tests",
)

_TEST_NICHE = f"test_{uuid.uuid4().hex[:8]}"

_ALL_TABLES = ("stories", "assets")


@pytest.fixture
def pg_backend():
    """Create a PostgresBackend connected to local genlab database."""
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


# ── Stories CRUD ─────────────────────────────────────────────────────


class TestStoriesCRUD:
    """Test CRUD on the stories table."""

    def test_create_and_get(self, pg_backend):
        sid = f"story_{uuid.uuid4().hex[:8]}"
        record_id = pg_backend.create(
            "stories",
            {
                "niche_id": _TEST_NICHE,
                "story_id": sid,
                "title": "AI breakthrough",
                "url": "https://example.com/story",
                "source_name": "TechCrunch",
                "source_type": "rss",
                "status": "NEW",
                "score": 0.85,
            },
        )
        uuid.UUID(record_id)

        record = pg_backend.get("stories", record_id)
        assert record is not None
        assert record["fields"]["story_id"] == sid
        assert record["fields"]["title"] == "AI breakthrough"
        assert record["fields"]["score"] == 0.85

    def test_update(self, pg_backend):
        sid = f"story_{uuid.uuid4().hex[:8]}"
        record_id = pg_backend.create(
            "stories",
            {
                "niche_id": _TEST_NICHE,
                "story_id": sid,
                "status": "NEW",
            },
        )

        pg_backend.update(
            "stories",
            record_id,
            {
                "status": "USED",
                "video_id": "dQw4w9WgXcQ",
                "video_url": "https://youtube.com/watch?v=dQw4w9WgXcQ",
            },
        )

        record = pg_backend.get("stories", record_id)
        assert record["fields"]["status"] == "USED"
        assert record["fields"]["video_id"] == "dQw4w9WgXcQ"

    def test_delete(self, pg_backend):
        sid = f"story_{uuid.uuid4().hex[:8]}"
        record_id = pg_backend.create(
            "stories",
            {
                "niche_id": _TEST_NICHE,
                "story_id": sid,
                "status": "NEW",
            },
        )
        pg_backend.delete("stories", record_id)
        assert pg_backend.get("stories", record_id) is None

    def test_find_with_formula(self, pg_backend):
        sid = f"story_{uuid.uuid4().hex[:8]}"
        pg_backend.create(
            "stories",
            {
                "niche_id": _TEST_NICHE,
                "story_id": sid,
                "status": "NEW",
            },
        )

        results = pg_backend.find(
            "stories",
            formula=f"{{story_id}}='{sid}'",
            niche_id=_TEST_NICHE,
        )
        assert len(results) >= 1
        assert any(r["fields"]["story_id"] == sid for r in results)

    def test_extra_jsonb_overflow(self, pg_backend):
        sid = f"story_{uuid.uuid4().hex[:8]}"
        record_id = pg_backend.create(
            "stories",
            {
                "niche_id": _TEST_NICHE,
                "story_id": sid,
                "status": "NEW",
                "custom_meta": "overflow_value",
            },
        )
        record = pg_backend.get("stories", record_id)
        assert record["fields"]["custom_meta"] == "overflow_value"


# ── Assets CRUD ──────────────────────────────────────────────────────


class TestAssetsCRUD:
    """Test CRUD on the assets table."""

    def test_create_and_get(self, pg_backend):
        aid = f"asset_{uuid.uuid4().hex[:8]}"
        record_id = pg_backend.create(
            "assets",
            {
                "niche_id": _TEST_NICHE,
                "asset_id": aid,
                "story_id": "story_abc",
                "url": "https://example.com/clip.mp4",
                "asset_type": "video",
                "status": "DOWNLOADED",
                "source_type": "youtube",
                "file_path": "/tmp/clips/clip.mp4",
            },
        )
        uuid.UUID(record_id)

        record = pg_backend.get("assets", record_id)
        assert record is not None
        assert record["fields"]["asset_id"] == aid
        assert record["fields"]["asset_type"] == "video"
        assert record["fields"]["file_path"] == "/tmp/clips/clip.mp4"

    def test_update(self, pg_backend):
        aid = f"asset_{uuid.uuid4().hex[:8]}"
        record_id = pg_backend.create(
            "assets",
            {
                "niche_id": _TEST_NICHE,
                "asset_id": aid,
                "status": "NEW",
            },
        )
        pg_backend.update("assets", record_id, {"status": "RENDERED"})
        record = pg_backend.get("assets", record_id)
        assert record["fields"]["status"] == "RENDERED"

    def test_delete(self, pg_backend):
        aid = f"asset_{uuid.uuid4().hex[:8]}"
        record_id = pg_backend.create(
            "assets",
            {
                "niche_id": _TEST_NICHE,
                "asset_id": aid,
                "status": "NEW",
            },
        )
        pg_backend.delete("assets", record_id)
        assert pg_backend.get("assets", record_id) is None


# ── RLS for Stories + Assets ─────────────────────────────────────────


class TestPhase2RLS:
    """Test Row Level Security isolation for stories and assets."""

    def test_stories_rls_isolates_niches(self, pg_backend):
        niche_a = f"rls_test_{uuid.uuid4().hex[:6]}"
        niche_b = f"rls_test_{uuid.uuid4().hex[:6]}"

        pg_backend.create(
            "stories",
            {
                "niche_id": niche_a,
                "story_id": f"rls_sa_{uuid.uuid4().hex[:8]}",
                "status": "NEW",
            },
        )
        pg_backend.create(
            "stories",
            {
                "niche_id": niche_b,
                "story_id": f"rls_sb_{uuid.uuid4().hex[:8]}",
                "status": "NEW",
            },
        )

        results_a = pg_backend.find("stories", niche_id=niche_a)
        results_b = pg_backend.find("stories", niche_id=niche_b)

        assert all(r["fields"]["niche_id"] == niche_a for r in results_a)
        assert all(r["fields"]["niche_id"] == niche_b for r in results_b)

    def test_assets_rls_isolates_niches(self, pg_backend):
        niche_a = f"rls_test_{uuid.uuid4().hex[:6]}"
        niche_b = f"rls_test_{uuid.uuid4().hex[:6]}"

        pg_backend.create(
            "assets",
            {
                "niche_id": niche_a,
                "asset_id": f"rls_aa_{uuid.uuid4().hex[:8]}",
                "status": "NEW",
            },
        )
        pg_backend.create(
            "assets",
            {
                "niche_id": niche_b,
                "asset_id": f"rls_ab_{uuid.uuid4().hex[:8]}",
                "status": "NEW",
            },
        )

        results_a = pg_backend.find("assets", niche_id=niche_a)
        results_b = pg_backend.find("assets", niche_id=niche_b)

        assert all(r["fields"]["niche_id"] == niche_a for r in results_a)
        assert all(r["fields"]["niche_id"] == niche_b for r in results_b)

    def test_stories_admin_mode_sees_all(self, pg_backend):
        niche = f"rls_test_{uuid.uuid4().hex[:6]}"
        sid = f"rls_all_{uuid.uuid4().hex[:8]}"
        pg_backend.create(
            "stories",
            {
                "niche_id": niche,
                "story_id": sid,
                "status": "NEW",
            },
        )

        results = pg_backend.find(
            "stories",
            formula=f"{{story_id}}='{sid}'",
            niche_id="",
        )
        assert len(results) >= 1
