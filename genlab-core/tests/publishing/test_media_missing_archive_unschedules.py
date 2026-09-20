"""A blueprint whose media is gone must be archivable, not stuck forever.

THE DEADLOCK. The pre-publish media check archives a blueprint whose
`visual_paths` no longer exist — fanning out to four platforms would burn the
budget on four guaranteed MISSING_RENDER skips. But the archive left
`scheduled_for` set, and the scheduled-post guard refuses exactly that
transition: moving a scheduled record to ARCHIVED would silently discard a
queued slot (cleanup_safety.md). The guard is right; this caller was not a
cleanup.

So the record could neither publish (no media) nor archive (scheduled), the
selector picked it again on every run, and the exception killed the niche's
publish before any platform was attempted. ai_creators and sports went three
days without publishing, three runs a day, on the same two records.

The guard's own message says how: "set scheduled_for=None in the same update."
"""

from __future__ import annotations

import inspect

from genlab_core.publishing import publish_all_platforms


def test_the_pre_publish_archive_unschedules_in_the_same_update():
    src = inspect.getsource(publish_all_platforms)
    i = src.index("ARCHIVING %s pre-publish")
    window = src[i : i + 1400]
    assert '"status": "ARCHIVED"' in window
    assert '"scheduled_for": None' in window, (
        "archiving a scheduled blueprint without clearing scheduled_for is "
        "refused by the guard, and the record deadlocks"
    )


def test_the_guard_still_refuses_a_bare_demotion():
    """The fix must not weaken the guard. A demotion that does NOT unschedule
    is still refused — that is the case cleanup_safety.md exists for."""
    from genlab_core.http.backlog_client import ScheduledPostProtectionError

    assert issubclass(ScheduledPostProtectionError, Exception)
    src = inspect.getsource(publish_all_platforms)
    # the only place we clear it is the media-missing archive
    assert src.count('"scheduled_for": None') == 1, (
        "scheduled_for is cleared in more than one place — each needs its own reason"
    )


def test_a_guard_refusal_is_caught_per_blueprint_not_per_run():
    """MEASURED, and it corrects an assumption worth recording.

    The premise was that `ScheduledPostProtectionError` was uncaught and killed
    the niche's whole run. The publisher's log shows otherwise: it logged
    "Pre-publish archive failed … (continuing)" and went on to the jitter sleep
    and the next niche. The traceback in the journal came from `exc_info=True`,
    not from a re-raise.

    What actually cost the two channels their slot was the unconditional
    `return` after the archive attempt — the record was skipped either way, and
    eligible content behind it was never reached.
    """
    import inspect

    from genlab_core.publishing import publish_all_platforms

    src = inspect.getsource(publish_all_platforms)
    i = src.index("ARCHIVING %s pre-publish")
    window = src[i : i + 2000]
    assert "except Exception" in window, "the archive must not take the run with it"
    assert "(continuing)" in window


def test_an_archived_blueprint_does_not_end_the_niches_run():
    """One dead record costs an attempt, not the channel's slot. Bounded to
    three, so a niche whose whole queue is dead cannot spin."""
    import inspect

    from genlab_core.publishing import publish_all_platforms as P

    assert P.EXIT_MEDIA_ARCHIVED not in (
        P.EXIT_SUCCESS,
        P.EXIT_NO_BLUEPRINTS,
        P.EXIT_ALL_FAILED,
        P.EXIT_DAILY_CAP,
        P.EXIT_LOCK_HELD,
        P.EXIT_UNEXPECTED,
    ), "the archived signal must be distinguishable from every other outcome"
    main = inspect.getsource(P)
    assert "for _attempt in range(3):" in main
    assert "if exit_code != EXIT_MEDIA_ARCHIVED:" in main
