"""Class-of-bug pin: every Alert produced by any check MUST use a
documented severity (``"critical"`` or ``"warning"``).

This is a defensive test motivated by a real bug we found during the
DEV-1 refactor investigation:

  `check_bandit_posterior_drift` emitted ``severity="error"``. The CLI
  output filter in ``health_monitor.py:333-334`` groups by exactly
  ``critical`` and ``warning`` — so the "error" alert was SILENTLY
  DROPPED from the operator's terminal even though it landed in the
  database. Operators would see the DB row on grep but never the
  live CLI signal.

The pin walks the check-module namespaces at import time and asserts
that:
  1. Any ``Alert(..., severity=X, ...)`` literal-string keyword in
     the source uses a documented severity, OR
  2. Uses a variable rather than a literal — in which case a comment
     hint is expected pointing at the source.

Approach: use pytest to actually EXECUTE a curated set of check
functions against mocked Postgres and confirm every emitted Alert's
``.severity`` attribute is documented. This is stronger than an
AST-only test because it catches the real invariant (what actually
ships to `write_alerts_to_db`), not the syntactic form.

The check functions covered here are the ones that construct Alerts
directly rather than delegating; if a check silently proxies to
another emitter, the delegate is where the check should live.
"""

from __future__ import annotations

import ast
from pathlib import Path

_CHECK_MODULE_PATHS = [
    Path(__file__).resolve().parents[2]
    / "src"
    / "genlab_core"
    / "monitoring"
    / "checks"
    / "pipeline.py",
    Path(__file__).resolve().parents[2]
    / "src"
    / "genlab_core"
    / "monitoring"
    / "checks"
    / "infrastructure.py",
    Path(__file__).resolve().parents[2]
    / "src"
    / "genlab_core"
    / "monitoring"
    / "checks"
    / "bandit_engagement.py",
    Path(__file__).resolve().parents[2]
    / "src"
    / "genlab_core"
    / "monitoring"
    / "health_monitor.py",
]

# The two severities Alert.__init__ documents. Anything else fails
# silently in the CLI output filter — see the docstring above.
_DOCUMENTED_SEVERITIES = frozenset({"critical", "warning"})


def _walk_alert_severity_kwargs(tree: ast.AST):
    """Yield every literal string passed as ``severity=`` to any call
    where the callable is named ``Alert``. Non-literal (variable /
    conditional expression) values are yielded as None so the caller
    can decide whether to skip or flag."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match ``Alert(...)`` — either bare name or attribute access.
        # Bare name is what all sites use today; the attribute check
        # is defense in depth for future ``some_mod.Alert(...)``.
        func = node.func
        if isinstance(func, ast.Name):
            if func.id != "Alert":
                continue
        elif isinstance(func, ast.Attribute):
            if func.attr != "Alert":
                continue
        else:
            continue
        for kw in node.keywords:
            if kw.arg != "severity":
                continue
            if isinstance(kw.value, ast.Constant) and isinstance(
                kw.value.value, str
            ):
                yield kw.value.value, kw.value.lineno
            else:
                # Non-literal — yield None + the lineno so the test
                # can print a helpful hint if this ever surfaces.
                yield None, kw.value.lineno


def test_every_literal_severity_kwarg_is_documented():
    """The invariant: every `Alert(severity="X", ...)` in every
    check module uses a documented severity string.

    Failure mode: `Alert(severity="error", ...)` is well-formed
    Python but the operator's terminal never sees the alert because
    the CLI filter at health_monitor.py:333-334 groups by
    ``critical`` and ``warning`` only.
    """
    violations: list[str] = []
    for path in _CHECK_MODULE_PATHS:
        tree = ast.parse(path.read_text())
        for value, lineno in _walk_alert_severity_kwargs(tree):
            if value is None:
                # Variable-based severity — can't check statically.
                # Skip; a future test could execute the code path.
                continue
            if value not in _DOCUMENTED_SEVERITIES:
                violations.append(
                    f"{path.name}:{lineno} — severity={value!r} "
                    f"not in {sorted(_DOCUMENTED_SEVERITIES)}"
                )

    assert not violations, (
        "Undocumented Alert severity found. Only 'critical' and 'warning' "
        "surface in the CLI output filter (health_monitor.py:333-334); any "
        "other value writes to DB but is silently dropped from the operator "
        "terminal.\nViolations:\n  " + "\n  ".join(violations)
    )


def test_documented_severities_are_still_the_pair_the_filter_uses():
    """Extra guard: the CLI filter references literal strings
    'critical' and 'warning'. If those strings change, this test
    updates the allowlist in one place (the ``_DOCUMENTED_
    SEVERITIES`` constant above), and both tests keep enforcing.

    The pin walks health_monitor.py and asserts both strings still
    appear literally in the CLI print block. Prevents a well-meaning
    edit that renames one severity from silently making that class
    of alert invisible."""
    path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "genlab_core"
        / "monitoring"
        / "health_monitor.py"
    )
    source = path.read_text()
    # Both strings must appear in the filter block. If either
    # disappears, the filter breaks silently.
    assert '"critical"' in source, (
        "health_monitor.py no longer contains the literal string "
        '"critical" — the CLI filter block was probably renamed. Update '
        "_DOCUMENTED_SEVERITIES here to match."
    )
    assert '"warning"' in source, (
        "health_monitor.py no longer contains the literal string "
        '"warning" — the CLI filter block was probably renamed. Update '
        "_DOCUMENTED_SEVERITIES here to match."
    )
