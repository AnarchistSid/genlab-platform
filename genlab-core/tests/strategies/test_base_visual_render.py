"""R-70 part 2 — PR 1 unit tests for ``BaseVisualRenderStrategy``.

The base class is a thin skeleton — one concrete method
(``_get_whisper_config``) plus abstract hooks for everything else.
These tests pin:

  1. A subclass that implements every abstract method instantiates
     cleanly — the abstract contract is what the design plan says
     it is.
  2. ``_get_whisper_config`` reads the right path through
     ``_visuals_config`` (``animation.word_by_word.whisper_sync``)
     and returns the documented default when any key is missing.
  3. ``_get_whisper_config`` calls ``_ensure_config`` exactly once
     per invocation — the subclass's own config-loading caching is
     what makes the call idempotent; the base doesn't second-guess.

No channel migration runs against this base yet (per the sequenced
design); SR/CW/FD continue to use their existing per-channel
implementations. PR 2 migrates SR as the pilot.
"""

from __future__ import annotations

from typing import Any

import pytest
from genlab_core.strategies.base_visual_render import BaseVisualRenderStrategy


class _StubVisualRender(BaseVisualRenderStrategy):
    """Concrete subclass with every abstract method stubbed for tests.

    Mirrors the shape a real channel subclass will take in PR 2:
    each channel's ``_ensure_config`` reads its own niche YAML and
    populates ``self._visuals_config``."""

    def __init__(self, visuals_config: dict | None = None) -> None:
        super().__init__()
        self._injected_config = visuals_config or {}
        self.ensure_call_count = 0

    def _ensure_config(self) -> None:
        self.ensure_call_count += 1
        self._visuals_config = self._injected_config

    def prepare_whisper_words(self, clip_path, story):
        return []

    def _compose_frame(self, clip_path, story, context):
        return "stub.mp4"

    def _build_pexels_queries(self, story):
        return []

    def _render_story(self, story):
        return {"status": "stub"}

    def execute(self, context):
        return context


def test_r70_pr1_subclass_with_full_abstract_impl_instantiates() -> None:
    """The abstract contract is what the design plan documents:
    one ``_ensure_config`` + five render methods + ``execute``.
    A subclass that provides all 7 instantiates cleanly."""
    stage = _StubVisualRender()
    assert stage._visuals_config is None  # set by _ensure_config on first use


def test_r70_pr1_cannot_instantiate_base_directly() -> None:
    """The base is abstract — direct instantiation must fail. Pin
    that so a future "let's make this a concrete fallback" edit
    can't silently weaken the contract."""
    with pytest.raises(TypeError, match="abstract"):
        BaseVisualRenderStrategy()  # type: ignore[abstract]


def test_r70_pr1_get_whisper_config_reads_documented_path() -> None:
    """Path: ``animation.word_by_word.whisper_sync`` — the documented
    shape of the three channels' ``visuals.yaml`` files."""
    cfg = {
        "animation": {
            "word_by_word": {
                "whisper_sync": {"enabled": True, "model": "small"},
            },
        },
    }
    stage = _StubVisualRender(visuals_config=cfg)
    assert stage._get_whisper_config() == {"enabled": True, "model": "small"}


def test_r70_pr1_get_whisper_config_returns_documented_default_when_missing() -> None:
    """When ANY key on the path is missing, return ``{"enabled":
    False}`` — the documented default that all three production
    channels relied on."""
    # Missing 'animation' key
    assert _StubVisualRender(visuals_config={})._get_whisper_config() == {"enabled": False}
    # Missing 'word_by_word' key
    assert _StubVisualRender(visuals_config={"animation": {}})._get_whisper_config() == {
        "enabled": False
    }
    # Missing 'whisper_sync' key
    assert _StubVisualRender(
        visuals_config={"animation": {"word_by_word": {}}}
    )._get_whisper_config() == {"enabled": False}


def test_r70_pr1_get_whisper_config_calls_ensure_config_once_per_invocation() -> None:
    """The base's ``_get_whisper_config`` calls ``_ensure_config()``
    at the top of every invocation — the subclass's own caching is
    what makes that cheap, not base-level memoization. Pin that
    contract so a 'helpful' future edit doesn't silently break
    subclasses that rely on _ensure_config being called per-use
    (e.g., for hot config reload)."""
    stage = _StubVisualRender(visuals_config={"animation": {"word_by_word": {}}})
    stage._get_whisper_config()
    stage._get_whisper_config()
    stage._get_whisper_config()
    assert stage.ensure_call_count == 3


def test_r70_pr1_get_whisper_config_tolerates_none_visuals_config() -> None:
    """If a buggy subclass forgets to set ``_visuals_config`` inside
    ``_ensure_config``, the base must still return the documented
    default rather than ``AttributeError`` on a None ``.get()``."""

    class _BrokenStub(_StubVisualRender):
        def _ensure_config(self) -> None:
            # Intentionally doesn't set _visuals_config.
            self.ensure_call_count += 1

    assert _BrokenStub()._get_whisper_config() == {"enabled": False}


def test_r70_pr1_base_subclasses_visual_render_strategy() -> None:
    """The base must inherit the existing
    ``VisualRenderStrategy(ABC)`` from interfaces.py, NOT introduce
    a parallel class hierarchy. Pin that so any subclass of the
    base also passes ``isinstance(x, VisualRenderStrategy)``
    checks that callers rely on."""
    from genlab_core.strategies.interfaces import VisualRenderStrategy

    stage: Any = _StubVisualRender()
    assert isinstance(stage, VisualRenderStrategy)
