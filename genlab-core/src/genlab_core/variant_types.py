"""Blueprint structural variant types — single source of truth.

Layer 3 architecture (2026-07-17). See `[[variant-architecture-roadmap]]`
for the ship plan (S1 foundation → S2 series_part → S3 watch_till_end →
S4 question_reveal → S5 bandit 2D → S6 split_screen → S7 storytime).

## Design

Variant types partition the space of "how a blueprint gets rendered
and published" into orthogonal structural categories. This is
DIFFERENT from ``writing.content_type_hint`` which handles the
THEMATIC dimension (gameplay_clip vs trailer_reaction vs …). A single
blueprint has BOTH — e.g. ``variant_type='series_part'`` ×
``content_type='trailer_reaction'`` is a valid combination.

## Variants

- ``single_clip`` (default, shipped) — current behavior. One trending
  clip + logo overlay + one hook. Empty payload.
- ``series_part`` (S2) — multi-part content with algorithmic hook for
  YT subscribe conversion. Payload: ``{series_id, part_number, total_parts}``.
- ``watch_till_end`` (S3) — hook engineered for retention. Payload: ``{}``.
- ``question_reveal`` (S4) — question hook + delayed answer reveal via
  timed text overlay. Payload: ``{question, reveal}``.
- ``split_screen`` (S6) — two clips composed side-by-side via ffmpeg
  hstack. Payload: ``{clip_a_video_id, clip_b_video_id}``.
- ``storytime`` (S7, blocked on whisper_sync) — narration + timed word
  overlays. Payload: ``{narration_text, tts_provider}``.

## Backward compatibility

Every existing blueprint retroactively defaults to ``single_clip`` +
empty payload via the migration's ``DEFAULT`` clause. Callers that
don't know about variants receive the same behavior as before.

## When adding a new variant

1. Add the string to ``VARIANT_TYPES``.
2. Add payload contract to ``PAYLOAD_CONTRACTS``.
3. Add writer branch in ``video_content_writer`` dispatch.
4. Add renderer branch in ``frame_compositor`` if visual layout changes.
5. Add push_to_backlog branch if pipeline clustering changes.
6. Update memory: `[[variant-architecture-roadmap]]`.
"""

from __future__ import annotations

from typing import Final

# Structural variant types. Bandit arms will be namespaced ``variant:X``.
# Order here is display order — single_clip first as the default.
VARIANT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "single_clip",
        "series_part",
        "watch_till_end",
        "question_reveal",
        "split_screen",
        "storytime",
    }
)

DEFAULT_VARIANT: Final[str] = "single_clip"

# Payload contract per variant. Values are the REQUIRED keys the
# variant_payload JSONB must contain. Empty tuple = no required keys.
# Additional optional keys are allowed (JSONB is open) but this list
# is the documented interface writers/renderers can rely on.
PAYLOAD_CONTRACTS: Final[dict[str, tuple[str, ...]]] = {
    "single_clip": (),
    "series_part": ("series_id", "part_number", "total_parts"),
    "watch_till_end": (),
    "question_reveal": ("question", "reveal"),
    "split_screen": ("clip_a_video_id", "clip_b_video_id"),
    "storytime": ("narration_text",),
}


def is_valid_variant(variant_type: str) -> bool:
    """Return True iff ``variant_type`` is a known variant.

    Used by the storage layer + writer dispatch to defensively check
    incoming values. Unknown variants fall back to ``single_clip`` with
    a WARNING log so silent-fail can't happen (rule #17 sibling).
    """
    return variant_type in VARIANT_TYPES


def validate_payload(variant_type: str, payload: dict) -> list[str]:
    """Return list of missing required keys for a variant's payload.

    Empty list = valid. Callers use this to fail-fast at the boundary
    where a variant is chosen (writer input, push_to_backlog output)
    rather than deep in the renderer where the failure mode is a
    black frame or a broken publish.
    """
    required = PAYLOAD_CONTRACTS.get(variant_type, ())
    return [key for key in required if key not in payload]


def choose_variant(story: dict) -> tuple[str, dict]:
    """Return ``(variant_type, variant_payload)`` for a story using the
    canonical priority chain.

    Layer 3 S4.5 (2026-07-17). Encapsulates the priority ordering that
    was previously enforced by SOURCE ORDER in the writer and push_to_backlog
    wire blocks. Extracting to one function:

    1. Makes the priority chain testable in isolation.
    2. Creates the single hook point that S5 (bandit extension) will
       replace or wrap — bandit-driven variant selection needs ONE
       function to intercept, not three sequential ifs in two call sites.
    3. Future-proofs against accidental source reordering breaking the
       chain silently.

    Canonical priority (highest to lowest):

    1. ``series_part`` — explicit series indicator in title
       (Part N / Episode N / SxxEyy / Chapter N). Strongest algorithmic
       signal for YT (3-5× subscribe rate).
    2. ``question_reveal`` — question-shaped title (Why/How/What... + "?").
       Cognitive-commitment hook drives past scroll decision.
    3. ``watch_till_end`` — compilation-type content (Top N, Highlights,
       Reactions) in 30-90s range. Retention-engineered hook for
       completion rate.
    4. ``single_clip`` — default. Unchanged pipeline behavior.

    Fail-open: any exception in a selector returns ``single_clip`` with
    empty payload. This function is a pure orchestrator — never crashes
    the pipeline regardless of selector state.

    ## Why the existing writer/push_to_backlog wire is NOT refactored to use this

    S1-S4a already committed with the priority chain enforced by
    source order. Those code paths are tested + working. This new
    function ships as an ADDITIVE hook — future S5 bandit work can
    call ``choose_variant`` at its own entry point, and IF an operator
    decides to unify the wire later, the migration is a mechanical
    replacement (one function call instead of three if-blocks).

    Not modifying tested paths tonight keeps deploy risk zero.
    """
    # Local imports — same lazy-import pattern the existing writer +
    # push_to_backlog paths use for these modules. Avoids widening the
    # top-level import surface of variant_types.
    try:
        from genlab_core.writing.series_detector import detect_series

        series_info = detect_series(story)
        if series_info is not None:
            return (
                "series_part",
                {
                    "series_id": series_info.series_id,
                    "part_number": series_info.part_number,
                    "total_parts": series_info.total_parts,
                    "series_title": series_info.series_title,
                    "detection_pattern": series_info.detection_pattern,
                },
            )
    except Exception:
        pass  # fall through to next selector

    try:
        from genlab_core.writing.question_reveal_selector import (
            is_question_reveal_eligible,
        )

        if is_question_reveal_eligible(story):
            return ("question_reveal", {})
    except Exception:
        pass

    try:
        from genlab_core.writing.watch_till_end_selector import (
            is_watch_till_end_eligible,
        )

        if is_watch_till_end_eligible(story):
            return ("watch_till_end", {})
    except Exception:
        pass

    # 2026-07-22 S6 addition — split_screen sits below watch_till_end in
    # priority (weaker mechanistic signal: comparison keyword vs explicit
    # question or compilation frame). If the payload builder returns an
    # empty dict (missing video_id, etc.), fall through to single_clip
    # rather than shipping an invalid payload that fails validate_payload.
    try:
        from genlab_core.writing.split_screen_selector import (
            build_split_screen_payload,
            is_split_screen_eligible,
        )

        if is_split_screen_eligible(story):
            payload = build_split_screen_payload(story)
            if payload:
                return ("split_screen", payload)
    except Exception:
        pass

    # 2026-07-22 S7 writer-only slice — storytime at the bottom of the
    # priority chain, above single_clip default. Requires narrative-arc
    # title signal + 60-120s duration. Payload seeds narration_text from
    # story summary; phase E compositor consumes for TTS + timed overlays.
    # Missing narration source → empty payload → fall through.
    try:
        from genlab_core.writing.storytime_selector import (
            build_storytime_payload,
            is_storytime_eligible,
        )

        if is_storytime_eligible(story):
            payload = build_storytime_payload(story)
            if payload:
                return ("storytime", payload)
    except Exception:
        pass

    return (DEFAULT_VARIANT, {})


__all__ = [
    "DEFAULT_VARIANT",
    "PAYLOAD_CONTRACTS",
    "VARIANT_TYPES",
    "choose_variant",
    "is_valid_variant",
    "validate_payload",
]
