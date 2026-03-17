"""Tests for PostgresBackend — uses real local PostgreSQL.

These tests require a running PostgreSQL with the genlab database
and the blueprints table (created by Alembic migration).
Set POSTGRES_PASSWORD env var to enable these tests.
"""
from __future__ import annotations

import os
import uuid

import pytest

from genlab_core.storage.protocol import StorageBackend

# Skip entire module if no PostgreSQL available
pytestmark = pytest.mark.skipif(
    not os.environ.get("POSTGRES_PASSWORD"),
    reason="POSTGRES_PASSWORD not set — skip PostgreSQL integration tests",
)

# Unique niche_id per test run to avoid collisions
_TEST_NICHE = f"test_{uuid.uuid4().hex[:8]}"


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
    )
    yield backend

    # Cleanup: delete all test records using the backend's own loop+pool
    async def cleanup():
        pool = backend._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM blueprints WHERE niche_id = $1", _TEST_NICHE
            )
            await conn.execute(
                "DELETE FROM blueprints WHERE niche_id LIKE 'rls_test_%'"
            )

    backend._ensure_pool()
    assert backend._loop is not None
    backend._loop.run_until_complete(cleanup())


class TestPostgresBackendProtocol:
    """Verify PostgresBackend satisfies the StorageBackend protocol."""

    def test_implements_protocol(self, pg_backend):
        assert isinstance(pg_backend, StorageBackend)


class TestPostgresBackendCRUD:
    """Test basic CRUD operations."""

    def test_create_returns_uuid(self, pg_backend):
        record_id = pg_backend.create("blueprints", {
            "niche_id": _TEST_NICHE,
            "candidate_id": f"cand_{uuid.uuid4().hex[:8]}",
            "title": "Test Blueprint",
            "status": "DRAFTED",
        })
        assert record_id
        # Should be a valid UUID
        uuid.UUID(record_id)

    def test_create_and_get(self, pg_backend):
        cand_id = f"cand_{uuid.uuid4().hex[:8]}"
        record_id = pg_backend.create("blueprints", {
            "niche_id": _TEST_NICHE,
            "candidate_id": cand_id,
            "title": "Get Test",
            "status": "DRAFTED",
        })

        record = pg_backend.get("blueprints", record_id)
        assert record is not None
        assert record["id"] == record_id
        assert record["fields"]["title"] == "Get Test"
        assert record["fields"]["status"] == "DRAFTED"
        assert record["fields"]["candidate_id"] == cand_id

    def test_get_nonexistent_returns_none(self, pg_backend):
        fake_id = str(uuid.uuid4())
        result = pg_backend.get("blueprints", fake_id)
        assert result is None

    def test_update(self, pg_backend):
        cand_id = f"cand_{uuid.uuid4().hex[:8]}"
        record_id = pg_backend.create("blueprints", {
            "niche_id": _TEST_NICHE,
            "candidate_id": cand_id,
            "status": "DRAFTED",
        })

        pg_backend.update("blueprints", record_id, {
            "status": "PUBLISHED",
            "hook": "Updated hook",
        })

        record = pg_backend.get("blueprints", record_id)
        assert record["fields"]["status"] == "PUBLISHED"
        assert record["fields"]["hook"] == "Updated hook"

    def test_delete(self, pg_backend):
        cand_id = f"cand_{uuid.uuid4().hex[:8]}"
        record_id = pg_backend.create("blueprints", {
            "niche_id": _TEST_NICHE,
            "candidate_id": cand_id,
            "status": "DRAFTED",
        })

        pg_backend.delete("blueprints", record_id)
        result = pg_backend.get("blueprints", record_id)
        assert result is None

    def test_batch_create(self, pg_backend):
        records = [
            {
                "niche_id": _TEST_NICHE,
                "candidate_id": f"batch_{uuid.uuid4().hex[:8]}",
                "status": "DRAFTED",
            }
            for _ in range(3)
        ]
        ids = pg_backend.batch_create("blueprints", records)
        assert len(ids) == 3
        for rid in ids:
            uuid.UUID(rid)  # each should be a valid UUID

    def test_extra_jsonb_for_unknown_fields(self, pg_backend):
        """Fields not in PROMOTED_COLUMNS go into the extra JSONB column."""
        cand_id = f"cand_{uuid.uuid4().hex[:8]}"
        record_id = pg_backend.create("blueprints", {
            "niche_id": _TEST_NICHE,
            "candidate_id": cand_id,
            "status": "DRAFTED",
            "custom_field": "custom_value",
            "another_field": 42,
        })

        record = pg_backend.get("blueprints", record_id)
        # custom_field should be accessible through fields (merged from extra)
        assert record["fields"]["custom_field"] == "custom_value"
        assert record["fields"]["another_field"] == 42


class TestPostgresBackendFind:
    """Test find() with formula and niche_id."""

    def test_find_with_formula(self, pg_backend):
        cand_id = f"cand_{uuid.uuid4().hex[:8]}"
        pg_backend.create("blueprints", {
            "niche_id": _TEST_NICHE,
            "candidate_id": cand_id,
            "status": "DRAFTED",
        })

        results = pg_backend.find(
            "blueprints",
            formula=f"{{candidate_id}}='{cand_id}'",
            niche_id=_TEST_NICHE,
        )
        assert len(results) >= 1
        assert any(r["fields"]["candidate_id"] == cand_id for r in results)

    def test_find_with_niche_filter(self, pg_backend):
        cand_id = f"cand_{uuid.uuid4().hex[:8]}"
        pg_backend.create("blueprints", {
            "niche_id": _TEST_NICHE,
            "candidate_id": cand_id,
            "status": "DRAFTED",
        })

        results = pg_backend.find("blueprints", niche_id=_TEST_NICHE)
        assert len(results) >= 1
        assert all(
            r["fields"]["niche_id"] == _TEST_NICHE for r in results
        )

    def test_find_empty_result(self, pg_backend):
        results = pg_backend.find(
            "blueprints",
            formula="{candidate_id}='nonexistent_xyz_999'",
            niche_id=_TEST_NICHE,
        )
        assert results == []


class TestPostgresBackendRLS:
    """Test Row Level Security isolation between niches."""

    def test_rls_isolates_niches(self, pg_backend):
        """Records from niche_a should not appear in niche_b queries."""
        niche_a = f"rls_test_{uuid.uuid4().hex[:6]}"
        niche_b = f"rls_test_{uuid.uuid4().hex[:6]}"

        pg_backend.create("blueprints", {
            "niche_id": niche_a,
            "candidate_id": f"rls_a_{uuid.uuid4().hex[:8]}",
            "status": "DRAFTED",
        })
        pg_backend.create("blueprints", {
            "niche_id": niche_b,
            "candidate_id": f"rls_b_{uuid.uuid4().hex[:8]}",
            "status": "DRAFTED",
        })

        results_a = pg_backend.find("blueprints", niche_id=niche_a)
        results_b = pg_backend.find("blueprints", niche_id=niche_b)

        assert all(r["fields"]["niche_id"] == niche_a for r in results_a)
        assert all(r["fields"]["niche_id"] == niche_b for r in results_b)

    def test_rls_empty_niche_sees_all(self, pg_backend):
        """With niche_id='' (admin mode), all records are visible."""
        niche = f"rls_test_{uuid.uuid4().hex[:6]}"
        cand_id = f"rls_all_{uuid.uuid4().hex[:8]}"

        pg_backend.create("blueprints", {
            "niche_id": niche,
            "candidate_id": cand_id,
            "status": "DRAFTED",
        })

        # Empty niche_id should see all records (admin mode)
        results = pg_backend.find(
            "blueprints",
            formula=f"{{candidate_id}}='{cand_id}'",
            niche_id="",
        )
        assert len(results) >= 1


class TestPostgresBackendRecordFormat:
    """Verify the {id, fields} record format matches SharePoint."""

    def test_record_has_id_and_fields(self, pg_backend):
        cand_id = f"cand_{uuid.uuid4().hex[:8]}"
        record_id = pg_backend.create("blueprints", {
            "niche_id": _TEST_NICHE,
            "candidate_id": cand_id,
            "status": "DRAFTED",
            "title": "Format Test",
        })

        record = pg_backend.get("blueprints", record_id)
        assert "id" in record
        assert "fields" in record
        assert isinstance(record["fields"], dict)

    def test_fields_excludes_internal_columns(self, pg_backend):
        """created_at and updated_at should not appear in fields."""
        cand_id = f"cand_{uuid.uuid4().hex[:8]}"
        record_id = pg_backend.create("blueprints", {
            "niche_id": _TEST_NICHE,
            "candidate_id": cand_id,
            "status": "DRAFTED",
        })

        record = pg_backend.get("blueprints", record_id)
        assert "created_at" not in record["fields"]
        assert "updated_at" not in record["fields"]
