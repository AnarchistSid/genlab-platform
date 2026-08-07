"""Pin tests for QB-FIX-07 A1 — push_to_backlog revive path must not
resurrect rows archived by staleness sweep.

Prior bug: video_id_dedup.is_blocking() returns False on ARCHIVED
(Y0's short-circuit for dedup path). push_to_backlog's revive path
partitioned existing blueprints into blocking vs non-blocking and
called `.update()` on the first non-blocking match. Result: rows
archived by X0-a/Z0 staleness sweeps got resurrected when a fresh
pipeline run produced a new render for the same candidate_id.

Live evidence: blueprint 8ce02a08 "Meet Birding Pal" archived by
X0-a at 22:35 IST 2026-08-06; resurrected at 23:00 IST when
ai_creators pipeline re-fetched the URL; auto-approver re-approved
on next fire. Two-step cascade.

A1 fix: `_is_terminally_archived()` predicate returns True for rows
with status=ARCHIVED and action_taken_source starting with
`auto_archived_qb_fix`. Non_blocking_match generator filters these
out so revive path never sees them; falls through to insert-fresh.

Ordinary lifecycle archives (auto_archived_render_never_completed,
rejected, archived_by_ops_*) remain revivable.
"""

from __future__ import annotations

from genlab_core.pipeline.stages.push_to_backlog import _is_terminally_archived


class TestIsTerminallyArchived:
    def test_qb_fix_04_tag_returns_true(self):
        row = {"status": "ARCHIVED", "action_taken_source": "auto_archived_qb_fix_04_pre_fix"}
        assert _is_terminally_archived(row) is True

    def test_qb_fix_06_armspace_tag_returns_true(self):
        row = {"status": "ARCHIVED", "action_taken_source": "auto_archived_qb_fix_06_armspace"}
        assert _is_terminally_archived(row) is True

    def test_qb_fix_06_z1_sports_tag_returns_true(self):
        row = {
            "status": "ARCHIVED",
            "action_taken_source": "auto_archived_qb_fix_06_z1_sports_drafted",
        }
        assert _is_terminally_archived(row) is True

    def test_future_qb_fix_tag_covered_by_prefix(self):
        # Any future staleness sweep using the auto_archived_qb_fix_* prefix
        # inherits terminal-archive semantics automatically.
        row = {"status": "ARCHIVED", "action_taken_source": "auto_archived_qb_fix_99_hypothetical"}
        assert _is_terminally_archived(row) is True

    def test_render_never_completed_stays_revivable(self):
        # Ordinary lifecycle archive — pipeline fresh render is the whole
        # point of retrying. Do NOT block revive.
        row = {"status": "ARCHIVED", "action_taken_source": "auto_archived_render_never_completed"}
        assert _is_terminally_archived(row) is False

    def test_rejected_by_operator_stays_revivable(self):
        row = {"status": "ARCHIVED", "action_taken_source": "rejected"}
        assert _is_terminally_archived(row) is False

    def test_archived_by_ops_stays_revivable(self):
        row = {"status": "ARCHIVED", "action_taken_source": "archived_by_ops_2026_07_21"}
        assert _is_terminally_archived(row) is False

    def test_source_null_stays_revivable(self):
        row = {"status": "ARCHIVED", "action_taken_source": None}
        assert _is_terminally_archived(row) is False

    def test_non_archived_status_returns_false(self):
        # VISUAL_READY / PUBLISHED / etc — not archived at all, predicate must
        # not fire regardless of source tag.
        row = {"status": "VISUAL_READY", "action_taken_source": "auto_archived_qb_fix_04_pre_fix"}
        assert _is_terminally_archived(row) is False

    def test_nested_fields_shape_supported(self):
        # BacklogClient returns rows with a nested ``fields`` dict.
        row = {
            "id": "abc",
            "fields": {
                "status": "ARCHIVED",
                "action_taken_source": "auto_archived_qb_fix_06_armspace",
            },
        }
        assert _is_terminally_archived(row) is True


class TestReviveGeneratorIntegration:
    """Simulate the non_blocking_match generator's behavior on mixed rows.

    Verifies that a candidate list containing a staleness-swept ARCHIVED
    row alongside eligible non-blocking rows correctly skips the swept
    one and picks the eligible one.
    """

    def test_generator_skips_terminally_archived(self):
        from genlab_core.pipeline.stages.push_to_backlog import (
            _is_blocking,
            _is_terminally_archived,
        )

        rows = [
            # Staleness-swept ARCHIVED — must be skipped
            {
                "id": "swept",
                "status": "ARCHIVED",
                "action_taken_source": "auto_archived_qb_fix_06_armspace",
            },
            # Ordinary ARCHIVED — legitimately non-blocking, but this test
            # uses only one non_blocking generator so we assert what it picks
            {
                "id": "ordinary",
                "status": "ARCHIVED",
                "action_taken_source": "auto_archived_render_never_completed",
            },
        ]
        picked = next(
            (bp for bp in rows if not _is_blocking(bp) and not _is_terminally_archived(bp)),
            None,
        )
        assert picked is not None
        assert picked["id"] == "ordinary"

    def test_generator_returns_none_when_only_swept_available(self):
        from genlab_core.pipeline.stages.push_to_backlog import (
            _is_blocking,
            _is_terminally_archived,
        )

        rows = [
            {
                "id": "swept1",
                "status": "ARCHIVED",
                "action_taken_source": "auto_archived_qb_fix_04_pre_fix",
            },
            {
                "id": "swept2",
                "status": "ARCHIVED",
                "action_taken_source": "auto_archived_qb_fix_06_armspace",
            },
        ]
        picked = next(
            (bp for bp in rows if not _is_blocking(bp) and not _is_terminally_archived(bp)),
            None,
        )
        assert picked is None
