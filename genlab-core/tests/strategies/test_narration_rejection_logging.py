"""A rejected narration candidate must be measured, never quoted.

OPS-02 Step 1. `_validate_narration_with_retry` returns `("", reason)` and
discards the candidate, so the logs recorded *that* a script was too long and
never *by how much*. On 2026-08-22 sizing #226 required reproducing the
generation, and that reproduction was impossible because both LLM providers
were out of credit — the one number that would have sized the fix was destroyed
at the moment it was measured.

Two properties, and they pull against each other:

  * the four numbers (words, projected, budget, overshoot) must be emitted;
  * the script TEXT must never be, because a rejected candidate is the
    least-vetted text the writer produces and narration is a compliance surface
    — URLs, affiliate pitches and first-person claims are precisely what the
    other reason slugs exist to catch.
"""
from __future__ import annotations

import logging

import pytest

from genlab_core.strategies import base_writing


class _Strategy(base_writing.BaseWritingStrategy):
    def _model_route_key(self) -> str:
        return "test"


@pytest.fixture
def strategy(tmp_path):
    return _Strategy("ai_creators", tmp_path)


# A candidate that is unmistakably over a 14s budget at 141 wpm, and that
# contains a phrase we can assert never reaches the log.
SECRET = "zzsentineltokenzz"
LONG_SCRIPT = (
    f"{SECRET} " + " ".join(["word"] * 60)
)


class TestNumbersAreEmitted:
    def test_all_four_numbers_present(self, strategy, caplog):
        with caplog.at_level(logging.WARNING):
            strategy._log_rejected_candidate(
                LONG_SCRIPT, attempt=1, reason="script_too_long",
                target_seconds=16.0, wpm=141, tail=2.0, margin=0.0,
            )
        msg = caplog.text
        assert "61 words" in msg, f"word count missing from: {msg}"
        assert "projected" in msg and "budget" in msg, "projection/budget missing"
        assert "overshoot" in msg, "overshoot delta missing"
        assert "attempt 1" in msg, "attempt number missing"

    def test_overshoot_arithmetic_is_right(self, strategy, caplog):
        """61 words at 141 wpm is ~25.96s against a 14.0s budget → ~+11.96s."""
        with caplog.at_level(logging.WARNING):
            strategy._log_rejected_candidate(
                LONG_SCRIPT, attempt=1, reason="script_too_long",
                target_seconds=16.0, wpm=141, tail=2.0, margin=0.0,
            )
        assert "budget 14.00s" in caplog.text, caplog.text
        assert "+11.9" in caplog.text or "+12.0" in caplog.text, caplog.text

    def test_margin_is_reflected_in_the_budget(self, strategy, caplog):
        """The budget logged must be the validator's EFFECTIVE budget."""
        with caplog.at_level(logging.WARNING):
            strategy._log_rejected_candidate(
                LONG_SCRIPT, attempt=2, reason="script_too_long",
                target_seconds=16.0, wpm=141, tail=2.0, margin=0.05,
            )
        assert "budget 13.30s" in caplog.text, caplog.text
        assert "attempt 2" in caplog.text


class TestScriptTextNeverLeaks:
    @pytest.mark.parametrize("margin", [0.0, 0.05])
    @pytest.mark.parametrize("attempt", [1, 2])
    def test_candidate_text_is_not_logged(self, strategy, caplog, attempt, margin):
        with caplog.at_level(logging.DEBUG):
            strategy._log_rejected_candidate(
                LONG_SCRIPT, attempt=attempt, reason="script_too_long",
                target_seconds=16.0, wpm=141, tail=2.0, margin=margin,
            )
        assert SECRET not in caplog.text, (
            "the rejected script text reached the log — it is the least-vetted "
            "text the writer produces and must never be quoted"
        )

    def test_compliance_reason_also_logs_no_text(self, strategy, caplog):
        """The compliance slugs matter most: a candidate rejected for
        containing a URL must not have that URL echoed into the log."""
        with caplog.at_level(logging.DEBUG):
            strategy._log_rejected_candidate(
                "check out https://evil.example.com/promo now",
                attempt=1, reason="script_contained_urls",
                target_seconds=16.0, wpm=141, tail=2.0, margin=0.0,
            )
        assert "evil.example.com" not in caplog.text
        assert "script_contained_urls" in caplog.text


class TestNeverRaises:
    def test_bad_input_does_not_propagate(self, strategy, caplog):
        """Diagnostics must not cost a reel."""
        with caplog.at_level(logging.WARNING):
            strategy._log_rejected_candidate(
                None, attempt=1, reason="script_too_long",  # type: ignore[arg-type]
                target_seconds=16.0, wpm=141, tail=2.0, margin=0.0,
            )
        assert caplog.text, "a failure to measure must still record the rejection"

    def test_zero_wpm_does_not_divide_by_zero(self, strategy, caplog):
        with caplog.at_level(logging.WARNING):
            strategy._log_rejected_candidate(
                LONG_SCRIPT, attempt=1, reason="script_too_long",
                target_seconds=16.0, wpm=0, tail=2.0, margin=0.0,
            )
        assert caplog.text


class TestWiredAtBothAttempts:
    def test_both_rejection_paths_call_the_logger(self):
        """Structural: the measurement is worthless if only one site calls it."""
        import inspect

        src = inspect.getsource(
            base_writing.BaseWritingStrategy._validate_narration_with_retry
        )
        assert src.count("_log_rejected_candidate(") == 2, (
            "expected the measurement at BOTH attempt-1 and attempt-2 "
            f"rejection sites, found {src.count('_log_rejected_candidate(')}"
        )
        assert "attempt=1" in src and "attempt=2" in src
