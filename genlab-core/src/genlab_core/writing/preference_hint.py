"""Preference-data prompt injection — Intelligence stack #4a (2026-07-18).

Reads accumulated (chosen, rejected) preference pairs from
``preference_data`` at writer runtime, formats top-engagement-ratio
pairs as few-shot examples, and injects them into the writer system
prompt alongside ``style_hint`` / ``content_angle_hint`` / other
existing hint blocks.

## Motivation

The 2026-06-22 audit identified DPO from operator+engagement edits as
the highest single intelligence lever. Producer has run weekly since
shipping — accumulated ~19 pairs across 4 weeks by 2026-07-18. The
audit's original consumer plan was fine-tuning Claude Haiku, but:

1. Anthropic Python SDK (0.102.0) doesn't expose fine-tune for
   Claude 4 — only AWS Bedrock supports it
2. 19 pairs is well below fine-tune signal threshold
3. Meanwhile the pairs sit unused = "producer without consumer" leak

This module closes that leak WITHOUT waiting for Bedrock setup:
in-context learning via few-shot examples in the writer prompt.

The chosen/rejected pattern teaches the LLM contrastive style:

    GOOD (34× engagement): "What does this phone have that iPhone 17 doesn't?"
    LESS GOOD:             "Codex vs every other coding tool: it's not close anymore"

## Selection rules

- Filter by (niche_id, platform) match — writing style differs per surface
- Order by engagement_ratio DESC — highest-signal pairs first
- Take top 3 pairs (6 prompt lines) — sweet spot for token budget
- Fail-open: any DB error returns empty hint (writer works exactly as before)

## Wiring

Called from ``video_content_writer.write_video_content`` in the same
try/except pattern as ``content_type_hint``. Injected into the system
prompt via string concatenation. Zero behavioral change when
preference_data table is empty (default state for fresh deployments).

## Why in-context vs fine-tune

Fine-tune requires: (a) Bedrock access + AWS credentials on prod,
(b) hundreds of pairs for meaningful signal, (c) canary eval infra.
In-context learning requires none of these. When Bedrock ships +
pair volume grows (Intelligence #4b covers that path), this module
stays functional as a fallback.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Cap examples to keep prompt token budget reasonable. Each pair is
# ~2 lines (chosen + rejected) plus label prefixes; 3 pairs = 6-8 lines.
_MAX_EXAMPLES = 3


def fetch_recent_preference_examples(
    niche_id: str,
    platform: str = "instagram",
    limit: int = _MAX_EXAMPLES,
) -> list[dict]:
    """Return top-engagement-ratio (chosen, rejected) pairs for niche+platform.

    Reads ``preference_data`` table directly. Fail-open: DB errors,
    missing table, connection failures all return empty list.

    Ordering: ``engagement_ratio DESC`` — highest-signal pairs first.
    Ties broken by ``created_at DESC`` (freshest wins).

    Filters out rows where either chosen_hook or rejected_hook is empty
    (no useful contrast signal from empty text).
    """
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        logger.debug("[preference_hint] DATABASE_URL not set — skipping example fetch")
        return []

    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError:
        logger.debug("[preference_hint] psycopg unavailable — skipping")
        return []

    try:
        conn = psycopg.connect(dsn)
        conn.row_factory = dict_row
    except Exception as exc:
        logger.warning(
            "[preference_hint] DB connect failed for niche=%s: %s",
            niche_id,
            exc,
        )
        return []

    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT chosen_hook, rejected_hook, engagement_ratio
            FROM preference_data
            WHERE niche_id = %s
              AND platform = %s
              AND chosen_hook IS NOT NULL
              AND rejected_hook IS NOT NULL
              AND chosen_hook <> ''
              AND rejected_hook <> ''
            ORDER BY engagement_ratio DESC NULLS LAST, created_at DESC
            LIMIT %s
            """,
            (niche_id, platform, limit),
        )
        rows = cur.fetchall()
        return [
            {
                "chosen": r["chosen_hook"],
                "rejected": r["rejected_hook"],
                "engagement_ratio": float(r["engagement_ratio"] or 0.0),
            }
            for r in rows
        ]
    except Exception as exc:
        logger.warning(
            "[preference_hint] query failed for niche=%s platform=%s: %s",
            niche_id,
            platform,
            exc,
        )
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def format_preference_prompt_section(examples: list[dict]) -> str:
    """Build the writer prompt section from preference examples.

    Empty string when no examples — caller uses ``str.join`` semantics
    so an empty section is a safe no-op in the concatenation chain.

    Format shows contrastive pairs so the LLM pattern-matches on the
    delta between higher- and lower-engagement hooks. Includes
    engagement_ratio so the model gets numeric confidence signal.
    """
    if not examples:
        return ""

    example_lines = []
    for ex in examples:
        chosen = (ex.get("chosen") or "").strip()
        rejected = (ex.get("rejected") or "").strip()
        ratio = float(ex.get("engagement_ratio") or 0.0)
        if not chosen or not rejected:
            continue
        # Format: two-line pair with engagement multiplier annotation
        example_lines.append(
            f"  - BETTER ({ratio:.1f}× engagement): {chosen!r}\n    WORSE: {rejected!r}"
        )

    if not example_lines:
        return ""

    return (
        "\nPREFERENCE-LEARNED EXAMPLES (from operator-approved hooks + engagement data,\n"
        "  ordered by observed engagement ratio — these are patterns the audience\n"
        "  has actually rewarded on this niche + platform):\n"
        + "\n".join(example_lines)
        + "\n  Bias toward the BETTER framing when the story naturally fits it.\n"
        "  Do NOT copy the phrasing verbatim — the underlying pattern is the signal.\n"
    )


__all__ = [
    "fetch_recent_preference_examples",
    "format_preference_prompt_section",
]
