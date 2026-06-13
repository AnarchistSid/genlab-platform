"""Regression pin: whisper_sync.enabled MUST stay false across all 5 niches.

Closes the captions-overlay disaster surfaced 2026-06-13. The
text_optimizer module that did adaptive font sizing was silently dropped
during the 2026-04-04 monorepo conversion. Without it, WordByWordAnimator
falls back to fontsize=160 for every caption, which overflows the 1920px
canvas (rendered_bottom = 1974px for a typical 60-char caption) and
smashes the hook + video content rendered by FrameCompositor.

This test guarantees no one flips whisper_sync.enabled back to true
without:
  1. Restoring (or inlining) `calculate_optimal_font_size`
  2. Fixing the misnamed `text_type="hook"` at render_whisper_captions.py:226
     to `"caption"` (42px fixed) or `"body"` (56-72px adaptive)
  3. Adding a regression pin that asserts
     rendered_bottom <= canvas_height for a long caption
  4. Capping the FFmpeg subprocess timeout to scale with source duration
     (current 120s hardcoded → corrupted _captioned.mp4 with no moov atom
     for sources >40s on the 4GB VPS)

See [[session-2026-06-13-render-audit-findings]] for the full audit.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_NICHE_VISUALS_YAMLS = {
    "ai_creators": _REPO_ROOT / "BlackboxBrief/config/visuals.yaml",
    "gaming": _REPO_ROOT / "CriticalRush/niches/gaming/config/visuals.yaml",
    "sports": _REPO_ROOT / "ClutchWire/config/visuals.yaml",
    "movies": _REPO_ROOT / "SpliceReel/config/visuals.yaml",
    "anime": _REPO_ROOT / "FrameDrift/config/visuals.yaml",
}


@pytest.mark.parametrize("niche,path", list(_NICHE_VISUALS_YAMLS.items()))
def test_whisper_sync_disabled(niche, path):
    """whisper_sync.enabled MUST be false until the optimizer regression is fixed."""
    assert path.exists(), f"visuals.yaml missing for {niche}: {path}"
    cfg = yaml.safe_load(path.read_text()) or {}
    whisper = (
        cfg.get("animation", {}).get("word_by_word", {}).get("whisper_sync", {})
    )
    assert whisper.get("enabled") is False, (
        f"{niche} visuals.yaml has whisper_sync.enabled={whisper.get('enabled')!r}. "
        "Setting it back to true re-introduces the captions-overflow disaster. "
        "See test docstring for the prerequisites to flip this back on safely."
    )


def test_all_five_niches_covered():
    """Sanity: the parametrize list must cover exactly the 5 production niches.
    A new niche should add itself to _NICHE_VISUALS_YAMLS as part of the
    create_niche tool flow (see genlab_core.tools.create_niche)."""
    assert set(_NICHE_VISUALS_YAMLS.keys()) == {
        "ai_creators",
        "gaming",
        "sports",
        "movies",
        "anime",
    }
