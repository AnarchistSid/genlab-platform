"""SR-C: tenant-isolation guards on PostgresBackend.create + batch_create.

PR #517 (2026-06-24). Pre-PR these methods inserted records without
setting ``app.niche_id`` GUC, leaving RLS WITH CHECK evaluation in
admin-mode (the policy permits ``app.niche_id IS NULL``). For the
single-tenant phase this is fine — only one niche exists — but it
becomes a critical bug the moment tenant #2 onboards: any caller that
forgets ``niche_id`` in the record dict silently creates an orphaned
row.

This PR adds an optional ``niche_id`` kwarg to both methods that:
  * SETs the GUC before INSERT (so RLS WITH CHECK matches)
  * Validates the record's ``niche_id`` field matches (or injects it)
  * Raises ValueError on tenant-spoofing attempt (record says tenant A,
    kwarg says tenant B)

These tests cover the PURE-PYTHON validation logic. The DB-integration
half (actual SET LOCAL effect on RLS) is tested in
``test_postgres_phase2_stories_assets.py`` which runs only when
POSTGRES_PASSWORD is set.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from genlab_core.storage.postgres import PostgresBackend


@pytest.fixture
def fake_backend():
    """Build a PostgresBackend with mocked pool so create() runs without DB."""
    backend = PostgresBackend.__new__(PostgresBackend)

    # Mock pool + connection + cursor — simulate a successful INSERT
    cur = MagicMock()
    cur.fetchone.return_value = ("00000000-0000-0000-0000-000000000001",)
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    conn.cursor.return_value.__exit__.return_value = None
    # conn.pipeline context manager for batch_create
    pipeline_cm = MagicMock()
    pipeline_cm.__enter__.return_value = None
    pipeline_cm.__exit__.return_value = None
    conn.pipeline.return_value = pipeline_cm

    pool_cm = MagicMock()
    pool_cm.__enter__.return_value = conn
    pool_cm.__exit__.return_value = None
    pool = MagicMock()
    pool.connection.return_value = pool_cm

    backend._get_pool = lambda: pool
    backend._cur = cur  # expose for assertions
    return backend


# ── create() validation ──────────────────────────────────────────────


def test_create_without_niche_id_falls_through_unchanged(fake_backend):
    """Backward compat: callers that don't pass niche_id keep working.

    No SET LOCAL is issued; pool connection just does the INSERT.
    """
    fake_backend.create("stories", {"title": "x", "niche_id": "gaming"})
    # SET LOCAL should NOT have been called
    set_local_calls = [
        c for c in fake_backend._cur.execute.call_args_list if "set_config" in str(c)
    ]
    assert set_local_calls == [], "No SET LOCAL when niche_id kwarg is None"


def test_create_with_niche_id_issues_set_local(fake_backend):
    """When niche_id kwarg provided, SET LOCAL fires before INSERT."""
    fake_backend.create("stories", {"title": "x", "niche_id": "gaming"}, niche_id="gaming")
    set_local_calls = [
        c for c in fake_backend._cur.execute.call_args_list if "set_config" in str(c)
    ]
    assert len(set_local_calls) == 1
    # The SQL passed should be the SET LOCAL form
    call = set_local_calls[0]
    assert "set_config" in call.args[0]
    assert call.args[1] == ("gaming",)


def test_create_raises_on_record_field_mismatch(fake_backend):
    """Tenant-spoofing prevention: record says one tenant, kwarg says
    another → ValueError before INSERT."""
    with pytest.raises(ValueError, match="tenant-spoofing"):
        fake_backend.create(
            "stories",
            {"title": "x", "niche_id": "movies"},  # record says movies
            niche_id="gaming",  # kwarg says gaming
        )


def test_create_injects_niche_id_when_record_lacks_it(fake_backend):
    """Defensive: caller passes kwarg but forgets field. Inject so the
    row's column matches the GUC."""
    fake_backend.create("stories", {"title": "x"}, niche_id="gaming")
    # Verify INSERT was called with niche_id in the column list
    insert_calls = [
        c
        for c in fake_backend._cur.execute.call_args_list
        if "INSERT INTO stories" in str(c.args[0] if c.args else "")
    ]
    assert len(insert_calls) == 1
    # The first arg is the SQL; the second is the values tuple/list
    sql = insert_calls[0].args[0]
    values = insert_calls[0].args[1]
    assert "niche_id" in sql, "niche_id column must appear in INSERT"
    assert "gaming" in values, "niche_id value must be present in row"


def test_create_matching_niche_id_in_record_and_kwarg_succeeds(fake_backend):
    """Happy path: both record AND kwarg agree on tenant → no raise."""
    # Should not raise
    record_id = fake_backend.create(
        "stories",
        {"title": "x", "niche_id": "gaming"},
        niche_id="gaming",
    )
    assert record_id == "00000000-0000-0000-0000-000000000001"


# ── batch_create() validation ──────────────────────────────────────


def test_batch_create_validates_every_record(fake_backend):
    """Tenant-mismatch in ANY record fails the WHOLE batch BEFORE any
    INSERT runs — atomic-pre-validation prevents partial cross-tenant
    inserts."""
    records = [
        {"title": "a", "niche_id": "gaming"},
        {"title": "b", "niche_id": "movies"},  # mismatch in middle
        {"title": "c", "niche_id": "gaming"},
    ]
    with pytest.raises(ValueError, match=r"record\[1\]"):
        fake_backend.batch_create("stories", records, niche_id="gaming")
    # Nothing should have been INSERTed (pre-validation runs first)
    assert not any(
        "INSERT" in str(c.args[0] if c.args else "")
        for c in fake_backend._cur.execute.call_args_list
    )


def test_batch_create_injects_niche_id_into_missing_records(fake_backend):
    """Mirror create()'s defensive injection for the batch path."""
    records = [
        {"title": "a"},  # no niche_id
        {"title": "b", "niche_id": "gaming"},  # matches
    ]
    ids = fake_backend.batch_create("stories", records, niche_id="gaming")
    assert len(ids) == 2
    # Both INSERTs should have happened with niche_id values
    insert_calls = [
        c
        for c in fake_backend._cur.execute.call_args_list
        if "INSERT INTO stories" in str(c.args[0] if c.args else "")
    ]
    assert len(insert_calls) == 2
    for call in insert_calls:
        assert "gaming" in call.args[1], "every batch row must carry the injected niche_id"


def test_batch_create_empty_returns_empty(fake_backend):
    """Edge case: empty list → empty result, no INSERT, no validation."""
    ids = fake_backend.batch_create("stories", [], niche_id="gaming")
    assert ids == []


def test_batch_create_without_niche_id_kwarg_skips_validation(fake_backend):
    """Backward compat: legacy callers without the kwarg get the
    pre-SR-C behavior (no SET LOCAL, no validation)."""
    records = [
        {"title": "a", "niche_id": "gaming"},
        {"title": "b", "niche_id": "movies"},  # mixed tenants tolerated
    ]
    # Should not raise — legacy behavior preserved
    ids = fake_backend.batch_create("stories", records)
    assert len(ids) == 2


# ── source-level pin ─────────────────────────────────────────────


def test_source_pins_sr_c_validation_in_both_methods():
    """Pin that the SR-C raise + SET LOCAL shape stays in both methods.
    A refactor that drops either re-introduces the silent-tenant-leak
    risk."""
    from pathlib import Path

    from genlab_core.storage import postgres as mod

    src = Path(mod.__file__).read_text()

    # ValueError message fragments (source has them split across f-string
    # continuations, so test per-fragment substring rather than the
    # concatenated form).
    assert "tenant-spoofing" in src
    assert "(SR-C guard, PR #517)" in src
    assert "heterogeneous-tenant batch" in src

    # SET LOCAL pattern present in both create + batch_create (in addition
    # to the 4 existing get/update/delete/claim_status sites added earlier
    # in the codebase). 6 total expected.
    set_local_count = src.count("SELECT set_config('app.niche_id', %s, true)")
    assert set_local_count >= 6, (
        f"SET LOCAL pattern should appear in get/update/delete + create + "
        f"batch_create (≥6 occurrences); found {set_local_count}"
    )
