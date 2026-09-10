"""Contract: every render strategy's blueprint_context carries every key the
transformation orchestrator reads.

This is the pin for the defect that cost the NARR canary its entire life. The
context dict is a contract between the render strategies and
``transformation_orchestrator``, enforced by nothing — so it drifted. The base
class carried twelve keys; BlackboxBrief and gaming each assembled their own
four-key dict inline with no narration fields. The orchestrator read
``narration_audio_path`` from a dict that could not contain it, found nothing,
and fell through to the two-input mix, silently, for months.

Two independent assertions, because either alone is escapable:

  1. Every consumer key the orchestrator reads is produced by the builder.
     Derived by SCANNING the orchestrator source, so a new ``ctx.get(...)``
     there fails this test until a producer supplies it.
  2. No strategy assembles a context dict of its own. A niche that reintroduces
     an inline dict fails even if it happens to include the right keys today.
"""
import re
from pathlib import Path

import pytest

from genlab_core.strategies.blueprint_context import (
    ORCHESTRATOR_KEYS,
    build_blueprint_context,
)

REPO = Path(__file__).resolve().parents[2]

STRATEGIES = [
    "BlackboxBrief/bb_strategies/visual_render.py",
    "ClutchWire/cw_strategies/visual_render.py",
    "SpliceReel/sr_strategies/visual_render.py",
    "FrameDrift/fd_strategies/visual_render.py",
    "CriticalRush/niches/gaming/stages/render_gaming_video.py",
]


def _story():
    return {
        "title": "T", "summary": "S", "variant_type": "single_clip",
        "content": {
            "caption_segments": ["a"], "narration_expected": True,
            "narration_script": "a script", "narration_degraded": False,
            "narration_degraded_reason": "",
        },
        "media": {"audio_path": "/tmp/vo.mp3"},
    }


def test_builder_covers_every_key_the_orchestrator_reads():
    """Derived from the orchestrator's own source, not a hand-copied list."""
    src = (REPO / "genlab-core/src/genlab_core/media/transformation_orchestrator.py").read_text()
    consumed = set(re.findall(r'ctx\.get\("([a-z_]+)"', src))
    produced = set(build_blueprint_context(_story(), hook="h"))
    missing = consumed - produced
    assert not missing, f"orchestrator reads keys no builder produces: {sorted(missing)}"


def test_declared_keys_match_what_the_orchestrator_actually_reads():
    """ORCHESTRATOR_KEYS must not rot away from the real consumer."""
    src = (REPO / "genlab-core/src/genlab_core/media/transformation_orchestrator.py").read_text()
    consumed = set(re.findall(r'ctx\.get\("([a-z_]+)"', src))
    assert consumed <= set(ORCHESTRATOR_KEYS) | {"variant_type"}, (
        f"undeclared consumer keys: {sorted(consumed - set(ORCHESTRATOR_KEYS))}"
    )


def test_narration_path_survives_the_builder():
    """THE regression: the VO path must reach the context."""
    ctx = build_blueprint_context(_story(), hook="h")
    assert ctx["narration_audio_path"] == "/tmp/vo.mp3"
    assert ctx["narration_script"] == "a script"
    assert ctx["narration_expected"] is True


@pytest.mark.parametrize("rel", STRATEGIES)
def test_no_strategy_assembles_its_own_context(rel):
    """A strategy that rebuilds the dict inline is the drift, whether or not
    its key set is correct today."""
    path = REPO / rel
    if not path.exists():
        pytest.skip(f"{rel} not present")
    src = path.read_text()
    inline = re.search(r"blueprint_context\s*=\s*\{", src)
    assert not inline, (
        f"{rel} assembles blueprint_context inline — route it through "
        f"build_blueprint_context() instead"
    )


@pytest.mark.parametrize("rel", STRATEGIES)
def test_strategies_that_use_context_use_the_builder(rel):
    path = REPO / rel
    if not path.exists():
        pytest.skip(f"{rel} not present")
    src = path.read_text()
    if "blueprint_context" not in src:
        pytest.skip(f"{rel} does not use blueprint_context")
    assert "build_blueprint_context" in src, f"{rel} must use the single writer"


def test_extra_cannot_shadow_an_orchestrator_key():
    """A niche silently overriding narration_* is the failure mode itself."""
    ctx = build_blueprint_context(
        _story(), hook="h",
        extra={"narration_audio_path": None, "niche_specific": 1},
    )
    assert ctx["narration_audio_path"] == "/tmp/vo.mp3"
    assert ctx["niche_specific"] == 1


def test_missing_content_and_media_do_not_raise():
    ctx = build_blueprint_context({"title": "T"}, hook="h")
    assert ctx["narration_audio_path"] is None
    assert ctx["narration_script"] == ""
