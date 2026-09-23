"""What the reveal SAYS, decided by the show's status.

ANIME-16 §6. v4 slammed "11 JULY 2025" over TOUGEN ANKI — a show that had
been airing for months — on a frame of the PV's own broadcast-schedule card.
Two errors in one shot: a stale fact, and it was already written on screen.

A date is not the news. The card's "Premiere" row stays a fact; the SLAM is
whatever is currently true and currently interesting:

    NOT_YET_RELEASED  -> the premiere date          "9 OCTOBER"
    RELEASING         -> where the show is now      "EP 12 OUT NOW"
    FINISHED          -> where to watch it          "NOW STREAMING"
    CANCELLED         -> say nothing; there is no good slam for it

``nextAiringEpisode`` is what makes RELEASING specific. Without it the slam
falls back to the airing day, which is still true and still useful.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

NOT_YET_RELEASED = "NOT_YET_RELEASED"
RELEASING = "RELEASING"
FINISHED = "FINISHED"

_MONTHS = (
    "",
    "JANUARY",
    "FEBRUARY",
    "MARCH",
    "APRIL",
    "MAY",
    "JUNE",
    "JULY",
    "AUGUST",
    "SEPTEMBER",
    "OCTOBER",
    "NOVEMBER",
    "DECEMBER",
)
_DAYS = ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY")


@dataclass(frozen=True)
class Reveal:
    """The slam, and why it says that."""

    text: str
    basis: str
    status: str

    def row(self) -> str:
        return f"{self.text!r}  ({self.basis}, status={self.status})"


def choose(story: dict, *, now: datetime | None = None) -> Reveal | None:
    """The reveal line for this show, or None when there is nothing true to say."""
    status = str(story.get("status") or "").upper()
    nxt = story.get("next_airing") or {}
    start = story.get("start_date") or {}

    if status == RELEASING:
        ep = nxt.get("episode")
        if ep:
            # nextAiringEpisode.episode is the one that has NOT aired yet, so
            # the episode a viewer can watch right now is the one before it.
            out = int(ep) - 1
            if out >= 1:
                return Reveal(f"EP {out} OUT NOW", "nextAiringEpisode - 1", status)
            return Reveal("PREMIERES THIS WEEK", "nextAiringEpisode = 1", status)
        at = nxt.get("airingAt")
        if at:
            day = _DAYS[datetime.fromtimestamp(int(at), tz=UTC).weekday()]
            return Reveal(f"NEW EP EVERY {day}", "airingAt weekday", status)
        return Reveal("AIRING NOW", "status only", status)

    if status == FINISHED:
        return Reveal("NOW STREAMING", "status only", status)

    if status == NOT_YET_RELEASED:
        y, m, d = start.get("year"), start.get("month"), start.get("day")
        if m and d:
            return Reveal(f"{d} {_MONTHS[int(m)]}", "startDate", status)
        if y:
            return Reveal(f"COMING {y}", "startDate year", status)
        return Reveal("COMING SOON", "status only", status)

    logger.warning(
        "[reveal] status %r has no reveal line — slamming nothing is better than "
        "slamming something false.",
        status or "(missing)",
    )
    return None
