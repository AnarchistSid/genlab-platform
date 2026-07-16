"""Tests for ``check_fetcher_stage_silent_failures``.

Background:
    PR ``2ac8dbb2`` shipped after weeks of silent fetcher no-ops caused
    by ``context['sources_config']`` being declared in StageContext but
    never populated. Diagnostic signature: stage_timing < 0.001s in
    ``metrics.jsonl`` across every consecutive run.

    This check scans recent ``metrics.jsonl`` files for any fetcher in
    ``_FETCHER_STAGES_TO_MONITOR`` that appears with duration_ms < 1.0
    across the most-recent N=3 consecutive runs. If so, it fires a
    warning alert routing operators to the StageContext audit doc.

    Tests use ``monkeypatch`` to redirect the module-level ``RUNS_DIR``
    constant to a tmp_path, then write synthetic metrics.jsonl files
    matching the production format.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from genlab_core.monitoring import health_monitor
from genlab_core.monitoring.health_monitor import (
    _FETCHER_STAGES_TO_MONITOR,
    _SILENT_FAILURE_CONSECUTIVE_RUNS,
    check_fetcher_stage_silent_failures,
)


def _write_metrics_jsonl(
    run_dir: Path,
    stage_durations: dict[str, float],
    *,
    status: str = "ok",
) -> None:
    """Helper: write a metrics.jsonl with the given stages + durations.

    Each stage in ``stage_durations`` becomes one JSON line. A
    pipeline_summary footer line is also appended (matching the
    production format from ``PipelineMetrics.flush``) — the check must
    correctly skip that line.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    for stage, duration_ms in stage_durations.items():
        lines.append(
            json.dumps(
                {
                    "stage": stage,
                    "duration_ms": duration_ms,
                    "status": status,
                    "timestamp": "2026-06-30T12:00:00+00:00",
                }
            )
        )
    # Production format also writes a summary footer
    lines.append(
        json.dumps(
            {
                "_type": "pipeline_summary",
                "total_stages": len(stage_durations),
                "ok": len(stage_durations),
                "errors": 0,
                "total_duration_ms": sum(stage_durations.values()),
            }
        )
    )
    (run_dir / "metrics.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def fake_runs_dir(tmp_path, monkeypatch):
    """Point health_monitor.RUNS_DIR at a tmp_path for the duration of the test."""
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(health_monitor, "RUNS_DIR", runs)
    return runs


class TestSilentNoOpDetection:
    def test_three_consecutive_silent_runs_fires_warning(self, fake_runs_dir):
        """The canonical sources_config-bug pattern: a fetcher reports
        0.0001ms three runs in a row → warning fires."""
        # 3 runs, oldest to newest naming so sorted-reverse gives newest first
        for i in (1, 2, 3):
            _write_metrics_jsonl(
                fake_runs_dir / f"gaming_run{i:03d}",
                {"FetchRedditClips": 0.0001, "FetchTrendingVideos": 234.5},
            )

        alerts = check_fetcher_stage_silent_failures("gaming")

        # Exactly one alert for FetchRedditClips; FetchTrendingVideos is healthy
        reddit_alerts = [a for a in alerts if "FetchRedditClips" in a.message]
        assert len(reddit_alerts) == 1, f"expected 1 alert, got {alerts}"
        a = reddit_alerts[0]
        assert a.severity == "warning"
        assert a.check == "fetcher_silent_no_op"
        assert a.niche_id == "gaming"
        assert "<1ms" in a.message
        assert "sources_config-style" in a.message
        assert a.details["stage"] == "FetchRedditClips"
        assert a.details["consecutive_runs"] == 3

        # FetchTrendingVideos was healthy — no alert
        tv_alerts = [a for a in alerts if "FetchTrendingVideos" in a.message]
        assert tv_alerts == []

    def test_single_silent_run_does_not_fire(self, fake_runs_dir):
        """One off-run could be a cache hit — needs 3 consecutive to fire."""
        # Two normal runs + one silent (newest)
        _write_metrics_jsonl(fake_runs_dir / "gaming_run001", {"FetchRedditClips": 234.5})
        _write_metrics_jsonl(fake_runs_dir / "gaming_run002", {"FetchRedditClips": 198.2})
        _write_metrics_jsonl(fake_runs_dir / "gaming_run003", {"FetchRedditClips": 0.0001})

        alerts = check_fetcher_stage_silent_failures("gaming")
        assert alerts == []

    def test_two_silent_runs_does_not_fire(self, fake_runs_dir):
        """Boundary: 2 silent is still under the consecutive-3 threshold."""
        _write_metrics_jsonl(fake_runs_dir / "gaming_run001", {"FetchRedditClips": 234.5})
        _write_metrics_jsonl(fake_runs_dir / "gaming_run002", {"FetchRedditClips": 0.0001})
        _write_metrics_jsonl(fake_runs_dir / "gaming_run003", {"FetchRedditClips": 0.0001})

        alerts = check_fetcher_stage_silent_failures("gaming")
        assert alerts == []

    def test_insufficient_history_does_not_fire(self, fake_runs_dir):
        """Fewer than N runs of history → no alert. Brand new niche, fresh VPS."""
        _write_metrics_jsonl(fake_runs_dir / "anime_run001", {"FetchAnimePromos": 0.0001})
        _write_metrics_jsonl(fake_runs_dir / "anime_run002", {"FetchAnimePromos": 0.0001})

        alerts = check_fetcher_stage_silent_failures("anime")
        assert alerts == []

    def test_no_runs_at_all_does_not_fire(self, fake_runs_dir):
        alerts = check_fetcher_stage_silent_failures("gaming")
        assert alerts == []

    def test_stage_not_present_in_runs_does_not_fire(self, fake_runs_dir):
        """A niche that never runs FetchRedditClips at all → no alert
        from its absence. We only flag stages that HAVE run silent."""
        for i in (1, 2, 3):
            _write_metrics_jsonl(
                fake_runs_dir / f"sports_run{i:03d}",
                {"FetchTrendingVideos": 234.5, "FetchScoreBatHighlights": 156.7},
            )

        alerts = check_fetcher_stage_silent_failures("sports")
        # No silent stages — should be empty
        silent_alerts = [a for a in alerts if a.check == "fetcher_silent_no_op"]
        assert silent_alerts == []

    def test_status_error_excluded_from_silent_count(self, fake_runs_dir):
        """A stage that ERRORED is doing legitimate work — even with low
        duration_ms, it shouldn't be flagged as 'silent no-op'."""
        # Two runs with status=error + one ok+silent. With status=error
        # the silent_count should be 1 (only the ok run counts), so no fire.
        _write_metrics_jsonl(
            fake_runs_dir / "gaming_run001",
            {"FetchRedditClips": 0.0001},
            status="error",
        )
        _write_metrics_jsonl(
            fake_runs_dir / "gaming_run002",
            {"FetchRedditClips": 0.0001},
            status="error",
        )
        _write_metrics_jsonl(
            fake_runs_dir / "gaming_run003",
            {"FetchRedditClips": 0.0001},
            status="ok",
        )
        alerts = check_fetcher_stage_silent_failures("gaming")
        assert alerts == []

    def test_only_monitors_known_fetcher_stages(self, fake_runs_dir):
        """A non-fetcher stage running in <1ms shouldn't fire — e.g.
        a fast filter stage is allowed to be quick."""
        for i in (1, 2, 3):
            _write_metrics_jsonl(
                fake_runs_dir / f"gaming_run{i:03d}",
                {"QcGates": 0.0001, "ValidateVideos": 0.0001},
            )

        alerts = check_fetcher_stage_silent_failures("gaming")
        assert alerts == []

    def test_niche_id_filter_isolates_runs(self, fake_runs_dir):
        """Runs from another niche must not contaminate the check
        (the prefix filter ``f'{niche_id}_'`` should isolate them)."""
        # gaming has 3 silent runs
        for i in (1, 2, 3):
            _write_metrics_jsonl(
                fake_runs_dir / f"gaming_run{i:03d}",
                {"FetchRedditClips": 0.0001},
            )
        # sports has 3 healthy runs of the SAME stage
        for i in (1, 2, 3):
            _write_metrics_jsonl(
                fake_runs_dir / f"sports_run{i:03d}",
                {"FetchRedditClips": 234.5},
            )

        # sports check should find nothing
        sports_alerts = check_fetcher_stage_silent_failures("sports")
        assert sports_alerts == []

        # gaming check should find the silent FetchRedditClips
        gaming_alerts = check_fetcher_stage_silent_failures("gaming")
        reddit = [a for a in gaming_alerts if "FetchRedditClips" in a.message]
        assert len(reddit) == 1
        assert reddit[0].niche_id == "gaming"

    def test_alert_message_points_to_audit_doc(self, fake_runs_dir):
        """Alert message must include the runbook pointer so operators
        know where to start debugging the next sources_config-class
        bug."""
        for i in (1, 2, 3):
            _write_metrics_jsonl(
                fake_runs_dir / f"gaming_run{i:03d}",
                {"FetchTwitchClips": 0.0001},
            )

        alerts = check_fetcher_stage_silent_failures("gaming")
        assert len(alerts) == 1
        assert "stage_context_population_audit.md" in alerts[0].message

    def test_all_known_fetchers_can_alert(self, fake_runs_dir):
        """Pin: every stage in _FETCHER_STAGES_TO_MONITOR can produce
        an alert when silent. If a future PR adds a new fetcher to the
        list, this test forces them to also verify it triggers."""
        # Build 3 runs where ALL monitored fetchers are silent
        synthetic_metrics = {s: 0.0001 for s in _FETCHER_STAGES_TO_MONITOR}
        for i in (1, 2, 3):
            _write_metrics_jsonl(
                fake_runs_dir / f"movies_run{i:03d}",
                synthetic_metrics,
            )

        alerts = check_fetcher_stage_silent_failures("movies")
        # One alert per monitored stage
        stages_alerted = {a.details["stage"] for a in alerts}
        # Use set comparison so reordering doesn't break the test
        assert stages_alerted == set(_FETCHER_STAGES_TO_MONITOR), (
            "every monitored fetcher should be capable of triggering "
            f"an alert; missing: {set(_FETCHER_STAGES_TO_MONITOR) - stages_alerted}"
        )


class TestIntentionalDisableAllowlist:
    """Pins for 2026-07-15 allowlist: operator-disabled stages don't false-fire.

    Background: the detector historically flagged sports FetchScoreBatHighlights
    as silent even though scorebat was deliberately disabled on 2026-07-14
    (URLs weren't yt-dlp-compatible). The alert trained ops to ignore
    fetcher_silent_no_op — exactly the risk this detector was meant to
    prevent. Now silent stages are cross-referenced against
    ``sources.yaml`` and only alert if their gate keys are NOT
    ``enabled: false``.
    """

    def test_stage_with_enabled_false_is_allowlisted(self, fake_runs_dir, monkeypatch):
        """Silent scorebat + sources.yaml scorebat.enabled=false → no alert."""
        from genlab_core.pipeline import pipeline_runner

        # Point the intentional-disable helper's loader at a synthetic
        # config where scorebat is deliberately off.
        monkeypatch.setattr(
            pipeline_runner,
            "_load_sources_yaml",
            lambda _root, _nid="": {"scorebat": {"enabled": False}},
        )

        for i in (1, 2, 3):
            _write_metrics_jsonl(
                fake_runs_dir / f"sports_run{i:03d}",
                {"FetchScoreBatHighlights": 0.0001},
            )

        alerts = check_fetcher_stage_silent_failures("sports")
        scorebat_alerts = [a for a in alerts if "FetchScoreBatHighlights" in a.message]
        assert scorebat_alerts == [], (
            f"operator-disabled scorebat MUST NOT fire fetcher_silent_no_op; got: {scorebat_alerts}"
        )

    def test_stage_with_enabled_true_still_alerts(self, fake_runs_dir, monkeypatch):
        """Silent reddit + sources.yaml reddit.enabled=true → alert still fires.

        This is the load-bearing case — a genuinely-buggy silent stage
        (like today's gaming Reddit path drift) must still surface even
        when the allowlist mechanism is active.
        """
        from genlab_core.pipeline import pipeline_runner

        monkeypatch.setattr(
            pipeline_runner,
            "_load_sources_yaml",
            lambda _root, _nid="": {"reddit": {"enabled": True, "subreddits": [{"name": "foo"}]}},
        )

        for i in (1, 2, 3):
            _write_metrics_jsonl(
                fake_runs_dir / f"gaming_run{i:03d}",
                {"FetchRedditClips": 0.0001},
            )

        alerts = check_fetcher_stage_silent_failures("gaming")
        reddit_alerts = [a for a in alerts if "FetchRedditClips" in a.message]
        assert len(reddit_alerts) == 1, (
            "reddit.enabled=true + silent stage MUST alert — the allowlist "
            "only suppresses explicitly-disabled stages"
        )

    def test_missing_key_still_alerts(self, fake_runs_dir, monkeypatch):
        """Silent stage + sources.yaml missing the gate key → alert fires.

        This is the exact class-of-bug the detector was built for:
        sources_config = {} because of a path-drift or field-population
        bug. "Missing" is NOT "disabled" — it means the operator didn't
        opt in either way, and the detector should keep watching.
        """
        from genlab_core.pipeline import pipeline_runner

        monkeypatch.setattr(
            pipeline_runner,
            "_load_sources_yaml",
            lambda _root, _nid="": {},  # empty — the exact bug shape
        )

        for i in (1, 2, 3):
            _write_metrics_jsonl(
                fake_runs_dir / f"gaming_run{i:03d}",
                {"FetchRedditClips": 0.0001},
            )

        alerts = check_fetcher_stage_silent_failures("gaming")
        reddit_alerts = [a for a in alerts if "FetchRedditClips" in a.message]
        assert len(reddit_alerts) == 1, (
            "sources_config={} + silent stage MUST alert — this is the "
            "sources_config-population class of bug the detector exists to catch"
        )

    def test_anime_promos_requires_both_providers_disabled(self, fake_runs_dir, monkeypatch):
        """FetchAnimePromos uses jikan OR anilist. Only both-disabled → allowlist."""
        from genlab_core.monitoring.health_monitor import _stage_intentionally_disabled
        from genlab_core.pipeline import pipeline_runner

        # Case A: only jikan disabled → NOT allowlisted (anilist could still work)
        monkeypatch.setattr(
            pipeline_runner,
            "_load_sources_yaml",
            lambda _root, _nid="": {"jikan": {"enabled": False}, "anilist": {"enabled": True}},
        )
        assert _stage_intentionally_disabled("anime", "FetchAnimePromos") is False

        # Case B: both disabled → allowlisted
        monkeypatch.setattr(
            pipeline_runner,
            "_load_sources_yaml",
            lambda _root, _nid="": {
                "jikan": {"enabled": False},
                "anilist": {"enabled": False},
            },
        )
        assert _stage_intentionally_disabled("anime", "FetchAnimePromos") is True

    def test_load_error_does_not_suppress_alert(self, fake_runs_dir, monkeypatch):
        """The allowlist is a false-positive suppressor, not a load-bearing
        gate. A broken sources.yaml MUST NOT silently suppress alerts.
        """
        from genlab_core.pipeline import pipeline_runner

        def _boom(_root, _nid=""):
            raise RuntimeError("simulated yaml load failure")

        monkeypatch.setattr(pipeline_runner, "_load_sources_yaml", _boom)

        for i in (1, 2, 3):
            _write_metrics_jsonl(
                fake_runs_dir / f"gaming_run{i:03d}",
                {"FetchRedditClips": 0.0001},
            )

        alerts = check_fetcher_stage_silent_failures("gaming")
        reddit_alerts = [a for a in alerts if "FetchRedditClips" in a.message]
        assert len(reddit_alerts) == 1, (
            "loader error must NOT swallow the alert — allowlist "
            "fail-open is fail-alert-open, not fail-silent"
        )

    def test_stage_without_config_gate_never_allowlisted(self, monkeypatch):
        """FetchTrendingVideos has no sources.yaml gate — cannot be allowlisted."""
        from genlab_core.monitoring.health_monitor import _stage_intentionally_disabled
        from genlab_core.pipeline import pipeline_runner

        # Even if EVERY key is disabled, FetchTrendingVideos isn't in the
        # gate map so its silence is always actionable.
        monkeypatch.setattr(
            pipeline_runner,
            "_load_sources_yaml",
            lambda _root, _nid="": {
                "reddit": {"enabled": False},
                "scorebat": {"enabled": False},
            },
        )
        assert _stage_intentionally_disabled("gaming", "FetchTrendingVideos") is False


class TestSummaryLineHandling:
    def test_pipeline_summary_footer_ignored(self, fake_runs_dir):
        """The summary footer line with ``_type=pipeline_summary`` must
        not be mistaken for a stage entry. (If it were, the loader
        could break or false-classify based on the footer's fields.)"""
        # Write metrics that include a healthy fetcher + the summary
        # footer (already added by _write_metrics_jsonl). Verify
        # everything still works.
        for i in (1, 2, 3):
            _write_metrics_jsonl(
                fake_runs_dir / f"anime_run{i:03d}",
                {"FetchAnimePromos": 0.0001},
            )

        alerts = check_fetcher_stage_silent_failures("anime")
        assert len(alerts) == 1
        # confirm summary line was filtered out — if it weren't, we'd
        # see a confused stage_name in the alert details
        assert alerts[0].details["stage"] == "FetchAnimePromos"


class TestConstants:
    def test_consecutive_runs_constant_is_three(self):
        """Pin: changing this number is a policy decision — touch the
        docstring + memory note + this test if you adjust it."""
        assert _SILENT_FAILURE_CONSECUTIVE_RUNS == 3

    def test_monitored_stages_include_all_known_fetchers(self):
        """Pin: every fetcher mentioned in the sources_config postmortem
        (pipeline_runner._load_sources_yaml docstring) must be in the
        monitored set."""
        # The 6 stages that read sources_config + the original
        # FetchTrendingVideos (the YouTube fetcher).
        expected = {
            "FetchTrendingVideos",
            "FetchRedditClips",
            "FetchTMDBTrailers",
            "FetchAnimePromos",
            "FetchTwitchClips",
            "FetchSteamTrailers",
        }
        # The list also accepts FetchScorebat/FetchScoreBatHighlights aliases
        monitored = set(_FETCHER_STAGES_TO_MONITOR)
        missing = expected - monitored
        assert not missing, f"monitor list missing known fetchers: {missing}"
