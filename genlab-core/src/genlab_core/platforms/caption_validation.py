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

## The validation contract (post-2026-07-11 audit — TIGHTENED)

``validate_caption_has_attribution(caption, source_url=None)`` now
requires the CAPTION ITSELF to carry a credit marker. Recognised
markers in the caption are:

  * ``"🎬 Original:"`` — format_source_attribution marker (writer
    wire + publisher backstop output)
  * ``"Footage:"`` — format_youtube_attribution marker (YouTube
    description path)

The ``source_url`` parameter is retained for backward compatibility
but **no longer satisfies validation on its own**. Rationale: today's
gaming publish demonstrated the audience-facing failure mode of the
old behaviour — the blueprint had ``video_url`` populated (Twitch
directory URL), so the old ``source_url`` escape hatch marked the
caption as "valid attribution" and the publish went out. Users saw
no credit line at all. Internal metric (Layer 5) said "attributed"
but real audiences saw nothing.

Fix: audience-facing validation must be strict about the CAPTION
content. ``source_url`` becomes secondary — used only when the
caller wants to ALSO log that a source URL is available, not as
a bypass path.

Returns ``(False, "missing_attribution_line")`` when the caption
lacks a marker. Substring-based on purpose so operator-formatted
credit variants ("🎬 Original creator: @X", "🎬 Original: URL") both
match without a brittle regex.
"""

from __future__ import annotations

import os

_MARKER_ORIGINAL = "\U0001f3ac original:"
_MARKER_FOOTAGE = "footage:"
# 2026-08-18 (task #192): AI-generated content backfill marker. When
# ``pruna_video_client.generate_backfill_clip`` produces a video for the
# anime dedup canary, there is no external creator to credit — the
# audience-facing attribution is "we made this with an AI model." This
# marker enforces that honesty rather than dressing up AI content with
# a false "🎬 Original:" line pointing at a Gen Lab handle.
_MARKER_AI_GENERATED = "\U0001f916 ai-generated"


def validate_caption_has_attribution(
    caption: str,
    *,
    source_url: str | None = None,  # noqa: ARG001 — reserved for future
) -> tuple[bool, str | None]:
    """Return (is_valid, error_reason).

    is_valid is True ONLY when the CAPTION contains a credit marker.
    ``source_url`` is kept in the signature for backward compat but
    no longer satisfies validation on its own (post-2026-07-11
    audit — Twitch directory URLs were satisfying the old check
    while shipping empty-of-credit captions to users).

    Recognised markers (all case-insensitive substring match):
      * ``"🎬 Original:"`` — external creator credit
      * ``"Footage:"``    — YouTube attribution
      * ``"🤖 AI-generated"`` — AI-generated content (task #192)

    error_reason is None on valid; otherwise a short machine-readable
    string suitable for logging into compliance_events.
    """
    lowered = (caption or "").lower()
    # 2026-07-21 Agent 1 side-finding: substring-only check accepts
    # truncated markers like `🎬 Original: @` (empty handle) or
    # `🎬 Original: @OpenAI —` (missing URL). Live-observed in tonight's
    # 4 scheduled blueprints: gaming + sports have NO marker (blocked),
    # ai_creators has `@OpenAI` handle but empty URL (passes lax check).
    # Now require at least ONE non-whitespace character after the
    # marker (handle OR URL). Retains substring compatibility for
    # legit variants like `🎬 Original creator: @X`.
    for marker in (_MARKER_ORIGINAL, _MARKER_FOOTAGE, _MARKER_AI_GENERATED):
        idx = lowered.find(marker)
        if idx == -1:
            continue
        tail = lowered[idx + len(marker):].strip()
        # Must have SOMETHING after marker beyond a bare '@' handle-prefix
        if len(tail) >= 3 and tail != "@":
            return (True, None)
    return (False, "missing_attribution_line")


def layer4_block_enabled() -> bool:
    """Read the env flag at call time (not import time).

    Operators can toggle without a process restart. Default off means
    shipping this PR is a no-op until deliberately flipped.
    """
    return os.environ.get("GENLAB_ATTRIBUTION_LAYER4_BLOCK", "0") == "1"
