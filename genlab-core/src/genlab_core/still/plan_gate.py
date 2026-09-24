"""Insufficient footage rejects the PLAN. It never degrades the reel.

ANIME-17 §3. v5's reel A passed 28 of 37 gates while being 67% generated
stills built from three usable windows of one PV. Every gate was correct and
every one was measuring the wrong object: they checked the reel that WAS
made, not whether it should have been made at all.

That is the false green in its purest form — a fallback that satisfies the
checks. So the check moves earlier: before rendering, count the material. If
a 30-45 s reel cannot be built from the show's own footage, the plan is
rejected with a reason and a counter, and the reel is not made. A shorter
format exists for thin bundles (see ``teaser``); holding one still for
thirty seconds is not one of the options.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: A full-length reel needs this many usable windows and this many distinct
#: source assets. Both, not either: eight windows from one PV is one location
#: and one grade.
MIN_USABLE_WINDOWS = 8
MIN_DISTINCT_SOURCES = 5
#: The teaser format's floor. Below this there is no reel at all.
TEASER_MIN_WINDOWS = 3
TEASER_MIN_SOURCES = 1
#: §3 — key art may not carry the reel.
MAX_KEY_ART_SHARE = 0.25
MAX_KEY_ART_HOLD_S = 4.0

FULL = "full"
TEASER = "teaser"


class InsufficientFootage(RuntimeError):
    """The plan is rejected. Do not render."""


@dataclass(frozen=True)
class PlanVerdict:
    format: str
    usable_windows: int
    distinct_sources: int
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.format in (FULL, TEASER)

    def row(self) -> str:
        return (
            f"{self.format}: {self.usable_windows} usable windows from "
            f"{self.distinct_sources} sources{' — ' + self.reason if self.reason else ''}"
        )


def decide(usable_windows: int, distinct_sources: int, *, allow_teaser: bool = True) -> PlanVerdict:
    """Which format this bundle can support, or refuse."""
    if usable_windows >= MIN_USABLE_WINDOWS and distinct_sources >= MIN_DISTINCT_SOURCES:
        return PlanVerdict(FULL, usable_windows, distinct_sources)
    if allow_teaser and (
        usable_windows >= TEASER_MIN_WINDOWS and distinct_sources >= TEASER_MIN_SOURCES
    ):
        return PlanVerdict(
            TEASER,
            usable_windows,
            distinct_sources,
            reason=(
                f"below the full-length floor ({MIN_USABLE_WINDOWS} windows / "
                f"{MIN_DISTINCT_SOURCES} sources) — a 12-18 s teaser instead of "
                f"a 30 s reel padded with a still"
            ),
        )
    raise InsufficientFootage(
        f"insufficient_footage:{usable_windows}w/{distinct_sources}s — below even the "
        f"teaser floor ({TEASER_MIN_WINDOWS} windows / {TEASER_MIN_SOURCES} source). "
        f"No reel is produced; this is counted, not worked around."
    )


def check_key_art_share(shots: list[dict], total_s: float) -> list[str]:
    """§3 — key art may not carry the reel. Returns failures, empty if fine."""
    art = [
        s
        for s in shots
        if s.get("origin") in ("cover", "banner", "character", "generated_with_reference")
    ]
    share = sum(s.get("duration_s", 0.0) for s in art) / total_s if total_s else 0.0
    problems = []
    if share > MAX_KEY_ART_SHARE:
        problems.append(
            f"key art and generated stills are {share:.0%} of the reel "
            f"(limit {MAX_KEY_ART_SHARE:.0%}) — this is the v5 failure: 67% of reel A"
        )
    for s in art:
        if s.get("duration_s", 0.0) > MAX_KEY_ART_HOLD_S:
            problems.append(
                f"shot {s.get('index')} holds key art for "
                f"{s['duration_s']:.1f}s (limit {MAX_KEY_ART_HOLD_S}s)"
            )
    return problems
