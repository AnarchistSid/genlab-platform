"""Pin CLAUDE.md rules #19-#21 (session 2026-07-17 batch-fix from deep-cuts audit).

Each rule codifies a class-of-bug that ate real production time. The
pins here are the greppable regression guards — if any of them break,
the invariant is broken.

Rule #19 — never swallow observability writes at DEBUG level
Rule #20 — never write to a secrets file without flock on a sidecar .lock
Rule #21 — never leave a weekly timer at Persistent=false
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


# ─── Rule #19 ────────────────────────────────────────────────────────
# dashboard/server/review_server.py:1443 must NOT downgrade the
# calibration_logger swallow back to DEBUG level. The 17-day silent-
# death of the auto-approver ratchet (2026-06-29 → 2026-07-16) came
# from exactly this shape. Elevating to WARNING gives operators a
# visible signal AND exc_info captures the traceback.


def test_rule_19_calibration_logger_swallow_not_debug() -> None:
    """The calibration_logger exception handler in review_server.py
    must use `logger.warning` with `exc_info=True`, NOT `logger.debug`.

    Regression scenario: someone re-runs a "quiet the logs" audit and
    downgrades this back to DEBUG. Silent for 17 days last time.
    """
    src_lines = (
        _REPO_ROOT / "dashboard" / "server" / "review_server.py"
    ).read_text(encoding="utf-8").splitlines()

    # Locate the `except Exception as _cal_exc:` line and inspect the
    # NEXT 20 lines only — enough to see the log call, small enough to
    # not accidentally capture the neighbouring handler's `logger.debug`
    # (which is a separate, unrelated dashboard-events swallow).
    handler_start = None
    for i, line in enumerate(src_lines):
        if "except Exception as _cal_exc" in line:
            handler_start = i
            break
    assert handler_start is not None, (
        "calibration_logger except handler moved or was removed — audit "
        "CLAUDE.md rule #19 and update this pin if the location changed."
    )
    handler_body = "\n".join(src_lines[handler_start : handler_start + 20])

    assert "logger.debug" not in handler_body, (
        "Rule #19 regression: calibration_logger swallow is DEBUG-level "
        "again. This masked 17 days of auto-approver ratchet dead-writes "
        "(2026-06-29 → 2026-07-16). Must be `logger.warning` with "
        "`exc_info=True`. See CLAUDE.md rule #19."
    )
    assert "logger.warning" in handler_body, (
        "Rule #19: calibration_logger swallow must emit at WARNING or "
        "higher (was DEBUG for 17 silent days). See CLAUDE.md rule #19."
    )
    assert "exc_info=True" in handler_body, (
        "Rule #19: calibration_logger swallow must include `exc_info=True` "
        "so the actual failure mode surfaces in journal. See CLAUDE.md "
        "rule #19."
    )


# ─── Rule #20 ────────────────────────────────────────────────────────
# Both secret-writing scripts MUST wrap their write in `fcntl.flock`
# on a sidecar `.lock` file. Prevents concurrent systemd-timer +
# manual-operator invocations from racing on the .tmp path.


@pytest.mark.parametrize(
    "path",
    [
        "genlab-core/src/genlab_core/scripts/refresh_threads_tokens.py",
        "genlab-core/src/genlab_core/monitoring/twitter_quota.py",
    ],
)
def test_rule_20_flock_on_secret_state_writes(path: str) -> None:
    """Both secret-writing sites must use fcntl.flock on a sidecar
    .lock file. Regression scenario: someone "cleans up" the flock
    import as unused because it's inside the function.
    """
    src = (_REPO_ROOT / path).read_text(encoding="utf-8")
    assert "import fcntl" in src, (
        f"Rule #20 regression in {path}: fcntl import removed. Concurrent "
        "systemd-timer + manual-operator invocations can race on the .tmp "
        "path. See CLAUDE.md rule #20."
    )
    assert "fcntl.flock" in src, (
        f"Rule #20 regression in {path}: fcntl.flock call removed. See "
        "CLAUDE.md rule #20."
    )
    assert "LOCK_EX" in src, (
        f"Rule #20 regression in {path}: exclusive lock (LOCK_EX) removed. "
        "Shared lock allows concurrent writers. See CLAUDE.md rule #20."
    )
    assert '.with_suffix(".lock")' in src or '.lock' in src, (
        f"Rule #20 regression in {path}: sidecar .lock file no longer "
        "referenced. Lock file must be separate from the secrets file "
        "(0o600 on secrets breaks lock-file open in different processes). "
        "See CLAUDE.md rule #20."
    )


# ─── Rule #21 ────────────────────────────────────────────────────────
# The strategist timer is weekly. Persistent=false silently loses
# missed Sundays. Must be Persistent=true so systemd catches up
# missed fires on next boot.


def test_rule_21_strategist_timer_persistent_true() -> None:
    """The strategist timer fires weekly. Persistent=false loses any
    missed Sunday (VPS down, service crashed). Was `false` for ~5
    weeks — operator noticed the missing report was 5 weeks stale.

    Regression scenario: someone copy-pastes a hourly-timer template
    that carries `Persistent=false`.
    """
    timer_src = (
        _REPO_ROOT / "deploy" / "systemd-phase2" / "genlab-strategist.timer"
    ).read_text(encoding="utf-8")

    # Ignore comment lines so the audit-trail comment
    # "flipped Persistent=false → true" doesn't false-fire this pin.
    non_comment_lines = [
        line
        for line in timer_src.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    non_comment_body = "\n".join(non_comment_lines)

    assert "Persistent=true" in non_comment_body, (
        "Rule #21 regression: genlab-strategist.timer has Persistent=false "
        "again. Weekly timers must catch up missed fires on boot; the "
        "strategist was silently dark for ~5 weeks under Persistent=false. "
        "See CLAUDE.md rule #21."
    )
    assert "Persistent=false" not in non_comment_body, (
        "Rule #21 regression: an active `Persistent=false` re-appeared "
        "in the strategist timer (checked non-comment lines only). See "
        "CLAUDE.md rule #21."
    )
