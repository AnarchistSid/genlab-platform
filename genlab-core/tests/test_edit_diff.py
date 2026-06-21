"""Pin: operator edit diff extractor (Lever M).

When operators edit a blueprint via the dashboard PATCH endpoint,
today the edit is persisted but the diff is not extracted. Operators
learn "operators most often correct hooks (62% of edits)" only via
manual inspection. Lever M ships the structured-diff primitive that
downstream aggregations can group by field + operation.

These pins lock in:

  classify_edit (5):
  1. Identical strings → unchanged
  2. Both empty → unchanged
  3. Before empty, after has content → added
  4. Before has content, after empty → removed
  5. Both have content + differ → reworded

  compute_edit_distance_ratio (4):
  6. Identical → 0.0
  7. Both empty → 0.0
  8. Either empty + other content → 1.0
  9. Minor edit (1 char diff in ~50-char string) → small ratio

  diff_blueprint (5):
  10. Fields absent from BOTH dicts skipped
  11. Unchanged fields excluded by default
  12. include_unchanged=True includes them
  13. Custom fields list overrides defaults
  14. Type coercion: None → "", int → str

  summarize_edits (3):
  15. Empty list → zero-shape summary
  16. by_field + by_operation aggregation correct
  17. total_char_delta + avg_edit_distance_ratio computed correctly
"""

from __future__ import annotations

from genlab_core.learning.edit_diff import (
    OPERATION_ADDED,
    OPERATION_REMOVED,
    OPERATION_REWORDED,
    OPERATION_UNCHANGED,
    EditDiff,
    classify_edit,
    compute_edit_distance_ratio,
    diff_blueprint,
    summarize_edits,
)


class TestClassifyEdit:
    def test_identical_returns_unchanged(self):
        assert classify_edit("hello", "hello") == OPERATION_UNCHANGED

    def test_both_empty_returns_unchanged(self):
        assert classify_edit("", "") == OPERATION_UNCHANGED
        assert classify_edit(None, None) == OPERATION_UNCHANGED  # type: ignore[arg-type]

    def test_empty_before_with_content_after_returns_added(self):
        assert classify_edit("", "new content") == OPERATION_ADDED
        assert classify_edit("   ", "new content") == OPERATION_ADDED  # whitespace stripped

    def test_content_before_empty_after_returns_removed(self):
        assert classify_edit("old content", "") == OPERATION_REMOVED

    def test_both_with_content_differing_returns_reworded(self):
        """Pin: the primary operator-intervention signal."""
        assert classify_edit("old hook", "new hook") == OPERATION_REWORDED


class TestComputeEditDistanceRatio:
    def test_identical_returns_zero(self):
        assert compute_edit_distance_ratio("hello", "hello") == 0.0

    def test_both_empty_returns_zero(self):
        assert compute_edit_distance_ratio("", "") == 0.0

    def test_either_empty_returns_one(self):
        """Pin: empty + content = completely different, ratio = 1.0."""
        assert compute_edit_distance_ratio("", "anything") == 1.0
        assert compute_edit_distance_ratio("anything", "") == 1.0

    def test_minor_edit_small_ratio(self):
        """Pin: 1 char change in a ~50-char string should be ratio ~0.02."""
        before = "Players are losing their minds over this patch"  # 46 chars
        after = "Players are losing their minds over that patch"  # 46 chars (1 char diff)
        ratio = compute_edit_distance_ratio(before, after)
        assert ratio < 0.05  # roughly 1/46 ≈ 0.022

    def test_substantial_rewrite_large_ratio(self):
        """Pin: completely different content → ratio close to 1.0."""
        before = "Old generic hook about gameplay"
        after = "Insane 1v5 clutch from ESL One Cologne grand finals"
        ratio = compute_edit_distance_ratio(before, after)
        assert ratio > 0.5


class TestDiffBlueprint:
    def test_fields_absent_from_both_skipped(self):
        """Pin: nothing to diff = empty result, no exception."""
        diffs = diff_blueprint({}, {})
        assert diffs == []

    def test_unchanged_excluded_by_default(self):
        """Pin: aggregations care about EDITS — unchanged fields are
        noise. Default excludes them."""
        before = {"hook": "same", "title": "same"}
        after = {"hook": "same", "title": "same"}
        assert diff_blueprint(before, after) == []

    def test_include_unchanged_returns_all(self):
        """Pin: testing / audit path can opt in to the full diff set."""
        before = {"hook": "same", "title": "same"}
        after = {"hook": "same", "title": "same"}
        diffs = diff_blueprint(before, after, include_unchanged=True)
        # Both default fields present in both dicts → both emitted
        assert len(diffs) >= 2
        assert all(d.operation == OPERATION_UNCHANGED for d in diffs)

    def test_custom_fields_override_defaults(self):
        """Pin: callers can pass a custom field list (e.g. for a new
        niche schema with extra fields)."""
        before = {"hook": "a", "novel_field": "x"}
        after = {"hook": "b", "novel_field": "y"}
        diffs = diff_blueprint(before, after, fields=["novel_field"])
        # Only novel_field diffed, hook ignored
        assert len(diffs) == 1
        assert diffs[0].field == "novel_field"

    def test_type_coercion(self):
        """Pin: None / int / list values are coerced to str so the
        diff doesn't crash on unexpected types."""
        before = {"tags": None}
        after = {"tags": ["esports", "valorant"]}
        diffs = diff_blueprint(before, after, fields=["tags"])
        assert len(diffs) == 1
        assert diffs[0].operation == OPERATION_ADDED
        # Note: list coerces to str representation
        assert "esports" in diffs[0].after

    def test_blueprint_id_propagated_to_diffs(self):
        before = {"hook": "old"}
        after = {"hook": "new"}
        diffs = diff_blueprint(before, after, blueprint_id="bp_123", fields=["hook"])
        assert diffs[0].blueprint_id == "bp_123"

    def test_char_delta_correct_sign(self):
        """Pin: char_delta = len(after) - len(before) — negative when
        operator SHORTENED the field."""
        before = {"hook": "a" * 100}
        after = {"hook": "b" * 50}
        diffs = diff_blueprint(before, after, fields=["hook"])
        assert diffs[0].char_delta == -50  # shortened by 50 chars


class TestSummarizeEdits:
    def test_empty_returns_zero_shape(self):
        """Pin: dashboards can render the shape even when no edits exist."""
        result = summarize_edits([])
        assert result == {
            "total_edits": 0,
            "by_field": {},
            "by_operation": {},
            "total_char_delta": 0,
            "avg_edit_distance_ratio": 0.0,
        }

    def test_aggregates_by_field_and_operation(self):
        diffs = [
            EditDiff("bp_1", "hook", "a", "b", OPERATION_REWORDED, 0, 0.5),
            EditDiff("bp_1", "title", "x", "y", OPERATION_REWORDED, 0, 0.5),
            EditDiff("bp_2", "hook", "c", "d", OPERATION_REWORDED, 0, 0.5),
            EditDiff("bp_3", "caption", "", "new", OPERATION_ADDED, 3, 1.0),
        ]
        s = summarize_edits(diffs)
        assert s["total_edits"] == 4
        # hook edited 2× across blueprints, caption + title once each
        assert s["by_field"] == {"hook": 2, "title": 1, "caption": 1}
        assert s["by_operation"] == {"reworded": 3, "added": 1}

    def test_total_char_delta_and_avg_ratio(self):
        """Pin: char_delta sums correctly; avg_ratio is per-diff average."""
        diffs = [
            EditDiff("bp_1", "hook", "x" * 10, "y" * 20, OPERATION_REWORDED, 10, 1.0),
            EditDiff("bp_1", "title", "a", "ab", OPERATION_REWORDED, 1, 0.5),
        ]
        s = summarize_edits(diffs)
        assert s["total_char_delta"] == 11
        assert s["avg_edit_distance_ratio"] == 0.75  # (1.0 + 0.5) / 2
