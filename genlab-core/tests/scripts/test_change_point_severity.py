"""Alert severity must reflect DIRECTION, not just confidence (2026-08-21).

`_emit_alert` previously computed severity as
``"warning" if cp.confidence < 0.9 else "critical"``, ignoring whether the
detected shift was up or down. A high-confidence UP shift — reward improving —
was therefore written as CRITICAL.

The operator's Mission Control banner opened on "4 unresolved CRITICAL system
alerts" whose first entry was good news (movies/instagram, +16.14σ, confidence
0.98). The cost is not the one alert: a CRITICAL band that routinely contains
no-action items trains the operator to skim it, and the genuine entries below
it — an imminent OOM, a permissions drift that halts every pipeline at startup
— inherit that discount.
"""
from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[3] / "scripts" / "run_change_point_detector.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("_cpd", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@dataclass
class FakeCP:
    direction: str
    confidence: float
    magnitude: float = 16.14
    at_index: int = 16


class FakeConn:
    """Captures the INSERT without needing a database."""

    def __init__(self):
        self.severity: str | None = None
        self.message: str | None = None

    def execute(self, sql, params=None):
        if "SELECT 1 FROM pipeline_alerts" in sql:
            return _Empty()          # no existing alert → proceed to insert
        if "INSERT INTO pipeline_alerts" in sql:
            self.severity = params[2]
            self.message = params[3]
        return _Empty()

    def commit(self):
        pass


class _Empty:
    def fetchone(self):
        return None

    def fetchall(self):
        return []


def _emit(direction: str, confidence: float) -> FakeConn:
    mod = _load()
    conn = FakeConn()
    mod._emit_alert(conn, "movies", "instagram", FakeCP(direction, confidence))
    return conn


class TestUpShiftNeverPages:
    @pytest.mark.parametrize("confidence", [0.5, 0.89, 0.9, 0.98, 1.0])
    def test_up_shift_is_never_critical(self, confidence):
        """Reward going UP is information at every confidence level."""
        conn = _emit("up", confidence)
        assert conn.severity == "warning", (
            f"an UP shift at confidence {confidence} was written as "
            f"{conn.severity!r}. Reward improving is not an incident, and "
            "putting it in the CRITICAL band devalues the band."
        )

    def test_up_shift_still_recorded(self):
        """Downgrading severity must not silence the signal — the operator
        still needs to see that a platform's reward moved."""
        conn = _emit("up", 0.98)
        assert conn.message is not None
        assert "UP shift" in conn.message
        assert "movies/instagram" in conn.message


class TestDownShiftStillEscalates:
    def test_high_confidence_down_shift_is_critical(self):
        """The regression case must keep paging — this is the behaviour the
        original code got right and the fix must preserve."""
        conn = _emit("down", 0.98)
        assert conn.severity == "critical"
        assert "DOWN shift" in conn.message

    def test_low_confidence_down_shift_is_warning(self):
        conn = _emit("down", 0.5)
        assert conn.severity == "warning"

    @pytest.mark.parametrize(
        "confidence,expected",
        [(0.89, "warning"), (0.9, "critical")],
    )
    def test_confidence_threshold_unchanged_for_down(self, confidence, expected):
        """Pins the 0.9 boundary so the direction fix didn't shift it."""
        assert _emit("down", confidence).severity == expected
