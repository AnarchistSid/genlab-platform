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


__all__ = [
    "DEFAULT_VARIANT",
    "PAYLOAD_CONTRACTS",
    "VARIANT_TYPES",
    "is_valid_variant",
    "validate_payload",
]
