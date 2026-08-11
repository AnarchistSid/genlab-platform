"""2026-08-11 Session 1: pin tests for the flag-gated LLM hook scorer.

Motivation: XGBoost `HookClassifier` had Spearman=0.0 vs reward_48h
per strategist diagnostic — the 8 lexical features can't distinguish
curiosity-gap hooks. LLM path (Claude Haiku) provides semantic
judgment. Flag `GENLAB_HOOK_CLASSIFIER_LLM_ENABLED` gates the
behavior; default OFF so shipping = zero behavior change.

These pins lock in:
1. Flag OFF → XGBoost path is used (backward compat)
2. Flag ON + successful LLM call → LLM score returned
3. Flag ON + LLM returns None (network / parse fail) → XGBoost fallback
4. Flag ON + LLM exception → XGBoost fallback (defense in depth)
5. In-process cache dedupes repeat calls for the same (niche, hook)
6. Parser accepts bare decimals, rejects out-of-range and garbage
7. Empty / whitespace hook → 0.5 neutral (no LLM call)
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from genlab_core.learning import hook_classifier
from genlab_core.learning.hook_classifier import (
    HookClassifier,
    _LLM_SCORE_CACHE,
    _parse_llm_score,
)


@pytest.fixture(autouse=True)
def _clear_llm_cache():
    """LLM cache is module-scoped — reset per-test to keep pins isolated."""
    _LLM_SCORE_CACHE.clear()
    yield
    _LLM_SCORE_CACHE.clear()


# -----------------------------------------------------------------
# Parser pins
# -----------------------------------------------------------------


def test_parser_accepts_bare_decimal():
    assert _parse_llm_score("0.72") == 0.72


def test_parser_accepts_zero_and_one():
    assert _parse_llm_score("0.0") == 0.0
    assert _parse_llm_score("1.0") == 1.0


def test_parser_accepts_leading_dot():
    assert _parse_llm_score(".85") == 0.85


def test_parser_rejects_out_of_range():
    # Regex only captures 0.x / 1.x / .x — 1.5 matches "1.5" but
    # the range clamp inside _parse_llm_score returns None.
    assert _parse_llm_score("1.5") is None
    # A leading "2" is outside the regex — nothing captured → None.
    assert _parse_llm_score("2.0") is None


def test_parser_rejects_garbage():
    assert _parse_llm_score("") is None
    assert _parse_llm_score("not a score") is None
    assert _parse_llm_score(None) is None  # type: ignore[arg-type]


def test_parser_extracts_from_verbose_response():
    # Some LLMs leak prose despite the "respond with ONLY" instruction.
    # First matching decimal wins.
    assert _parse_llm_score("Score: 0.65 (moderate)") == 0.65


# -----------------------------------------------------------------
# score_hook routing pins
# -----------------------------------------------------------------


def test_flag_off_uses_xgboost_path(monkeypatch):
    """Default (no env var set) MUST use the existing XGBoost path
    — no LLM function should be called."""
    monkeypatch.delenv("GENLAB_HOOK_CLASSIFIER_LLM_ENABLED", raising=False)
    clf = HookClassifier(niche_id="gaming")

    with patch.object(
        hook_classifier, "_llm_score_hook_impl"
    ) as mock_llm:
        result = clf.score_hook("Ranked but no cap")

    assert 0.0 <= result <= 1.0
    mock_llm.assert_not_called()


def test_flag_on_uses_llm_path(monkeypatch):
    monkeypatch.setenv("GENLAB_HOOK_CLASSIFIER_LLM_ENABLED", "1")
    clf = HookClassifier(niche_id="gaming")

    with patch.object(
        hook_classifier, "_llm_score_hook_impl", return_value=0.83
    ) as mock_llm:
        result = clf.score_hook("Ranked but no cap")

    assert result == 0.83
    mock_llm.assert_called_once_with("Ranked but no cap", "gaming")


def test_flag_on_llm_returns_none_falls_back_to_xgboost(monkeypatch):
    """Defense-in-depth: LLM path returning None (parse fail, network
    down, missing key) MUST fall back to the XGBoost path — not
    return an error, not raise."""
    monkeypatch.setenv("GENLAB_HOOK_CLASSIFIER_LLM_ENABLED", "1")
    clf = HookClassifier(niche_id="sports")

    with patch.object(
        hook_classifier, "_llm_score_hook_impl", return_value=None
    ):
        result = clf.score_hook("Comeback shot heard round the world")

    # XGBoost returns 0.5 when no model is loaded — the point of this
    # pin is that we didn't raise + we didn't return None.
    assert 0.0 <= result <= 1.0


def test_flag_on_llm_raises_falls_back_to_xgboost(monkeypatch):
    """If the LLM helper itself raises (e.g. anthropic package
    corrupted, import loop), we still return a usable score."""
    monkeypatch.setenv("GENLAB_HOOK_CLASSIFIER_LLM_ENABLED", "1")
    clf = HookClassifier(niche_id="anime")

    with patch.object(
        hook_classifier,
        "_llm_score_hook_impl",
        side_effect=RuntimeError("something exploded"),
    ):
        result = clf.score_hook("Sub reveal broke the internet")

    assert 0.0 <= result <= 1.0


# -----------------------------------------------------------------
# Cache pin — dedupes within a run
# -----------------------------------------------------------------


def test_llm_cache_dedupes_repeated_calls(monkeypatch):
    """Same (niche_id, hook) MUST hit cache on 2nd call — the LLM
    fn is only invoked once. Motivating scenario: same hook is
    scored by the gate, ensemble, calibration — 3x per blueprint."""
    monkeypatch.setenv("GENLAB_HOOK_CLASSIFIER_LLM_ENABLED", "1")
    clf = HookClassifier(niche_id="gaming")

    with patch.object(
        hook_classifier, "_llm_score_hook_impl", return_value=0.77
    ) as mock_llm:
        a = clf.score_hook("Same hook text")
        b = clf.score_hook("Same hook text")
        c = clf.score_hook("Same hook text")

    assert a == b == c == 0.77
    assert mock_llm.call_count == 1


def test_llm_cache_partitions_by_niche(monkeypatch):
    """Same hook under different niches MUST get separate cache
    entries — a "sports" scoring rubric ≠ an "anime" rubric."""
    monkeypatch.setenv("GENLAB_HOOK_CLASSIFIER_LLM_ENABLED", "1")

    with patch.object(
        hook_classifier,
        "_llm_score_hook_impl",
        side_effect=[0.4, 0.9],
    ) as mock_llm:
        sports_score = HookClassifier(niche_id="sports").score_hook("hook")
        anime_score = HookClassifier(niche_id="anime").score_hook("hook")

    assert sports_score == 0.4
    assert anime_score == 0.9
    assert mock_llm.call_count == 2


# -----------------------------------------------------------------
# Input-guard pins
# -----------------------------------------------------------------


def test_empty_hook_returns_neutral_without_llm_call(monkeypatch):
    """Empty / whitespace hook is a common upstream error — return
    0.5 without wasting an LLM call."""
    monkeypatch.setenv("GENLAB_HOOK_CLASSIFIER_LLM_ENABLED", "1")
    clf = HookClassifier(niche_id="gaming")

    with patch.object(
        hook_classifier, "_llm_score_hook_impl"
    ) as mock_llm:
        assert clf.score_hook("") == 0.5
        assert clf.score_hook("   ") == 0.5

    mock_llm.assert_not_called()


def test_flag_variants_all_enable(monkeypatch):
    """Flag accepts common truthy strings (1, true, yes, on)."""
    for value in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("GENLAB_HOOK_CLASSIFIER_LLM_ENABLED", value)
        assert hook_classifier._is_llm_enabled(), f"failed for value={value!r}"


def test_flag_variants_all_disable(monkeypatch):
    """Anything other than a truthy value → OFF (fail closed)."""
    for value in ("", "0", "false", "no", "off", "maybe"):
        monkeypatch.setenv("GENLAB_HOOK_CLASSIFIER_LLM_ENABLED", value)
        assert not hook_classifier._is_llm_enabled(), (
            f"failed for value={value!r}"
        )
