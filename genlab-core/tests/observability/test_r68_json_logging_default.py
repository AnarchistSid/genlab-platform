"""Tests for R-68: JSON logging defaults + run_id contextvars.

The audit found two compounding problems:
  1. ``GENLAB_LOG_JSON`` was set on zero systemd units, so prod
     ran the console renderer despite the "JSON in production"
     docstring.
  2. ``bind_contextvars(run_id=...)`` was never called, so the
     journald/console stream only carried ``current_stage`` — log
     lines couldn't be correlated to their parent run from outside
     the per-niche JSONL handler.

The fix:
  * cli.py reads ``GENLAB_LOG_JSON``; if unset, defaults to JSON
    when stderr is not a TTY (systemd journald is the canonical
    no-TTY destination).
  * pipeline_runner.py binds ``run_id`` + ``niche_id`` into structlog
    contextvars at run start, unbinds in the run-end ``finally``.

These tests pin the env-var parsing decision tree (no I/O needed)
and the bind/unbind contract (introspecting structlog's
contextvars module directly so we never depend on stderr's TTY
status in CI).
"""

from __future__ import annotations

import io
from unittest.mock import patch

import structlog.contextvars as _ctxvars

# ---------------------------------------------------------------------------
# The env-var-parsing decision tree
# ---------------------------------------------------------------------------


def _decide_is_json(env_val: str, isatty: bool) -> bool:
    """Mirror cli.py's decision tree exactly. Test the algorithm in
    isolation so we don't have to invoke the whole CLI parser."""
    env_val = env_val.lower()
    if env_val in {"true", "1", "yes"}:
        return True
    if env_val in {"false", "0", "no"}:
        return False
    return not isatty


class TestEnvVarDecisionTree:
    def test_explicit_true_forces_json_even_on_tty(self) -> None:
        assert _decide_is_json("true", isatty=True) is True
        assert _decide_is_json("1", isatty=True) is True
        assert _decide_is_json("yes", isatty=True) is True

    def test_explicit_false_forces_console_even_off_tty(self) -> None:
        assert _decide_is_json("false", isatty=False) is False
        assert _decide_is_json("0", isatty=False) is False
        assert _decide_is_json("no", isatty=False) is False

    def test_unset_on_non_tty_defaults_to_json(self) -> None:
        # systemd: journald is not a TTY -> JSON wins.
        assert _decide_is_json("", isatty=False) is True

    def test_unset_on_tty_defaults_to_console(self) -> None:
        # Local dev / interactive: pretty console wins.
        assert _decide_is_json("", isatty=True) is False

    def test_unknown_value_falls_through_to_tty_default(self) -> None:
        # An accidental ``GENLAB_LOG_JSON=maybe`` shouldn't crash —
        # it falls through to the TTY-based default.
        assert _decide_is_json("maybe", isatty=False) is True
        assert _decide_is_json("maybe", isatty=True) is False


# ---------------------------------------------------------------------------
# structlog contextvars bind/unbind round-trip
# ---------------------------------------------------------------------------


class TestRunIdContextvars:
    def setup_method(self) -> None:
        # Ensure each test starts with a clean contextvars set.
        _ctxvars.clear_contextvars()

    def teardown_method(self) -> None:
        _ctxvars.clear_contextvars()

    def test_bind_makes_run_id_visible(self) -> None:
        _ctxvars.bind_contextvars(run_id="gaming_20260611_063000", niche_id="gaming")
        bound = _ctxvars.get_contextvars()
        assert bound.get("run_id") == "gaming_20260611_063000"
        assert bound.get("niche_id") == "gaming"

    def test_unbind_clears_keys(self) -> None:
        _ctxvars.bind_contextvars(run_id="x", niche_id="y", other_key="keep_me")
        _ctxvars.unbind_contextvars("run_id", "niche_id")
        bound = _ctxvars.get_contextvars()
        assert "run_id" not in bound
        assert "niche_id" not in bound
        # Other keys are untouched — the pipeline-runner's unbind must
        # not nuke unrelated context the dashboard or a stage might
        # have bound.
        assert bound.get("other_key") == "keep_me"

    def test_unbind_is_safe_on_missing_keys(self) -> None:
        """A defensive guarantee — calling unbind on keys we never
        bound shouldn't raise. structlog's API matches this contract,
        but we pin it because the pipeline-runner's finally block
        runs unconditionally (even on init-failure paths)."""
        _ctxvars.unbind_contextvars("run_id", "niche_id")
        # Should not have raised.
        assert _ctxvars.get_contextvars() == {}


# ---------------------------------------------------------------------------
# End-to-end: configure_logging + bind = visible in JSON output
# ---------------------------------------------------------------------------


class TestE2EJsonRendererSurfacesBoundContext:
    def setup_method(self) -> None:
        _ctxvars.clear_contextvars()

    def teardown_method(self) -> None:
        _ctxvars.clear_contextvars()

    def test_json_render_includes_bound_run_id(self) -> None:
        """The full processor chain in ``configure_logging`` runs
        ``merge_contextvars`` so bound keys flow into every log
        line — pin that contract end-to-end."""
        import json
        import logging
        import sys

        from genlab_core.observability.logging import configure_logging

        captured = io.StringIO()

        # Redirect stderr (where configure_logging attaches its
        # handler) into a string buffer for assertion.
        with patch.object(sys, "stderr", captured):
            configure_logging(json_output=True, level=logging.INFO)
            _ctxvars.bind_contextvars(run_id="test_run_42", niche_id="gaming")
            log = logging.getLogger("test.r68")
            log.info("starting stage")

        # The handler writes one JSON line per log call.
        line = captured.getvalue().strip().splitlines()[-1]
        record = json.loads(line)
        assert record.get("run_id") == "test_run_42"
        assert record.get("niche_id") == "gaming"
        assert record.get("event") == "starting stage"
