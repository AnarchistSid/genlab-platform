"""Pin tests for the 2026-07-14 _coerce_uuid deterministic UUID5 fix.

Session 2026-07-14 sweep found post_decision_trace stayed at 0 rows
for months. Root cause: _coerce_uuid rejected any non-UUID input.
All 3 writer surfaces pass SHA256 candidate_id (64 hex chars, not
a UUID) → writes silently skipped → table structurally dead.

Fix: derive stable UUID5 from non-UUID inputs using a genlab-scoped
namespace. Idempotent (same input → same UUID) so ON CONFLICT DO
UPDATE stays valid across the 3 lifecycle writers.
"""

from __future__ import annotations

import uuid

from genlab_core.learning.post_decision_trace import _coerce_uuid


class TestCoerceUuid:
    def test_valid_uuid_returned_unchanged(self):
        input_uuid = "1eea4199-3910-47cb-b2f0-b3f4ae198916"
        result = _coerce_uuid(input_uuid)
        assert result == input_uuid

    def test_sha256_hex_gets_uuid5(self):
        """The 64-char hex candidate_id — must produce a UUID."""
        sha256_hash = "e70eef4332c83f8c06f7fd616ed9852ad42bcb5fe00cc81ccef2d04a118631ee"
        result = _coerce_uuid(sha256_hash)
        assert result is not None
        # Must be a valid UUID
        uuid.UUID(result)

    def test_uuid5_is_idempotent(self):
        """Same SHA256 input → same UUID5 output. Critical for the
        ON CONFLICT (blueprint_id) DO UPDATE clause to work."""
        sha256_hash = "e70eef4332c83f8c06f7fd616ed9852ad42bcb5fe00cc81ccef2d04a118631ee"
        r1 = _coerce_uuid(sha256_hash)
        r2 = _coerce_uuid(sha256_hash)
        assert r1 == r2

    def test_different_hashes_produce_different_uuids(self):
        """Two different candidate_ids must map to different UUIDs so
        blueprints don't accidentally overwrite each other's traces."""
        h1 = "e70eef4332c83f8c06f7fd616ed9852ad42bcb5fe00cc81ccef2d04a118631ee"
        h2 = "7354c19153f23c7bf5224deff0af6703d75455cf10b158226a762132e2d2fb6f"
        assert _coerce_uuid(h1) != _coerce_uuid(h2)

    def test_short_record_id_still_produces_uuid(self):
        """SharePoint record ids (also not UUIDs) — should also work."""
        result = _coerce_uuid("recABC123XYZ")
        assert result is not None
        uuid.UUID(result)

    def test_empty_returns_none(self):
        assert _coerce_uuid("") is None
        assert _coerce_uuid(None) is None
        assert _coerce_uuid("   ") is None

    def test_uuid_and_sha256_of_same_input_differ(self):
        """A UUID string and a SHA256 hash that happens to look
        similar must NOT collide."""
        # This is a synthetic case but pins the invariant.
        looks_like_uuid = "1eea4199-3910-47cb-b2f0-b3f4ae198916"
        # UUID stays canonical; hash gets uuid5-derived.
        uuid_result = _coerce_uuid(looks_like_uuid)
        assert uuid_result == looks_like_uuid  # unchanged
        # A SHA256 that starts with the same prefix but no hyphens
        sha_result = _coerce_uuid("1eea4199391047cbb2f0b3f4ae198916" + "0" * 32)
        assert sha_result != uuid_result
