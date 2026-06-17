"""Regression guard: every pipeline_alerts writer must use lowercase severity.

Post-Wave-4 audit (2026-06-17) found 2 shell-script writers emitting
``'CRITICAL'`` while 4 Python writers emit ``'critical'``. The reader
(``notify_critical_alerts.py``, fixed in PR #296) was case-sensitive
``= 'CRITICAL'`` and silently dropped the 4 lowercase classes for an
unknown duration.

Migration ``v2q3r4s5t6u7`` (this PR) adds a CHECK constraint
``severity IN ('critical', 'warning', 'error', 'info')`` that fails
at the DB level if any future writer drifts. But CHECK constraints
fire at write time — they catch the regression but in a hot path.
This test catches it at PR time so the regression never reaches main.

We scan the actual writer files (not their tests) for uppercase
severity strings in INSERT statements. Anyone re-adding ``'CRITICAL'``
will trip this test in CI.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]

# Every known pipeline_alerts writer. New writers must be added here OR
# converge on lowercase from the start (the canonical contract).
_KNOWN_WRITERS = (
    "scripts/systemd_failure_alert.sh",
    "scripts/check_permissions_drift.sh",
    "scripts/archive_stale_visual_paths.py",
    "genlab-core/src/genlab_core/monitoring/health_monitor.py",
    "genlab-core/src/genlab_core/engagement/token_health.py",
    "genlab-core/src/genlab_core/learning/hook_classifier.py",
)


# Severity values that would fail the v2q3r4s5t6u7 CHECK constraint.
# Each is a literal string written to the severity column — catching
# them at PR time avoids a noisy prod ALERT-write failure.
_FORBIDDEN_PATTERNS = (
    re.compile(r"'CRITICAL'"),
    re.compile(r'"CRITICAL"'),
    re.compile(r"'WARNING'"),
    re.compile(r'"WARNING"'),
    re.compile(r"'ERROR'"),
    re.compile(r'"ERROR"'),
)


# Files where the uppercase strings DO appear, but legitimately — log
# messages, exception text, dashboard labels, etc. We allowlist these
# rather than make the regex smarter, because the writer-vs-log
# distinction is contextual and a single greppable test stays simple.
# Path -> reason (for the next reader of this file).
_ALLOWED_OCCURRENCES = {
    # health_monitor builds log messages containing the word CRITICAL
    # for the operator banner — these are NOT writes to the severity
    # column; the actual severity= kwarg is always lowercase.
    "genlab-core/src/genlab_core/monitoring/health_monitor.py": (
        # log/banner-only references
        "CRITICAL ALERT",
    ),
    # token_health logs a message with the word CRITICAL ALERT — same
    # pattern as health_monitor.
    "genlab-core/src/genlab_core/engagement/token_health.py": ("CRITICAL ALERT",),
    # archive_stale_visual_paths writes a documentary comment about
    # the severity it uses. The actual INSERT is lowercase.
    "scripts/archive_stale_visual_paths.py": ("CRITICAL pipeline_alerts",),
}


@pytest.mark.parametrize("rel_path", _KNOWN_WRITERS)
def test_writer_uses_lowercase_severity(rel_path: str):
    """A writer file must NOT contain uppercase severity string
    literals OUTSIDE the allow-listed positions."""
    path = _REPO_ROOT / rel_path
    assert path.exists(), f"writer file not found: {rel_path} — update _KNOWN_WRITERS"
    content = path.read_text(encoding="utf-8")

    # Strip allowed substrings before scanning so the regex doesn't
    # trip on legitimate log-message uses.
    allowed = _ALLOWED_OCCURRENCES.get(rel_path, ())
    for sub in allowed:
        content = content.replace(sub, "<<allowed-occurrence-stripped>>")

    for pat in _FORBIDDEN_PATTERNS:
        matches = pat.findall(content)
        assert not matches, (
            f"{rel_path} contains forbidden uppercase severity literal "
            f"{pat.pattern!r}: {matches}. "
            "Severity must be lowercase per migration v2q3r4s5t6u7's "
            "CHECK constraint. If this is a legitimate log message, "
            "add the surrounding context to _ALLOWED_OCCURRENCES."
        )
