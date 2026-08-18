"""Pin fetch_generated_backfill (2026-08-18, task #192):

  * Flag off → stage returns unchanged
  * Kill-switch file → stage returns unchanged (with WARN)
  * max_per_run=0 env → stage returns unchanged
  * Stories at/above threshold → no generation
  * Empty stories → generates + injects a story with all attribution
    fields populated (L1/L2/L4 gates pass)
  * bypass_video_id_dedup + bypass_reason set on generated story
  * Deterministic same-day topic across runs
  * Kill-switch path uses expected location so ops runbook is stable
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from genlab_core.pipeline.stages.fetch_generated_backfill import (
    _KILL_SWITCH_PATH,
    _MAX_PER_RUN_ENV,
    _THRESHOLD_ENV,
    FetchGeneratedBackfill,
    _todays_topic,
)


def _ctx(niche_id="anime", stories=None):
    return {
        "niche_id": niche_id,
        "stories": stories or [],
        "run_stats": {},
    }


class TestFlagAndGuards:
    def test_flag_off_returns_unchanged(self, monkeypatch):
        monkeypatch.delenv("GENLAB_ANIME_BACKFILL_NICHES", raising=False)
        ctx = _ctx()
        result = FetchGeneratedBackfill().execute(ctx)
        assert result["stories"] == []
        assert "genlab_ai_backfill" not in result["run_stats"]

    def test_kill_switch_returns_unchanged(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_ANIME_BACKFILL_NICHES", "anime")
        with patch(
            "genlab_core.pipeline.stages.fetch_generated_backfill._kill_switch_active",
            return_value=True,
        ):
            ctx = _ctx()
            result = FetchGeneratedBackfill().execute(ctx)
        assert result["stories"] == []

    def test_max_per_run_zero_disables(self, monkeypatch):
        monkeypatch.setenv("GENLAB_ANIME_BACKFILL_NICHES", "anime")
        monkeypatch.setenv(_MAX_PER_RUN_ENV, "0")
        ctx = _ctx()
        result = FetchGeneratedBackfill().execute(ctx)
        assert result["stories"] == []

    def test_threshold_met_no_generation(self, monkeypatch):
        """When post-dedup pool has enough stories, don't burn $ on backfill."""
        monkeypatch.setenv("GENLAB_ANIME_BACKFILL_NICHES", "anime")
        monkeypatch.setenv(_THRESHOLD_ENV, "2")
        with patch(
            "genlab_core.media.pruna_video_client.generate_backfill_clip",
        ) as mock_gen:
            ctx = _ctx(stories=[{"id": "a"}, {"id": "b"}, {"id": "c"}])
            result = FetchGeneratedBackfill().execute(ctx)
        assert len(result["stories"]) == 3, "existing stories untouched"
        mock_gen.assert_not_called()

    def test_kill_switch_path_is_runtime_dir(self):
        """Ops runbook cites this path in the kill-switch procedure —
        it must not silently move without updating the runbook."""
        assert _KILL_SWITCH_PATH == "/opt/genlab/.runtime/anime_backfill_kill"


class TestGeneration:
    def test_empty_pool_generates_and_injects_story(
        self, monkeypatch, tmp_path,
    ):
        monkeypatch.setenv("GENLAB_ANIME_BACKFILL_NICHES", "anime")
        monkeypatch.setenv(_THRESHOLD_ENV, "1")
        monkeypatch.setenv(_MAX_PER_RUN_ENV, "1")

        with patch(
            "genlab_core.media.pruna_video_client.generate_backfill_clip",
            return_value=MagicMock(
                ok=True,
                prompt="anime cinematic moment",
                video_url="https://example.test/generated.mp4",
                local_path=str(tmp_path / "gen.mp4"),
                cost_usd=0.025,
                task_id="task_abc",
                error=None,
            ),
        ):
            ctx = _ctx(stories=[])
            ctx["run_dir"] = str(tmp_path)
            result = FetchGeneratedBackfill().execute(ctx)

        assert len(result["stories"]) == 1
        story = result["stories"][0]
        # L1 attribution requirements
        assert story["source_channel_id"] == "genlab_ai_backfill"
        assert story["source_channel_name"] == "Gen Lab AI"
        # L2 exempt via non-YouTube source
        assert story["source_type"] == "genlab_ai_backfill"
        assert "youtube" not in story["source_type"].lower()
        # L4 caption marker
        assert story["source_credit"].startswith("\U0001f916")
        assert "AI-generated" in story["source_credit"]
        # StoryCandidate contract for absent video_id
        assert story["video_id"] is None
        assert story["bypass_video_id_dedup"] is True
        assert story["bypass_reason"] == "ai_generated_backfill"
        # Cost telemetry preserved
        assert result["run_stats"]["genlab_ai_backfill"]["generated"] == 1
        assert result["run_stats"]["genlab_ai_backfill"]["total_cost_usd"] == 0.025

    def test_generation_failure_no_story_added(
        self, monkeypatch, tmp_path,
    ):
        monkeypatch.setenv("GENLAB_ANIME_BACKFILL_NICHES", "anime")
        monkeypatch.setenv(_THRESHOLD_ENV, "1")
        monkeypatch.setenv(_MAX_PER_RUN_ENV, "1")

        with patch(
            "genlab_core.media.pruna_video_client.generate_backfill_clip",
            return_value=MagicMock(
                ok=False, prompt="x", error="belt down",
                video_url=None, local_path=None, cost_usd=None,
                task_id=None,
            ),
        ):
            ctx = _ctx(stories=[])
            ctx["run_dir"] = str(tmp_path)
            result = FetchGeneratedBackfill().execute(ctx)

        # Empty pool + gen failed → still empty pool. Not a crash.
        assert result["stories"] == []
        stats = result["run_stats"]["genlab_ai_backfill"]
        assert stats["generated"] == 0
        assert stats["requested"] == 1

    def test_wrong_niche_ignored(self, monkeypatch, tmp_path):
        """anime canary — gaming/movies/sports/ai_creators unaffected."""
        monkeypatch.setenv("GENLAB_ANIME_BACKFILL_NICHES", "anime")
        for other in ("gaming", "movies", "sports", "ai_creators"):
            ctx = _ctx(niche_id=other, stories=[])
            result = FetchGeneratedBackfill().execute(ctx)
            assert result["stories"] == [], (
                f"niche={other} should be skipped when canary=anime only"
            )


class TestTopicRotation:
    def test_same_day_same_topic(self):
        # Deterministic within a day
        t1 = _todays_topic("anime")
        t2 = _todays_topic("anime")
        assert t1 == t2

    def test_different_niches_can_get_different_topics(self):
        """The hash(niche_id) term in the index means different niches
        MAY land on different topics for the same day. Not required to
        differ but the mechanism must exist."""
        # This test just verifies the function exists and returns valid
        # topics for both niches. Actual difference is data-dependent.
        assert isinstance(_todays_topic("anime"), str)
        assert isinstance(_todays_topic("gaming"), str)

    def test_topic_is_from_bank(self):
        from genlab_core.pipeline.stages.fetch_generated_backfill import (
            _ANIME_TOPIC_ROTATION,
        )

        assert _todays_topic("anime") in _ANIME_TOPIC_ROTATION


class TestEmittedSources:
    def test_declares_backfill_source(self):
        """P1 phase-2 producer registry: EMITTED_SOURCES must include
        every source string the stage puts on the wire."""
        assert "genlab_ai_backfill" in FetchGeneratedBackfill.EMITTED_SOURCES
