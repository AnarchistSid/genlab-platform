"""Pin the source_starvation health check across all fetcher paths.

## Background

Pre-2026-08-12: `check_source_starvation` only read
`trending_videos.json` (populated by `FetchTrendingVideos`). Anime's
pipeline uses RSS + FetchAnimePromos + AnimeContentResearchStrategy —
does not run FetchTrendingVideos. So anime's `trending_videos.json`
was always empty, producing a persistent false-positive warning
every 30 min.

Fix: consult `run_report.metrics.stories_count` as authoritative
"did fetch succeed" signal. Warn only when BOTH signals show low
counts.
"""

from __future__ import annotations

import json

import pytest

from genlab_core.monitoring.checks.pipeline import check_source_starvation


@pytest.fixture
def run_dir(tmp_path):
    return tmp_path


def _report(run_dir, *, stories_count: int = 0) -> dict:
    return {
        "_run_dir": str(run_dir),
        "metrics": {"stories_count": stories_count},
    }


class TestStandardFetcherPath:
    """Niches that use FetchTrendingVideos (gaming/sports/movies/ai_creators)."""

    def test_trending_videos_healthy_no_alert(self, run_dir):
        (run_dir / "trending_videos.json").write_text(json.dumps(
            [{"channel_name": f"c{i}"} for i in range(5)]
        ))
        alerts = check_source_starvation(
            [_report(run_dir, stories_count=5)], "gaming",
        )
        assert alerts == []

    def test_trending_videos_low_but_stories_ok_no_alert(self, run_dir):
        """If trending_videos < 3 BUT stories_count >= 3, treat as
        healthy (alt-source fetch succeeded, e.g. RSS)."""
        (run_dir / "trending_videos.json").write_text(json.dumps(
            [{"channel_name": "c1"}]  # only 1 trending
        ))
        alerts = check_source_starvation(
            [_report(run_dir, stories_count=5)], "gaming",
        )
        assert alerts == []

    def test_both_low_warns(self, run_dir):
        (run_dir / "trending_videos.json").write_text(json.dumps(
            [{"channel_name": "c1"}]
        ))
        alerts = check_source_starvation(
            [_report(run_dir, stories_count=1)], "gaming",
        )
        assert len(alerts) == 1
        assert alerts[0].check == "source_starvation"

    def test_single_source_warns(self, run_dir):
        (run_dir / "trending_videos.json").write_text(json.dumps(
            [{"channel_name": "SameChannel"} for _ in range(5)]
        ))
        alerts = check_source_starvation(
            [_report(run_dir, stories_count=5)], "gaming",
        )
        assert any(a.check == "single_source" for a in alerts)


class TestAnimeAltSourcePath:
    """Anime uses RSS + FetchAnimePromos — trending_videos.json is
    always empty on healthy runs. Regression pin for the 2026-08-12
    false-positive fix."""

    def test_empty_trending_but_stories_healthy_no_alert(self, run_dir):
        """The original anime failure mode: trending_videos.json is
        empty [] but stories_count is 3+ from RSS/promos."""
        (run_dir / "trending_videos.json").write_text("[]")
        alerts = check_source_starvation(
            [_report(run_dir, stories_count=3)], "anime",
        )
        assert alerts == []

    def test_no_trending_manifest_but_stories_healthy_no_alert(self, run_dir):
        """When FetchTrendingVideos didn't run at all, no manifest
        file exists. If alt sources produced stories, healthy."""
        # No trending_videos.json written
        alerts = check_source_starvation(
            [_report(run_dir, stories_count=5)], "anime",
        )
        assert alerts == []

    def test_no_trending_manifest_and_no_stories_warns(self, run_dir):
        """Both signals starve: real starvation, do warn."""
        alerts = check_source_starvation(
            [_report(run_dir, stories_count=0)], "anime",
        )
        assert len(alerts) == 1
        assert alerts[0].check == "source_starvation"

    def test_empty_trending_and_zero_stories_warns(self, run_dir):
        """Both signals starve, warn."""
        (run_dir / "trending_videos.json").write_text("[]")
        alerts = check_source_starvation(
            [_report(run_dir, stories_count=0)], "anime",
        )
        assert len(alerts) == 1


class TestFailModes:
    def test_no_reports_no_alerts(self):
        assert check_source_starvation([], "anime") == []

    def test_malformed_stories_count_treated_as_zero(self, run_dir):
        (run_dir / "trending_videos.json").write_text("[]")
        report = {
            "_run_dir": str(run_dir),
            "metrics": {"stories_count": "not_a_number"},
        }
        alerts = check_source_starvation([report], "anime")
        # Treated as 0 stories -> both-signals-low -> warn
        assert len(alerts) == 1

    def test_missing_metrics_key_treated_as_zero(self, run_dir):
        (run_dir / "trending_videos.json").write_text("[]")
        report = {"_run_dir": str(run_dir)}  # no 'metrics'
        alerts = check_source_starvation([report], "anime")
        assert len(alerts) == 1

    def test_corrupt_trending_json_no_alert(self, run_dir):
        """Parse failure on trending_videos.json shouldn't crash the
        check; return no alerts."""
        (run_dir / "trending_videos.json").write_text("{malformed")
        alerts = check_source_starvation(
            [_report(run_dir, stories_count=5)], "anime",
        )
        assert alerts == []
