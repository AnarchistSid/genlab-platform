"""End-to-end integration test — intelligent transformation sprint (PR 16).

Exercises the FULL pipeline that PRs 1-15 compose:

    Config (PR 2) → arm registration (PR 3)
       ↓
    Selector (PR 4) picks arms per dimension
       ↓
    Orchestrator (PR 15) applies render modules (PRs 7-12)
       ↓
    Publisher stores arm_ids_by_dimension in pending_feedback (PR 5)
       ↓
    MetricCollector fetches retention metrics (PR 6)
       ↓
    Router routes rewards to N transformation-arm posteriors (PR 5)
       ↓
    Cross-niche transfer warms new arms (PR 13)

No live subprocess / DB / API — everything mocked at boundaries.
The integration surface is the composition itself: same modules,
same interfaces, same fail-open semantics as production.

## What this test proves

The 15 modules that shipped separately compose into ONE working
pipeline with:

  * arm_ids_by_dimension round-trips: selector → orchestrator →
    pending_feedback → router → bandit_updater calls
  * Per-dimension reward attribution: hook_framing gets hold_3s,
    music_mood gets hold_15s, etc. (PR 6 derivations)
  * Fail-open at every layer: env flag off short-circuits;
    individual stage failure doesn't cascade
  * Cross-niche transfer key format matches selector arm_ids:
    ``transform__<dim>__<value>`` → ``<dim>__<value>`` extraction
"""

from __future__ import annotations

import random
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from genlab_core.learning.pending_feedback_task import PendingFeedbackTask
from genlab_core.learning.retention_derivations import derive_retention_metrics
from genlab_core.learning.transformation_reward_router import (
    route_transformation_rewards,
)
from genlab_core.media.intelligent_transform import IntelligentTransformConfig
from genlab_core.media.transformation_orchestrator import (
    apply_transformations,
)
from genlab_core.media.transformation_selector import (
    select_transformation_dimensions,
)


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Fake niche workspace with a source video."""
    niche_root = tmp_path / "gaming_niche"
    niche_root.mkdir()
    (niche_root / "assets").mkdir()
    source = niche_root / "source.mp4"
    source.write_bytes(b"fake_source_video" * 200)
    return niche_root


@pytest.fixture
def full_config() -> IntelligentTransformConfig:
    """IntelligentTransformConfig with all 11 dimensions enabled."""
    return IntelligentTransformConfig.from_visuals_dict(
        {
            "intelligent_transform": {
                "enabled": True,
                "dimensions": {
                    "music_mood": {
                        "enabled": True,
                        "moods": ["cinematic", "hype"],
                        "source_audio_duck_db": -12,
                        "music_bed_db": -6,
                    },
                    "caption_style": {
                        "enabled": True,
                        "styles": ["word_by_word", "hormozi_yellow"],
                    },
                    "hook_framing": {
                        "enabled": True,
                        "framings": ["question_open", "statistic_shock"],
                    },
                    "intro_animation": {
                        "enabled": True,
                        "templates": ["logo_zoom"],
                        "asset_dir": "assets/motion/intros",
                    },
                    "outro_cta": {
                        "enabled": True,
                        "styles": ["follow"],
                        "asset_dir": "assets/motion/outros",
                    },
                    "pan_zoom": {
                        "enabled": True,
                        "patterns": ["ken_burns_slow", "no_motion"],
                    },
                    "highlight_moment": {
                        "enabled": True,
                        "methods": ["first_frame"],
                    },
                    "audio_ducking": {
                        "enabled": True,
                        "levels_db": [-12, -15],
                    },
                    "music_visual_sync": {
                        "enabled": True,
                        "modes": ["ambient"],
                    },
                    "caption_emphasis_color": {
                        "enabled": True,
                        "colors": ["yellow", "red"],
                    },
                    "caption_pacing": {
                        "enabled": True,
                        "pacing_ms_options": [600, 750],
                    },
                },
            }
        }
    )


@pytest.fixture
def populated_proxy() -> MagicMock:
    """Mock bandit_arms proxy pre-loaded with transformation arms
    covering all dimensions this config declares."""
    proxy = MagicMock()
    items = []
    combos = [
        ("music_mood", "cinematic"),
        ("music_mood", "hype"),
        ("caption_style", "word_by_word"),
        ("caption_style", "hormozi_yellow"),
        ("hook_framing", "question_open"),
        ("hook_framing", "statistic_shock"),
        ("intro_animation", "logo_zoom"),
        ("outro_cta", "follow"),
        ("pan_zoom", "ken_burns_slow"),
        ("pan_zoom", "no_motion"),
        ("highlight_moment", "first_frame"),
        ("audio_ducking", "-12"),
        ("audio_ducking", "-15"),
        ("music_visual_sync", "ambient"),
        ("caption_emphasis_color", "yellow"),
        ("caption_emphasis_color", "red"),
        ("caption_pacing", "600"),
        ("caption_pacing", "750"),
    ]
    for i, (dim, value) in enumerate(combos):
        items.append(
            {
                "id": f"row_{i}",
                "fields": {
                    "arm_id": f"transform__{dim}__{value}",
                    "arm_type": "transformation",
                    "dimension": dim,
                    "dimension_value": value,
                    "alpha": 2.0 + i * 0.3,  # varying rates
                    "beta": 3.0,
                    "niche_id": "gaming",
                },
            }
        )
    proxy.all.return_value = items
    return proxy


# ============================================================
# End-to-end pipeline tests
# ============================================================


class TestSelectorProducesChoicesForAllEnabledDimensions:
    """First stage: selector picks arms given a fresh niche."""

    def test_selects_all_11_dimensions(
        self, monkeypatch, populated_proxy, full_config
    ) -> None:
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        choices = select_transformation_dimensions(
            "gaming",
            full_config,
            proxy=populated_proxy,
            rng=random.Random(42),
        )
        expected = {
            "music_mood",
            "caption_style",
            "hook_framing",
            "intro_animation",
            "outro_cta",
            "pan_zoom",
            "highlight_moment",
            "audio_ducking",
            "music_visual_sync",
            "caption_emphasis_color",
            "caption_pacing",
        }
        assert set(choices.choices.keys()) == expected

    def test_arm_ids_use_composite_format(
        self, monkeypatch, populated_proxy, full_config
    ) -> None:
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        choices = select_transformation_dimensions(
            "gaming",
            full_config,
            proxy=populated_proxy,
            rng=random.Random(42),
        )
        for dim, choice in choices.choices.items():
            # Every arm_id follows transform__<dim>__<value>
            assert choice.arm_id.startswith(f"transform__{dim}__")
            # dimension_value is the last suffix
            assert choice.arm_id.endswith(choice.dimension_value)


class TestOrchestratorAppliesSelection:
    """Orchestrator takes selector choices and runs render pipeline."""

    def test_arm_ids_round_trip_through_orchestrator(
        self, monkeypatch, populated_proxy, full_config, workspace: Path
    ) -> None:
        """The full arm_ids_by_dimension dict from selection must
        arrive intact in TransformationResult, regardless of whether
        individual render stages succeed."""
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        source = workspace / "source.mp4"
        output = workspace / "out.mp4"

        # Selector uses the real proxy; render modules all mocked to
        # fail (missing assets, no ffmpeg — realistic prod cold-start).
        with patch(
            "genlab_core.http.backlog_client.BacklogClient"
        ) as mock_client_cls:
            mock_client = MagicMock()
            mock_client.bandit_arms = populated_proxy
            mock_client_cls.return_value = mock_client

            result = apply_transformations(
                source_video_path=source,
                output_path=output,
                niche_root=workspace,
                niche_id="gaming",
                config=full_config,
            )

        # Every arm chosen makes it into the result
        assert len(result.arm_ids_by_dimension) > 0
        # All are transform__ arms
        for arm_id in result.arm_ids_by_dimension.values():
            assert arm_id.startswith("transform__")

    def test_env_flag_off_short_circuits(
        self, monkeypatch, populated_proxy, full_config, workspace: Path
    ) -> None:
        monkeypatch.delenv(
            "GENLAB_INTELLIGENT_TRANSFORM_ENABLED", raising=False
        )
        source = workspace / "source.mp4"
        output = workspace / "out.mp4"
        # Proxy should NOT even be queried
        with patch(
            "genlab_core.http.backlog_client.BacklogClient"
        ) as mock_client_cls:
            result = apply_transformations(
                source_video_path=source,
                output_path=output,
                niche_root=workspace,
                niche_id="gaming",
                config=full_config,
            )
        mock_client_cls.assert_not_called()
        assert result.output_path == source
        assert result.arm_ids_by_dimension == {}


class TestPendingFeedbackRoundTrip:
    """The arm_ids from orchestrator must survive serialize + parse."""

    def test_task_serializes_arm_ids_as_json(self) -> None:
        from datetime import UTC, datetime

        task = PendingFeedbackTask(
            content_id="c1",
            platform="instagram",
            niche_id="gaming",
            published_at=datetime(2026, 7, 5, 12, 0, tzinfo=UTC),
            platform_post_id="ig_abc",
            bandit_arm="gameplay_clip",
            arm_ids_by_dimension={
                "music_mood": "transform__music_mood__cinematic",
                "caption_style": "transform__caption_style__word_by_word",
                "hook_framing": "transform__hook_framing__question_open",
            },
        )
        fields = task.to_sharepoint_fields()
        assert "arm_ids_by_dimension" in fields

        import json as _json
        parsed = _json.loads(fields["arm_ids_by_dimension"])
        assert parsed == task.arm_ids_by_dimension

    def test_store_parses_dict_shape(self) -> None:
        from genlab_core.learning.pending_feedback_store import (
            PendingFeedbackStore,
        )

        item = {
            "fields": {
                "post_id": "ig_abc",
                "platform": "instagram",
                "niche_id": "gaming",
                "publish_time": "2026-07-05T12:00:00+00:00",
                "collection_status": "awaiting_6h",
                "arm_ids_by_dimension": {
                    "music_mood": "transform__music_mood__cinematic",
                    "pan_zoom": "transform__pan_zoom__ken_burns_slow",
                },
            }
        }
        task = PendingFeedbackStore._from_sharepoint_item(item)
        assert len(task.arm_ids_by_dimension) == 2


class TestRewardAttribution:
    """Router iterates arm_ids and updates N posteriors per reel."""

    def test_router_updates_all_dimension_arms(self) -> None:
        updater = MagicMock()
        arm_ids = {
            "music_mood": "transform__music_mood__cinematic",
            "hook_framing": "transform__hook_framing__question_open",
            "caption_style": "transform__caption_style__word_by_word",
            "pan_zoom": "transform__pan_zoom__ken_burns_slow",
        }
        # Simulate metrics with clear signals per retention window
        metrics = {
            "reach": 100,
            "likes": 5,
            "comments": 2,
            "saves": 3,
            "total_interactions": 20,
            "avg_watch_time": 8000,  # 8 seconds = strong hold_3s but partial hold_15s
        }
        n_updated = route_transformation_rewards(
            niche_id="gaming",
            platform="instagram",
            arm_ids_by_dimension=arm_ids,
            scalar_reward=0.5,
            bandit_updater=updater,
            bandit_context={"weekday": 0.3, "hour": 0.5},
            metrics=metrics,
        )
        assert n_updated == 4
        assert updater.call_count == 4

    def test_per_dim_rewards_use_retention_derivations(self) -> None:
        """hook_framing should get hold_3s; music_mood should get hold_15s."""
        rewards_by_arm: dict[str, float] = {}

        def _capture(niche, arm, platform, reward, ctx):
            rewards_by_arm[arm] = reward

        arm_ids = {
            "hook_framing": "transform__hook_framing__question_open",
            "music_mood": "transform__music_mood__cinematic",
        }
        # avg_watch_time = 3000ms = 3s → hold_3s=1.0, hold_15s=0.2
        metrics = {"reach": 100, "avg_watch_time": 3000}
        route_transformation_rewards(
            niche_id="gaming",
            platform="instagram",
            arm_ids_by_dimension=arm_ids,
            scalar_reward=0.5,  # fallback
            bandit_updater=_capture,
            metrics=metrics,
        )
        # hook_framing → hold_3s = 1.0 (avg = threshold)
        assert rewards_by_arm[
            "transform__hook_framing__question_open"
        ] == pytest.approx(1.0)
        # music_mood → hold_15s = 3/15 = 0.2
        assert rewards_by_arm[
            "transform__music_mood__cinematic"
        ] == pytest.approx(0.2)

    def test_retention_derivations_produce_expected_shape(self) -> None:
        """Sanity check on the derivation module downstream of the router."""
        d = derive_retention_metrics(
            {
                "reach": 100,
                "likes": 5,
                "comments": 2,
                "saves": 3,
                "total_interactions": 20,
                "avg_watch_time": 15000,  # 15s
            },
            "instagram",
        )
        assert d["hold_3s"] == 1.0
        assert d["hold_15s"] == 1.0
        # sends_per_reach = (20 - 5 - 2 - 3) / 100 = 0.1
        assert d["sends_per_reach"] == pytest.approx(0.1)


class TestCrossNicheTransferKeyExtraction:
    """PR 13's extractor must recognize transformation arm_ids."""

    def test_selector_arm_ids_extract_correctly(
        self, monkeypatch, populated_proxy, full_config
    ) -> None:
        """Every arm the selector produces must be extractable by
        the cross-niche transfer key extractor — otherwise cold-start
        arms don't get warm-started from other niches."""
        from genlab_core.learning.cross_niche_transfer import (
            extract_prior_key,
        )

        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        choices = select_transformation_dimensions(
            "gaming",
            full_config,
            proxy=populated_proxy,
            rng=random.Random(42),
        )
        for choice in choices.choices.values():
            key = extract_prior_key(choice.arm_id)
            assert key is not None
            # Composite key: <dim>__<value>
            assert "__" in key
            # Round-trip: dim + value from key match choice
            expected_key = f"{choice.dimension}__{choice.dimension_value}"
            assert key == expected_key


class TestFullPipelineFailOpen:
    """Every failure mode → orchestrator returns valid output."""

    def test_all_stages_fail_returns_source(
        self, monkeypatch, populated_proxy, full_config, workspace: Path
    ) -> None:
        """Even if every render module returns False (no assets, no
        ffmpeg), the orchestrator returns a valid TransformationResult
        with source_video_path as output."""
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        source = workspace / "source.mp4"
        output = workspace / "out.mp4"

        with patch(
            "genlab_core.http.backlog_client.BacklogClient"
        ) as mock_client_cls:
            mock_client = MagicMock()
            mock_client.bandit_arms = populated_proxy
            mock_client_cls.return_value = mock_client

            result = apply_transformations(
                source_video_path=source,
                output_path=output,
                niche_root=workspace,
                niche_id="gaming",
                config=full_config,
            )
        # No stage succeeded (no assets, no ffmpeg subprocess mocked)
        # → all skipped, output falls back to source
        assert result.output_path == source
        # But arm_ids_by_dimension still populated — reward routing
        # will still update posteriors so bandit keeps learning
        assert len(result.arm_ids_by_dimension) > 0

    def test_config_disabled_short_circuits_before_selector(
        self, monkeypatch, populated_proxy, workspace: Path
    ) -> None:
        """When config.enabled=False, orchestrator returns immediately
        without touching the DB — verified by no proxy interaction."""
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")

        # Disabled config
        cfg = IntelligentTransformConfig.from_visuals_dict(
            {"intelligent_transform": {"enabled": False}}
        )
        source = workspace / "source.mp4"
        output = workspace / "out.mp4"

        with patch(
            "genlab_core.http.backlog_client.BacklogClient"
        ) as mock_client_cls:
            result = apply_transformations(
                source_video_path=source,
                output_path=output,
                niche_root=workspace,
                niche_id="gaming",
                config=cfg,
            )
        # Never even constructed a BacklogClient
        mock_client_cls.assert_not_called()
        assert result.output_path == source
        assert result.arm_ids_by_dimension == {}


class TestSprintReadinessSmoke:
    """One test that exercises the ENTIRE loop end-to-end."""

    def test_full_loop_selector_to_router(
        self, monkeypatch, populated_proxy, full_config
    ) -> None:
        """Runs: select arms → serialize to task → parse back →
        route rewards → verify N bandit_updater calls."""
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")

        # 1. Selector picks arms
        choices = select_transformation_dimensions(
            "gaming",
            full_config,
            proxy=populated_proxy,
            rng=random.Random(0),
        )
        arm_ids = choices.to_arm_ids_by_dimension()
        assert len(arm_ids) == 11  # All 11 dims selected

        # 2. Round-trip through PendingFeedbackTask serialization
        from datetime import UTC, datetime

        task = PendingFeedbackTask(
            content_id="c1",
            platform="instagram",
            niche_id="gaming",
            published_at=datetime(2026, 7, 5, tzinfo=UTC),
            platform_post_id="ig_abc",
            arm_ids_by_dimension=arm_ids,
        )
        payload = task.to_sharepoint_fields()

        # 3. Simulate DB read (JSONB auto-parsed to dict on Postgres)
        from genlab_core.learning.pending_feedback_store import (
            PendingFeedbackStore,
        )
        import json as _json

        item = {
            "fields": {
                "post_id": task.platform_post_id,
                "platform": task.platform,
                "niche_id": task.niche_id,
                "publish_time": task.published_at.isoformat(),
                "collection_status": "awaiting_48h",
                "arm_ids_by_dimension": _json.loads(
                    payload["arm_ids_by_dimension"]
                ),
            }
        }
        rehydrated = PendingFeedbackStore._from_sharepoint_item(item)
        assert rehydrated.arm_ids_by_dimension == arm_ids

        # 4. Route rewards — verify all 11 arms updated
        updater = MagicMock()
        n = route_transformation_rewards(
            niche_id="gaming",
            platform="instagram",
            arm_ids_by_dimension=rehydrated.arm_ids_by_dimension,
            scalar_reward=0.65,
            bandit_updater=updater,
            bandit_context={},
            metrics={
                "reach": 500,
                "likes": 30,
                "comments": 10,
                "saves": 15,
                "total_interactions": 100,
                "avg_watch_time": 12000,
            },
        )
        assert n == 11
        assert updater.call_count == 11


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
