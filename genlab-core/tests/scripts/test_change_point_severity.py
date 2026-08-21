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


class FakeConnWithExisting(FakeConn):
    """A connection where an alert is ALREADY open at `existing_severity`."""

    def __init__(self, existing_severity: str | None):
        super().__init__()
        self.existing_severity = existing_severity
        self.resolved = 0

    def execute(self, sql, params=None):
        if "SELECT 1, severity FROM pipeline_alerts" in sql:
            return _Row(self.existing_severity)
        if "UPDATE pipeline_alerts" in sql and "resolved_at = now()" in sql:
            self.resolved += 1
            return _Updated(1)
        if "INSERT INTO pipeline_alerts" in sql:
            self.severity = params[2]
            self.message = params[3]
        return _Empty()


class _Row:
    def __init__(self, severity):
        self.severity = severity

    def fetchone(self):
        return None if self.severity is None else (1, self.severity)

    def fetchall(self):
        return []


class _Updated:
    def __init__(self, rowcount):
        self.rowcount = rowcount

    def fetchone(self):
        return None

    def fetchall(self):
        return []


class TestStaleAlertsGetCorrected:
    """2026-08-21: a row written under the OLD severity rules could never be
    corrected — unconditional dedup made the stale alert block its own
    replacement, so a CRITICAL 'reward improved' sat on the operator's banner
    until the 24h blanket sweep. The operator saw it and reported the errors
    as still present."""

    def test_open_alert_at_different_severity_is_superseded(self):
        mod = _load()
        conn = FakeConnWithExisting("critical")
        mod._emit_alert(conn, "movies", "instagram", FakeCP("up", 0.98))
        assert conn.resolved == 1, (
            "an open CRITICAL was not resolved when the corrected severity is "
            "warning — the stale row would outlive the rule that produced it"
        )
        assert conn.severity == "warning"

    def test_same_severity_is_still_deduped(self):
        """Re-detecting the same thing must not churn the table."""
        mod = _load()
        conn = FakeConnWithExisting("warning")
        mod._emit_alert(conn, "movies", "instagram", FakeCP("up", 0.98))
        assert conn.resolved == 0
        assert conn.severity is None, "a duplicate should not be re-inserted"


class TestResolveWhenShiftIsGone:
    def test_resolver_closes_open_alerts(self):
        mod = _load()
        conn = FakeConnWithExisting("critical")
        mod._resolve_alert(conn, "movies", "instagram")
        assert conn.resolved == 1

    def test_resolver_never_raises(self):
        """A failed resolve must not abort the scan — the 24h sweep is the
        backstop, but the remaining niches still need checking."""
        mod = _load()

        class Boom:
            def execute(self, *a, **k):
                raise RuntimeError("db gone")

        mod._resolve_alert(Boom(), "movies", "instagram")  # must not raise

    def test_undetected_pair_is_resolved_in_main_loop(self):
        """The `cp is None` branch must resolve rather than bare-continue."""
        import inspect

        mod = _load()
        src = inspect.getsource(mod.main)
        assert "_resolve_alert(conn, niche, platform)" in src, (
            "main() no longer resolves when detect_change_point returns None; "
            "alerts will outlive the condition that produced them again"
        )


class TestRowShapeTolerance:
    """2026-08-21: the supersede check indexed the dedup row positionally
    (`existing[1]`). main() connects with row_factory=dict_row, so rows are
    DICTS — the index raised KeyError(1), which _emit_alert's fail-open handler
    turned into a bare 'alert emit failed: 1'. Every emit stopped working while
    the logs looked merely noisy.

    The unit fakes returned tuples, so they agreed with each other and not with
    production. These pin BOTH shapes.
    """

    def test_dict_row(self):
        mod = _load()
        assert mod._row_severity({"?column?": 1, "severity": "critical"}) == "critical"

    def test_tuple_row(self):
        mod = _load()
        assert mod._row_severity((1, "warning")) == "warning"

    def test_none_and_malformed_rows_do_not_raise(self):
        mod = _load()
        assert mod._row_severity(None) is None
        assert mod._row_severity((1,)) is None
        assert mod._row_severity({}) is None

    def test_supersede_works_with_dict_rows_end_to_end(self):
        """The failure was only visible end-to-end — pin it there too."""
        mod = _load()

        class DictRowConn(FakeConnWithExisting):
            def execute(self, sql, params=None):
                if "SELECT 1, severity FROM pipeline_alerts" in sql:
                    return _DictRow(self.existing_severity)
                return super().execute(sql, params)

        conn = DictRowConn("critical")
        mod._emit_alert(conn, "movies", "instagram", FakeCP("up", 0.98))
        assert conn.resolved == 1, "supersede did not fire against dict rows"
        assert conn.severity == "warning"


class _DictRow:
    def __init__(self, severity):
        self.severity = severity

    def fetchone(self):
        return None if self.severity is None else {"?column?": 1, "severity": self.severity}

    def fetchall(self):
        return []
