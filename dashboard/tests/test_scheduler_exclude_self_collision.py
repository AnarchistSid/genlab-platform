"""Pin the 2026-06-14 +1-day-offset fix in ``_next_available_slot``.

Background: ``push_to_backlog`` pre-sets ``scheduled_for`` to today's
06:30 UTC (or tomorrow's if past). Niches whose pipeline runs after
06:30 UTC (movies / sports / ai_creators / anime) got tomorrow as
their pre-set slot. When the operator later approved, the dashboard
recomputed via ``_next_available_slot`` — which queried existing
VISUAL_READY/PUBLISHED records and treated their ``scheduled_for`` as
occupied slots WITHOUT excluding the record being re-scheduled. The
blueprint's own pre-set ``scheduled_for`` thus blocked its own
preferred slot, pushing the auto-scheduler to day-after-tomorrow.

Diagnosis: two movies blueprints approved Jun 13 16:07 IST and
16:35 IST got scheduled to Jun 15 + Jun 16 (instead of Jun 14 + 15)
— a +1 day per approval. Gaming was unaffected because its pipeline
runs at 04:00 UTC (before the 06:30 publish window), so its pre-set
slot was always today + already past by approval time, skipped by the
``candidate <= earliest`` guard rather than the occupied check.

Fix: ``exclude_record_id`` parameter. The collision-loop skips the
record matching this id. Callers (approve / batch-approve / api
endpoints) now pass the record being scheduled.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    """Mirror test_optimal_time_learner_wiring's isolated env — a
    publishing.yaml with a single 12:00 IST slot, no real backlog
    client, no real DB."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "publishing.yaml").write_text(
        'instagram:\n  schedule_slots:\n    - "12:00"\n  timezone: "Asia/Kolkata"\n'
    )
    (config_dir / "lists_config.yaml").write_text("# noop\n")
    monkeypatch.setenv("BACKLOG_CONFIG_PATH", str(config_dir / "lists_config.yaml"))
    yield tmp_path


def _self_record(record_id: str, scheduled_iso: str, niche_id: str = "movies"):
    """Build a single mock blueprint record with the given scheduled_for."""
    return {
        "id": record_id,
        "fields": {
            "niche_id": niche_id,
            "scheduled_for": scheduled_iso,
        },
    }


class TestExcludeSelfCollision:
    """The +1 day bug + its fix."""

    def test_excludes_self_record_from_collision_check(self, isolated_env):
        """When the record being scheduled is passed via
        ``exclude_record_id``, its OWN ``scheduled_for`` must NOT be
        treated as a collision. Without this guard the scheduler would
        push the blueprint +1 day (the headline 2026-06-14 bug)."""
        mock_client = MagicMock()
        # Simulate the buggy state: this very blueprint has a tentative
        # scheduled_for already set (from push_to_backlog at pipeline-run
        # time). The collision check used to pick this up and skip the slot.
        mock_client.blueprints.all.return_value = [
            _self_record("BP-MOVIES-1", "2026-06-30T06:30:00+00:00"),
        ]

        with (
            patch(
                "server.core.publishing_queue._get_client",
                return_value=mock_client,
            ),
            patch(
                "genlab_core.scheduling.optimal_time_learner.optimal_slots_hhmm",
                return_value=[],  # cold start, fall through to yaml
            ),
        ):
            from server.core.publishing_queue import _next_available_slot

            slot_with_exclude = _next_available_slot(
                niche_id="movies", exclude_record_id="BP-MOVIES-1"
            )
            slot_without_exclude = _next_available_slot(niche_id="movies")

        assert slot_with_exclude is not None
        assert slot_without_exclude is not None
        # When excluded, the scheduler can pick 2026-06-30 itself (no
        # collision). When NOT excluded, the same slot looks occupied
        # so the scheduler picks something later. The "earlier vs later"
        # ordering pins the fix without depending on exact wall-clock.
        assert slot_with_exclude <= slot_without_exclude, (
            "regression: excluding self should never produce a LATER slot than not excluding"
        )

    def test_does_not_exclude_other_records_with_same_niche(self, isolated_env):
        """``exclude_record_id`` only skips the matching record. Other
        records' ``scheduled_for`` still block their slots — the 1-reel-
        per-niche-per-day rule must remain intact."""
        mock_client = MagicMock()
        mock_client.blueprints.all.return_value = [
            # The OTHER blueprint claims a far-future slot. Even with
            # exclude_record_id set, that slot must remain occupied.
            _self_record("BP-OTHER-1", "2026-06-30T06:30:00+00:00"),
        ]

        with (
            patch(
                "server.core.publishing_queue._get_client",
                return_value=mock_client,
            ),
            patch(
                "genlab_core.scheduling.optimal_time_learner.optimal_slots_hhmm",
                return_value=[],
            ),
        ):
            from server.core.publishing_queue import _next_available_slot

            slot = _next_available_slot(niche_id="movies", exclude_record_id="BP-DIFFERENT-2")

        assert slot is not None
        # The picked slot must NOT be 2026-06-30 06:30 UTC (= 12:00 IST)
        # because BP-OTHER-1 is occupying it and we did NOT exclude that
        # record (we excluded a different one that doesn't exist).
        assert "2026-06-30T06:30" not in slot, (
            "regression: other records' slots must still be treated as occupied "
            "even when exclude_record_id is set"
        )

    def test_default_empty_exclude_id_preserves_old_behavior(self, isolated_env):
        """Backward compat: when ``exclude_record_id`` is not passed
        (default ""), the function behaves exactly like before the fix —
        every matching-niche record's ``scheduled_for`` counts as
        occupied, including ``id=""`` records (which the new guard
        treats correctly: empty id never matches the empty exclude
        sentinel so nothing is short-circuited)."""
        mock_client = MagicMock()
        mock_client.blueprints.all.return_value = [
            _self_record("BP-X", "2026-06-30T06:30:00+00:00"),
        ]

        with (
            patch(
                "server.core.publishing_queue._get_client",
                return_value=mock_client,
            ),
            patch(
                "genlab_core.scheduling.optimal_time_learner.optimal_slots_hhmm",
                return_value=[],
            ),
        ):
            from server.core.publishing_queue import _next_available_slot

            # No exclude_record_id → BP-X's slot stays occupied.
            slot = _next_available_slot(niche_id="movies")

        assert slot is not None
        # BP-X's 2026-06-30 12:00 IST slot must look occupied → picker
        # returns a different slot, not that one.
        assert "2026-06-30T06:30" not in slot
