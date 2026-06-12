"""R-70 part 2 — PR 5a unit tests for ``BaseScoringStrategy``.

The base class is a thin skeleton — one concrete method
(``_score_novelty``) plus abstract hooks for everything else.
These tests pin:

1. A subclass that implements every abstract method instantiates
   cleanly — the abstract contract is what this PR's docstring
   says it is.
2. ``BaseScoringStrategy()`` is not directly instantiable — the
   ``ABC`` contract is real, not vestigial.
3. ``_score_novelty`` returns the documented default (0.5) when
   the upstream item has no ``novelty_score`` field, and reads
   the field verbatim when present — the byte-identical body
   surfaced by the body-diff measurement.
4. The base subclasses the existing ``ScoringStrategy(ABC)`` —
   ``isinstance(x, ScoringStrategy)`` keeps working for callers
   that type on the parent interface.

No channel migration runs against this base in PR 5a — SR / CW /
FD continue to use their existing per-channel scoring strategies.
PR 5b adds the SR + FD second-tier base + pilots SR; PR 5c migrates
CW against this narrow base only.
"""

from __future__ import annotations

from typing import Any

import pytest
from genlab_core.strategies.base_scoring import BaseScoringStrategy


class _StubScoring(BaseScoringStrategy):
    """Concrete subclass with every abstract method stubbed for tests.

    Mirrors the shape a real channel subclass will take in PR 5b/5c:
    each channel's ``_ensure_config`` reads its own scoring YAML and
    its own ``score_item`` assembles its own per-niche axes."""

    def __init__(self) -> None:
        self.ensure_call_count = 0
        self.magnitude_calls: list[dict] = []
        self.score_item_calls: list[dict] = []

    def _ensure_config(self) -> None:
        self.ensure_call_count += 1

    def _score_magnitude(self, item: dict) -> float:
        self.magnitude_calls.append(item)
        return 0.42

    def score_item(self, item: dict) -> dict:
        self.score_item_calls.append(item)
        return {**item, "scores": {}, "score": 0.0, "final_score": 0.0}

    def execute(self, context: Any) -> Any:
        return context


def test_r70_pr5a_subclass_with_full_abstract_impl_instantiates() -> None:
    """The abstract contract is what the PR docstring documents:
    ``_ensure_config`` + ``_score_magnitude`` + ``score_item`` +
    ``execute``. A subclass that provides all 4 instantiates cleanly
    — ``_score_novelty`` is inherited concretely from the base."""
    stage = _StubScoring()
    assert isinstance(stage, BaseScoringStrategy)


def test_r70_pr5a_cannot_instantiate_base_directly() -> None:
    """The base is abstract — direct instantiation must fail. Pin
    that so a future "let's make this a concrete fallback" edit
    can't silently weaken the contract."""
    with pytest.raises(TypeError, match="abstract"):
        BaseScoringStrategy()  # type: ignore[abstract]


def test_r70_pr5a_score_novelty_reads_field_verbatim() -> None:
    """The byte-identical body across SR/CW/FD reads
    ``item["novelty_score"]`` verbatim. Pin so a future
    "let's clamp at 1.0" or "let's apply decay" edit on the base
    can't silently change channel behavior."""
    stage = _StubScoring()
    assert stage._score_novelty({"novelty_score": 0.0}) == 0.0
    assert stage._score_novelty({"novelty_score": 0.73}) == 0.73
    assert stage._score_novelty({"novelty_score": 1.0}) == 1.0


def test_r70_pr5a_score_novelty_default_when_field_missing() -> None:
    """When the upstream dedup/clustering stage hasn't written
    ``novelty_score`` onto the item, the body returns 0.5 — the
    documented default that all three production channels relied
    on. Pin so a refactor can't drift to a different default."""
    stage = _StubScoring()
    assert stage._score_novelty({}) == 0.5
    assert stage._score_novelty({"other": 99}) == 0.5
    # Explicit None must NOT be treated as missing — ``dict.get``
    # returns None, which is what every channel's body returned
    # before this extraction. Pin the surprise so the base body
    # can't be "helpfully" coerced to 0.5 in a future edit.
    assert stage._score_novelty({"novelty_score": None}) is None


def test_r70_pr5a_score_novelty_does_not_call_ensure_config() -> None:
    """Unlike ``_get_whisper_config`` on BaseVisualRenderStrategy,
    ``_score_novelty`` reads from the *item*, not from
    ``self._config`` — so it must NOT call ``_ensure_config``. Pin
    that contract so a future edit doesn't add a "safety"
    _ensure_config call that surprises subclasses on cold paths."""
    stage = _StubScoring()
    stage._score_novelty({"novelty_score": 0.4})
    assert stage.ensure_call_count == 0


def test_r70_pr5a_base_subclasses_scoring_strategy() -> None:
    """The base must inherit the existing
    ``ScoringStrategy(ABC)`` from interfaces.py, NOT introduce a
    parallel class hierarchy. Pin that so any subclass of the base
    also passes ``isinstance(x, ScoringStrategy)`` checks that
    callers rely on."""
    from genlab_core.strategies.interfaces import ScoringStrategy

    stage: Any = _StubScoring()
    assert isinstance(stage, ScoringStrategy)
