"""Content ideation module (Phase 4.E session 1).

LLM-generates 10-20 content ideas per niche per week, seeded on
existing intelligence signals so the ideas are grounded in what
we already know works:

  * Trend anticipation snapshot (Phase 3.B) — what's about to
    peak in search + creator uploads
  * Competitor deltas (Phase 3.A) — hooks + topics that outperformed
    our reach 5x+ recently
  * Persona.yaml (Phase 4.D) — voice / topic constraints
  * Top-hook styles (Phase 4.C) — what's working in our own bandit

## Ideation prompt shape

System: brand-voice-aware content strategist
User: {niche, persona, top competitor examples, top styles this
week, recent trends}
Output: strict JSON list of 10-20 ideas, each with
{title, hook_seed, rationale, score_0_to_1}

Score is the LLM's own confidence, used for pool ranking.

## Cost model

One LLM call per niche per week × 5 niches = 5 calls/week ≈
$0.02/week (Haiku). Budget-gated via caller_type='optional'.

## Fail-open

Any missing input signal is passed to the prompt as "(no data)".
LLM produces empty list on any parse failure → caller writes
zero rows for that niche → downstream fall-through unaffected.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Idea:
    """One ideated content concept. All string fields UTF-8 safe,
    truncated to sensible lengths."""
    title: str
    hook_seed: str
    rationale: str
    score: float

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "hook_seed": self.hook_seed,
            "rationale": self.rationale,
            "score": self.score,
        }


@dataclass(frozen=True)
class IdeaBatch:
    """One ideation run's output. ``source_signals`` snapshots the
    input state so the analyzer can attribute reward back to which
    signal drove which idea."""
    niche_id: str
    ideas: list[Idea]
    source_signals: dict[str, Any] = field(default_factory=dict)
    llm_cost_usd: float = 0.0


_SYSTEM_PROMPT = """\
You are a content strategist for a video-first social channel.
Given the channel's persona + recent trend/competitor/style
signals, propose 10-20 NEW content ideas the channel could produce
this week.

Each idea should be:
  * SPECIFIC (a concrete topic + framing, not a broad theme)
  * ON-BRAND (respects the persona voice + topic constraints)
  * SEEDED by at least one of the input signals (trend, competitor,
    top-style)
  * NOVEL relative to the existing recent hooks

Return STRICT JSON matching this schema — no prose, no markdown:

{
  "ideas": [
    {
      "title": "one-line topic description",
      "hook_seed": "candidate hook draft (<=60 chars)",
      "rationale": "1-2 sentence WHY: which signal seeded this + why on-brand",
      "score": FLOAT in [0, 1] — your confidence this idea will work
    },
    ...
  ]
}

Rules:
  - 10-20 ideas per response
  - hook_seed <= 60 chars
  - title <= 100 chars
  - rationale <= 200 chars
  - Ideas that violate persona.topics_to_avoid: MUST NOT include
  - No duplicate titles
"""


def _build_user_prompt(
    niche_id: str,
    persona: dict[str, Any],
    trend_topics: list[str],
    competitor_hooks: list[str],
    top_styles: list[str],
    recent_hooks: list[str],
) -> str:
    voice = (persona or {}).get("voice") or {}
    topics_engage = (persona or {}).get("topics_to_engage") or []
    topics_avoid = (persona or {}).get("topics_to_avoid") or []
    lines = [
        f"NICHE: {niche_id}",
        f"CHANNEL: {(persona or {}).get('name', niche_id)}",
        "",
        "PERSONA voice:",
        f"  formality: {voice.get('formality', '?')}",
        f"  enthusiasm: {voice.get('enthusiasm', '?')}",
        f"  vocabulary: {voice.get('vocabulary', '?')}",
        f"  emoji_density: {voice.get('emoji_density', '?')}",
        "",
        f"topics_to_engage: {topics_engage[:8]}",
        f"topics_to_avoid: {topics_avoid}",
        "",
        f"TREND TOPICS ({len(trend_topics)}): {trend_topics[:10] or '(no data)'}",
        "",
        f"COMPETITOR HOOKS THAT OUTPERFORMED US ≥5x ({len(competitor_hooks)}):",
    ]
    if competitor_hooks:
        for h in competitor_hooks[:5]:
            lines.append(f"  - {h[:100]}")
    else:
        lines.append("  (no data)")
    lines.extend([
        "",
        f"TOP HOOK STYLES THIS WEEK: {top_styles or '(no data)'}",
        "",
        f"RECENT HOOKS (avoid duplication): {recent_hooks[:8] or '(none)'}",
        "",
        "Propose 10-20 ideas. JSON only.",
    ])
    return "\n".join(lines)


def _parse_ideas_response(raw: str) -> list[Idea]:
    """Parse LLM JSON list. Skip malformed items, cap fields."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("[ideator] JSON decode failed: %s", exc)
        return []
    if not isinstance(obj, dict):
        return []
    raw_ideas = obj.get("ideas") or []
    if not isinstance(raw_ideas, list):
        return []
    ideas: list[Idea] = []
    seen_titles: set[str] = set()
    for entry in raw_ideas:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title", "")).strip()[:100]
        hook_seed = str(entry.get("hook_seed", "")).strip()[:60]
        rationale = str(entry.get("rationale", "")).strip()[:200]
        try:
            score = float(entry.get("score", 0.5))
        except (TypeError, ValueError):
            score = 0.5
        score = max(0.0, min(1.0, score))
        if not title or title.lower() in seen_titles:
            continue
        seen_titles.add(title.lower())
        ideas.append(Idea(
            title=title, hook_seed=hook_seed,
            rationale=rationale, score=score,
        ))
    return ideas


def generate_ideas(
    niche_id: str,
    persona: dict[str, Any] | None,
    trend_topics: list[str],
    competitor_hooks: list[str],
    top_styles: list[str],
    recent_hooks: list[str],
    *,
    _client=None,  # test seam
) -> IdeaBatch:
    """LLM-call to produce 10-20 ideas. Returns IdeaBatch with
    empty list on any failure (fail-open per module contract)."""
    signals = {
        "trend_topics_n": len(trend_topics),
        "competitor_hooks_n": len(competitor_hooks),
        "top_styles": top_styles[:5],
        "persona_present": bool(persona),
        "recent_hooks_n": len(recent_hooks),
    }
    empty = IdeaBatch(niche_id=niche_id, ideas=[], source_signals=signals)

    system = _SYSTEM_PROMPT
    user = _build_user_prompt(
        niche_id, persona or {}, trend_topics,
        competitor_hooks, top_styles, recent_hooks,
    )

    try:
        if _client is not None:
            client = _client
        else:
            from genlab_core.intelligence.anthropic_client import (
                AnthropicStrategistClient,
            )
            client = AnthropicStrategistClient()
        result = client.generate_report(system, user, caller_type="optional")
    except Exception as exc:
        logger.warning("[ideator] LLM call failed niche=%s: %s", niche_id, exc)
        return empty

    text = getattr(result, "text", "") or ""
    cost = float(getattr(result, "cost_usd", 0.0) or 0.0)
    if not text:
        logger.info("[ideator] empty LLM response niche=%s (budget?)", niche_id)
        return IdeaBatch(niche_id=niche_id, ideas=[],
                         source_signals=signals, llm_cost_usd=cost)
    ideas = _parse_ideas_response(text)
    return IdeaBatch(
        niche_id=niche_id, ideas=ideas,
        source_signals=signals, llm_cost_usd=cost,
    )
