"""Pin the 2026-06-15 audit fix: push_to_backlog must NOT pre-set
``scheduled_for`` on new blueprints.

Bug: the previous block in `push_to_backlog.py:1518-1542` hardcoded
``scheduled_for = today/tomorrow 06:30 UTC`` for every publishable
blueprint. With N new blueprints per pipeline run, all N got the
SAME slot — the source of the over-scheduling pattern that PR #220
tried to catch at the dashboard consumer side.

Fix: dropped the pre-set entirely. The dashboard's
``_execute_review_action`` calls ``_next_available_slot`` (cap-aware
per PR #220) at operator-approval time, ignoring whatever
``scheduled_for`` held before (PR #191's ``exclude_record_id``).
Removing the pre-set has zero impact on operator path and prevents
auto-approval / direct-DB-write paths from inheriting a stale 06:30
UTC slot.

This pin reads the source string + asserts the old hardcoded
``today 06:30 UTC`` pattern is NOT present.
"""

from __future__ import annotations

import inspect


def test_no_hardcoded_06_30_utc_pre_set():
    """If a future refactor reinstates the hardcoded
    ``today 06:30 UTC`` pre-set, this pin fires loudly.

    Approach: scan the source string of `push_to_backlog.run` for any
    line that hardcodes hour=6, minute=30 paired with a
    `scheduled_for` assignment. The shape we're forbidding:

      today_slot = datetime(year, month, day, 6, 30, tzinfo=UTC)
      ...
      fields["scheduled_for"] = ...today_slot...

    If the file ever has both `hour=6, minute=30` AND
    `scheduled_for = today_slot.isoformat()` (or similar), the test
    fails. Comments explaining the OLD behavior are fine; we look for
    actual assignments.
    """
    from genlab_core.pipeline.stages.push_to_backlog import PushToBacklog

    src = inspect.getsource(PushToBacklog)

    # Forbidden pattern: setting scheduled_for from a hardcoded 06:30
    # construction. Be strict about the literal `06:30`/`6, 30` shape
    # because the legitimate use of hour=6 in genlab-core is rare
    # enough that this won't false-positive.
    forbidden_pairs = [
        ('fields["scheduled_for"] = publish_time.isoformat()', "publish_time = datetime"),
        ("scheduled_for", "hour=6"),
    ]
    for assign, hardcode in forbidden_pairs:
        if assign in src and hardcode in src:
            raise AssertionError(
                f"push_to_backlog has reinstated the hardcoded 06:30 UTC "
                f"scheduled_for pre-set (found {assign!r} + {hardcode!r}). "
                f"The dashboard's _next_available_slot picks the slot at "
                f"approval time — pre-setting here violates the per-day cap "
                f"PR #220 enforces. If you genuinely need a pre-set, route "
                f"through a cap-aware helper, not hardcoded constants."
            )


def test_publishable_branch_no_longer_writes_scheduled_for():
    """Pin the exact removal: the `if publishable:` branch that used
    to set `scheduled_for` should no longer call `.isoformat()` on a
    datetime built from a 06:30 UTC constant.

    Looks for the canonical removed-line: `fields["scheduled_for"] =
    publish_time.isoformat()` paired with `if publishable:` in the
    same function source.
    """
    from genlab_core.pipeline.stages import push_to_backlog

    src = inspect.getsource(push_to_backlog)
    # The exact assignment line from the old code:
    forbidden_line = 'fields["scheduled_for"] = publish_time.isoformat()'
    if forbidden_line in src:
        raise AssertionError(
            f"Old scheduled_for pre-set found in push_to_backlog.py: "
            f"{forbidden_line!r}. The dashboard handles slot selection "
            f"at approval time — this pre-set was the producer of the "
            f"2026-06-15 over-scheduling pattern."
        )
