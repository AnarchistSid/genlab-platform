"""How far ahead the scheduler may book a slot, per niche.

T-53 recorded that this number is a hardcoded ``range(0, 8)`` in TWO places --
``auto_approver.py`` and ``dashboard/server/core/publishing_queue.py`` -- and
that they are one contract with two implementers, so they must change together.
This module is that contract, so the next change has one place to happen.

Why a niche needs its own number: the lookahead decides how STALE a queued
blueprint may be by the time it publishes. A niche whose content ages fast wants
a short one; a slow-cycling niche can hold more. Booking eight days out is not
free -- it fills the slots a fresher candidate would otherwise take, which is
what the auto-approver meant on 2026-09-17 when it logged "no cap-available slot
in next 7 days" for ai_creators and gaming while both were fully booked.

DECISIONS-0917 §2 confirmed anime at 5 days. The other niches keep 8, the
current behaviour, because only anime's value was decided -- T-53 proposes 2 for
ai_creators/sports and 4 for gaming/movies, but those are not confirmed and
shortening them would silently expire queued work.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_LOOKAHEAD_DAYS = 8

_PER_NICHE: dict[str, int] = {
    # DECISIONS-0917 §2 (T-53): anime trends weekly, and its queue was one deep.
    # Five days is long enough to hold a week's cycle and short enough that a
    # thin queue does not book stale clips into next week.
    "anime": 5,
}


def lookahead_days(niche_id: str | None = None) -> int:
    """Days ahead the scheduler may search for a free slot.

    ``GENLAB_QUEUE_LOOKAHEAD_DAYS`` overrides every niche, for an operator who
    needs to widen or pin the horizon without a deploy. A non-numeric or
    out-of-range value is ignored with a warning rather than silently taken --
    a lookahead of 0 would stop all scheduling.
    """
    raw = os.environ.get("GENLAB_QUEUE_LOOKAHEAD_DAYS", "").strip()
    if raw:
        if raw.isdigit() and 1 <= int(raw) <= 60:
            return int(raw)
        logger.warning(
            "[queue_policy] ignoring GENLAB_QUEUE_LOOKAHEAD_DAYS=%r "
            "(want an integer 1-60)", raw)
    return _PER_NICHE.get((niche_id or "").strip(), DEFAULT_LOOKAHEAD_DAYS)
