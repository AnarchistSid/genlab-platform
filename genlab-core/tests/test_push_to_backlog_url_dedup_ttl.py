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
<=0 preserves the old "block forever" behaviour.

Originally believed (2026-06-23) that anime/movies/ai_creators were exempt
because they use YouTube watch?v= URLs — but the 2026-07-14 video/dedup
audit (commit 8cf31bd6) found the same block-forever pattern latent on all
3 uncovered niches. All 5 niches now set url_dedup_ttl_days=3 as a floor.

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


def test_gaming_niche_has_3day_url_dedup_ttl() -> None:
    """CriticalRush (gaming) must enable a SHORT URL-dedup TTL.

    Gaming's source URLs are immortal per game (Twitch directory pages +
    Steam store pages). Removing this config entirely restores the
    2026-06-23 outage shape — 0 blueprints despite fresh trending
    content.

    2026-06-28 — lowered from 7 → 3 after the structural ceiling became
    visible: PreflightDedup correctly blocks the high-quality candidates
    (Overwatch, LoL etc) for the whole TTL window, but the SURVIVORS
    are often pre-order trailers / discussion content where no real
    gameplay clip exists → clip_sourcer returns no clip → 0 blueprints.
    Shortening the TTL lets sticky-but-actually-playable games
    re-feature every 3 days instead of weekly, trading slight feed
    repetition for steady cadence. This pin guards against silent
    regression to 7d (which would re-create the structural-zero pattern).
    """
    repo_root = Path(__file__).resolve().parent.parent.parent
    cfg = yaml.safe_load((repo_root / "CriticalRush/niches/gaming/config/niche.yaml").read_text())
    assert cfg["pipeline"]["url_dedup_ttl_days"] == 3, (
        "Gaming's url_dedup_ttl_days must be 3 (2026-06-28 trade-off). "
        "Raising back to 7 will re-create the structural-zero pattern. "
        "Removing the key entirely will re-create the 2026-06-23 outage."
    )


def test_sports_niche_has_3day_url_dedup_ttl() -> None:
    """ClutchWire (sports) must enable a SHORT URL-dedup TTL.

    Sports' ScoreBat URLs recur for popular fixtures within 1-2 weeks.
    Removing this config entirely contributes to multi-day publishing
    gaps.

    2026-06-28 — lowered from 7 → 3 days (matches gaming PR #627
    trade-off). Sports' 7-day pattern shows 3 zero-blueprint days out
    of 7, same structural shape as gaming. Shorter TTL lets popular
    fixtures re-feature every 3 days. Pin guards against silent
    regression in either direction:
    - Raising back to 7 reintroduces structural variance
    - Removing entirely reintroduces the 2026-06-23 outage shape
    """
    repo_root = Path(__file__).resolve().parent.parent.parent
    cfg = yaml.safe_load((repo_root / "ClutchWire/config/niche.yaml").read_text())
    assert cfg["pipeline"]["url_dedup_ttl_days"] == 3, (
        "Sports' url_dedup_ttl_days must be 3 (2026-06-28 trade-off, "
        "mirroring gaming PR #627). Raising back to 7 or removing "
        "entirely re-creates known starvation patterns."
    )


def test_all_niches_have_url_dedup_ttl_configured() -> None:
    """Every niche must set url_dedup_ttl_days — block-forever is the
    structurally worse default.

    History — the 2026-06-23 outage first surfaced this on gaming +
    sports (sticky-source URLs recurring in upstream trending).
    Anime/movies/ai_creators were originally believed to be exempt
    because they use naturally-unique YouTube watch?v= URLs — but the
    2026-07-14 video/dedup audit (commit 8cf31bd6) found the same
    block-forever pattern latent on all 3 uncovered niches:

      - BB (ai_creators): AI news cycles fast; the same tool
        announcement legitimately re-blueprints 3 days apart when
        new features drop
      - FD (anime): episode clips are seasonal; beloved moments
        re-blueprint 3 days apart when new remix compilations trend
      - SR (movies): press tours + trailer campaigns cycle 2-3
        unique moments per title over days

    3-day TTL is a floor; each niche can tune independently. Prior
    state (absent → interpreted as block-forever by PushToBacklog)
    was structurally worse than any operator-tuned value.

    Removing the key from ANY niche re-creates the block-forever
    trap that this pin exists to prevent.
    """
    repo_root = Path(__file__).resolve().parent.parent.parent
    niches = (
        ("gaming", "CriticalRush/niches/gaming/config/niche.yaml"),
        ("sports", "ClutchWire/config/niche.yaml"),
        ("anime", "FrameDrift/config/niche.yaml"),
        ("movies", "SpliceReel/config/niche.yaml"),
        ("ai_creators", "BlackboxBrief/config/niche.yaml"),
    )
    for niche_id, cfg_path in niches:
        cfg = yaml.safe_load((repo_root / cfg_path).read_text())
        pipeline = cfg.get("pipeline", {}) or {}
        ttl = pipeline.get("url_dedup_ttl_days")
        assert ttl is not None, (
            f"{cfg_path} ({niche_id}) is missing url_dedup_ttl_days — "
            "PushToBacklog will block URLs forever, re-creating the "
            "2026-06-23 outage class. Set a positive integer (3 is the "
            "current floor; each niche can tune independently)."
        )
        assert isinstance(ttl, int) and ttl > 0, (
            f"{cfg_path} url_dedup_ttl_days must be a positive integer, got {ttl!r}"
        )
