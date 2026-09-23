"""FrameDrift's type, and the ONE function that fits text to the frame.

ANIME-16 §8, §9, §16. Two things live here because they failed together:

* **The brand pair.** Everything before v5 drew in ffmpeg's default sans.
  Typography is the difference between a reel and a channel, and it is the
  single change that touches every frame at once — slams, hook, captions and
  covers.
* **One fit function.** ``x=(w-text_w)/2`` with no width budget has now
  clipped text on THREE separate paths: captions (ANIME-12), the hook
  (ANIME-14) and the cover (ANIME-16 §16). Each was fixed where it was found
  and the next path was written without the fix. There is one implementation
  here and every caller uses it.

Widths are MEASURED with the actual font file, not derived from a
character-ratio constant. A condensed display face and a rounded body face
have very different ratios — Bebas Neue is roughly 0.35 of its point size per
character where Nunito is roughly 0.52 — so a single constant is wrong for at
least one of them, and "wrong for one of them" is exactly how a headline ends
up 40% over the frame.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

FRAME_W = 1080
FRAME_H = 1920
#: Text may occupy this share of the frame width.
MAX_TEXT_WIDTH_FRAC = 0.90

_FONT_DIR = Path(__file__).resolve().parents[4] / "assets" / "fonts"


class FontMissing(RuntimeError):
    """A brand font is not on disk. Never silently fall back to the default —
    that is how every reel shipped in the default sans without anyone
    noticing."""


@dataclass(frozen=True)
class BrandType:
    """A niche's type pair."""

    display: Path  # slams, hook, cover headline — condensed, all-caps
    body: Path  # captions — rounded, readable small
    name: str = ""

    def check(self) -> None:
        for role, p in (("display", self.display), ("body", self.body)):
            if not p.exists():
                raise FontMissing(
                    f"{self.name} {role} font missing at {p}. Fetch it into "
                    f"assets/fonts/ — do not fall back to the system default, "
                    f"which is what shipped for four versions."
                )


#: Anton for slams and hooks: heavier than Bebas at the same height, which is
#: what a 25-30% width slam over footage needs. Nunito for captions.
FRAMEDRIFT = BrandType(
    display=_FONT_DIR / "Anton-Regular.ttf",
    body=_FONT_DIR / "Nunito-Variable.ttf",
    name="FrameDrift",
)

_BY_NICHE = {"anime": FRAMEDRIFT}


def for_niche(niche_id: str) -> BrandType:
    bt = _BY_NICHE.get(niche_id)
    if bt is None:
        raise FontMissing(f"no brand type registered for niche {niche_id!r}")
    bt.check()
    return bt


@lru_cache(maxsize=64)
def _pil_font(path: str, size: int):
    from PIL import ImageFont

    return ImageFont.truetype(path, size)


def text_width_px(text: str, font: Path, size: int) -> float:
    """Measured advance width of ``text`` at ``size``, in pixels.

    Uses the real font metrics. A ratio constant cannot be right for two faces
    at once, and being wrong for one of them is how a headline ends up 40%
    over the frame.
    """
    try:
        return float(_pil_font(str(font), size).getlength(text))
    except Exception as exc:  # noqa: BLE001
        # Fail loud-ish: fall back to a conservative ratio and SAY so, rather
        # than returning a confident wrong number.
        logger.warning(
            "[type] could not measure %r with %s (%s); using 0.60 ratio", text[:20], font.name, exc
        )
        return len(text) * 0.60 * size


@dataclass(frozen=True)
class Fitted:
    lines: list[str]
    size: int
    widest_px: float
    font: Path

    @property
    def fits(self) -> bool:
        return self.widest_px <= FRAME_W * MAX_TEXT_WIDTH_FRAC


def fit(
    text: str,
    font: Path,
    *,
    max_size: int,
    min_size: int = 40,
    max_lines: int = 2,
    width_frac: float = MAX_TEXT_WIDTH_FRAC,
) -> Fitted:
    """Split into at most ``max_lines`` and return the LARGEST size that fits.

    Largest, not the first that fits: one line at 69 px fits and two lines at
    118 px fit better, and the hook is the biggest text in the reel. Balanced
    by measured WIDTH rather than word count, because a two-word line and a
    four-word line can be the same length on screen.
    """
    budget = FRAME_W * width_frac
    words = text.split()
    if not words:
        return Fitted([], min_size, 0.0, font)

    best: tuple[int, int, list[str]] | None = None
    for n_lines in range(1, max_lines + 1):
        if n_lines == 1:
            cands = [[" ".join(words)]]
        else:
            cands = [[" ".join(words[:k]), " ".join(words[k:])] for k in range(1, len(words))]
        for lines in cands:
            if any(not ln for ln in lines):
                continue
            size = max_size
            while size >= min_size:
                widest = max(text_width_px(ln, font, size) for ln in lines)
                if widest <= budget:
                    break
                size -= 2
            if size < min_size:
                continue
            widest = max(text_width_px(ln, font, size) for ln in lines)
            spread = max(text_width_px(ln, font, size) for ln in lines) - min(
                text_width_px(ln, font, size) for ln in lines
            )
            key = (size, -int(spread), lines)
            if best is None or key[:2] > best[:2]:
                best = key
    if best:
        lines = best[2]
        size = best[0]
        return Fitted(lines, size, max(text_width_px(ln, font, size) for ln in lines), font)

    # Nothing fits inside max_lines at min_size — shrink rather than clip.
    half = len(words) // 2 or 1
    lines = [" ".join(words[:half]), " ".join(words[half:])] if len(words) > 1 else [words[0]]
    size = min_size
    while size > 12 and max(text_width_px(ln, font, size) for ln in lines) > budget:
        size -= 2
    logger.warning(
        "[type] %r does not fit %d lines at >=%dpx; shrunk to %dpx",
        text[:40],
        max_lines,
        min_size,
        size,
    )
    return Fitted(lines, size, max(text_width_px(ln, font, size) for ln in lines), font)


def target_width_fit(
    text: str, font: Path, *, target_frac: float, max_lines: int = 1, max_size: int = 200
) -> Fitted:
    """Fit so the text occupies about ``target_frac`` of the frame width.

    §9 asks slams to be 25-30% of frame width; that is a MINIMUM size
    requirement, not a maximum, and the plain ``fit`` only enforces the
    ceiling. This grows the size until the text reaches the target or the
    90% ceiling stops it, whichever comes first.
    """
    want = FRAME_W * target_frac
    words = text.split()
    lines = (
        [" ".join(words)]
        if max_lines == 1
        else fit(text, font, max_size=max_size, max_lines=max_lines).lines
    )
    size = 12
    while size < max_size:
        widest = max(text_width_px(ln, font, size + 2) for ln in lines)
        if widest > min(want, FRAME_W * MAX_TEXT_WIDTH_FRAC):
            break
        size += 2
    return Fitted(lines, size, max(text_width_px(ln, font, size) for ln in lines), font)
