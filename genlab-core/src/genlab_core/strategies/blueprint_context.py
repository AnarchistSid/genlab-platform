"""Single writer for the render-time ``blueprint_context``.

Every niche render strategy hands a ``blueprint_context`` dict to
``apply_post_render_transformations``, and the transformation orchestrator reads
named keys back out of it. That makes the dict a **contract between two layers
written by different people at different times**, enforced by nothing.

It drifted, and the drift was invisible for months. The base class assembled
twelve keys including the whole NARR family; BlackboxBrief and gaming each built
their own four-key dict inline (``hook``, ``caption_segments``, ``title``,
``summary``) and never carried a single narration field. The orchestrator duly
read ``ctx.get("narration_audio_path")`` from a dict that structurally could not
contain it, found nothing, and fell through to the two-input mix.

The cost of that shape: BlackboxBrief is the **only** niche narration was
enabled for. Two prior fixes — the NARR-05 stage reordering and the
``media["audio_path"]`` mapping — both landed on the base-class path, which is
the one path BB does not take. Every fix was correct and none of them reached
the canary. The bisection that finally located it had to prove the mixer worked
by re-executing its own argv before the missing input became the only remaining
explanation.

So: one builder, here. Subclasses that need extra keys pass ``extra``; they do
not assemble the dict themselves. ``test_blueprint_context_contract.py`` asserts
every strategy routes through this function and that the result covers every key
the orchestrator reads.
"""
from __future__ import annotations

from typing import Any

# Keys the transformation orchestrator reads back out of the context. Kept here
# so the contract test can import ONE list rather than re-deriving it, and so a
# new consumer key fails the test until a producer supplies it.
ORCHESTRATOR_KEYS: tuple[str, ...] = (
    "narration_audio_path",
    "narration_degraded",
    "narration_degraded_reason",
    "narration_expected",
    "narration_script",
    "variant_type",
)


def build_blueprint_context(
    story: dict[str, Any],
    *,
    hook: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the render-time context for one story.

    Reads ``story["content"]`` and ``story["media"]`` rather than taking them as
    arguments, because every prior divergence started with a caller passing its
    own idea of where those live.

    Args:
        story: the pipeline story dict, post-GenerateAudio.
        hook: the hook text as the caller resolved it — the one genuinely
            caller-specific value, since strategies differ in how they pick it.
        extra: additional niche-specific keys. Merged last, but cannot override
            an orchestrator key: a niche silently shadowing ``narration_*`` is
            the exact failure this module exists to prevent.
    """
    content = story.get("content") if isinstance(story.get("content"), dict) else {}
    media = story.get("media") if isinstance(story.get("media"), dict) else {}

    ctx: dict[str, Any] = {
        "hook": hook,
        "caption_segments": content.get("caption_segments"),
        "title": story.get("title", ""),
        "summary": story.get("summary", ""),
        # NARR-01: the VO path GenerateAudio published. Empty until that stage
        # runs, which is why stage ORDER and this mapping are both load-bearing
        # — NARR-05 fixed the first and it did not help, because BB never
        # reached the code carrying the second.
        "narration_audio_path": media.get("audio_path"),
        # NARR-05: did the gate actually ask for narration on this story?
        # Lets the orchestrator distinguish "canary reel lost its VO" from
        # "this niche never wanted one".
        "narration_expected": bool(content.get("narration_expected", False)),
        # NARR-13: an expectation signal that does NOT originate in the handoff
        # being policed. Written by the writer a stage earlier, so it survives
        # when narration_expected does not.
        "narration_script": str(content.get("narration_script", "") or ""),
        "narration_degraded": bool(content.get("narration_degraded", False)),
        "narration_degraded_reason": content.get("narration_degraded_reason", ""),
        "variant_type": content.get("variant_type") or story.get("variant_type"),
    }

    for key, value in (extra or {}).items():
        if key in ORCHESTRATOR_KEYS:
            continue
        ctx[key] = value
    return ctx
