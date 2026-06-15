"""Pin the 2026-06-15 per-day-cap fix in ``_next_available_slot`` +
``mark_cap_violations``.

Background: the dashboard scheduler's collision key was
per-(date,time,niche), not per-(date,niche). When
``optimal_time_learner.optimal_slots_hhmm(top_n=3)`` returned 3
learned slots, unioned with yaml ``["12:00"]``, the scheduler had 4
candidate slots/day. The first 4 operator approvals each picked the
next unoccupied tuple — packing 4 posts into today instead of 1/day.
``DailyCapEnforcer`` caught the overflow at publish-time (silent
skip with ``[daily_cap] daily cap reached``), so no over-publishing
happened, but stranded ``scheduled_for`` values pointed at slots
that never fired and the operator UI misleadingly showed "4
scheduled for Jun 15".

Fix: ``_next_available_slot`` now reuses ``DailyCapEnforcer``'s cap
logic via ``_effective_per_day_cap`` and short-circuits any day
already at cap. ``mark_cap_violations`` tags historical overflow
rows so the frontend can render a warning badge on each.

Diagnosis details + 3-layer breakdown:
``memory/session_2026_06_15_scheduler_over_schedule_bug.md``.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    """Mirror the +1-day-offset test's fixture — temp publishing.yaml
    with 4 IG slots so the scheduler has room to over-schedule unless
    the cap fires."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "publishing.yaml").write_text(
        "instagram:\n"
        "  schedule_slots:\n"
        '    - "11:30"\n'
        '    - "12:00"\n'
        '    - "12:30"\n'
        '    - "15:30"\n'
        '  timezone: "Asia/Kolkata"\n'
    )
    (config_dir / "lists_config.yaml").write_text("# noop\n")
    monkeypatch.setenv("BACKLOG_CONFIG_PATH", str(config_dir / "lists_config.yaml"))
    yield tmp_path


def _record(record_id: str, scheduled_iso: str, niche_id: str = "anime"):
    return {
        "id": record_id,
        "fields": {
            "niche_id": niche_id,
            "scheduled_for": scheduled_iso,
        },
    }


class TestPerDayCap:
    """The 4-of-5-FrameDrift-on-one-day bug + its fix."""

    def test_second_approval_same_day_pushes_to_next_day(self, isolated_env):
        """The headline pin. With cap=1 (default, multi_publish off) and
        one post already scheduled for tomorrow 11:30 IST, a new
        approval must NOT pick tomorrow's 12:00/12:30/15:30 slots —
        the whole day is at cap and the scheduler must roll to the
        day after."""
        from datetime import UTC, datetime, timedelta

        from server.core import publishing_queue

        # Pre-existing post on tomorrow 11:30 IST.
        tomorrow_1130_ist = (datetime.now(UTC) + timedelta(days=1)).replace(
            hour=6,
            minute=0,
            second=0,
            microsecond=0,  # 06:00 UTC = 11:30 IST
        )
        mock_client = MagicMock()
        mock_client.blueprints.all.return_value = [
            _record("BP-A", tomorrow_1130_ist.isoformat()),
        ]

        with (
            patch.object(publishing_queue, "_get_client", return_value=mock_client),
            patch(
                "genlab_core.scheduling.optimal_time_learner.optimal_slots_hhmm",
                return_value=[],
            ),
            # Multi-publish OFF → cap stays at 1
            patch.object(publishing_queue, "_effective_per_day_cap", return_value=1),
        ):
            slot = publishing_queue._next_available_slot(niche_id="anime")

        assert slot is not None
        picked = datetime.fromisoformat(slot.replace("Z", "+00:00"))
        # Must NOT be tomorrow (already at cap).
        assert picked.date() > tomorrow_1130_ist.date(), (
            f"picked {picked.date()} but tomorrow {tomorrow_1130_ist.date()} was already at cap=1"
        )

    def test_multi_publish_raises_cap_allows_packing(self, isolated_env):
        """When operator opts into ``multi_publish.enabled: true`` and
        IG ceiling is 3, the scheduler MAY pack a 2nd post into a day
        that already has 1 — up to the ceiling. This is the explicit
        owner-directive path from 2026-06-12 (agent can publish more
        than once when justified)."""
        from datetime import UTC, datetime, timedelta

        from server.core import publishing_queue

        tomorrow_1130_ist = (datetime.now(UTC) + timedelta(days=1)).replace(
            hour=6, minute=0, second=0, microsecond=0
        )
        mock_client = MagicMock()
        mock_client.blueprints.all.return_value = [
            _record("BP-A", tomorrow_1130_ist.isoformat()),
        ]

        with (
            patch.object(publishing_queue, "_get_client", return_value=mock_client),
            patch(
                "genlab_core.scheduling.optimal_time_learner.optimal_slots_hhmm",
                return_value=[],
            ),
            patch.object(publishing_queue, "_effective_per_day_cap", return_value=3),
        ):
            slot = publishing_queue._next_available_slot(niche_id="anime")

        assert slot is not None
        picked = datetime.fromisoformat(slot.replace("Z", "+00:00"))
        # Should land on the SAME day as the existing post (cap=3 > 1 existing).
        assert picked.date() == tomorrow_1130_ist.date(), (
            f"picked {picked.date()} but cap=3 should have allowed same-day packing"
        )

    def test_cap_lookup_failure_fails_closed_at_one(self, isolated_env):
        """If the cap helper raises (genlab-core import broken,
        platform_caps.yaml unreadable, etc.), the scheduler must
        fail-CLOSED at cap=1. Failing OPEN here would silently
        reintroduce the bug we just fixed."""
        from datetime import UTC, datetime, timedelta

        from server.core import publishing_queue

        tomorrow_1130_ist = (datetime.now(UTC) + timedelta(days=1)).replace(
            hour=6, minute=0, second=0, microsecond=0
        )
        mock_client = MagicMock()
        mock_client.blueprints.all.return_value = [
            _record("BP-A", tomorrow_1130_ist.isoformat()),
        ]

        with (
            patch.object(publishing_queue, "_get_client", return_value=mock_client),
            patch(
                "genlab_core.scheduling.optimal_time_learner.optimal_slots_hhmm",
                return_value=[],
            ),
            patch.object(
                publishing_queue,
                "_effective_per_day_cap",
                side_effect=RuntimeError("simulated import failure"),
            ),
        ):
            # The slot picker should still return something — just not today.
            # The helper itself returns 1 on failure; here we simulate the
            # caller path that wraps the helper. With cap=1 (the safe default),
            # tomorrow is already at cap, so the next slot must be day+2.
            slot = publishing_queue._next_available_slot(niche_id="anime")

        # On RuntimeError from _effective_per_day_cap, the caller path
        # in _next_available_slot would currently propagate the exception
        # because the helper is called directly. The pin here documents
        # what the SAFE behavior is — if this test fails after a refactor,
        # the refactor introduced over-scheduling regression.
        # Acceptable outcomes: slot is None (refusal to schedule) OR slot
        # lands day+2 (cap=1 forced tomorrow over-cap). Either way, NEVER
        # tomorrow.
        if slot is not None:
            picked = datetime.fromisoformat(slot.replace("Z", "+00:00"))
            assert picked.date() > tomorrow_1130_ist.date(), (
                "On cap-lookup failure, scheduler must not over-schedule the "
                "day with an existing post"
            )

    def test_self_record_still_excluded_from_per_day_count(self, isolated_env):
        """Regression pin against PR #191's +1-day-offset fix. The
        self-record exclusion must apply to BOTH the slot-collision
        check AND the per-day-count bucket — otherwise re-approving a
        blueprint that's already scheduled for today would count itself
        and look like the day is at cap, pushing it +1 day all over
        again."""
        from datetime import UTC, datetime, timedelta

        from server.core import publishing_queue

        # The blueprint being re-scheduled has a pre-set scheduled_for
        # for tomorrow at 11:30 IST (pre-set by push_to_backlog at
        # pipeline time). Operator now re-approves it.
        tomorrow_1130_ist = (datetime.now(UTC) + timedelta(days=1)).replace(
            hour=6, minute=0, second=0, microsecond=0
        )
        record_id = "BP-SELF"
        mock_client = MagicMock()
        mock_client.blueprints.all.return_value = [
            _record(record_id, tomorrow_1130_ist.isoformat()),
        ]

        with (
            patch.object(publishing_queue, "_get_client", return_value=mock_client),
            patch(
                "genlab_core.scheduling.optimal_time_learner.optimal_slots_hhmm",
                return_value=[],
            ),
            patch.object(publishing_queue, "_effective_per_day_cap", return_value=1),
        ):
            slot = publishing_queue._next_available_slot(
                niche_id="anime",
                exclude_record_id=record_id,
            )

        assert slot is not None
        picked = datetime.fromisoformat(slot.replace("Z", "+00:00"))
        # Must land tomorrow — self should not count toward the day's cap.
        assert picked.date() == tomorrow_1130_ist.date(), (
            f"Self-record should be excluded from per-day count. Got {picked.date()}, "
            f"expected tomorrow {tomorrow_1130_ist.date()}"
        )

    def test_other_niches_dont_count_against_this_niche_cap(self, isolated_env):
        """Per-day cap is per-(niche, day). Gaming having a post on Jun
        15 must not prevent FrameDrift from scheduling one on Jun 15.
        Without the niche-scoped count, all 5 niches would silently
        share a global daily quota."""
        from datetime import UTC, datetime, timedelta

        from server.core import publishing_queue

        tomorrow_1130_ist = (datetime.now(UTC) + timedelta(days=1)).replace(
            hour=6, minute=0, second=0, microsecond=0
        )
        mock_client = MagicMock()
        # Gaming already scheduled for tomorrow.
        mock_client.blueprints.all.return_value = [
            _record("BP-GAMING", tomorrow_1130_ist.isoformat(), niche_id="gaming"),
        ]

        with (
            patch.object(publishing_queue, "_get_client", return_value=mock_client),
            patch(
                "genlab_core.scheduling.optimal_time_learner.optimal_slots_hhmm",
                return_value=[],
            ),
            patch.object(publishing_queue, "_effective_per_day_cap", return_value=1),
        ):
            # FrameDrift should still get tomorrow.
            slot = publishing_queue._next_available_slot(niche_id="anime")

        assert slot is not None
        picked = datetime.fromisoformat(slot.replace("Z", "+00:00"))
        assert picked.date() == tomorrow_1130_ist.date(), (
            "Per-day cap leaked across niches — anime got pushed by gaming"
        )


class TestMarkCapViolations:
    """Tag historical over-scheduled rows so the frontend renders a warning."""

    def test_marks_all_but_earliest_in_overcap_bucket(self):
        """When 4 anime posts share Jun 15 and cap=1, the 3 later ones
        get ``cap_violation: True``. The earliest is the one the
        publisher will actually fire; it stays clean."""
        from server.core.publishing_queue import mark_cap_violations

        # 4 anime posts on Jun 15 IST at 11:30, 12:00, 12:30, 15:30.
        records = [
            _record("BP-1", "2026-06-15T06:00:00+00:00"),  # 11:30 IST
            _record("BP-2", "2026-06-15T06:30:00+00:00"),  # 12:00 IST
            _record("BP-3", "2026-06-15T07:00:00+00:00"),  # 12:30 IST
            _record("BP-4", "2026-06-15T10:00:00+00:00"),  # 15:30 IST
        ]

        with patch(
            "server.core.publishing_queue._effective_per_day_cap",
            return_value=1,
        ):
            mark_cap_violations(records)

        # Earliest (11:30 IST) is clean. Others are flagged.
        assert records[0]["fields"].get("cap_violation") is not True
        assert records[1]["fields"].get("cap_violation") is True
        assert records[2]["fields"].get("cap_violation") is True
        assert records[3]["fields"].get("cap_violation") is True

    def test_under_cap_bucket_leaves_all_clean(self):
        """1 post in a day with cap=1 is fine — no warning."""
        from server.core.publishing_queue import mark_cap_violations

        records = [_record("BP-1", "2026-06-15T06:30:00+00:00")]

        with patch(
            "server.core.publishing_queue._effective_per_day_cap",
            return_value=1,
        ):
            mark_cap_violations(records)

        assert records[0]["fields"].get("cap_violation") is not True

    def test_cross_niche_doesnt_mark_each_other(self):
        """Anime and gaming on the same date must NOT share a bucket —
        each niche has its own cap=1 budget for the day."""
        from server.core.publishing_queue import mark_cap_violations

        records = [
            _record("BP-A", "2026-06-15T06:30:00+00:00", niche_id="anime"),
            _record("BP-G", "2026-06-15T06:30:00+00:00", niche_id="gaming"),
        ]

        with patch(
            "server.core.publishing_queue._effective_per_day_cap",
            return_value=1,
        ):
            mark_cap_violations(records)

        # Both are the only post in their respective (niche, day) bucket.
        assert records[0]["fields"].get("cap_violation") is not True
        assert records[1]["fields"].get("cap_violation") is not True
