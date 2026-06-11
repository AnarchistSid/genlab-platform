"""R-45: backend create()/batch_create() return-type honesty.

Before R-45, ``StorageBackend.create`` was annotated as returning
``dict[str, Any]`` but the Postgres backend actually returned a bare
``str``. The audit verified-benign'd it (no live caller crashed —
``push_to_backlog.py:902`` and ``analytics_store.py:301`` both
defensively isinstance-checked), but the protocol annotation was a
quiet lie.

After R-45, the protocol annotates the union ``str | dict[str, Any]``
(aliased as ``CreateResult``) and ships :func:`id_from_create_result`
as the single seam that extracts an id portably. These tests pin:

  1. The Postgres backend really does satisfy the protocol shape
     (returns ``str`` for create / ``list[str]`` for batch_create).
  2. The helper extracts the id correctly from both shapes.
  3. Both branches honor the same contract: caller asks the helper
     for an id and gets a string back, regardless of which backend
     fired.

The Postgres half needs a live DB; it's collected only when
``POSTGRES_PASSWORD`` is set (matches the rest of ``tests/storage/``).
The helper-level pins run unconditionally.
"""

from __future__ import annotations

import os
import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from genlab_core.storage.protocol import (
    CreateResult,
    StorageBackend,
    id_from_create_result,
)

# Module-level constants used by the shared ``pg_backend`` fixture in
# tests/storage/conftest.py to scope its cleanup pass.
_TEST_NICHE = f"r45_{uuid.uuid4().hex[:8]}"
_ALL_TABLES = ("stories",)

# ── helper-level pins (unconditional) ─────────────────────────────────


def test_r45_helper_extracts_id_from_postgres_str_return() -> None:
    """Postgres returns a UUID string — helper passes it through."""
    fake_uuid = "550e8400-e29b-41d4-a716-446655440000"
    assert id_from_create_result(fake_uuid) == fake_uuid


def test_r45_helper_extracts_id_from_sharepoint_dict_return() -> None:
    """SharePoint returns ``{"id": <int>|<str>, "fields": {...}}`` —
    helper extracts the ``id`` field."""
    sp_record = {"id": "1234", "fields": {"title": "x"}}
    assert id_from_create_result(sp_record) == "1234"


def test_r45_helper_raises_on_dict_without_id_key() -> None:
    """A dict without an ``id`` key is malformed — surface the failure
    instead of returning None or silently breaking downstream."""
    with pytest.raises(KeyError):
        id_from_create_result({"fields": {"title": "no id here"}})


def test_r45_helper_raises_on_unexpected_type() -> None:
    """Backends that return something other than str or dict are out of
    contract — TypeError is the right shape, not a quiet None."""
    with pytest.raises(TypeError, match="Unexpected create.. return"):
        id_from_create_result(42)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        id_from_create_result(None)  # type: ignore[arg-type]


def test_r45_create_result_alias_accepts_both_shapes() -> None:
    """The ``CreateResult`` type alias must mypy-accept both backend
    shapes — a brittle assignment check, but it pins the alias
    matches reality at the type level (this test exists primarily
    so a future refactor can't narrow the alias by accident)."""

    def takes_create_result(r: CreateResult) -> str:
        return id_from_create_result(r)

    assert takes_create_result("uuid-str") == "uuid-str"
    assert takes_create_result({"id": "sp-id"}) == "sp-id"


# ── protocol-shape pin via a duck-typed mock ───────────────────────────


def test_r45_storage_backend_protocol_is_runtime_checkable() -> None:
    """The protocol is ``@runtime_checkable`` so isinstance() works.
    Pin that here — if a future edit drops the decorator, the live
    code's isinstance(backend, StorageBackend) checks would silently
    pass nothing instead of validating shape."""

    class DuckBackend:
        def find(self, table, *, formula=None, niche_id=None, max_records=None):
            return []

        def get(self, table, record_id):
            return {}

        def create(self, table, fields, *, typecast=False):
            return "duck-id"

        def update(self, table, record_id, fields, *, typecast=False):
            return {}

        def delete(self, table, record_id):
            pass

        def batch_create(self, table, records):
            return []

        def batch_update(self, table, records, **kwargs):
            return []

        def upload_attachment(self, table, record_id, field_name, file_path):
            return None

    assert isinstance(DuckBackend(), StorageBackend)


# ── backend-parity contract (live Postgres only) ──────────────────────
#
# The shared ``pg_backend`` fixture lives in tests/storage/conftest.py
# and reads credentials from POSTGRES_PASSWORD / GENLAB_TEST_PG_* env
# vars. Skipping the live-DB tests is per-test (not module-level) so
# the helper-level pins above always run.


_REQUIRE_PG = pytest.mark.skipif(
    not os.environ.get("POSTGRES_PASSWORD"),
    reason="POSTGRES_PASSWORD not set — skip live-Postgres parity check",
)


@_REQUIRE_PG
def test_r45_postgres_create_returns_satisfies_union(pg_backend) -> None:
    """The live Postgres backend's ``create()`` return must satisfy
    the documented ``CreateResult`` union — i.e. be either a str or
    a dict that the helper can consume. Today it's a str; if a
    future refactor changes it to a dict, the helper still works."""
    result = pg_backend.create(
        "stories",
        {
            "story_id": f"r45-parity-{uuid.uuid4().hex[:8]}",
            "title": "R-45 parity",
            "url": "https://example.test/r45",
            "niche_id": _TEST_NICHE,
        },
    )
    # The helper's job is exactly this: turn either shape into a str.
    record_id = id_from_create_result(result)
    assert isinstance(record_id, str)
    uuid.UUID(record_id)  # extra pin — a Postgres UUID, not garbage
    # No explicit delete — the conftest pg_backend teardown cleans up
    # rows tagged with ``_TEST_NICHE``.


@_REQUIRE_PG
def test_r45_postgres_batch_create_returns_satisfy_union(pg_backend) -> None:
    """Same parity check for ``batch_create``: each element must be
    extractable by the helper."""
    results = pg_backend.batch_create(
        "stories",
        [
            {
                "story_id": f"r45-batch-{i}-{uuid.uuid4().hex[:6]}",
                "title": f"R-45 batch {i}",
                "url": f"https://example.test/r45-batch-{i}",
                "niche_id": _TEST_NICHE,
            }
            for i in range(3)
        ],
    )
    assert len(results) == 3
    ids = [id_from_create_result(r) for r in results]
    for record_id in ids:
        assert isinstance(record_id, str)
        uuid.UUID(record_id)
    # No explicit deletes — the conftest pg_backend teardown cleans up
    # rows tagged with ``_TEST_NICHE``.


# ── SharePoint-shape pin via a mock backend ────────────────────────────


def test_r45_sharepoint_shape_create_consumes_correctly() -> None:
    """Pin that a SharePoint-shape backend (dict return) flows through
    the helper. We mock the backend rather than spinning up Graph —
    the contract under test is the helper's handling of the dict
    shape, not Graph's actual response."""
    backend = MagicMock(spec=StorageBackend)
    backend.create.return_value = {
        "id": "sp-record-id-9999",
        "fields": {"story_id": "stub"},
    }
    fields: dict[str, Any] = {"story_id": "stub", "title": "x", "url": "https://e.test/"}
    record_id = id_from_create_result(backend.create("stories", fields))
    assert record_id == "sp-record-id-9999"


def test_r45_sharepoint_shape_batch_create_consumes_correctly() -> None:
    """Same for batch_create — each element is a SharePoint record dict."""
    backend = MagicMock(spec=StorageBackend)
    backend.batch_create.return_value = [{"id": f"sp-{i}", "fields": {}} for i in range(3)]
    results = backend.batch_create("stories", [{} for _ in range(3)])
    ids = [id_from_create_result(r) for r in results]
    assert ids == ["sp-0", "sp-1", "sp-2"]
