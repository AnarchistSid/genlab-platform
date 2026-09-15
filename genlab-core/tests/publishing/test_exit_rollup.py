"""Pin: the publisher's exit codes are identifiers, not a severity scale.

2026-09-15. Four niches published 19 of 20 platform cells and the run exited 1,
so systemd recorded a service failure and fired OnFailure on a good day.

Cause: `total_exit = max(total_exit, exit_code)` aggregated the per-niche codes
numerically, but they are not ordered by severity:

    0 SUCCESS        1 NO_BLUEPRINTS (benign)   2 ALL_FAILED (real)
    3 DAILY_CAP (benign)  4 LOCK_HELD (benign)  5 UNEXPECTED (real)

max() therefore ranks DAILY_CAP above ALL_FAILED, and NO_BLUEPRINTS above
SUCCESS. One quiet niche poisons the whole run's exit code.

Rule #26: exit non-zero only when a genuine incident needs operator paging. A
niche with no fresh blueprint is a DATA-side signal the operator sees on the
dashboard, not a service failure.
"""

from __future__ import annotations

import inspect

from genlab_core.publishing import publish_all_platforms as pap
from genlab_core.publishing.publish_all_platforms import (
    EXIT_ALL_FAILED,
    EXIT_DAILY_CAP,
    EXIT_LOCK_HELD,
    EXIT_NO_BLUEPRINTS,
    EXIT_PARTIAL,
    EXIT_SUCCESS,
    EXIT_UNEXPECTED,
    _rollup_exit,
)


def test_the_day_that_motivated_this() -> None:
    """4 niches published, 1 had no blueprint. Not an incident."""
    got = _rollup_exit([("ai_creators", EXIT_SUCCESS), ("gaming", EXIT_SUCCESS),
                        ("sports", EXIT_SUCCESS), ("movies", EXIT_SUCCESS),
                        ("anime", EXIT_NO_BLUEPRINTS)])
    assert got == EXIT_SUCCESS == 0


def test_partial_is_zero_on_the_wire() -> None:
    """EXIT_PARTIAL must be 0 — systemd reads the number, not the name."""
    assert EXIT_PARTIAL == 0


def test_one_published_one_really_failed_is_still_zero() -> None:
    """Something shipped. The failure is a WARN, not a page."""
    assert _rollup_exit([("ai", EXIT_SUCCESS), ("gaming", EXIT_ALL_FAILED)]) == 0


def test_nothing_published_and_a_real_failure_pages() -> None:
    assert _rollup_exit([("ai", EXIT_ALL_FAILED), ("g", EXIT_ALL_FAILED)]) == EXIT_ALL_FAILED


def test_unexpected_outranks_all_failed_when_nothing_shipped() -> None:
    assert _rollup_exit([("ai", EXIT_ALL_FAILED), ("g", EXIT_UNEXPECTED)]) == EXIT_UNEXPECTED


def test_benign_only_run_keeps_its_benign_code() -> None:
    """No blueprints anywhere is still reported, just not as an incident."""
    assert _rollup_exit([("ai", EXIT_NO_BLUEPRINTS), ("g", EXIT_DAILY_CAP)]) == EXIT_NO_BLUEPRINTS


def test_daily_cap_no_longer_outranks_all_failed() -> None:
    """The specific max() inversion: 3 (benign) used to beat 2 (real)."""
    assert _rollup_exit([("ai", EXIT_ALL_FAILED), ("g", EXIT_DAILY_CAP)]) == EXIT_ALL_FAILED


def test_lock_held_is_benign() -> None:
    assert _rollup_exit([("ai", EXIT_LOCK_HELD)]) == EXIT_LOCK_HELD


def test_empty_run_is_success() -> None:
    assert _rollup_exit([]) == EXIT_SUCCESS


def test_max_aggregation_is_gone() -> None:
    """Guard the guard: the bug was one line, and it could come back."""
    src = inspect.getsource(pap.main)
    assert "max(total_exit" not in src, (
        "per-niche exit codes are being aggregated with max() again — they are "
        "identifiers, not a severity scale"
    )
