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
from pathlib import Path
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


def render_card(
    card: Card,
    out_path: Path,
    *,
    kit: dict[str, Any],
    duration_s: float = 3.0,
    timeout_s: int = 180,
    outro_handle: str | None = None,
    outro_label: str | None = None,
) -> bool:
    """Draw the card locally with drawtext. Free, and exact.

    NOT a generated image. `chart-broll-renderer` takes numeric bars and this
    card is mostly text; the registry's `card` capability would draw it for
    $0.045 — but a model asked to render "Studio Hibari / 24 episodes" can
    produce "Studio Hibari / 26 episodes", and a card is the one element a
    viewer reads as fact. Drawing the strings directly means the numbers on
    screen are the numbers in `card.rows`, by construction.

    The card also CARRIES the outro. Drawing a separate handle/CTA slate over
    the timeline put three layers on one frame -- card rows, a caption still
    running, and the outro -- overlapping into something unreadable. Making
    the card the end slate removes that collision by construction instead of
    by timing arithmetic, which is the thing that went wrong.
    """
    import subprocess

    from genlab_core.still.overlay import _textfile

    accent = kit.get("accent", "#FFFFFF").lstrip("#")
    lines = [(f"{k}", f"{v}") for k, v in card.rows]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    textdir = out_path.parent / f".{out_path.stem}_text"

    # textfile=, not text=: an apostrophe in a title ("Journey's End") ends the
    # quoted value early and the next comma is parsed as a filter separator.
    # Same defect the caption path shipped with; one module away from here.
    _n = [0]

    def esc(t: str) -> str:
        _n[0] += 1
        return _textfile(textdir, f"card_{_n[0]:03d}", t)

    parts = [
        f"drawtext=textfile='{esc(card.title)}':fontsize=64:fontcolor=0x{accent}"
        f":x=(w-text_w)/2:y=h*0.26",
    ]
    y = 0.38
    for key, val in lines:
        parts.append(
            f"drawtext=textfile='{esc(key)}':fontsize=34:fontcolor=white@0.62:x=w*0.18:y=h*{y:.3f}"
        )
        parts.append(
            f"drawtext=textfile='{esc(val)}':fontsize=38:fontcolor=white:x=w*0.52:y=h*{y:.3f}"
        )
        y += 0.055
    parts.append(
        f"drawtext=textfile='{esc('source: ' + card.provenance)}':fontsize=26"
        f":fontcolor=white@0.45:x=(w-text_w)/2:y=h*{y + 0.04:.3f}"
    )
    if outro_handle:
        parts.append(
            f"drawtext=textfile='{esc(outro_handle)}':fontsize=64"
            f":fontcolor=0x{accent}:x=(w-text_w)/2:y=h*0.80"
        )
    if outro_label:
        parts.append(
            f"drawtext=textfile='{esc(outro_label)}':fontsize=30"
            f":fontcolor=white@0.55:x=(w-text_w)/2:y=h*0.855"
        )

    cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c=0x101018:s=1080x1920:d={duration_s:.2f}:r=30",
        "-vf",
        ",".join(parts),
        "-c:v",
        "libx264",
        "-crf",
        "20",
        "-preset",
        "fast",
        "-pix_fmt",
        "yuv420p",
        "-colorspace",
        "bt709",
        str(out_path),
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    if p.returncode != 0:
        logger.warning("[still] card render failed: %s", p.stderr[-300:])
        return False
    return True
