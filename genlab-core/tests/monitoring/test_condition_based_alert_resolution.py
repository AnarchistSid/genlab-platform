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

        def execute(self, _sql, params=None):
            captured["names"] = list(params[0]) if params else []

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
    assert "service_down" in captured["names"], (
        "a check that did NOT fire should be resolved"
    )


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
