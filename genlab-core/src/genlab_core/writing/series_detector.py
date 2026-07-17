"""Series detection from video/story metadata — pure function, no I/O.

Layer 3 S2 (2026-07-17). Detects whether a source video title indicates
part of a series — "Part N", "Episode N", "Chapter N", "S02E05" —
and returns structured `SeriesInfo` for downstream consumers.

## Design

Two consumers call `detect_series(story)` independently:

1. **`video_content_writer.write_video_content`** — reads the result,
   injects a SERIES CONTEXT section into the LLM prompt so the writer
   crafts a hook that references the series arc.

2. **`push_to_backlog`** — reads the same result, sets
   `blueprint["variant_type"] = "series_part"` +
   `blueprint["variant_payload"] = {series_id, part_number, total_parts}`
   so the bandit reward loop attributes performance to the series
   dimension.

Both call sites are cheap (regex + str.split). Duplication is
intentional — decoupling avoids state passed between stages.

## Detection principles

- **Explicit indicators only.** We match ``Part 3``, ``Episode 22``,
  ``S03E11``, ``Chapter 5``, ``Pt. 2`` — NOT ``Top 5``, ``3 things``,
  ``1st place``, or ``Matrix 3``. False positives here would
  mis-mark ordinary content as series and corrupt the reward attribution.
- **Word boundaries** prevent matching inside longer words
  (``Chapter`` matches, ``chaptered`` does not).
- **Case-insensitive** — YouTube titles are inconsistent.
- **Prefer more-specific pattern** — ``S03E11`` matches before ``E11``
  standalone, avoids double-attribution.
- **Series ID derivation** — canonical form: ``sha1(channel_id + "|" +
  normalized_series_title)[:12]``. Stable across time; same series
  gets the same ID whenever detected. Falls back to ``sha1(title)``
  when channel_id absent.

## What this doesn't do (yet)

- Doesn't verify part N is actually part of a series (i.e. we've
  seen part N-1 before). That would require DB lookup — deferred
  to S2 stretch. Current design: any explicit "Part N" indicator
  is treated as series regardless of history.
- Doesn't detect implicit series (same recurring premise, same
  presenter). LLM-based classification is a future intervention.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SeriesInfo:
    """Structured result of series detection.

    ``series_id`` is stable across detections — same source series
    always gets the same ID. Consumers can join on this to reason
    about series-level performance.
    """

    series_id: str
    part_number: int
    total_parts: int  # best-effort; equals part_number when "of N" absent
    series_title: str  # normalized title without part indicator
    detection_pattern: str  # which regex matched — useful for debugging


# Order matters: try MORE SPECIFIC patterns first so `S03E11` is not
# double-counted by the standalone `E11` pattern.
#
# Each entry: (name, compiled_pattern, part_group_index, total_group_index)
#   - name: human-readable label for debugging + telemetry
#   - part_group_index: regex group holding the part number
#   - total_group_index: regex group holding "of N" total (or None)
_PATTERNS: list[tuple[str, re.Pattern, int, int | None]] = [
    # "S03E11" or "S3E11" — season/episode notation. Most specific.
    (
        "season_episode",
        re.compile(r"\bS(?:eason)?\s?(\d+)\s?E(?:pisode)?\s?(\d+)\b", re.IGNORECASE),
        2,  # episode number is the part
        None,  # no "of N" support for this format
    ),
    # "Part 3 of 5" or "Part 3/5" — slash form has no required whitespace
    (
        "part_of",
        re.compile(
            r"\b(?:Part|Pt\.?)\s+(\d+)(?:\s+of\s+|\s*/\s*)(\d+)\b",
            re.IGNORECASE,
        ),
        1,
        2,
    ),
    # "Episode 22 of 24" or "Ep 22/24"
    (
        "episode_of",
        re.compile(
            r"\b(?:Episode|Ep\.?)\s+(\d+)(?:\s+of\s+|\s*/\s*)(\d+)\b",
            re.IGNORECASE,
        ),
        1,
        2,
    ),
    # "Chapter 5 of 10" or "Ch 5/10"
    (
        "chapter_of",
        re.compile(
            r"\b(?:Chapter|Ch\.?)\s+(\d+)(?:\s+of\s+|\s*/\s*)(\d+)\b",
            re.IGNORECASE,
        ),
        1,
        2,
    ),
    # "Part 3" (no total)
    (
        "part_only",
        re.compile(r"\b(?:Part|Pt\.?)\s+(\d+)\b", re.IGNORECASE),
        1,
        None,
    ),
    # "Episode 22" (no total)
    (
        "episode_only",
        re.compile(r"\b(?:Episode|Ep\.?)\s+(\d+)\b", re.IGNORECASE),
        1,
        None,
    ),
    # "Chapter 5" (no total)
    (
        "chapter_only",
        re.compile(r"\b(?:Chapter|Ch\.?)\s+(\d+)\b", re.IGNORECASE),
        1,
        None,
    ),
]


def _normalize_series_title(title: str, matched_span: tuple[int, int]) -> str:
    """Return the title with the part indicator excised + cleaned.

    Removes the matched part-indicator span AND common separator
    punctuation left dangling. "Part 3: The Ending" → "The Ending",
    "Cyberpunk Ep 5 - Recap" → "Cyberpunk Recap".
    """
    start, end = matched_span
    stripped = title[:start] + title[end:]
    # Collapse double spaces + strip separator punctuation the pattern
    # commonly leaves behind: colons, dashes, pipes, brackets.
    stripped = re.sub(r"\s+", " ", stripped)
    stripped = re.sub(r"[\s\-:|\[\]()]+", " ", stripped)
    return stripped.strip()


def _derive_series_id(channel_id: str | None, series_title: str) -> str:
    """Compute stable ID from channel + normalized title.

    ``sha1[:12]`` is short enough for logs + queries while providing
    sufficient collision resistance for realistic per-niche series
    volumes (thousands of distinct series would collide at ~1 in 10^7).
    """
    key = f"{channel_id or ''}|{series_title.lower()}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def detect_series(story: dict) -> SeriesInfo | None:
    """Return SeriesInfo if the story title signals part of a series.

    Reads ``story['title']`` + optionally ``story['channel_id']``
    (from source video metadata). Returns None when no pattern matches
    or when required fields are missing.

    Fail-open: any exception returns None + logs at DEBUG. Series
    detection is a soft optimization — a false negative just publishes
    as ``single_clip``, which is the safe default.
    """
    title = story.get("title") or ""
    if not title:
        return None

    channel_id = story.get("channel_id") or story.get("source_channel_id")

    try:
        for name, pattern, part_idx, total_idx in _PATTERNS:
            match = pattern.search(title)
            if not match:
                continue

            part_number = int(match.group(part_idx))
            total_parts = int(match.group(total_idx)) if total_idx else part_number
            series_title = _normalize_series_title(title, match.span())

            # Sanity check — reject nonsense part numbers. Legitimate
            # series max out at maybe a few hundred episodes; anything
            # above that is likely a false positive (year, view count).
            if part_number < 1 or part_number > 500:
                logger.debug(
                    "[series_detector] out-of-range part_number=%d in title=%r — skipping",
                    part_number,
                    title,
                )
                continue

            if total_parts < part_number:
                # "Part 5 of 3" makes no sense — fall back to part_number
                total_parts = part_number

            series_id = _derive_series_id(channel_id, series_title)
            return SeriesInfo(
                series_id=series_id,
                part_number=part_number,
                total_parts=total_parts,
                series_title=series_title,
                detection_pattern=name,
            )
    except (ValueError, IndexError) as exc:
        logger.debug("[series_detector] detection error for title=%r: %s", title, exc)
        return None

    return None


def format_series_prompt_section(series_info: SeriesInfo) -> str:
    """Build the writer prompt section for a detected series.

    Injected into ``video_content_writer.write_video_content`` alongside
    the ``style_hint`` and ``content_angle_hint``. Follows the same
    "gentle prompt-side steering" pattern as those: informs the LLM
    of context without overriding its editorial judgment on hook wording.
    """
    # Different framing when we have a total vs not
    if series_info.total_parts > series_info.part_number:
        position = f"Part {series_info.part_number} of {series_info.total_parts}"
    else:
        position = f"Part {series_info.part_number}"

    return (
        f"\nSERIES CONTEXT ({series_info.detection_pattern}): This video is\n"
        f"  {position} of the series '{series_info.series_title}'.\n"
        "  Your hook must acknowledge the series continuity: reference the\n"
        "  arc, hint at previous parts, or tease what comes next. Viewers\n"
        "  who saw earlier parts feel rewarded; new viewers feel FOMO for\n"
        "  what they missed. This is the YouTube algorithm's #1 subscribe\n"
        "  trigger — do not treat it as a standalone clip.\n"
    )


__all__ = [
    "SeriesInfo",
    "detect_series",
    "format_series_prompt_section",
]
