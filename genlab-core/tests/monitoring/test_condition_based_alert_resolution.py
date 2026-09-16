"""Pin: alerts resolve when their condition clears, not only when they age out.

2026-09-15. Permissions drift was repaired (260 -> 0 non-genlab files), git
ownership was repaired (129 -> 0), and post-deploy-verify was exiting 0, while
Mission Control still showed SERVICE_DOWN and GIT_OWNERSHIP_DRIFT as unresolved
CRITICALs. Nothing was broken -- ``resolve_stale_alerts()`` resolves on AGE
(>24h) and relies on "it'll be re-created if still active", which keeps
persistent conditions visible but leaves a FIXED condition red for up to a day.

Red the operator learns to ignore is how a real alert gets missed.

The load-bearing test here is the last one. ``resolve_cleared_conditions``
infers "condition is clear" from "the check did not fire", which is only sound
because ``run_all_checks`` lets exceptions propagate. Wrap any single check in
try/except and absence starts meaning "crashed", silently resolving alerts for
checks that never ran.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from genlab_core.monitoring import health_monitor as hm
from genlab_core.monitoring.alerts import Alert

_MONITORING = Path(hm.__file__).resolve().parent


def _emitted_check_names() -> set[str]:
    """Every literal passed as ``check=`` anywhere in the monitoring package."""
    names: set[str] = set()
    for path in _MONITORING.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg == "check" and isinstance(kw.value, ast.Constant):
                    if isinstance(kw.value.value, str):
                        names.add(kw.value.value)
    return names


def test_the_scan_finds_check_names() -> None:
    """Guard the guard: an empty scan would make the drift test vacuous."""
    found = _emitted_check_names()
    assert len(found) > 30, f"only {len(found)} check names found — AST scan is dead"
    assert "service_down" in found


def test_every_resolvable_name_is_actually_emitted() -> None:
    """A typo here means that alert silently never resolves on clear."""
    emitted = _emitted_check_names()
    unknown = sorted(hm._CONDITION_RESOLVABLE - emitted)
    assert not unknown, (
        f"_CONDITION_RESOLVABLE names that no check emits: {unknown}. "
        f"A name that never fires is also a name that never clears."
    )


def test_partial_niche_run_resolves_nothing() -> None:
    """A --niche run skips the system-wide checks, so absence proves nothing."""
    assert hm.resolve_cleared_conditions([], full_run=False) == 0


def test_a_still_firing_check_is_not_resolved(monkeypatch) -> None:
    """The cleared set is the complement of what fired — by construction."""
    captured: dict = {}

    class _Cur:
        rowcount = 0

        def execute(self, sql, params=None):
            # Only the cleared-set UPDATE uses `= ANY(%s)`. De-escalation
            # issues one UPDATE per check with a scalar name, and capturing
            # those too would overwrite the list with a string.
            if params and "ANY(%s)" in sql:
                captured["names"] = list(params[0])

    class _Conn:
        def cursor(self):
            return _Cur()

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(hm, "pg_connect", lambda *a, **k: _Conn())
    still_firing = Alert(
        check="anthropic_credit_exhausted",
        severity="critical",
        message="still exhausted",
    )
    hm.resolve_cleared_conditions([still_firing], full_run=True)
    assert "anthropic_credit_exhausted" not in captured["names"], (
        "a check that fired this cycle must never be in the cleared set"
    )
    assert "service_down" in captured["names"], "a check that did NOT fire should be resolved"


def test_no_per_check_exception_swallowing_in_run_all_checks() -> None:
    """THE safety invariant.

    resolve_cleared_conditions reads "absent from the result" as "measured
    clear". That holds only while run_all_checks lets exceptions propagate. If
    a per-check try/except is ever added, a crashing check looks identical to a
    passing one and its alert gets resolved while the condition is unknown.
    """
    tree = ast.parse(inspect.getsource(hm.run_all_checks))
    handlers = [n for n in ast.walk(tree) if isinstance(n, ast.Try)]
    assert not handlers, (
        "run_all_checks now swallows exceptions per-check. "
        "resolve_cleared_conditions infers 'condition clear' from 'check did "
        "not fire' and that inference is now unsound. Either remove the "
        "try/except, or make run_all_checks return the set of checks that "
        "actually completed and resolve only from that."
    )


class TestDeEscalation:
    """A critical must be replaceable by the warning that supersedes it.

    write_alerts_to_db dedups by keeping the highest OPEN severity. That
    correctly suppresses duplicate noise, but it also means a check that starts
    reporting a lower severity can never displace its own stale critical --
    exactly what happened when ANTHROPIC_CREDIT_EXHAUSTED was retargeted to
    warning while belt held $105.81.
    """

    @staticmethod
    def _capture(monkeypatch):
        calls: list[tuple] = []

        class _Cur:
            rowcount = 1

            def execute(self, sql, params=None):
                calls.append((" ".join(sql.split()), params))

        class _Conn:
            def cursor(self):
                return _Cur()

            def commit(self):
                pass

            def close(self):
                pass

        monkeypatch.setattr(hm, "pg_connect", lambda *a, **k: _Conn())
        return calls

    def test_lower_severity_resolves_the_outranking_open_row(self, monkeypatch) -> None:
        calls = self._capture(monkeypatch)
        warning = Alert(
            check="anthropic_credit_exhausted",
            severity="warning",
            message="fallback tier only; belt funded",
        )
        hm.resolve_cleared_conditions([warning], full_run=True)
        deesc = [c for c in calls if "CASE severity" in c[0]]
        assert deesc, "no de-escalation UPDATE was issued for a warning-level alert"
        sql, params = deesc[0]
        assert params == ("anthropic_credit_exhausted", 2), (
            "must resolve rows strictly outranking warning (rank 2)"
        )
        assert "> %s" in sql, "must be strictly-greater, or it resolves its own new row"

    def test_same_severity_is_not_de_escalated(self, monkeypatch) -> None:
        """A still-critical check must keep its open critical — dedup's job."""
        calls = self._capture(monkeypatch)
        crit = Alert(check="anthropic_credit_exhausted", severity="critical", message="no provider")
        hm.resolve_cleared_conditions([crit], full_run=True)
        deesc = [c for c in calls if "CASE severity" in c[0]]
        assert deesc and deesc[0][1] == ("anthropic_credit_exhausted", 3), (
            "rank 3 with a strict > means nothing outranks it — correct no-op"
        )


def test_reconcile_runs_before_write_in_main() -> None:
    """Ordering is load-bearing, not stylistic.

    If write_alerts_to_db runs first, the de-escalated alert's new lower row is
    dropped by dedup against the critical this function is about to resolve —
    and the operator sees neither.
    """
    import re

    src = inspect.getsource(hm.main)
    # Match assignments, not prose: the explanatory comment above the call
    # names write_alerts_to_db first, and a substring search finds that.
    resolve_at = re.search(r"^\s+\w+ = resolve_cleared_conditions\(", src, re.M)
    write_at = re.search(r"^\s+\w+ = write_alerts_to_db\(", src, re.M)
    assert resolve_at and write_at, (
        f"call sites not found — resolve={bool(resolve_at)} write={bool(write_at)}"
    )
    assert resolve_at.start() < write_at.start(), (
        "resolve_cleared_conditions must run BEFORE write_alerts_to_db"
    )
