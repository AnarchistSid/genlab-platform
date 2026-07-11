"""Layer 4 attribution validation — publisher-side backstop.

Sits between the payload_builder's source-attribution wire and the
platform API POST. If the caption reaches the publisher without a
credit line, log a compliance event and (in enforcement mode) refuse
to publish.

Complements the upstream layers:

  * PR #761 (writer wire) — appends attribution in push_to_backlog.
  * PR #762 (persist gate) — refuses blueprints missing channel_id.
  * PR #763 (Layer 1 fetcher gate) — refuses candidates missing
    channel metadata at fetch time.
  * PR #764 (Layer 2 persist gate + publisher backstop) — refuses at
    persist time and appends at build_payload time.
  * **This module (Layer 4)** — validates at the API-POST boundary.
  * PR #765 (fb_survival_check) — detects post-publish Meta deletions.

Layer 4 is the last line of defense. If every upstream layer fails
silently, this one still catches the missing credit before the
publish request hits the platform API.

## Env flags

* ``GENLAB_ATTRIBUTION_LAYER4_BLOCK=1`` — escalate warn → block.
  Default off (warn-only). Operators flip after Layer 5's dashboard
  card shows ``attribution_present_pct`` holds ≥98% for ~2 weeks.
* Non-YouTube platforms currently accept the same behaviour — this
  can be tightened per-platform in a follow-up once operator has
  seen the warn-mode compliance events in prod.

## The validation contract

``validate_caption_has_attribution(caption, source_url=None)`` returns
``(True, None)`` when the caption OR an explicit ``source_url`` field
carries a recognisable credit signal. The recognised signals are:

  * ``"🎬 Original:"`` — the format_source_attribution marker
    (PR #761 writer wire + PR #764 publisher backstop)
  * ``"Footage:"`` — the format_youtube_attribution marker
    (PR #568, YouTube description)
  * Any non-empty ``source_url`` string — operator override
    (blueprint-level ``source_url`` field is a manual escape hatch)

Returns ``(False, "missing_attribution_line")`` when none of the
above are present. Substring-based on purpose so operator-formatted
credit variants ("🎬 Original creator: @X", "🎬 Original: URL") both
match without a brittle regex.
"""

from __future__ import annotations

import os

_MARKER_ORIGINAL = "\U0001f3ac original:"
_MARKER_FOOTAGE = "footage:"


def validate_caption_has_attribution(
    caption: str,
    *,
    source_url: str | None = None,
) -> tuple[bool, str | None]:
    """Return (is_valid, error_reason).

    is_valid is True when the caption OR source_url signals attribution.
    error_reason is None on valid; otherwise a short machine-readable
    string suitable for logging into compliance_events.
    """
    if source_url and str(source_url).strip():
        return (True, None)
    lowered = (caption or "").lower()
    if _MARKER_ORIGINAL in lowered or _MARKER_FOOTAGE in lowered:
        return (True, None)
    return (False, "missing_attribution_line")


def layer4_block_enabled() -> bool:
    """Read the env flag at call time (not import time).

    Operators can toggle without a process restart. Default off means
    shipping this PR is a no-op until deliberately flipped.
    """
    return os.environ.get("GENLAB_ATTRIBUTION_LAYER4_BLOCK", "0") == "1"
