"""Pin tests for Layer 3 variant_type + variant_payload wiring.

Layer 3 kickoff (2026-07-17). Migration a8w9x0y1z2a3 added
``variant_type`` + ``variant_payload`` columns; ``BlueprintStore``
wires them through both ``create_blueprint`` + ``batch_create_blueprints``.

## What these pin

1. **Backward compatibility** — a blueprint dict WITHOUT variant_type
   still creates successfully and gets ``single_clip`` + empty payload.
   This is load-bearing: tomorrow's pipeline fire creates blueprints
   from push_to_backlog which doesn't know about variants yet.

2. **Explicit variant_type** — when the caller passes a valid variant,
   it lands on the backend record.

3. **Invalid variant_type falls back with WARNING** — silent-fail
   rule #17 sibling. Bad input from callers or from stale bandit
   arms can't corrupt the stored variant enum.

4. **UNKNOWN_FIELD_NAME retry strips variant fields** — if the
   migration hasn't run on a target backend, the graceful-retry
   path strips variant_type + variant_payload alongside the other
   optional-column-strip list. Pipeline must not hard-fail.

5. **Batch path parity** — batch_create_blueprints applies the same
   defaults + validation as single-create. Prevents drift between
   the two persistence paths (a class-of-bug in this codebase per
   PR #527's SR-C bug that affected only the bulk path).
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from genlab_core.http.blueprint_store import BlueprintStore
from genlab_core.variant_types import (
    DEFAULT_VARIANT,
    PAYLOAD_CONTRACTS,
    VARIANT_TYPES,
    is_valid_variant,
    validate_payload,
)


def _make_store():
    backend_mock = MagicMock()
    backend_mock.create.return_value = {"id": "bp_new"}
    backend_mock.batch_create.return_value = ["bp_1", "bp_2"]
    backend_lookup = MagicMock(return_value=backend_mock)
    sp_call = lambda fn, *a, **kw: fn(*a, **kw)  # noqa: E731
    store = BlueprintStore(
        sp_call=sp_call,
        backend=backend_lookup,
        find_story=lambda sid, **kw: {"id": "story_rec"},
        find_template=lambda tid, **kw: {"id": "tpl_rec"},
        assert_not_scheduled=lambda bp, status: None,
    )
    return store, backend_mock


# ---------------------------------------------------------------------------
# variant_types module invariants
# ---------------------------------------------------------------------------


class TestVariantTypesModule:
    def test_default_variant_is_single_clip(self) -> None:
        assert DEFAULT_VARIANT == "single_clip"

    def test_default_is_a_member_of_variants(self) -> None:
        assert DEFAULT_VARIANT in VARIANT_TYPES

    def test_all_variants_have_payload_contract(self) -> None:
        # Every variant enum member MUST have a payload contract entry.
        # Preventing "add a variant, forget to declare its shape" drift.
        missing = VARIANT_TYPES - PAYLOAD_CONTRACTS.keys()
        assert missing == set(), f"variants without payload contract: {missing}"

    def test_is_valid_variant_true_for_known(self) -> None:
        assert is_valid_variant("single_clip")
        assert is_valid_variant("series_part")

    def test_is_valid_variant_false_for_unknown(self) -> None:
        assert not is_valid_variant("bogus_variant")
        assert not is_valid_variant("")

    def test_validate_payload_empty_for_single_clip(self) -> None:
        assert validate_payload("single_clip", {}) == []
        # Extra keys allowed
        assert validate_payload("single_clip", {"extra": "ok"}) == []

    def test_validate_payload_reports_missing_required(self) -> None:
        missing = validate_payload("series_part", {"series_id": "s1"})
        assert "part_number" in missing
        assert "total_parts" in missing


# ---------------------------------------------------------------------------
# create_blueprint variant behavior
# ---------------------------------------------------------------------------


class TestCreateBlueprintVariant:
    def _minimal(self) -> dict:
        return {"candidate_id": "cid_1", "story_id": "sid_1"}

    def test_no_variant_type_defaults_to_single_clip(self) -> None:
        """Load-bearing: tomorrow's pipeline fire hits this path."""
        store, backend = _make_store()
        store.create_blueprint(self._minimal())
        fields = backend.create.call_args[0][1]
        assert fields["variant_type"] == "single_clip"
        assert fields["variant_payload"] == {}

    def test_explicit_variant_type_passes_through(self) -> None:
        store, backend = _make_store()
        bp = self._minimal()
        bp["variant_type"] = "series_part"
        bp["variant_payload"] = {
            "series_id": "s_abc",
            "part_number": 2,
            "total_parts": 5,
        }
        store.create_blueprint(bp)
        fields = backend.create.call_args[0][1]
        assert fields["variant_type"] == "series_part"
        assert fields["variant_payload"]["series_id"] == "s_abc"
        assert fields["variant_payload"]["part_number"] == 2

    def test_invalid_variant_falls_back_with_warning(self, caplog) -> None:
        """Rule #17 sibling: never silent-fail on unknown variant."""
        store, backend = _make_store()
        bp = self._minimal()
        bp["variant_type"] = "bogus_variant_that_does_not_exist"

        with caplog.at_level(logging.WARNING, logger="genlab_core.http.blueprint_store"):
            store.create_blueprint(bp)

        fields = backend.create.call_args[0][1]
        assert fields["variant_type"] == "single_clip"
        assert any(
            "unknown variant_type" in rec.message and "bogus_variant" in rec.message
            for rec in caplog.records
        ), "expected WARNING mentioning the bad variant_type"

    def test_empty_string_variant_treated_as_missing(self) -> None:
        """Empty string is falsy — should get the default, not fall through."""
        store, backend = _make_store()
        bp = self._minimal()
        bp["variant_type"] = ""
        store.create_blueprint(bp)
        fields = backend.create.call_args[0][1]
        assert fields["variant_type"] == "single_clip"

    def test_unknown_field_retry_strips_variant_columns(self) -> None:
        """If migration hasn't run, backend raises UNKNOWN_FIELD_NAME.
        Retry must strip variant_type + variant_payload alongside the
        other optional fields — otherwise pipeline hard-fails on
        environments that haven't migrated yet."""
        store, backend = _make_store()

        call_count = {"n": 0}

        def create_side_effect(table, fields, **kw):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # First call: raise as if column doesn't exist
                raise Exception("UNKNOWN_FIELD_NAME: variant_type")
            return {"id": "bp_new"}

        backend.create.side_effect = create_side_effect

        bp = self._minimal()
        bp["variant_type"] = "series_part"
        bp["variant_payload"] = {"series_id": "s"}
        store.create_blueprint(bp)

        # Second (retry) call — variant fields must be stripped
        retry_fields = backend.create.call_args_list[1][0][1]
        assert "variant_type" not in retry_fields
        assert "variant_payload" not in retry_fields


# ---------------------------------------------------------------------------
# batch_create_blueprints variant behavior — parity with single-create
# ---------------------------------------------------------------------------


class TestBatchCreateBlueprintsVariant:
    def _minimal(self, cid: str, sid: str) -> dict:
        return {"candidate_id": cid, "story_id": sid, "niche_id": "gaming"}

    def test_batch_no_variant_defaults_to_single_clip(self) -> None:
        store, backend = _make_store()
        bps = [self._minimal("c1", "s1"), self._minimal("c2", "s2")]
        store.batch_create_blueprints(bps)
        records = backend.batch_create.call_args[0][1]
        assert len(records) == 2
        for r in records:
            assert r["variant_type"] == "single_clip"
            assert r["variant_payload"] == {}

    def test_batch_mixed_variants(self) -> None:
        store, backend = _make_store()
        bp1 = self._minimal("c1", "s1")
        bp2 = self._minimal("c2", "s2")
        bp2["variant_type"] = "watch_till_end"
        store.batch_create_blueprints([bp1, bp2])
        records = backend.batch_create.call_args[0][1]
        assert records[0]["variant_type"] == "single_clip"
        assert records[1]["variant_type"] == "watch_till_end"

    def test_batch_invalid_variant_falls_back_with_warning(self, caplog) -> None:
        store, backend = _make_store()
        bp = self._minimal("c1", "s1")
        bp["variant_type"] = "nope_not_real"

        with caplog.at_level(logging.WARNING, logger="genlab_core.http.blueprint_store"):
            store.batch_create_blueprints([bp])

        records = backend.batch_create.call_args[0][1]
        assert records[0]["variant_type"] == "single_clip"
        assert any(
            "unknown variant_type" in rec.message and "nope_not_real" in rec.message
            for rec in caplog.records
        )
