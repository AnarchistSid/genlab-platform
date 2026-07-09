"""Cross-niche regression pin for HighlightMoment.window_seconds
minimum (task #617, 2026-07-09).

The post_render_transform module (see genlab-core/src/genlab_core/media/
post_render_transform.py) enforces a 15s min-duration guard on
transformed output. If the pipeline of (highlight_moment trim → intro
concat → outro concat) produces a composite below 15s, the orchestrator
falls back to the untransformed base composite AND returns empty
``arm_ids_by_dimension``. That empty dict prevents the transformation
bandit arms (255 total, 5 niches × ~50 dims) from accumulating any
observations.

Root cause of the 2026-07-06→07-09 attribution regression: all 4
enabled niches shipped with ``window_seconds: 8`` (matching the
Pydantic default in HighlightMomentConfig). With intro (~3s) + outro
(~3s) added to the 8s highlight trim, composites landed at ~14s —
just under the 15s guard. Every render on prod hit path 258 of
post_render_transform.py and reward attribution stopped fully.

This pin asserts each enabled niche's ``window_seconds`` is high
enough that even a naive intro+outro overhead of ~3s each leaves ≥15s
composite (i.e. ``window_seconds >= 12``). If a future refactor
accidentally reverts to 8 (via ``git checkout`` of an old branch or
similar), this pin fires immediately.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

# Only niches with intelligent_transform.enabled = true are subject to
# this constraint. FrameDrift (anime) has enabled=false intentionally,
# so its window_seconds value doesn't affect production behaviour and
# is exempt.
ENABLED_NICHE_CONFIGS = {
    "ai_creators": REPO_ROOT / "BlackboxBrief/config/visuals.yaml",
    "gaming": REPO_ROOT / "CriticalRush/niches/gaming/config/visuals.yaml",
    "sports": REPO_ROOT / "ClutchWire/config/visuals.yaml",
    "movies": REPO_ROOT / "SpliceReel/config/visuals.yaml",
}

# Minimum viable value. Below this, the min-duration guard (SPEC = 15s)
# will fire on typical source clips + typical intro/outro overhead
# (~3s each), disabling arm attribution across the board.
#
# Bumping ABOVE 12 is safe; lowering below 12 re-opens the 2026-07-09
# regression. The pin uses ``>=`` so future increases (13, 15, etc.)
# don't accidentally fail the test.
MIN_WINDOW_SECONDS = 12


@pytest.mark.parametrize("niche_id,config_path", ENABLED_NICHE_CONFIGS.items())
def test_highlight_window_seconds_meets_min_duration_floor(niche_id, config_path):
    """Every enabled niche's ``highlight_moment.window_seconds`` MUST
    be >= 12 so the transformed composite doesn't fall below the 15s
    post_render_transform min-duration guard after intro+outro concat.

    See ``genlab-core/src/genlab_core/media/post_render_transform.py:241``
    for the guard, and ``:216-224`` for the diagnostic comment that
    predicted this exact regression on 2026-07-07 (before it was
    understood as a config-side fix rather than a code-side one)."""
    assert config_path.is_file(), (
        f"visuals.yaml not found for {niche_id!r} at {config_path}. "
        "Test needs updating if the niche was moved."
    )
    cfg = yaml.safe_load(config_path.read_text()) or {}
    it_block = cfg.get("intelligent_transform") or {}

    # Skip check if this niche has disabled intelligent_transform —
    # window_seconds is irrelevant then.
    if not it_block.get("enabled", False):
        pytest.skip(
            f"{niche_id!r} has intelligent_transform.enabled=false — "
            "window_seconds is not exercised in production"
        )

    dims = it_block.get("dimensions") or {}
    hm = dims.get("highlight_moment") or {}
    window = hm.get("window_seconds")
    assert window is not None, (
        f"{niche_id!r}'s visuals.yaml is missing "
        "intelligent_transform.dimensions.highlight_moment.window_seconds. "
        "The Pydantic default is 8 which would fail this pin — set an "
        "explicit value >= 12."
    )
    assert window >= MIN_WINDOW_SECONDS, (
        f"{niche_id!r}'s window_seconds={window} is below the "
        f"{MIN_WINDOW_SECONDS}s floor. This will cause "
        "post_render_transform.py:241 to reject the transformed "
        "composite as too-short (< 15s), fall back to base composite, "
        "and return empty arm_ids_by_dimension — silently disabling "
        "attribution for all reels in this niche. See task #617 for "
        "the full diagnostic chain."
    )


def test_all_4_enabled_niches_in_scope():
    """Regression pin: if a new enabled niche is added, this test
    file must be updated so its window_seconds is also policed.
    Anime (FrameDrift) intentionally not in scope because its config
    has enabled=false."""
    assert set(ENABLED_NICHE_CONFIGS.keys()) == {
        "ai_creators",
        "gaming",
        "sports",
        "movies",
    }
