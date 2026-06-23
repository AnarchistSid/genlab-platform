"""Regression pin: per-niche url_dedup_ttl_days config wiring.

Live-diagnosed root cause 2026-06-23: gaming + sports were producing 0
blueprints because their ``video_url`` field stores sticky source URLs
(Twitch directory pages, Steam store pages, ScoreBat match URLs) that
recur in upstream trending data. Without a TTL on the dedup set, once
a URL publishes once it blocks every subsequent fetch forever.

Symptom logs (gaming run 2026-06-23 13:21 UTC):
    [PUSH] URL dedup: skipping 'Grand Theft Auto V'   (URL already in active set)
    [PUSH] URL dedup: skipping 'Overwatch'             (URL already in active set)
    [PUSH] URL dedup: skipping 'MECCHA CHAMELEON'      (URL already in active set)
    [PUSH] URL dedup: skipping 'League of Legends'    (URL already in active set)
    [PUSH] 0 stories, 0 blueprints pushed to backlog

Fix: opt-in ``pipeline.url_dedup_ttl_days`` config knob. When set, blueprints
older than the TTL stop contributing to the URL dedup set. None / missing /
<=0 preserves the old "block forever" behaviour. Anime/movies/ai_creators
use video-level URLs (unique per item) and leave it unset.

If these tests fail the config wire is broken — the regression would be
invisible because both "TTL active" and "TTL silently ignored" produce
similar-looking logs until the next sticky-URL trending day.
"""

from __future__ import annotations

from pathlib import Path

import yaml


def test_push_to_backlog_reads_url_dedup_ttl_days_from_niche_config() -> None:
    """The TTL config key path must remain reachable from niche_config."""
    import genlab_core.pipeline.stages.push_to_backlog as ptb_mod

    src = Path(ptb_mod.__file__).read_text()
    assert 'context.get("niche_config", {})' in src
    assert '.get("pipeline", {})' in src
    assert '.get("url_dedup_ttl_days")' in src


def test_push_to_backlog_filters_active_bps_through_ttl_helper() -> None:
    """The active_bps comprehension must apply the TTL helper.

    Pin the literal that combines both predicates — a refactor that
    accidentally drops one half (e.g. keeps _is_blocking, removes
    _is_within_url_ttl) would silently restore the old behaviour for
    gaming/sports.
    """
    import genlab_core.pipeline.stages.push_to_backlog as ptb_mod

    src = Path(ptb_mod.__file__).read_text()
    assert "_is_blocking(bp) and _is_within_url_ttl(bp)" in src, (
        "active_bps must filter via BOTH _is_blocking and _is_within_url_ttl"
    )


def test_gaming_niche_has_7day_url_dedup_ttl() -> None:
    """CriticalRush (gaming) must enable the 7-day TTL.

    Gaming's source URLs are immortal per game (Twitch directory pages +
    Steam store pages). Removing this config restores the 2026-06-23
    outage shape — 0 blueprints despite fresh trending content.
    """
    repo_root = Path(__file__).resolve().parent.parent.parent
    cfg = yaml.safe_load((repo_root / "CriticalRush/niches/gaming/config/niche.yaml").read_text())
    assert cfg["pipeline"]["url_dedup_ttl_days"] == 7


def test_sports_niche_has_7day_url_dedup_ttl() -> None:
    """ClutchWire (sports) must enable the 7-day TTL.

    Sports' ScoreBat URLs recur for popular fixtures within 1-2 weeks.
    Removing this config contributes to multi-day publishing gaps.
    """
    repo_root = Path(__file__).resolve().parent.parent.parent
    cfg = yaml.safe_load((repo_root / "ClutchWire/config/niche.yaml").read_text())
    assert cfg["pipeline"]["url_dedup_ttl_days"] == 7


def test_naturally_unique_url_niches_leave_ttl_unset() -> None:
    """Anime/movies/ai_creators use unique YouTube watch?v= URLs.

    Enabling TTL for them would allow the same evergreen content to
    re-publish, which is the opposite of what their content model
    expects. The TTL is an opt-in fix for sticky-source niches only.
    """
    repo_root = Path(__file__).resolve().parent.parent.parent
    for cfg_path in (
        "FrameDrift/config/niche.yaml",
        "SpliceReel/config/niche.yaml",
        "BlackboxBrief/config/niche.yaml",
    ):
        cfg = yaml.safe_load((repo_root / cfg_path).read_text())
        pipeline = cfg.get("pipeline", {}) or {}
        assert "url_dedup_ttl_days" not in pipeline, (
            f"{cfg_path} should NOT enable url_dedup_ttl_days — it uses "
            "naturally-unique video-level URLs and doesn't have the "
            "sticky-source problem gaming/sports do."
        )
