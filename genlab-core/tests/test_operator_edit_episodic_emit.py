"""Pin: operator_edit episodic emit on PATCH /blueprints/{id}/content.

The 2026-06-22 audit found 4 of 8 EVENT_* types had no producers in
production. ``EVENT_OPERATOR_EDIT`` is the highest-leverage missing
producer: operator edits are the strongest taste signal in the system
(PR #468's DPO pipeline turns engagement deltas into preference pairs;
operator edits ADD the proactive layer — the operator REWROTE this
hook, here is how).

This module pins the wire at dashboard/server/api/blueprints.py:
``update_content`` PATCH endpoint:
  1. Fetch BEFORE values for each editable field
  2. Update via backlog client (existing behavior)
  3. For each ACTUALLY-CHANGED field: emit one EVENT_OPERATOR_EDIT
     event with {field, operation, edit_distance_ratio,
     before_chars, after_chars, char_delta, before[:2KB], after[:2KB]}
  4. Skip fields where classify_edit returned UNCHANGED (operator
     clicked save without editing)
  5. Every step is fail-OPEN — operator's edit MUST always succeed
"""

from __future__ import annotations

from pathlib import Path


class TestUpdateContentWireSource:
    """Source-level pins: the PATCH endpoint imports + uses both the
    edit_diff primitives AND the episodic_memory event constant.

    Source-level (vs runtime) is appropriate because the runtime path
    requires the dashboard's Flask app, BacklogClient mocking, and a
    Postgres episodic_events backend — all of which are tested in
    their own test files. The source pin guards the WIRE.
    """

    def setup_method(self):
        self.src = (
            Path(__file__).resolve().parents[2] / "dashboard" / "server" / "api" / "blueprints.py"
        )
        self.content = self.src.read_text()

    def test_imports_edit_diff_primitives(self):
        """Pin: classify_edit + compute_edit_distance_ratio + the
        OPERATION_UNCHANGED constant are imported. These are the
        canonical pure-function primitives for edit classification."""
        assert "classify_edit" in self.content
        assert "compute_edit_distance_ratio" in self.content
        assert "OPERATION_UNCHANGED" in self.content

    def test_imports_episodic_event_constant_and_helper(self):
        """Pin: EVENT_OPERATOR_EDIT + record_event are imported from
        episodic_memory. Without both, the event can't be emitted."""
        assert "EVENT_OPERATOR_EDIT" in self.content
        # The fire-and-forget helper that handles the backend lookup
        assert "from genlab_core.learning.episodic_memory import" in self.content
        assert "record_event" in self.content

    def test_captures_before_values_for_diff(self):
        """Pin: the endpoint MUST fetch the blueprint BEFORE updating
        so it can compute the diff. Without the before-snapshot, the
        diff is undefined and the event payload is useless."""
        assert "before_values" in self.content
        # The phrase used in the explanatory comment
        assert "capture BEFORE values" in self.content

    def test_skips_unchanged_fields(self):
        """Pin: when classify_edit returns OPERATION_UNCHANGED, the
        emit is SKIPPED. Otherwise every save-without-edit would
        pollute episodic_events with zero-signal entries."""
        assert "OPERATION_UNCHANGED" in self.content
        # The skip is implemented via `continue` after the check
        assert "if operation == OPERATION_UNCHANGED:" in self.content
        assert "continue" in self.content

    def test_emits_one_event_per_changed_field(self):
        """Pin: the loop iterates over ``updates`` (the changed fields)
        and emits per-field events. One event per field gives downstream
        consumers (DPO, RAG) clean per-field aggregation."""
        # The for-loop pattern over the updates dict
        assert "for field_name, after_value in updates.items():" in self.content

    def test_payload_includes_required_fields(self):
        """Pin: every event payload MUST include the canonical fields
        future consumers (DPO export, edit-pattern mining) depend on.
        Missing any of these breaks downstream queries."""
        required_payload_keys = [
            "field",
            "operation",
            "edit_distance_ratio",
            "before_chars",
            "after_chars",
            "char_delta",
            "before",
            "after",
        ]
        for key in required_payload_keys:
            assert f'"{key}"' in self.content, (
                f"Operator-edit event payload missing required key '{key}'. "
                f"DPO + RAG consumers expect this exact shape."
            )

    def test_truncates_before_after_to_2kb(self):
        """Pin: long edit values are truncated at 2KB per field. This
        bounds event payload size — a 10MB caption shouldn't blow up
        the episodic_events table or downstream consumers."""
        assert "before[:2048]" in self.content
        assert "after_str[:2048]" in self.content

    def test_emit_failure_is_fail_open(self):
        """Pin: the emit block is wrapped in try/except so the
        operator's edit always succeeds even if episodic_memory has a
        hiccup. The phrase 'episodic emit must never block edit' is
        the canonical signal for this contract."""
        assert "episodic emit must never block edit" in self.content
        # The except block uses the noqa: BLE001 pattern
        assert "operator_edit episodic emit skipped" in self.content


class TestEditDiffStillImportable:
    """The edit_diff module was previously dead code (zero production
    callers per the 2026-06-22 audit). This PR is the first caller.
    Pin that the module's public API is still importable + has the
    primitives we're depending on."""

    def test_edit_diff_module_exports_required_primitives(self):
        from genlab_core.learning.edit_diff import (
            OPERATION_ADDED,
            OPERATION_REMOVED,
            OPERATION_REWORDED,
            OPERATION_UNCHANGED,
            classify_edit,
            compute_edit_distance_ratio,
        )

        # All constants are non-empty strings
        assert all(
            isinstance(c, str) and c
            for c in (
                OPERATION_ADDED,
                OPERATION_REMOVED,
                OPERATION_REWORDED,
                OPERATION_UNCHANGED,
            )
        )
        # Functions are callable
        assert callable(classify_edit)
        assert callable(compute_edit_distance_ratio)

    def test_classify_edit_basic_contract(self):
        """Pin: the classifier matches the operations the wire relies on.
        Without this contract being stable, the wire's payload would
        emit nonsense operations to downstream consumers."""
        from genlab_core.learning.edit_diff import (
            OPERATION_ADDED,
            OPERATION_REMOVED,
            OPERATION_REWORDED,
            OPERATION_UNCHANGED,
            classify_edit,
        )

        assert classify_edit("", "") == OPERATION_UNCHANGED
        assert classify_edit("same", "same") == OPERATION_UNCHANGED
        assert classify_edit("", "new") == OPERATION_ADDED
        assert classify_edit("old", "") == OPERATION_REMOVED
        assert classify_edit("hook A", "hook B") == OPERATION_REWORDED

    def test_distance_ratio_basic_contract(self):
        """Pin: distance ratio is 0 for identical strings, 1 for
        empty-vs-non-empty, and in (0, 1) for partial edits.
        Downstream aggregation depends on these bounds."""
        from genlab_core.learning.edit_diff import compute_edit_distance_ratio

        assert compute_edit_distance_ratio("same", "same") == 0.0
        assert compute_edit_distance_ratio("", "new") == 1.0
        partial = compute_edit_distance_ratio("hello world", "hello there")
        assert 0.0 < partial < 1.0


class TestEpisodicEventConstant:
    """Pin: EVENT_OPERATOR_EDIT is still defined in episodic_memory.

    Source-level pin guards against accidental removal of the constant
    that would silently break this wire (the import would raise +
    fail-OPEN would mask the bug)."""

    def test_event_operator_edit_defined(self):
        from genlab_core.learning.episodic_memory import EVENT_OPERATOR_EDIT

        assert EVENT_OPERATOR_EDIT == "operator_edit"
