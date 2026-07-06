"""Pins the post-render transformation wire (task #466 wire, 2026-07-06).

The intelligent-transformation orchestrator shipped in the 16-PR sprint
2026-07-05 and was flag-activated on prod that night, but no niche's
VisualRenderStrategy actually CALLED it. Every prod render from
2026-07-05 through the morning of 2026-07-06 was base-composite-only —
no music mood, no highlight trim, no motion intros/outros, no animated
captions. All 11 bandit dimensions dormant.

This module is the wire. These pins:

1. Verify apply_post_render_transformations honors the env kill-switch
   and per-niche config flag (fail-open contract).
2. Source-grep verify each activated niche's visual_render.py actually
   calls the wire. Regression = one of the 4 niches goes back to
   base-composite-only silently.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from genlab_core.media.post_render_transform import (
    apply_post_render_transformations,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


# ── apply_post_render_transformations contract pins ──────────────────


def test_flag_off_returns_input_path(monkeypatch, tmp_path: Path):
    """Env kill-switch: GENLAB_INTELLIGENT_TRANSFORM_ENABLED != "1"
    means the whole pipeline is a no-op."""
    monkeypatch.delenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", raising=False)

    input_path = str(tmp_path / "composite.mp4")
    (tmp_path / "composite.mp4").write_bytes(b"x")

    result = apply_post_render_transformations(
        input_path,
        niche_id="ai_creators",
        niche_root=tmp_path,
        visuals_yaml_path=str(tmp_path / "visuals.yaml"),
    )
    assert result == input_path


def test_visuals_yaml_missing_returns_input_path(
    monkeypatch, tmp_path: Path,
):
    """Config file missing → log warning + return input. Base render
    must not be lost because the config file moved."""
    monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
    input_path = str(tmp_path / "composite.mp4")
    (tmp_path / "composite.mp4").write_bytes(b"x")

    result = apply_post_render_transformations(
        input_path,
        niche_id="ai_creators",
        niche_root=tmp_path,
        visuals_yaml_path=str(tmp_path / "does_not_exist.yaml"),
    )
    assert result == input_path


def test_config_disabled_returns_input_path(monkeypatch, tmp_path: Path):
    """visuals.yaml.intelligent_transform.enabled: false → no-op."""
    monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")

    yaml_path = tmp_path / "visuals.yaml"
    yaml_path.write_text(
        "intelligent_transform:\n  enabled: false\n"
    )
    input_path = str(tmp_path / "composite.mp4")
    (tmp_path / "composite.mp4").write_bytes(b"x")

    result = apply_post_render_transformations(
        input_path,
        niche_id="anime",
        niche_root=tmp_path,
        visuals_yaml_path=str(yaml_path),
    )
    assert result == input_path


def test_orchestrator_raise_returns_input_path(
    monkeypatch, tmp_path: Path,
):
    """apply_transformations promises no-raise, but if some import path
    breaks the wire must still return the base composite."""
    monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")

    yaml_path = tmp_path / "visuals.yaml"
    yaml_path.write_text("intelligent_transform:\n  enabled: true\n")
    input_path = str(tmp_path / "composite.mp4")
    (tmp_path / "composite.mp4").write_bytes(b"x")

    with patch(
        "genlab_core.media.transformation_orchestrator.apply_transformations",
        side_effect=RuntimeError("boom"),
    ):
        result = apply_post_render_transformations(
            input_path,
            niche_id="ai_creators",
            niche_root=tmp_path,
            visuals_yaml_path=str(yaml_path),
        )
    assert result == input_path


# ── source-grep pins: every niche actually calls the wire ────────────


@pytest.mark.parametrize(
    "path,niche",
    [
        (
            REPO_ROOT / "BlackboxBrief" / "bb_strategies" / "visual_render.py",
            "ai_creators",
        ),
        (
            REPO_ROOT
            / "CriticalRush"
            / "niches" / "gaming" / "stages" / "render_gaming_video.py",
            "gaming",
        ),
        (
            REPO_ROOT
            / "genlab-core" / "src" / "genlab_core" / "strategies"
            / "base_visual_render.py",
            "base (sports + movies + anime)",
        ),
    ],
)
def test_visual_render_calls_apply_post_render_transformations(path, niche):
    """Regression pin: if this file stops calling the wire, that
    niche's renders silently regress to base-composite-only."""
    if not path.is_file():
        pytest.skip(f"{path} not present in this checkout")
    src = path.read_text()
    assert "apply_post_render_transformations(" in src, (
        f"{path} no longer calls apply_post_render_transformations — "
        f"{niche} renders will be base composite only."
    )
