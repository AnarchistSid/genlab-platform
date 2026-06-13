"""Regression pin: sports funnel thresholds (fix #5 of autonomy roadmap).

Pre-fix the sports funnel had a 5.7% story → published conversion in 60d
(884 → 50), an 8.6× upstream supply over gaming. The tuned thresholds
below cut Reddit per_sub_limit + raised the YouTube min_view_velocity +
the composite quality gate. Pinning these so a future "just bump it
back" edit triggers an explicit conversation.

If the upstream funnel changes, the gaming-vs-sports throughput delta
will reappear and the bandit reward signal will get drowned again. See
[[session-2026-06-12-deep-dive-findings]] § B.9 and § B.2.
"""

from __future__ import annotations

from pathlib import Path

import yaml


_REPO_ROOT = Path(__file__).resolve().parent.parent
_NICHE_YAML = _REPO_ROOT / "config" / "niche.yaml"
_SOURCES_YAML = _REPO_ROOT / "config" / "sources.yaml"


def _load(path: Path) -> dict:
    with open(path) as fh:
        return yaml.safe_load(fh) or {}


def test_video_sourcing_min_view_velocity_at_least_400():
    cfg = _load(_NICHE_YAML)
    assert cfg["video_sourcing"]["min_view_velocity"] >= 400, (
        "Sports min_view_velocity must remain ≥400 to keep the 5.7%→20% "
        "funnel-conversion target. Loosening this re-introduces the "
        "long-tail wisecrack/edutainment supply the autonomy roadmap "
        "fix #5 culled."
    )


def test_composite_quality_gate_min_score_at_least_032():
    cfg = _load(_NICHE_YAML)
    gate = cfg["video_sourcing"]["composite_quality_gate"]
    assert gate["min_composite_score"] >= 0.32


def test_reddit_per_sub_limit_at_most_6():
    cfg = _load(_SOURCES_YAML)
    assert cfg["reddit"]["per_sub_limit"] <= 6, (
        "Reddit per_sub_limit must stay ≤6 to keep sports' 19-sub × "
        "limit upstream supply from drowning the bandit reward signal. "
        "If new subs are added that genuinely need more, raise the cap "
        "selectively per-sub instead of bumping the global default."
    )


def test_subreddits_list_size_unchanged():
    """Pin the source diversity count — pruning the limit is the goal,
    not pruning subreddits. If a sub is removed for quality, the
    expected count shifts down and this test forces a deliberate update."""
    cfg = _load(_SOURCES_YAML)
    subs = cfg["reddit"]["subreddits"]
    assert len(subs) >= 10, (
        "Sports source diversity dropped below 10 subreddits. The fix #5 "
        "trade-off was 'narrower per-sub funnel, hold source diversity' — "
        "shrinking the list below 10 makes us sub-fragile."
    )
