"""Two same-typed scalars in a tuple bind to the wrong name silently.

`prof, fps = motion_profile(clip)` bound a DURATION to `fps`. Both fields
were floats, so nothing complained: no type error, no test failure, no log.
The clip was 40.658 s long, the container 60 fps, and the plan then indexed
a per-frame motion profile at 40.658 samples/s — `motion_at(12.65)` scored
the motion at 8.58 s. A post-fight interview won the velocity ranking and
94 minutes of compute returned 96/96 mattes with every internal gate green.

A named field cannot do that: `MotionProfile.fps` is an AttributeError.

Part 30 §1. The AST pin in test_decode_fps.py guards the one line that broke;
this guards the next one.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_SRC = pathlib.Path(__file__).resolve().parents[3] / "genlab-core/src/genlab_core"
_SCALARS = {"float", "int", "str"}

# ── measured debt, 2026-09-21 ────────────────────────────────────────────────
# Every function below returns two same-typed scalars positionally. They are
# allowed ONLY because converting them now means editing monetization,
# platforms, frame_compositor and validate_videos while publishing is up on
# five channels — and the standing rule is that plumbing waits for publishing
# to be down. The list is the debt, written out so it is countable rather
# than implied, and so a NEW one fails this test instead of joining it.
#
# Ranked by what a swap actually costs, for whoever burns this down:
#
#   HIGH   — the two fields carry different meanings and a swap is silent
#            get_resolution (w,h) · _video_dims (w,h) · _measure_loudness
#            (lufs, true_peak) · _moment_match_beta (alpha, beta) ·
#            encode_*_cyclical (sin, cos) · _pricing (in, out) ·
#            _fetch_post_breakdown · welch_t_test (t, p)
#   MEDIUM — (system, user) prompt pairs; a swap inverts the prompt
#            *::_build_prompt ×4 · _canonical_headers · _canonical_request
#   LOW    — (x, y) or (a, b) where order is universal convention
#            centroid · _centroid · _cache_key · position_to_xy
_ALLOWED: frozenset[str] = frozenset(
    {
        "action/effects/_ops.py::centroid",
        "action/finish.py::_centroid",
        "intelligence/anthropic_client.py::_pricing",
        "learning/bayesian_gate.py::sample_preference",
        "learning/cross_niche_transfer.py::_moment_match_beta",
        "learning/cross_niche_transfer.py::get_transferred_prior",
        "learning/hook_classifier.py::_cache_key",
        "learning/linucb.py::encode_hour_cyclical",
        "learning/linucb.py::encode_weekday_cyclical",
        "learning/meta_prior.py::compute_warm_start_prior",
        "learning/top_creator_priors.py::correlation_to_prior_delta",
        "learning/top_creator_priors.py::get_arm_prior_adjustment",
        "llm/prompt_cache.py::extract_cache_usage",
        "llm/router.py::get_model_for_task",
        "media/fetch_ai_news_with_video.py::_search_youtube_for",
        "media/ffmpeg_utils.py::get_resolution",
        "media/ffmpeg_utils.py::position_to_xy",
        "media/frame_compositor.py::_build_branding_filters",
        "media/frame_compositor.py::_build_hook_filters",
        "media/frame_compositor.py::_build_reveal_filter",
        "media/highlight_detector.py::_longest_non_silent",
        "media/niche_fit_ranker.py::_build_prompt",
        "media/transformation_selector.py::_parse_arm_id",
        "monetization/geo_link_resolver.py::_try_earnkaro_transform",
        "monetization/geo_link_resolver.py::resolve_affiliate_link_with_network",
        "monetization/paapi_signing.py::_canonical_headers",
        "monetization/paapi_signing.py::_canonical_request",
        "observability/alert_auto_resolver.py::_extract_missing_slot",
        "pipeline/stages/validate_videos.py::_measure_loudness",
        "pipeline/stages/validate_videos.py::_video_dims",
        "platforms/metrics/facebook.py::_fetch_post_breakdown",
        "platforms/metrics/instagram.py::_resolve_credentials",
        "platforms/threads.py::_resolve_url_full",
        "quality/persona_drift.py::_build_prompt",
        "scheduling/auto_experiment_parser.py::_build_prompt",
        "scheduling/shadow_reviewer.py::_build_prompt",
        "strategies/base_writing.py::_validate_narration_with_retry",
        "tools/analyze_engagement_question_ab.py::welch_t_test",
    }
)


def _same_typed_scalar_pair_returns() -> set[str]:
    found: set[str] = set()
    for f in sorted(_SRC.rglob("*.py")):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            ret = node.returns
            if isinstance(ret, ast.BinOp):  # `tuple[..] | None`
                ret = ret.left
            if not (isinstance(ret, ast.Subscript) and getattr(ret.value, "id", "") == "tuple"):
                continue
            elts = ret.slice.elts if isinstance(ret.slice, ast.Tuple) else []
            if len(elts) != 2:
                continue
            names = [getattr(e, "id", None) for e in elts]
            if not all(n in _SCALARS for n in names) or names[0] != names[1]:
                continue
            found.add(f"{f.relative_to(_SRC)}::{node.name}")
    return found


def test_no_new_positional_scalar_pairs():
    new = _same_typed_scalar_pair_returns() - _ALLOWED
    assert not new, (
        "These return two same-typed scalars positionally:\n  "
        + "\n  ".join(sorted(new))
        + "\n\nReturn a NamedTuple with named fields instead. Both values are "
        "the same type, so binding one to the other's name is silent — that is "
        "what put a duration into `fps` and chose a post-fight interview."
    )


def test_the_allowlist_has_not_gone_stale():
    """A fixed entry must leave the list, or the debt count lies."""
    stale = _ALLOWED - _same_typed_scalar_pair_returns()
    assert not stale, (
        "Allowlisted but no longer matching (fixed, renamed, or moved):\n  "
        + "\n  ".join(sorted(stale))
        + "\nRemove them so the remaining count is real."
    )


def test_motion_profile_has_no_fps_attribute():
    """The pin Part 30 §1 asks for, stated as the failure it prevents."""
    from genlab_core.action.window import MotionProfile

    prof = MotionProfile(values=[1.0, 2.0], duration_s=40.658141)
    assert not hasattr(prof, "fps"), "a duration must never be reachable as a rate"
    assert prof.duration_s == pytest.approx(40.658141)
    assert prof._fields == ("values", "duration_s")


@pytest.mark.parametrize(
    ("factory", "fields"),
    [
        ("genlab_core.action.finish:Bracket", ("lo", "hi")),
        ("genlab_core.action.grid:BeatFit", ("score", "phase")),
        ("genlab_core.action.effects.impact:WorldMultipliers", ("world", "subject")),
        ("genlab_core.action.window:MotionProfile", ("values", "duration_s")),
    ],
)
def test_converted_returns_expose_named_fields(factory, fields):
    import importlib

    mod, cls = factory.split(":")
    assert getattr(importlib.import_module(mod), cls)._fields == fields
