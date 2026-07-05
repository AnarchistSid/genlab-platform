"""Pins for ``scripts/generate_placeholder_motion.py``.

The script generates 32 placeholder MP4s (3 intros + 5 outros × 4
niches) that plug into the ``motion_graphics`` bandit dimension. If
these pins regress:

* Dropping a niche from NICHES silently starves that niche's motion
  bandit — it gets 0 arms until Canva assets ship.
* Dropping an intro/outro template drops one arm from the bandit's
  choice space — a specific style stops being explorable.
* Mis-shaping the FFmpeg command breaks encode compliance (bt709
  colorspace, yuv420p, AAC 48kHz stereo — MotionGraphicsCompositor
  and CLAUDE.md's render rules require these).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "generate_placeholder_motion.py"
)


@pytest.fixture(scope="module")
def script_module():
    spec = importlib.util.spec_from_file_location(
        "generate_placeholder_motion", SCRIPT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ── niche + template inventory pins ─────────────────────────────────


def test_all_4_activated_niches_present(script_module):
    """FrameDrift/anime deliberately absent — motion_graphics is off
    for that niche as of 2026-07-05. All 4 activated niches must
    have generator entries."""
    assert set(script_module.NICHES) == {
        "ai_creators",
        "gaming",
        "sports",
        "movies",
    }


def test_intro_templates_match_visualsyaml_arm_names(script_module):
    """Names must match ``visuals.yaml.intelligent_transform.dimensions
    .intro_animation.templates`` — otherwise the bandit picks an arm
    the compositor can't resolve, and motion_graphics silently
    fail-opens."""
    assert set(script_module.INTRO_TEMPLATES) == {
        "logo_zoom",
        "logo_tagline_reveal",
        "pattern_break_intro",
    }


def test_outro_templates_match_visualsyaml_arm_names(script_module):
    """Names must match ``outro_cta.styles`` in every activated niche's
    visuals.yaml. Same silent-fail risk as intros."""
    assert set(script_module.OUTRO_TEMPLATES) == {
        "follow",
        "comment",
        "save",
        "share",
        "question",
    }


# ── FFmpeg command shape pins ───────────────────────────────────────


def test_intro_cmd_has_bt709_flags(script_module, tmp_path):
    """CLAUDE.md render rule: bt709 colorspace is non-negotiable."""
    cmd = script_module.build_intro_cmd(
        tmp_path / "x.mp4",
        Path("fakelogo.png"),
        "0x00D4FF",
        0.6,
        "Test",
        "logo_zoom",
    )
    assert "-colorspace" in cmd
    idx = cmd.index("-colorspace")
    assert cmd[idx + 1] == "bt709"


def test_outro_cmd_has_bt709_flags(script_module, tmp_path):
    """Same bt709 pin, but for outros."""
    cmd = script_module.build_outro_cmd(
        tmp_path / "x.mp4",
        Path("fakelogo.png"),
        "0x00D4FF",
        "FOLLOW",
    )
    assert "-color_primaries" in cmd
    idx = cmd.index("-color_primaries")
    assert cmd[idx + 1] == "bt709"


def test_intro_cmd_has_silent_audio_source(script_module, tmp_path):
    """motion_compositor.py:23 requires each concat input to carry
    audio — even silent. Missing anullsrc = concat filter aborts."""
    cmd = script_module.build_intro_cmd(
        tmp_path / "x.mp4",
        Path("fakelogo.png"),
        "0x00D4FF",
        0.6,
        "Test",
        "logo_zoom",
    )
    argv = " ".join(cmd)
    assert "anullsrc" in argv
    assert "48000" in argv
    assert "cl=stereo" in argv


def test_intro_cmd_yuv420p(script_module, tmp_path):
    """yuv420p is CLAUDE.md-required for platform compat."""
    cmd = script_module.build_intro_cmd(
        tmp_path / "x.mp4",
        Path("fakelogo.png"),
        "0x00D4FF",
        0.6,
        "Test",
        "logo_zoom",
    )
    argv = " ".join(cmd)
    # yuv420p ships via -pix_fmt OR the format= filter — both count
    assert "format=yuv420p" in argv or "yuv420p" in cmd


# ── output path resolution pins ─────────────────────────────────────


def test_repo_mode_writes_under_assets_motion_placeholders(
    script_module, tmp_path
):
    """repo mode writes under a mirror path so PR reviewers can inspect
    generated MP4s in-tree."""
    intros, outros = script_module._out_paths(
        "repo", tmp_path, Path("/opt/genlab"), "gaming", "CriticalRush/niches/gaming"
    )
    assert intros == tmp_path / "assets" / "motion_placeholders" / "gaming" / "intros"
    assert outros == tmp_path / "assets" / "motion_placeholders" / "gaming" / "outros"


def test_prod_mode_writes_under_channel_dir(script_module, tmp_path):
    """prod mode writes the paths MotionGraphicsCompositor reads from.
    The channel_dir mapping mirrors NICHES[niche][0]."""
    intros, outros = script_module._out_paths(
        "prod", tmp_path, Path("/opt/genlab"), "gaming", "CriticalRush/niches/gaming"
    )
    assert intros == Path("/opt/genlab/CriticalRush/niches/gaming/assets/motion/intros")
    assert outros == Path("/opt/genlab/CriticalRush/niches/gaming/assets/motion/outros")


# ── committed baseline assets pin ───────────────────────────────────


def test_repo_ships_baseline_32_mp4s():
    """The generated MP4s ship in-repo so reviewers can inspect them
    and so a fresh clone doesn't need to run FFmpeg before deploy.
    3 intros + 5 outros × 4 niches = 32."""
    repo_root = Path(__file__).resolve().parents[1]
    root = repo_root / "assets" / "motion_placeholders"
    if not root.is_dir():
        pytest.skip("assets/motion_placeholders/ not present — run "
                    "scripts/generate_placeholder_motion.py")
    mp4s = list(root.rglob("*.mp4"))
    assert len(mp4s) == 32, f"expected 32 MP4s under {root}, got {len(mp4s)}"
