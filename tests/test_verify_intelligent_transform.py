"""Pins for ``scripts/verify_intelligent_transform.py``.

This script is the tomorrow-morning readiness check for the intelligent
transformation sprint (2026-07-05). It reads
``pending_feedback.arm_ids_by_dimension`` and reports per-niche
attribution coverage. The pins cover the pure aggregation logic (no DB
mocking required) and the exit-code contract that alerting depends on.

If these pins regress:

* Removing a dimension from ``CORE_DIMENSIONS`` would silently drop
  it from the coverage table — operator wouldn't notice if the
  bandit stopped picking that dimension.
* Changing the exit-code contract would break any systemd wrapper or
  cron watchdog that keys off code 3.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "verify_intelligent_transform.py"


@pytest.fixture(scope="module")
def script_module():
    """Load the CLI script as a module so we can call its functions.

    The script is not on ``sys.path`` — it lives under ``scripts/`` — so
    we import it via ``importlib.util`` the same way the archive-stale
    pins do.
    """
    spec = importlib.util.spec_from_file_location("verify_intelligent_transform", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ── dimension inventory pins ─────────────────────────────────────────


def test_core_dimensions_frozen(script_module):
    """The 9 core dimensions must all be listed — dropping one means
    that dimension silently stops appearing in the coverage table.
    """
    expected = {
        "music_mood",
        "highlight_moment",
        "caption_style",
        "caption_pacing",
        "hook_framing",
        "pan_zoom",
        "audio_ducking",
        "music_visual_sync",
        "caption_emphasis_color",
    }
    assert set(script_module.CORE_DIMENSIONS) == expected


def test_asset_gated_dimensions_frozen(script_module):
    """intro_animation and outro_cta are asset-gated (task #524)
    so they're surfaced in the table but not in the regression gate.
    """
    assert set(script_module.ASSET_GATED_DIMENSIONS) == {
        "intro_animation",
        "outro_cta",
    }


# ── aggregate() pure-function pins ───────────────────────────────────


def test_aggregate_counts_dimensions_per_niche(script_module):
    """Each row's dict keys increment that niche's per-dim counter."""
    rows = [
        {
            "niche_id": "ai_creators",
            "arm_ids_by_dimension": {
                "music_mood": "arm_a",
                "caption_style": "arm_b",
            },
            "created_at": None,
        },
        {
            "niche_id": "ai_creators",
            "arm_ids_by_dimension": {"music_mood": "arm_c"},
            "created_at": None,
        },
        {
            "niche_id": "gaming",
            "arm_ids_by_dimension": {"hook_framing": "arm_d"},
            "created_at": None,
        },
    ]
    agg = script_module.aggregate(rows)
    assert agg["ai_creators"]["_reels"] == 2
    assert agg["ai_creators"]["music_mood"] == 2
    assert agg["ai_creators"]["caption_style"] == 1
    assert agg["gaming"]["_reels"] == 1
    assert agg["gaming"]["hook_framing"] == 1


def test_aggregate_empty_dict_still_counts_reel(script_module):
    """A row with ``arm_ids_by_dimension={}`` (flag off) still counts
    toward the niche's reel count so the regression check ("N reels,
    0 dims applied") can fire.
    """
    rows = [
        {"niche_id": "sports", "arm_ids_by_dimension": {}, "created_at": None},
        {"niche_id": "sports", "arm_ids_by_dimension": {}, "created_at": None},
    ]
    agg = script_module.aggregate(rows)
    assert agg["sports"]["_reels"] == 2
    # And zero for every dimension
    for dim in script_module.CORE_DIMENSIONS:
        assert agg["sports"].get(dim, 0) == 0


def test_aggregate_null_dict_treated_as_empty(script_module):
    """arm_ids_by_dimension defaults to ``{}`` in the migration but a
    NULL slipping through must not crash the aggregator.
    """
    rows = [{"niche_id": "movies", "arm_ids_by_dimension": None, "created_at": None}]
    agg = script_module.aggregate(rows)
    assert agg["movies"]["_reels"] == 1


# ── exit-code contract pins (alerting keys off code 3) ──────────────


def test_print_table_returns_3_when_reels_but_no_dims(script_module, capsys):
    """Regression signal: niche published reels but no dimension
    attributed → exit code 3 → operator alert.
    """
    agg = {"gaming": {"_reels": 3}}
    code = script_module.print_table(agg)
    assert code == 3
    out = capsys.readouterr().out
    assert "regression suspected" in out


def test_print_table_returns_0_when_dims_applied(script_module, capsys):
    """Healthy: at least one core dimension applied per niche → code 0."""
    agg = {
        "ai_creators": {
            "_reels": 4,
            "music_mood": 4,
            "highlight_moment": 4,
            "caption_style": 4,
        }
    }
    code = script_module.print_table(agg)
    assert code == 0


def test_print_table_returns_0_when_no_reels(script_module, capsys):
    """Empty window (no publishes yet) → no regression, code 0."""
    code = script_module.print_table({})
    assert code == 0
    out = capsys.readouterr().out
    assert "no reels published" in out.lower()
