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


def _today_ist_slot_iso(hour: int = 23, minute: int = 0) -> str:
    """Return an ISO timestamp for an IST slot on the FROZEN test
    today (see ``frozen_ist_now`` fixture).

    Builds the slot off the fixed test "now" so tests are deterministic
    across CI run times — the original date-relative test failed when
    CI ran during a time-of-day where today_UTC still had free slots
    relative to "now+1h"."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    ist = ZoneInfo("Asia/Kolkata")
    # _FROZEN_TEST_NOW_IST is set by the ``frozen_ist_now`` fixture
    # before each test runs; it's the test's view of "now in IST".
    frozen_now = _FROZEN_TEST_NOW_IST or datetime.now(ist)
    today_ist = frozen_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return today_ist.isoformat()


def _today_ist_date_str() -> str:
    """The IST date string for the scheduler's ``posts_per_day`` key,
    relative to the frozen test "now"."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    frozen_now = _FROZEN_TEST_NOW_IST or datetime.now(ZoneInfo("Asia/Kolkata"))
    return frozen_now.strftime("%Y-%m-%d")


# Module-level holder so the helpers see what the fixture set. Avoids
# threading the value through every test signature.
_FROZEN_TEST_NOW_IST = None


@pytest.fixture(autouse=True)
def frozen_ist_now(monkeypatch):
    """Freeze the scheduler's clock at 10:00 IST on a fixed date so
    every test sees the same "now" — no flakiness from CI run time.

    Why 10:00 IST: it's well before all our test yaml's slots
    (11:30, 12:00, 12:30, 15:30) AND before the 23:00 IST mark we
    use for "existing post LATE TODAY" — earliest=11:00 IST so any
    of those slots is in the future, scheduler will try today first
    unless the per-day cap blocks it."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    ist = ZoneInfo("Asia/Kolkata")
    fixed = datetime(2026, 6, 15, 10, 0, 0, tzinfo=ist)

    # Make helpers see the fixed time
    global _FROZEN_TEST_NOW_IST
    _FROZEN_TEST_NOW_IST = fixed

    # Patch the scheduler's `datetime.now()` so its base_date + earliest
    # computations align with our fixed "now". The scheduler imports
    # `from datetime import ... datetime`, so we replace the class on
    # the module attribute.
    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.astimezone(tz) if tz else fixed

    from server.core import publishing_queue

    monkeypatch.setattr(publishing_queue, "datetime", _FixedDatetime)

    yield
    _FROZEN_TEST_NOW_IST = None


class TestPerDayCap:
    """The 4-of-5-FrameDrift-on-one-day bug + its fix."""

    def test_second_approval_same_day_pushes_to_next_day(self, isolated_env):
        """The headline pin. With cap=1 (default, multi_publish off) and
        one post already scheduled for TODAY at any IST slot, a new
        approval must NOT pick today's remaining slots — today is at
        cap so the scheduler must roll to tomorrow.

        Uses IST date (not UTC) because the scheduler buckets
        ``posts_per_day`` by IST date — matching the existing-post's
        IST date to the scheduler's base_date is what makes this test
        deterministic across CI run times."""
        from datetime import datetime

        from server.core import publishing_queue

        # Pre-existing post LATE TODAY IST — guaranteed to occupy
        # today's bucket regardless of when CI fires.
        existing_iso = _today_ist_slot_iso(hour=23, minute=0)
        today_ist_date = _today_ist_date_str()
        mock_client = MagicMock()
        mock_client.blueprints.all.return_value = [
            _record("BP-A", existing_iso),
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
        from zoneinfo import ZoneInfo

        picked_ist_date = (
            datetime.fromisoformat(slot.replace("Z", "+00:00"))
            .astimezone(ZoneInfo("Asia/Kolkata"))
            .strftime("%Y-%m-%d")
        )
        # Must NOT be today_IST (already at cap=1).
        assert picked_ist_date > today_ist_date, (
            f"picked {picked_ist_date} (IST) but today {today_ist_date} was already at cap=1"
        )

    def test_multi_publish_raises_cap_allows_packing(self, isolated_env):
        """When operator opts into ``multi_publish.enabled: true`` and
        IG ceiling is 3, the scheduler MAY pack a 2nd post into a day
        that already has 1 — up to the ceiling. This is the explicit
        owner-directive path from 2026-06-12 (agent can publish more
        than once when justified)."""
        from datetime import datetime

        from server.core import publishing_queue

        existing_iso = _today_ist_slot_iso(hour=23, minute=0)
        today_ist_date = _today_ist_date_str()
        mock_client = MagicMock()
        mock_client.blueprints.all.return_value = [
            _record("BP-A", existing_iso),
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
        from zoneinfo import ZoneInfo

        picked_ist_date = (
            datetime.fromisoformat(slot.replace("Z", "+00:00"))
            .astimezone(ZoneInfo("Asia/Kolkata"))
            .strftime("%Y-%m-%d")
        )
        # With cap=3, today still has headroom — scheduler picks today.
        assert picked_ist_date == today_ist_date, (
            f"picked {picked_ist_date} (IST) but cap=3 should have allowed same-day packing on {today_ist_date}"
        )

    def test_cap_lookup_failure_fails_closed_at_one(self, isolated_env):
        """If the cap helper raises (genlab-core import broken,
        platform_caps.yaml unreadable, etc.), the scheduler must
        fail-CLOSED at cap=1. Failing OPEN here would silently
        reintroduce the bug we just fixed."""
        from datetime import datetime

        from server.core import publishing_queue

        existing_iso = _today_ist_slot_iso(hour=23, minute=0)
        today_ist_date = _today_ist_date_str()
        mock_client = MagicMock()
        mock_client.blueprints.all.return_value = [
            _record("BP-A", existing_iso),
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
            slot = publishing_queue._next_available_slot(niche_id="anime")

        # The slot-picker's call-site wrapper catches the exception
        # and falls back to cap=1, so today (at cap) is blocked and
        # tomorrow (IST) is picked. NEVER same-day as the existing
        # post — that would be over-scheduling.
        if slot is not None:
            from zoneinfo import ZoneInfo

            picked_ist_date = (
                datetime.fromisoformat(slot.replace("Z", "+00:00"))
                .astimezone(ZoneInfo("Asia/Kolkata"))
                .strftime("%Y-%m-%d")
            )
            assert picked_ist_date > today_ist_date, (
                f"On cap-lookup failure, scheduler picked {picked_ist_date} (IST) "
                f"— same day as existing post at {today_ist_date}. Over-scheduling regression."
            )

    def test_self_record_still_excluded_from_per_day_count(self, isolated_env):
        """Regression pin against PR #191's +1-day-offset fix. The
        self-record exclusion must apply to BOTH the slot-collision
        check AND the per-day-count bucket — otherwise re-approving a
        blueprint that's already scheduled for today would count itself
        and look like the day is at cap, pushing it +1 day all over
        again."""
        from datetime import datetime

        from server.core import publishing_queue

        # The blueprint being re-scheduled has a pre-set scheduled_for
        # for TODAY IST (mirrors push_to_backlog's pre-set behavior).
        # Operator now re-approves it.
        existing_iso = _today_ist_slot_iso(hour=23, minute=0)
        today_ist_date = _today_ist_date_str()
        record_id = "BP-SELF"
        mock_client = MagicMock()
        mock_client.blueprints.all.return_value = [
            _record(record_id, existing_iso),
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
        from zoneinfo import ZoneInfo

        picked_ist_date = (
            datetime.fromisoformat(slot.replace("Z", "+00:00"))
            .astimezone(ZoneInfo("Asia/Kolkata"))
            .strftime("%Y-%m-%d")
        )
        # Self-record excluded → posts_per_day[today] = 0 → today open
        # → scheduler picks today (first valid slot).
        assert picked_ist_date == today_ist_date, (
            f"Self-record should be excluded from per-day count. "
            f"Got {picked_ist_date} (IST), expected {today_ist_date} (today)"
        )

    def test_other_niches_dont_count_against_this_niche_cap(self, isolated_env):
        """Per-day cap is per-(niche, day). Gaming having a post on
        today must not prevent FrameDrift from scheduling one today.
        Without the niche-scoped count, all 5 niches would silently
        share a global daily quota."""
        from datetime import datetime

        from server.core import publishing_queue

        existing_iso = _today_ist_slot_iso(hour=23, minute=0)
        today_ist_date = _today_ist_date_str()
        mock_client = MagicMock()
        # Gaming already scheduled for today IST.
        mock_client.blueprints.all.return_value = [
            _record("BP-GAMING", existing_iso, niche_id="gaming"),
        ]

        with (
            patch.object(publishing_queue, "_get_client", return_value=mock_client),
            patch(
                "genlab_core.scheduling.optimal_time_learner.optimal_slots_hhmm",
                return_value=[],
            ),
            patch.object(publishing_queue, "_effective_per_day_cap", return_value=1),
        ):
            # FrameDrift should still get today (gaming's post doesn't count for anime).
            slot = publishing_queue._next_available_slot(niche_id="anime")

        assert slot is not None
        from zoneinfo import ZoneInfo

        picked_ist_date = (
            datetime.fromisoformat(slot.replace("Z", "+00:00"))
            .astimezone(ZoneInfo("Asia/Kolkata"))
            .strftime("%Y-%m-%d")
        )
        assert picked_ist_date == today_ist_date, (
            f"Per-day cap leaked across niches — anime got pushed "
            f"to {picked_ist_date} by gaming's existing {today_ist_date} post"
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
