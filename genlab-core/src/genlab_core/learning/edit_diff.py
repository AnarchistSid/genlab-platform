"""Operator edit diff extraction (Lever M).

When operators edit a blueprint via the dashboard (PATCH to
``/blueprints/{id}/content``), today the edit is persisted but the
DIFF between AI-generated and operator-edited content is not extracted
or aggregated anywhere. Operators learn "operators most often correct
hooks (62% of edits), titles (28%), captions (10%)" only by manual
inspection through the UI.

Lever M ships the structured-diff primitive. Given a ``before`` dict
(AI-generated) and ``after`` dict (operator-edited), produces per-
field ``EditDiff`` records that downstream aggregation can group by
field, count by operation type, and feed back into prompt tuning.

Combined with Lever B (per-rejection-reason breakdown, shipped 2026-06-21
late evening), Lever O (post-bomb RCA), and the Lever M analyzer, the
system has a complete picture of WHERE operators intervene:

- Lever B: WHY operators REJECT (6 categorical reasons)
- Lever O: WHY published posts BOMB (LLM-judged RCA)
- Lever M: WHERE operators EDIT (per-field diff aggregation)

This module is pure logic. Wire-up (dashboard middleware that
captures before/after on PATCH + writes to an ``operator_edits``
table) is a follow-up PR — same "ship the primitive first" pattern
that proved itself across the I-suite.

## Edit operation taxonomy

- **unchanged**: ``before == after`` (exact string match after strip)
- **added**: ``before`` is empty/blank, ``after`` has content
- **removed**: ``before`` has content, ``after`` is empty/blank
- **reworded**: both have content, strings differ — the primary
  operator-intervention signal

## Edit-distance ratio

The ``edit_distance_ratio`` field is a ``[0, 1]`` normalized
Levenshtein distance:

  ratio = levenshtein(before, after) / max(len(before), len(after))

- 0.0 = identical strings
- ~0.1-0.3 = minor edits (typo fix, word swap)
- ~0.5+ = substantial rewrites
- 1.0 = completely different content

Combined with the operation classification, this lets aggregations
distinguish "operators tweaked the hook 30%" from "operators
rewrote the hook from scratch".

Run via:
    python -m genlab_core.learning.edit_diff
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Final

logger = logging.getLogger(__name__)


# Default fields to diff. Callers can pass a custom list via the
# ``fields`` kwarg on ``diff_blueprint``. Defaults cover the per-niche
# canonical content fields written by ``base_writing.py`` + the per-
# platform overrides emitted by the platform adapter layer.
_DEFAULT_DIFF_FIELDS: Final[list[str]] = [
    "hook",
    "title",
    "caption",
    "yt_title",
    "ig_caption",
    "fb_caption",
    "x_caption",
    "threads_caption",
    "tiktok_caption",
    "tags",
]

# Operation taxonomy — closed set so downstream aggregations don't get
# polluted by free-text or new categories added without thought.
OPERATION_UNCHANGED: Final[str] = "unchanged"
OPERATION_ADDED: Final[str] = "added"
OPERATION_REMOVED: Final[str] = "removed"
OPERATION_REWORDED: Final[str] = "reworded"


@dataclass(frozen=True)
class EditDiff:
    """One per-field diff record.

    Aggregations group by ``field`` (which fields are edited most),
    by ``operation`` (added vs reworded vs removed), and by
    ``edit_distance_ratio`` (substantial rewrites vs minor tweaks).
    """

    blueprint_id: str
    field: str
    before: str
    after: str
    operation: str  # one of OPERATION_*
    char_delta: int  # len(after) - len(before)
    edit_distance_ratio: float  # 0..1


def _strip(value: object) -> str:
    """Coerce + strip an arbitrary blueprint value to a comparable string.

    None / int / list etc. get str()'d so the diff doesn't blow up on
    surprising types. Tags lists become ``str(list)`` — which is fine
    for diff purposes (a tag added/removed produces a string change).
    """
    if value is None:
        return ""
    return str(value).strip()


def classify_edit(before: str, after: str) -> str:
    """Classify the relationship between two field values.

    Pure function. Returns one of the OPERATION_* constants.

    Edge cases:
    - both empty → unchanged
    - identical (after strip) → unchanged
    - before empty, after has content → added
    - before has content, after empty → removed
    - both have content + differ → reworded
    """
    b = _strip(before)
    a = _strip(after)
    if b == a:
        return OPERATION_UNCHANGED
    if not b and a:
        return OPERATION_ADDED
    if b and not a:
        return OPERATION_REMOVED
    return OPERATION_REWORDED


def compute_edit_distance_ratio(before: str, after: str) -> float:
    """Normalized Levenshtein distance in [0, 1].

    Uses a small pure-Python implementation so the module has no extra
    dependencies. For typical hook/title/caption lengths (~50-500
    chars), the O(n*m) DP is fast enough — microseconds per call.

    Edge cases:
    - both empty → 0.0 (identical)
    - either empty + other has content → 1.0 (completely different)
    - identical → 0.0
    """
    b = _strip(before)
    a = _strip(after)
    if b == a:
        return 0.0
    if not b or not a:
        return 1.0

    # Standard DP Levenshtein
    m, n = len(b), len(a)
    # Two-row rolling array — O(n) memory
    prev = list(range(n + 1))
    curr = [0] * (n + 1)
    for i in range(1, m + 1):
        curr[0] = i
        for j in range(1, n + 1):
            cost = 0 if b[i - 1] == a[j - 1] else 1
            curr[j] = min(
                curr[j - 1] + 1,  # insertion
                prev[j] + 1,  # deletion
                prev[j - 1] + cost,  # substitution
            )
        prev, curr = curr, prev

    distance = prev[n]
    longer = max(m, n)
    return distance / longer if longer else 0.0


def diff_blueprint(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    blueprint_id: str = "",
    fields: list[str] | None = None,
    include_unchanged: bool = False,
) -> list[EditDiff]:
    """Compute per-field EditDiffs between two blueprint dicts.

    ``fields`` defaults to ``_DEFAULT_DIFF_FIELDS``. Fields missing
    from BOTH dicts are skipped (no edit possible). Fields present in
    only one dict are diffed as added/removed.

    By default, ``unchanged`` operations are excluded from the output
    (the primary use case is aggregating EDITS — unchanged fields are
    noise). Pass ``include_unchanged=True`` to get the complete diff
    set for testing / audit purposes.

    Pure function — no I/O.
    """
    target_fields = fields if fields is not None else _DEFAULT_DIFF_FIELDS
    diffs: list[EditDiff] = []
    for field in target_fields:
        # Skip fields absent from BOTH — no edit possible
        if field not in before and field not in after:
            continue
        b_val = _strip(before.get(field))
        a_val = _strip(after.get(field))
        operation = classify_edit(b_val, a_val)
        if not include_unchanged and operation == OPERATION_UNCHANGED:
            continue
        ratio = compute_edit_distance_ratio(b_val, a_val)
        diffs.append(
            EditDiff(
                blueprint_id=blueprint_id,
                field=field,
                before=b_val,
                after=a_val,
                operation=operation,
                char_delta=len(a_val) - len(b_val),
                edit_distance_ratio=round(ratio, 4),
            )
        )
    return diffs


def summarize_edits(diffs: list[EditDiff]) -> dict[str, Any]:
    """Aggregate a list of EditDiffs into operator-readable summary.

    Returns:
        {
            "total_edits": N,
            "by_field": {field → count},
            "by_operation": {operation → count},
            "total_char_delta": int,
            "avg_edit_distance_ratio": float,
        }

    Pure function — no I/O. The summary shape is stable so dashboards
    can render aggregate cards without per-period diff parsing.
    """
    if not diffs:
        return {
            "total_edits": 0,
            "by_field": {},
            "by_operation": {},
            "total_char_delta": 0,
            "avg_edit_distance_ratio": 0.0,
        }

    by_field: dict[str, int] = {}
    by_operation: dict[str, int] = {}
    total_char_delta = 0
    total_ratio = 0.0

    for d in diffs:
        by_field[d.field] = by_field.get(d.field, 0) + 1
        by_operation[d.operation] = by_operation.get(d.operation, 0) + 1
        total_char_delta += d.char_delta
        total_ratio += d.edit_distance_ratio

    return {
        "total_edits": len(diffs),
        "by_field": by_field,
        "by_operation": by_operation,
        "total_char_delta": total_char_delta,
        "avg_edit_distance_ratio": round(total_ratio / len(diffs), 4),
    }


if __name__ == "__main__":
    # CLI: demo the diff module on stdin JSON
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(description="Operator edit diff extractor (Lever M)")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Print a sample diff against built-in fixtures",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.demo:
        before = {
            "hook": "Players are losing their minds over this",
            "title": "Big patch dropping today",
            "caption": "The community is going wild after the announcement",
        }
        after = {
            "hook": "Insane 1v5 clutch from ESL One Cologne finals",
            "title": "Big patch dropping today",
            "caption": "Community erupts after surprise patch announcement",
        }
        diffs = diff_blueprint(before, after, blueprint_id="demo_001")
        for d in diffs:
            print(
                f"  {d.field}: {d.operation} (Δchars={d.char_delta:+d}, "
                f"ratio={d.edit_distance_ratio:.2f})"
            )
            print(f"    before: {d.before[:80]}")
            print(f"    after:  {d.after[:80]}")
        print()
        print("Summary:")
        print(json.dumps(summarize_edits(diffs), indent=2))
    else:
        print("Use --demo to see a sample diff output.")
        print("Programmatic usage:")
        print("  from genlab_core.learning.edit_diff import diff_blueprint, summarize_edits")
        sys.exit(0)
