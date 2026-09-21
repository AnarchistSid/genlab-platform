"""One data card per reel, or none.

The card carries real numbers the story already has -- an airing calendar, an
episode count, a studio. If the data is not there, there is NO card. A card
is the one element a viewer reads as fact, so inventing its contents is the
worst available failure, and "no data, no card" is cheaper than a plausible
lie.

Rendering prefers ``chart-broll-renderer`` (local, free) when the data is
tabular, and falls back to the registry's ``card`` capability otherwise.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from genlab_core.capabilities import select

logger = logging.getLogger(__name__)

#: Fields that may appear on a card. Anything not here is not card material,
#: however tempting -- scores and rankings move, and a stale number on a card
#: is indistinguishable from a wrong one.
CARD_FIELDS = ("studio", "season", "premiere", "genres", "episodes", "following")


class NoCardData(RuntimeError):
    """The story carries nothing a card could truthfully show."""


@dataclass(frozen=True)
class Card:
    rows: tuple[tuple[str, str], ...]
    title: str
    renderer: str
    cost_usd: float = 0.0
    provenance: str = ""
    extra: dict[str, Any] = field(default_factory=dict, repr=False)


def _fmt(key: str, value: Any) -> str:
    if isinstance(value, list | tuple):
        return ", ".join(str(v) for v in value)
    if key == "following" and isinstance(value, int | float):
        return f"{int(value):,}"
    return str(value)


def build_card(
    title: str, facts: dict[str, Any], *, provenance: str = "", context: str = "fire"
) -> Card:
    """A card from whatever real fields exist, or ``NoCardData``.

    ``provenance`` names where the facts came from and is rendered on the
    card. A number without a source is the shape this refuses to produce.
    """
    rows = tuple(
        (k.replace("_", " ").title(), _fmt(k, facts[k]))
        for k in CARD_FIELDS
        if facts.get(k) not in (None, "", [], ())
    )
    if len(rows) < 2:
        raise NoCardData(
            f"only {len(rows)} usable field(s) for {title!r} — a card needs at "
            "least two real facts. No card is correct here; inventing one is not."
        )
    if not provenance:
        raise NoCardData(
            f"facts for {title!r} carry no provenance — a card states things as "
            "fact and must be able to say where they came from"
        )

    try:
        from genlab_core.media import chart_broll  # noqa: F401

        renderer, cost = "chart-broll-renderer", 0.0
    except ImportError:
        cap = select("card", context=context)
        renderer, cost = cap.ref, (cap.cost_per_unit_usd or 0.0)

    logger.info("[still] card %r via %s ($%.5f): %d rows", title, renderer, cost, len(rows))
    return Card(rows=rows, title=title, renderer=renderer, cost_usd=cost, provenance=provenance)
