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

    def test_unknown_platform_fails_closed(self):
        """R-09 — an unconfigured platform must block, not unblock,
        publishing. A typo or stale ``platform_caps.yaml`` should
        never silently allow over-publishing."""
        assert make_enforcer({}).can_publish("myspace") is False

    def test_unknown_platform_logs_warning(self, caplog):
        """The fail-closed path must surface loudly so an operator
        adding a new platform notices the missing cap entry."""
        with caplog.at_level("WARNING"):
            make_enforcer({}).can_publish("myspace")
        assert "[daily_cap]" in caplog.text
        assert "myspace" in caplog.text
        assert "R-09" in caplog.text

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


class TestBundledConfigInvariant:
    """Pin R-09: every cap in the bundled ``platform_caps.yaml``
    must be exactly 1. The "1 reel per channel per day" rule is
    non-negotiable; a future edit that bumps caps back to >1
    (e.g. for "retry headroom") fails CI immediately."""

    def test_every_bundled_cap_is_one(self):
        from pathlib import Path

        repo_caps = _load_caps(
            config_path=Path(__file__).resolve().parents[3] / "config" / "platform_caps.yaml"
        )
        for platform, cap in repo_caps.items():
            assert cap == 1, (
                f"platform_caps.yaml: {platform}={cap} violates "
                f"R-09's 1-per-day invariant. Bumping caps risks "
                f"re-introducing the over-publish hazard."
            )


class TestLoadCountsLifecycleStatuses:
    """Pin the 2026-06-15 metric-collector-flipped-status bug.

    Bug: ``_load_today_counts`` filtered ``status != 'SUCCESS' -> skip``.
    The metric collector flips ``publishing_analytics.status`` from
    SUCCESS to INSIGHTS_6H ~5h after publish (then INSIGHTS_24H, _48H,
    _168H later). A 2nd publisher invocation after the flip saw count=0
    and bypassed the cap entirely. Verified prod cases (2026-06-14):
    gaming/instagram 2×, gaming/youtube 2×, sports/IG+YT+FB 2× each.
    """

    @staticmethod
    def _enforcer_with_items(items: list[dict]) -> DailyCapEnforcer:
        """Create an enforcer whose backlog client returns the given
        publishing_analytics items. Resets counts cache so the next
        ``_get_counts()`` triggers a fresh load."""
        client = MagicMock()
        client.publishing_analytics.all.return_value = items
        enforcer = DailyCapEnforcer(client, niche_id="gaming")
        enforcer._caps = {
            p: 1 for p in ["instagram", "youtube", "facebook", "tiktok", "twitter", "threads"]
        }
        # Force a fresh load on next can_publish()
        enforcer._counts_loaded_for = None  # type: ignore[assignment]
        return enforcer

    def _item(self, *, platform: str, status: str, niche: str = "gaming") -> dict:
        today_iso = datetime.now(UTC).strftime("%Y-%m-%dT06:00:00+00:00")
        return {
            "fields": {
                "platform": platform,
                "status": status,
                "niche_id": niche,
                "published_at": today_iso,
            }
        }

    def test_success_status_counts(self):
        """Baseline regression: SUCCESS still counts."""
        enf = self._enforcer_with_items([self._item(platform="instagram", status="SUCCESS")])
        assert enf.can_publish("instagram") is False, "SUCCESS row must consume cap"

    def test_insights_6h_status_counts(self):
        """THE HEADLINE PIN. After the metric collector flips SUCCESS ->
        INSIGHTS_6H, the cap loader must STILL count the row. Without
        this, a 2nd publish 5+h later silently bypasses the cap."""
        enf = self._enforcer_with_items([self._item(platform="instagram", status="INSIGHTS_6H")])
        assert enf.can_publish("instagram") is False, (
            "INSIGHTS_6H means publish already happened — must count toward cap"
        )

    def test_insights_24h_48h_168h_all_count(self):
        """All post-publish lifecycle states must count as 'this fired'."""
        for status in ("INSIGHTS_24H", "INSIGHTS_48H", "INSIGHTS_168H"):
            enf = self._enforcer_with_items([self._item(platform="youtube", status=status)])
            assert enf.can_publish("youtube") is False, (
                f"{status} must consume cap — it's a SUCCESS row past N-hour metric collection"
            )

    def test_failed_skipped_do_not_count(self):
        """Non-publish outcomes must NOT consume cap (they didn't fire)."""
        for status in ("FAILED", "SKIPPED"):
            enf = self._enforcer_with_items([self._item(platform="facebook", status=status)])
            assert enf.can_publish("facebook") is True, (
                f"{status} did not actually publish — must not count toward cap"
            )

    def test_unknown_future_status_does_not_count(self):
        """Allowlist (not denylist) means a new unknown status is
        under-counted, not over-counted. Under-counting risks over-publish
        which the test setup catches; this pin documents the deliberate
        trade-off so a future refactor doesn't flip to denylist without
        thinking."""
        enf = self._enforcer_with_items(
            [self._item(platform="threads", status="SOMETHING_NEW_2027")]
        )
        # Will allow publish because unknown status isn't in allowlist —
        # documents the trade-off, NOT a "correct" behavior.
        assert enf.can_publish("threads") is True

    def test_mixed_statuses_count_only_publish_outcomes(self):
        """Multiple rows of mixed states — only publish-outcome rows count."""
        enf = self._enforcer_with_items(
            [
                self._item(platform="instagram", status="INSIGHTS_24H"),  # counts
                self._item(platform="instagram", status="FAILED"),  # doesn't count
                self._item(platform="instagram", status="SKIPPED"),  # doesn't count
            ]
        )
        # 1 published row → cap=1 → blocked
        assert enf.can_publish("instagram") is False

    def test_other_niche_doesnt_consume_this_niches_cap(self):
        """Niche isolation: anime's published post must not block gaming."""
        enf = self._enforcer_with_items(
            [
                self._item(platform="instagram", status="INSIGHTS_6H", niche="anime"),
            ]
        )
        # enforcer is niche='gaming' — anime row filtered out
        assert enf.can_publish("instagram") is True
