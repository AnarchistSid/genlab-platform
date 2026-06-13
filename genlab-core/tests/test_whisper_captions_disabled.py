"""Regression pin: whisper_sync.enabled stays false across all 5 niches.

History — disabled 2026-06-13 to stop the captions-overlay disaster
(huge fontsize=160 text smashing the actual video content because the
text_optimizer module was silently dropped in the 2026-04-04 monorepo
conversion). The underlying bug is now FIXED in the code (RENDER #1):
  - calculate_optimal_font_size inlined into word_animator.py
  - text_type="caption" passed at render_whisper_captions.py:226
  - 22 regression tests in test_word_animator_fits_canvas.py guarantee
    rendered_bottom ≤ 1920 for every text_type + length combination

So flipping these flags back to true would now produce small bottom-strip
captions that don't overflow. The reason they stay disabled is the
owner's stated preference 2026-06-13: "remove the text over the video".
The text WAS removed by setting enabled=false. Captions can be re-enabled
at any time without a code change — flip the flag in each visuals.yaml.

This test pins:
  1. The current owner preference (off by default)
  2. The off-state matches across all 5 niches (no drift)
  3. A future re-enable is a single-line config change per niche

See [[session-2026-06-13-render-audit-findings]] for the audit history.
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
    whisper = cfg.get("animation", {}).get("word_by_word", {}).get("whisper_sync", {})
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
