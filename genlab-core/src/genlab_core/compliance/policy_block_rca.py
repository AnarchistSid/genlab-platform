"""Policy-block learning loop L2 — LLM-judge RCA over compliance_events.

Reads recent ``platform_policy_block`` rows from ``compliance_events``
and asks an LLM to identify the likely violation category + concrete
"avoid patterns" the writer should steer clear of.

Public surface
--------------
    analyze_recent_policy_blocks(
        niche_id: str,
        *,
        window_days: int = 30,
        min_samples: int = 3,
    ) -> list[RCAVerdict]

Flag-gated OFF by default (``GENLAB_POLICY_BLOCK_RCA_ENABLED=1`` to
activate). Data-first pattern: L1 (`platform_policy_block` write path)
ships without a consumer to accumulate ground-truth samples. L2 fires
only when enough have accrued to make the LLM judgment meaningful.

Cold-start floor at ``min_samples`` (default 3) — one policy block is
random noise, two is a coincidence, three+ is a pattern the model can
speak to. Below the floor: returns empty list without burning tokens.

L3 (writer prompt injection) is the intended consumer — it calls this
helper per-niche + formats the top-N ``avoid_patterns`` into the
system prompt as "Never do X" rules. Also usable directly from the
dashboard for the operator's investigation surface.

Design decisions
----------------

- **Read-only.** Verdicts are RETURNED not persisted. The consumer
  decides what to do with them (writer prompt injection is ephemeral;
  the dashboard renders + never writes). If a future consumer wants
  durable verdicts, add a separate table — don't overload
  compliance_events (that table's contract is one-row-per-decision).

- **Anthropic → OpenAI fallback.** Reuses the strategist client so
  the 9-site fallback pattern extends naturally. On both providers
  exhausted, returns empty list (log at WARNING) — the writer's
  system prompt without avoid_patterns is strictly better than a
  hard failure blocking every publish.

- **Structured output via JSON.** Prompt requests a strict JSON
  array; parser rejects any row that fails schema. One malformed
  verdict must not poison the batch — parser continues past it and
  logs the drop.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Closed set — a verdict's ``violation_category`` MUST be one of these
# so downstream (writer prompt, dashboard grouping) can trust the
# column values. LLM outputs matching nothing get coerced to "unknown".
VALID_CATEGORIES: frozenset[str] = frozenset(
    {
        "spam_signals",  # excessive hashtags, repetitive text, cta stuffing
        "misleading_content",  # clickbait, false claims, thumbnail mismatch
        "copyright_flag",  # unauthorized third-party footage / audio
        "coordinated_behavior",  # near-duplicate posts / suspicious timing
        "policy_violation_other",  # community standards, hate, adult, etc.
        "unknown",  # LLM couldn't determine
    }
)


@dataclass(frozen=True)
class RCAVerdict:
    """One judged violation category with concrete avoid_patterns.

    Frozen so callers can't accidentally rewrite a verdict once emitted
    — same rationale as ComplianceDecision in compliance/events.py.
    """

    violation_category: str
    confidence: float  # 0.0-1.0 model self-assessment
    avoid_patterns: list[str] = field(default_factory=list)
    sample_blueprint_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.violation_category not in VALID_CATEGORIES:
            raise ValueError(
                f"violation_category must be in {sorted(VALID_CATEGORIES)}; "
                f"got {self.violation_category!r}"
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0, 1]; got {self.confidence}")


def _flag_enabled() -> bool:
    """Env-flag check at call time (not import time).

    Default OFF — data-first pattern. Operator flips after L1 has
    accumulated ≥5 policy_block rows per niche of interest.
    """
    return os.environ.get("GENLAB_POLICY_BLOCK_RCA_ENABLED", "0") == "1"


def _load_recent_events(niche_id: str, window_days: int) -> list[dict]:
    """Read policy_block rows from compliance_events.

    Returns [] on any DB error (fail-open; RCA is observability +
    downstream steering, not enforcement). Same connect helper as
    compliance/events.py::stats_by_niche.
    """
    from genlab_core.compliance.events import _connect

    conn_cm = _connect()
    if conn_cm is None:
        return []

    try:
        with conn_cm as conn:
            rows = conn.execute(
                """
                SELECT blueprint_id, platform, metadata, created_at
                FROM compliance_events
                WHERE niche_id = %s
                  AND event_type = 'platform_policy_block'
                  AND created_at > NOW() - make_interval(days => %s)
                ORDER BY created_at DESC
                LIMIT 100
                """,
                (niche_id, int(max(1, min(90, window_days)))),
            ).fetchall()
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning("[policy_rca] load failed for niche=%s: %s", niche_id, exc)
        return []

    out: list[dict] = []
    for row in rows:
        # Tolerate both tuple and dict row factories — same defensive
        # normalisation as stats_by_niche.
        if isinstance(row, dict):
            bp_id = row["blueprint_id"]
            platform = row["platform"]
            metadata = row["metadata"]
        else:
            bp_id, platform, metadata, _ = row[0], row[1], row[2], row[3]
        # metadata comes back as dict (psycopg auto-decodes jsonb) OR
        # str depending on psycopg version — tolerate both.
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}
        out.append(
            {
                "blueprint_id": str(bp_id) if bp_id else "",
                "platform": platform or "",
                "hook": (metadata or {}).get("hook", ""),
                "caption_fragment": (metadata or {}).get("caption_fragment", ""),
                "hashtag_count": (metadata or {}).get("hashtag_count", 0),
                "has_video_url": (metadata or {}).get("has_video_url", False),
                "error_snippet": (metadata or {}).get("error_snippet", ""),
            }
        )
    return out


_SYSTEM_PROMPT = """You are a compliance-forensics analyst for a
short-form video content pipeline. You will be given a set of
blueprints that a platform (Facebook / Instagram / YouTube) has
blocked with a policy-violation error.

For each distinct violation PATTERN you identify, emit exactly ONE
verdict object. Group blueprints that share the same likely cause;
each verdict covers 1+ sample blueprint_ids.

Return STRICT JSON — a top-level array of objects. Each object:

  {
    "violation_category": one of [spam_signals, misleading_content,
        copyright_flag, coordinated_behavior,
        policy_violation_other, unknown],
    "confidence": float in [0, 1],
    "avoid_patterns": array of 1-3 short imperative rules
        (e.g. "avoid more than 4 hashtags", "avoid claiming
        exclusivity you cannot prove"),
    "sample_blueprint_ids": array of blueprint_id strings this
        verdict covers
  }

Rules:
- Emit 1-4 verdicts total. Fewer is better if the pattern is unified.
- ``avoid_patterns`` MUST be concrete + directly actionable by a
  writer. NOT "be more careful"; DO "avoid captions with more than
  4 hashtags in a row."
- If you cannot infer a category, use "unknown" with confidence ≤0.4.
- Output MUST parse as JSON with no prose before or after.
"""


def _build_user_prompt(events: list[dict]) -> str:
    """Format the compliance_events rows into the LLM input body."""
    lines = ["Blocked blueprints (most recent first):", ""]
    for i, ev in enumerate(events, 1):
        lines.append(f"[{i}] blueprint_id={ev['blueprint_id']}")
        lines.append(f"    platform={ev['platform']}")
        lines.append(f"    hook={ev['hook']!r}")
        lines.append(f"    caption_fragment={ev['caption_fragment']!r}")
        lines.append(f"    hashtag_count={ev['hashtag_count']}")
        lines.append(f"    has_video_url={ev['has_video_url']}")
        lines.append(f"    error_snippet={ev['error_snippet']!r}")
        lines.append("")
    return "\n".join(lines)


def _parse_llm_response(text: str) -> list[RCAVerdict]:
    """Best-effort JSON parse. One bad row must not poison the batch."""
    try:
        # The prompt requests raw JSON with no prose; strip in case
        # the model adds ```json fences anyway.
        stripped = text.strip()
        if stripped.startswith("```"):
            # Strip triple-backtick fence + optional language tag
            stripped = stripped.split("\n", 1)[-1]
            if stripped.endswith("```"):
                stripped = stripped[: -3]
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        logger.warning("[policy_rca] LLM output not JSON-parseable: %s", exc)
        return []

    if not isinstance(data, list):
        logger.warning(
            "[policy_rca] LLM output not an array (got %s); dropping batch",
            type(data).__name__,
        )
        return []

    verdicts: list[RCAVerdict] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            logger.debug("[policy_rca] verdict %d not a dict; skipping", i)
            continue
        try:
            category = str(item.get("violation_category", "unknown"))
            if category not in VALID_CATEGORIES:
                category = "unknown"
            confidence = float(item.get("confidence", 0.0))
            confidence = max(0.0, min(1.0, confidence))
            patterns = item.get("avoid_patterns") or []
            if not isinstance(patterns, list):
                patterns = []
            patterns = [str(p)[:200] for p in patterns if p]
            sample_ids = item.get("sample_blueprint_ids") or []
            if not isinstance(sample_ids, list):
                sample_ids = []
            sample_ids = [str(s) for s in sample_ids if s]
            verdicts.append(
                RCAVerdict(
                    violation_category=category,
                    confidence=confidence,
                    avoid_patterns=patterns,
                    sample_blueprint_ids=sample_ids,
                )
            )
        except (TypeError, ValueError) as exc:
            logger.warning("[policy_rca] verdict %d rejected: %s", i, exc)
            continue

    return verdicts


def analyze_recent_policy_blocks(
    niche_id: str,
    *,
    window_days: int = 30,
    min_samples: int = 3,
) -> list[RCAVerdict]:
    """Return LLM-judged RCA verdicts for recent policy_blocks.

    Returns [] (never raises) when:
      * ``GENLAB_POLICY_BLOCK_RCA_ENABLED`` env flag is not set
      * DB is unreachable
      * fewer than ``min_samples`` rows in the window
      * LLM call fails on both Anthropic + OpenAI fallback
      * LLM output is unparseable

    The empty-list return contract lets L3 (writer prompt injection)
    treat the "no verdicts yet" and "RCA failed" cases identically —
    the writer just skips the avoid_patterns block.
    """
    if not _flag_enabled():
        return []

    if not niche_id:
        logger.warning("[policy_rca] refusing empty niche_id")
        return []

    events = _load_recent_events(niche_id, window_days)
    if len(events) < min_samples:
        logger.info(
            "[policy_rca] niche=%s: %d samples < min_samples=%d, skipping LLM call",
            niche_id,
            len(events),
            min_samples,
        )
        return []

    try:
        from genlab_core.intelligence.anthropic_client import AnthropicStrategistClient

        client = AnthropicStrategistClient()
        result = client.generate_report(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=_build_user_prompt(events),
        )
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning(
            "[policy_rca] LLM call failed for niche=%s (%s); returning empty",
            niche_id,
            exc,
        )
        return []

    verdicts = _parse_llm_response(result.text)
    logger.info(
        "[policy_rca] niche=%s: parsed %d verdicts from %d samples (cost=$%.4f)",
        niche_id,
        len(verdicts),
        len(events),
        result.cost_usd,
    )
    return verdicts
