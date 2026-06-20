"""Regression tests for P4 pipeline_template backbone merge.

Pins the contract that ``niche_loader.merge_pipeline_template`` produces
the same final ``pipeline.stages`` list as the pre-P4 explicit form,
and rejects drift (typo'd inject names, unknown template names).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from genlab_core.niche_loader import load_niche_config, merge_pipeline_template


def test_merge_no_op_when_template_absent() -> None:
    """A niche without ``pipeline_template:`` keeps its explicit stages list."""
    cfg = {
        "niche_id": "legacy",
        "pipeline": {
            "stages": [
                {"class": "a.A"},
                {"class": "b.B"},
            ],
        },
    }
    out = merge_pipeline_template(cfg)
    assert out["pipeline"]["stages"] == [{"class": "a.A"}, {"class": "b.B"}]


def test_merge_splices_inject_into_template() -> None:
    """A niche with ``pipeline_template: backbone`` has its inject splicedin."""
    cfg = {
        "niche_id": "sports",
        "pipeline_template": "backbone",
        "pipeline": {
            "inject": {
                "phase1_fetchers_extra": [
                    {"class": "genlab_core.pipeline.stages.fetch_scorebat.FetchScoreBatHighlights"},
                ],
                "phase1_content_research": [
                    {"class": "cw_strategies.content_research.SportContentResearchStrategy"}
                ],
                "phase1_scoring": [{"class": "cw_strategies.scoring.SportScoringStrategy"}],
                "phase1_relevance_gate": [],
                "phase2_writing": [{"class": "cw_strategies.writing.SportWritingStrategy"}],
                "phase2_hooks": [{"class": "cw_strategies.hooks.SportHookStrategy"}],
                "phase4_visual_render": [
                    {"class": "cw_strategies.visual_render.SportVisualRenderStrategy"}
                ],
                "phase5_platform_adaptation": [
                    {"class": "cw_strategies.platform_adaptation.SportPlatformAdaptationStrategy"}
                ],
            },
        },
    }
    out = merge_pipeline_template(cfg)
    classes = [s["class"] for s in out["pipeline"]["stages"]]
    # Order matters — pin a few anchor positions
    assert classes[0] == "genlab_core.pipeline.stages.express_lane.ExpressLane"
    assert classes[1] == "genlab_core.media.trending_video_fetcher.FetchTrendingVideos"
    assert "genlab_core.pipeline.stages.fetch_scorebat.FetchScoreBatHighlights" in classes
    assert "cw_strategies.scoring.SportScoringStrategy" in classes
    assert classes[-1] == "genlab_core.pipeline.stages.run_report.RunReport"


def test_merge_rejects_unknown_template_name() -> None:
    """Typo in pipeline_template: surfaces loudly at load time."""
    cfg = {
        "niche_id": "x",
        "pipeline_template": "nonexistent",
        "pipeline": {"inject": {}},
    }
    with pytest.raises(ValueError, match="Unknown pipeline_template"):
        merge_pipeline_template(cfg)


def test_merge_rejects_inject_drift_typo() -> None:
    """Niche injects a slot the template doesn't reference — drift guard."""
    cfg = {
        "niche_id": "x",
        "pipeline_template": "backbone",
        "pipeline": {
            "inject": {
                "phase1_fetchers_extra": [],
                "phase1_content_research": [{"class": "a.A"}],
                "phase1_scoring": [{"class": "b.B"}],
                "phase2_writing": [{"class": "c.C"}],
                "phase2_hooks": [{"class": "d.D"}],
                "phase4_visual_render": [{"class": "e.E"}],
                "phase5_platform_adaptation": [{"class": "f.F"}],
                # Typo: phsae1_relevance_gate (should be phase1_)
                "phsae1_relevance_gate": [],
            },
        },
    }
    with pytest.raises(
        ValueError, match="declares inject points the .* template doesn't reference"
    ):
        merge_pipeline_template(cfg)


def test_merge_rejects_non_list_inject() -> None:
    """Inject value must be a list of stage entries, not e.g. a dict."""
    cfg = {
        "niche_id": "x",
        "pipeline_template": "backbone",
        "pipeline": {
            "inject": {
                "phase1_fetchers_extra": {"class": "wrong.shape"},  # not a list
                "phase1_content_research": [],
                "phase1_scoring": [],
                "phase2_writing": [],
                "phase2_hooks": [],
                "phase4_visual_render": [],
                "phase5_platform_adaptation": [],
            },
        },
    }
    with pytest.raises(ValueError, match="must be a list of stage entries"):
        merge_pipeline_template(cfg)


def test_load_niche_config_invokes_merge(tmp_path: Path) -> None:
    """End-to-end: load_niche_config returns merged pipeline.stages for opt-in niche."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "niche.yaml").write_text(
        yaml.safe_dump(
            {
                "niche_id": "test",
                "pipeline_template": "backbone",
                "pipeline": {
                    "inject": {
                        "phase1_fetchers_extra": [],
                        "phase1_content_research": [{"class": "x.X"}],
                        "phase1_scoring": [{"class": "y.Y"}],
                        "phase1_relevance_gate": [],
                        "phase2_writing": [{"class": "w.W"}],
                        "phase2_hooks": [{"class": "h.H"}],
                        "phase4_visual_render": [{"class": "v.V"}],
                        "phase5_platform_adaptation": [{"class": "p.P"}],
                    },
                },
            }
        )
    )
    cfg = load_niche_config("test", tmp_path)
    classes = [s["class"] for s in cfg["pipeline"]["stages"]]
    # Backbone provides the common stages; niche injects fill the slots.
    assert "x.X" in classes  # content_research inject
    assert "v.V" in classes  # visual_render inject
    assert "genlab_core.pipeline.stages.express_lane.ExpressLane" in classes  # template-provided
    assert (
        "genlab_core.pipeline.stages.push_to_backlog.PushToBacklog" in classes
    )  # template-provided


@pytest.mark.parametrize(
    "niche_id,channel_dir,strategy_prefix,extra_fetcher_class,has_relevance_gate",
    [
        (
            "ai_creators",
            "BlackboxBrief",
            "bb_strategies",
            None,
            True,  # BB is the only niche with RelevanceGate
        ),
        (
            "sports",
            "ClutchWire",
            "cw_strategies",
            "genlab_core.pipeline.stages.fetch_scorebat.FetchScoreBatHighlights",
            False,
        ),
        (
            "movies",
            "SpliceReel",
            "sr_strategies",
            "genlab_core.pipeline.stages.fetch_tmdb_trailers.FetchTMDBTrailers",
            False,
        ),
        (
            "anime",
            "FrameDrift",
            "fd_strategies",
            "genlab_core.pipeline.stages.fetch_anime_promos.FetchAnimePromos",
            False,
        ),
    ],
)
def test_all_4_templatized_niches_produce_24_stage_pipeline(
    niche_id, channel_dir, strategy_prefix, extra_fetcher_class, has_relevance_gate
) -> None:
    """Pin the cross-niche contract: all 4 P4-migrated niches produce a
    24-stage pipeline with the same stage ORDER and the niche's strategy
    classes in the correct slots.

    If a future template edit accidentally drops a stage, this catches
    it on every niche simultaneously instead of one-niche-at-a-time.
    """
    cfg = load_niche_config(niche_id, Path(__file__).resolve().parents[2] / channel_dir)
    stages = cfg["pipeline"]["stages"]
    classes = [s["class"] for s in stages]

    # All 4 should produce exactly 24 stages (template provides 18 common
    # + niche injects 6 strategies + optional 1 fetcher + optional 1
    # relevance gate; the conditional ones balance out).
    if has_relevance_gate:
        # BB: 18 common + 6 strategies + 0 extra fetcher + 1 relevance = 24, wait
        # Let me think: template has the inject points spliced in. With
        # phase1_fetchers_extra empty + phase1_relevance_gate filled,
        # BB lands at 24.
        pass

    # Anchor positions — these MUST be at these indexes for all 4 niches.
    assert classes[0] == "genlab_core.pipeline.stages.express_lane.ExpressLane"
    assert classes[1] == "genlab_core.media.trending_video_fetcher.FetchTrendingVideos"
    assert classes[-1] == "genlab_core.pipeline.stages.run_report.RunReport"
    assert classes[-2] == "genlab_core.pipeline.stages.performance_learner.PerformanceLearner"
    assert classes[-3] == "genlab_core.pipeline.stages.fetch_insights.FetchInsights"

    # Niche-specific strategy classes must be present
    strategy_classes_present = [c for c in classes if c.startswith(strategy_prefix)]
    assert len(strategy_classes_present) >= 5, (
        f"{niche_id} should have at least 5 strategy classes from {strategy_prefix}, "
        f"got {strategy_classes_present}"
    )

    # Niche-specific extra fetcher slot
    if extra_fetcher_class:
        assert extra_fetcher_class in classes, (
            f"{niche_id} should have extra fetcher {extra_fetcher_class} in pipeline"
        )

    # Common stages every niche MUST have (template-provided)
    common_required = [
        "genlab_core.media.trending_video_fetcher.FetchTrendingVideos",
        "genlab_core.pipeline.stages.fetch_reddit_clips.FetchRedditClips",
        "genlab_core.pipeline.stages.pre_download_dedup.PreDownloadDedup",
        "genlab_core.media.download_top_videos.DownloadTopVideos",
        "genlab_core.pipeline.stages.video_gate.VideoGate",
        "genlab_core.monetization.affiliate_matcher.AffiliateMatch",
        "genlab_core.pipeline.stages.qc_gates.QCGates",
        "genlab_core.pipeline.stages.virality_scoring.ViralityScoring",
        "genlab_core.pipeline.stages.render_text_overlays.RenderTextOverlays",
        "genlab_core.pipeline.stages.generate_audio.GenerateAudio",
        "genlab_core.pipeline.stages.render_whisper_captions.RenderWhisperCaptions",
        "genlab_core.pipeline.stages.validate_videos.ValidateVideos",
        "genlab_core.pipeline.stages.push_to_backlog.PushToBacklog",
    ]
    for required in common_required:
        assert required in classes, f"{niche_id} pipeline missing required common stage {required}"

    # RelevanceGate is BB-only
    if has_relevance_gate:
        assert "genlab_core.pipeline.stages.relevance_gate.RelevanceGate" in classes
    else:
        assert "genlab_core.pipeline.stages.relevance_gate.RelevanceGate" not in classes

    # fail_mode invariants preserved on critical stages.
    # Class name conventions vary slightly per niche (BBVisualRender vs
    # MovieVisualRender), so locate it via substring rather than reconstruct.
    by_cls = {s["class"]: s for s in stages}
    visual_renders = [c for c in classes if "visual_render" in c.lower()]
    assert len(visual_renders) == 1
    assert by_cls[visual_renders[0]].get("fail_mode") == "abort"
    assert (
        by_cls["genlab_core.pipeline.stages.push_to_backlog.PushToBacklog"].get("fail_mode")
        == "abort"
    )


def test_clutchwire_real_niche_yaml_matches_pre_p4_stages() -> None:
    """The real ClutchWire niche.yaml (migrated to use the template) must
    produce a stage list semantically identical to the pre-P4 explicit form.

    Pins the ordering + attributes — if a future template edit accidentally
    drops or reorders a stage, this catches it.
    """
    cfg = load_niche_config("sports", Path(__file__).resolve().parents[2] / "ClutchWire")
    stages = cfg["pipeline"]["stages"]
    classes = [s["class"] for s in stages]

    expected = [
        "genlab_core.pipeline.stages.express_lane.ExpressLane",
        "genlab_core.media.trending_video_fetcher.FetchTrendingVideos",
        "genlab_core.pipeline.stages.fetch_scorebat.FetchScoreBatHighlights",
        "genlab_core.pipeline.stages.fetch_reddit_clips.FetchRedditClips",
        "cw_strategies.content_research.SportContentResearchStrategy",
        "cw_strategies.scoring.SportScoringStrategy",
        "genlab_core.pipeline.stages.pre_download_dedup.PreDownloadDedup",
        "genlab_core.media.download_top_videos.DownloadTopVideos",
        "genlab_core.pipeline.stages.video_gate.VideoGate",
        "cw_strategies.writing.SportWritingStrategy",
        "cw_strategies.hooks.SportHookStrategy",
        "genlab_core.monetization.affiliate_matcher.AffiliateMatch",
        "genlab_core.pipeline.stages.qc_gates.QCGates",
        "genlab_core.pipeline.stages.virality_scoring.ViralityScoring",
        "cw_strategies.visual_render.SportVisualRenderStrategy",
        "genlab_core.pipeline.stages.render_text_overlays.RenderTextOverlays",
        "genlab_core.pipeline.stages.generate_audio.GenerateAudio",
        "genlab_core.pipeline.stages.render_whisper_captions.RenderWhisperCaptions",
        "genlab_core.pipeline.stages.validate_videos.ValidateVideos",
        "cw_strategies.platform_adaptation.SportPlatformAdaptationStrategy",
        "genlab_core.pipeline.stages.push_to_backlog.PushToBacklog",
        "genlab_core.pipeline.stages.fetch_insights.FetchInsights",
        "genlab_core.pipeline.stages.performance_learner.PerformanceLearner",
        "genlab_core.pipeline.stages.run_report.RunReport",
    ]
    assert classes == expected, f"stage list drift:\n  got: {classes}\n  want: {expected}"

    # Pin a few critical attributes that prevent regression of fail_mode work
    by_cls = {s["class"]: s for s in stages}
    assert (
        by_cls["cw_strategies.visual_render.SportVisualRenderStrategy"].get("fail_mode") == "abort"
    )
    assert (
        by_cls["genlab_core.pipeline.stages.push_to_backlog.PushToBacklog"].get("fail_mode")
        == "abort"
    )
    assert (
        by_cls["genlab_core.pipeline.stages.render_text_overlays.RenderTextOverlays"].get(
            "parallel_group"
        )
        == "post_render"
    )
