"""Tests for DailyCapEnforcer."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

from genlab_core.publishing.daily_cap import DailyCapEnforcer, _load_caps


def make_enforcer(published_today: dict[str, int]) -> DailyCapEnforcer:
    """Helper: create enforcer with pre-seeded today counts, no SharePoint call.

    Uses explicit cap=1 per platform so tests are independent of whatever
    platform_caps.yaml contains on disk.
    """
    client = MagicMock()
    enforcer = DailyCapEnforcer(client)
    # Override caps loaded from YAML to guarantee cap=1 (Sprint 45 target)
    enforcer._caps = {
        p: 1 for p in ["instagram", "youtube", "facebook", "tiktok", "twitter", "threads"]
    }
    enforcer._session_counts = dict(published_today)
    # Must use UTC date to match _today_utc() in the enforcer
    enforcer._counts_loaded_for = datetime.now(UTC).date()
    return enforcer


class TestCanPublish:
    def test_allows_first_post(self):
        assert make_enforcer({}).can_publish("instagram") is True

    def test_blocks_second_post(self):
        # Sprint 45: cap is 1, so second post is blocked
        assert make_enforcer({"instagram": 1}).can_publish("instagram") is False

    def test_exactly_at_cap_is_blocked(self):
        assert make_enforcer({"youtube": 1}).can_publish("youtube") is False

    def test_platforms_are_independent(self):
        enforcer = make_enforcer({"instagram": 1})
        assert enforcer.can_publish("youtube") is True
        assert enforcer.can_publish("facebook") is True

    def test_unknown_platform_fails_open(self):
        assert make_enforcer({}).can_publish("myspace") is True

    def test_case_insensitive(self):
        enforcer = make_enforcer({"instagram": 1})
        assert enforcer.can_publish("INSTAGRAM") is False
        assert enforcer.can_publish("Instagram") is False


class TestRecordPublish:
    def test_increments_session_count(self):
        enforcer = make_enforcer({})
        enforcer.record_publish("instagram")
        assert enforcer._session_counts["instagram"] == 1

    def test_blocks_after_recording_to_cap(self):
        enforcer = make_enforcer({})
        enforcer.record_publish("instagram")
        assert enforcer.can_publish("instagram") is False

    def test_initialises_new_platform(self):
        enforcer = make_enforcer({})
        enforcer.record_publish("youtube")
        assert enforcer._session_counts["youtube"] == 1


class TestGetRemaining:
    def test_full_remaining(self):
        # Sprint 45: default cap is 1
        assert make_enforcer({}).get_remaining("instagram") == 1

    def test_zero_remaining(self):
        assert make_enforcer({"instagram": 1}).get_remaining("instagram") == 0

    def test_never_negative(self):
        # Defensive: data integrity issue where count exceeds cap
        assert make_enforcer({"instagram": 5}).get_remaining("instagram") == 0


class TestLoadCaps:
    def test_missing_config_returns_defaults(self, tmp_path):
        caps = _load_caps(config_path=tmp_path / "nonexistent.yaml")
        assert caps["instagram"] == 1
        assert caps["youtube"] == 1

    def test_loads_from_yaml(self, tmp_path):
        cfg = tmp_path / "platform_caps.yaml"
        cfg.write_text("daily_post_cap:\n  instagram: 3\n  youtube: 1\n")
        caps = _load_caps(config_path=cfg)
        assert caps["instagram"] == 3
        assert caps["youtube"] == 1
