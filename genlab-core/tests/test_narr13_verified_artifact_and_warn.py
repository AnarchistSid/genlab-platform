"""NARR-13 — reported success must equal a verified artifact, and the
missing-VO warn must not be disabled by the fault it detects.

Both defects shipped a reel with no voice-over while every log said success:
``GenerateAudio`` logged "1 generated, 0 skipped, 0 errors", and the WARN that
exists to catch a missing VO at mix time was gated on ``narration_expected`` —
a flag stamped by the same upstream handoff whose failure the WARN polices.
When that propagation broke, the flag was absent, so the alarm for the failure
was disabled BY the failure.
"""
import inspect

from genlab_core.media import transformation_orchestrator as TO
from genlab_core.pipeline.stages import generate_audio as GA
from genlab_core.strategies import base_visual_render as BVR


class _Ctx(dict):
    pass


def _warn_fires(ctx: dict, monkeypatch) -> bool:
    """Did _warn_narration_absent emit, given this ctx?"""
    seen = []
    monkeypatch.setattr(
        TO.logger, "warning", lambda *a, **k: seen.append(a), raising=False
    )
    TO._warn_narration_absent(ctx, "ai_creators", "vo_path_absent", "detail")
    return bool(seen)


def test_warn_fires_on_script_without_expected_flag(monkeypatch):
    """THE regression. A written script with no VO is an anomaly even when
    narration_expected never propagated — which is the exact broken case."""
    assert _warn_fires({"narration_script": "a real narration script"}, monkeypatch)


def test_warn_still_fires_on_expected_flag(monkeypatch):
    """The original signal keeps working."""
    assert _warn_fires({"narration_expected": True}, monkeypatch)


def test_warn_silent_for_non_canary_niche(monkeypatch):
    """No script, no flag -> the four non-canary niches stay quiet. A warn that
    fires on every niche gets muted, which is how the next one hides."""
    assert not _warn_fires({}, monkeypatch)
    assert not _warn_fires({"narration_script": "   "}, monkeypatch)


def test_warn_never_raises(monkeypatch):
    """An observability helper that can break a render is worse than the gap."""
    TO._warn_narration_absent(None, "ai_creators", "r", "d")  # type: ignore[arg-type]


def test_blueprint_context_carries_narration_script():
    """The warn's independent signal must actually reach the orchestrator.
    Without this the guard is dead code -- the failure being fixed.

    Asserts against the BUILT context rather than the source text: after the
    single-writer extraction there is no inline dict to grep, and a source-text
    assertion would have gone quietly green on a refactor while proving nothing.
    """
    from genlab_core.strategies.blueprint_context import build_blueprint_context

    ctx = build_blueprint_context(
        {"content": {"narration_script": "a real script"}}, hook="h"
    )
    assert ctx["narration_script"] == "a real script"


def test_generate_audio_verifies_published_artifact():
    """Success is claimed only after checking the file the consumer reads."""
    src = inspect.getsource(GA)
    tail = src.split('media["audio_path"] = str(out_path)')[1]
    guard = tail.split("generated += 1")[0]
    assert "st_size" in guard, "size check missing before the success claim"
    assert "exists()" in guard, "existence check missing before the success claim"
    assert "skipped += 1" in guard, "unverified artifact must not count as generated"
