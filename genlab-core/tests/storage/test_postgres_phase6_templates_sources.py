"""Tests for PostgresBackend — Templates + Sources tables (Phase 6).

These tests require a running PostgreSQL with the genlab database and
the templates + sources tables (migration e5f6a7b8c9d0).
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

_ALL_TABLES = ("templates", "sources")


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


# ── Templates CRUD ───────────────────────────────────────────────────

class TestTemplatesCRUD:

    def test_create_and_get(self, pg_backend):
        tid = f"tmpl_{uuid.uuid4().hex[:8]}"
        record_id = pg_backend.create("templates", {
            "niche_id": _TEST_NICHE,
            "template_id": tid,
            "name": "Breaking News Reel",
            "category": "news",
            "max_duration": 45,
            "status": "ACTIVE",
        })
        uuid.UUID(record_id)

        record = pg_backend.get("templates", record_id)
        assert record is not None
        assert record["fields"]["template_id"] == tid
        assert record["fields"]["name"] == "Breaking News Reel"
        assert record["fields"]["max_duration"] == 45
        assert record["fields"]["status"] == "ACTIVE"

    def test_update(self, pg_backend):
        tid = f"tmpl_{uuid.uuid4().hex[:8]}"
        record_id = pg_backend.create("templates", {
            "niche_id": _TEST_NICHE,
            "template_id": tid,
            "name": "Old Template",
            "status": "ACTIVE",
        })
        pg_backend.update("templates", record_id, {
            "name": "Updated Template",
            "status": "INACTIVE",
        })
        record = pg_backend.get("templates", record_id)
        assert record["fields"]["name"] == "Updated Template"
        assert record["fields"]["status"] == "INACTIVE"

    def test_delete(self, pg_backend):
        tid = f"tmpl_{uuid.uuid4().hex[:8]}"
        record_id = pg_backend.create("templates", {
            "niche_id": _TEST_NICHE,
            "template_id": tid,
            "name": "To Delete",
            "status": "ACTIVE",
        })
        pg_backend.delete("templates", record_id)
        assert pg_backend.get("templates", record_id) is None

    def test_find_by_category(self, pg_backend):
        tid = f"tmpl_{uuid.uuid4().hex[:8]}"
        pg_backend.create("templates", {
            "niche_id": _TEST_NICHE,
            "template_id": tid,
            "name": "Findable",
            "category": "gaming_highlight",
            "status": "ACTIVE",
        })
        results = pg_backend.find(
            "templates",
            formula=f"{{template_id}}='{tid}'",
            niche_id=_TEST_NICHE,
        )
        assert len(results) >= 1

    def test_extra_jsonb_overflow(self, pg_backend):
        tid = f"tmpl_{uuid.uuid4().hex[:8]}"
        record_id = pg_backend.create("templates", {
            "niche_id": _TEST_NICHE,
            "template_id": tid,
            "name": "Custom",
            "status": "ACTIVE",
            "custom_layout": {"columns": 2},
        })
        record = pg_backend.get("templates", record_id)
        assert record["fields"]["custom_layout"] == {"columns": 2}


# ── Sources CRUD ─────────────────────────────────────────────────────

class TestSourcesCRUD:

    def test_create_and_get(self, pg_backend):
        record_id = pg_backend.create("sources", {
            "niche_id": _TEST_NICHE,
            "source_id": f"src_{uuid.uuid4().hex[:8]}",
            "name": "YouTube Gaming",
            "url": "https://youtube.com/gaming",
            "source_type": "youtube_api",
            "tier": "primary",
            "weight": 1.5,
            "status": "ACTIVE",
        })
        uuid.UUID(record_id)

        record = pg_backend.get("sources", record_id)
        assert record is not None
        assert record["fields"]["name"] == "YouTube Gaming"
        assert record["fields"]["tier"] == "primary"
        assert record["fields"]["weight"] == 1.5

    def test_update(self, pg_backend):
        record_id = pg_backend.create("sources", {
            "niche_id": _TEST_NICHE,
            "source_id": f"src_{uuid.uuid4().hex[:8]}",
            "name": "Old Source",
            "source_type": "rss",
            "status": "ACTIVE",
            "weight": 1.0,
        })
        pg_backend.update("sources", record_id, {
            "weight": 0.5,
            "status": "INACTIVE",
        })
        record = pg_backend.get("sources", record_id)
        assert record["fields"]["weight"] == 0.5
        assert record["fields"]["status"] == "INACTIVE"

    def test_delete(self, pg_backend):
        record_id = pg_backend.create("sources", {
            "niche_id": _TEST_NICHE,
            "source_id": f"src_{uuid.uuid4().hex[:8]}",
            "name": "Delete Me",
            "source_type": "rss",
            "status": "ACTIVE",
            "weight": 1.0,
        })
        pg_backend.delete("sources", record_id)
        assert pg_backend.get("sources", record_id) is None


# ── RLS for Phase 6 ─────────────────────────────────────────────────

class TestPhase6RLS:

    def test_templates_rls_isolates(self, pg_backend):
        niche_a = f"rls_test_{uuid.uuid4().hex[:6]}"
        niche_b = f"rls_test_{uuid.uuid4().hex[:6]}"

        pg_backend.create("templates", {
            "niche_id": niche_a,
            "template_id": f"rls_t_a_{uuid.uuid4().hex[:8]}",
            "name": "Template A",
            "status": "ACTIVE",
        })
        pg_backend.create("templates", {
            "niche_id": niche_b,
            "template_id": f"rls_t_b_{uuid.uuid4().hex[:8]}",
            "name": "Template B",
            "status": "ACTIVE",
        })

        results_a = pg_backend.find("templates", niche_id=niche_a)
        results_b = pg_backend.find("templates", niche_id=niche_b)

        assert all(r["fields"]["niche_id"] == niche_a for r in results_a)
        assert all(r["fields"]["niche_id"] == niche_b for r in results_b)

    def test_sources_rls_isolates(self, pg_backend):
        niche_a = f"rls_test_{uuid.uuid4().hex[:6]}"
        niche_b = f"rls_test_{uuid.uuid4().hex[:6]}"

        pg_backend.create("sources", {
            "niche_id": niche_a,
            "source_id": f"rls_s_a_{uuid.uuid4().hex[:8]}",
            "name": "Source A",
            "source_type": "rss",
            "status": "ACTIVE",
            "weight": 1.0,
        })
        pg_backend.create("sources", {
            "niche_id": niche_b,
            "source_id": f"rls_s_b_{uuid.uuid4().hex[:8]}",
            "name": "Source B",
            "source_type": "youtube_api",
            "status": "ACTIVE",
            "weight": 1.0,
        })

        results_a = pg_backend.find("sources", niche_id=niche_a)
        results_b = pg_backend.find("sources", niche_id=niche_b)

        assert all(r["fields"]["niche_id"] == niche_a for r in results_a)
        assert all(r["fields"]["niche_id"] == niche_b for r in results_b)

    def test_templates_admin_mode(self, pg_backend):
        niche = f"rls_test_{uuid.uuid4().hex[:6]}"
        tid = f"rls_adm_{uuid.uuid4().hex[:8]}"
        pg_backend.create("templates", {
            "niche_id": niche,
            "template_id": tid,
            "name": "Admin visible",
            "status": "ACTIVE",
        })
        results = pg_backend.find(
            "templates",
            formula=f"{{template_id}}='{tid}'",
            niche_id="",
        )
        assert len(results) >= 1
