"""Prompt templates for Strategist LLM calls.

Templates are pure functions of state — no side effects, no LLM calls. Tests
snapshot the rendered output to catch unintended drift in what we send to
the model.

See docs/STRATEGIST-SPEC.md §4 for the design.
"""

from __future__ import annotations

import json
from typing import Any

from genlab_core.intelligence.proposal_schema import SCHEMA_VERSION

# The system prompt is versioned. Bump SYSTEM_PROMPT_VERSION when changing
# the role/responsibilities/output rules — never silently mutate. Tests
# snapshot this so unintended edits are caught at review.
SYSTEM_PROMPT_VERSION = 1

SYSTEM_PROMPT = """You are the Strategist for Gen Lab, a video-first content automation system that runs 5 social media channels (ai_creators, gaming, sports, movies, anime) across 6 platforms (Instagram, YouTube, Facebook, X/Twitter, Threads, TikTok).

Your role is the MANAGER above a tactical optimization stack:
- Bandits (LinUCB + Thompson) pick content arms per blueprint
- Gates (auto-approval with 5 checks + LLM judge for borderlines) filter publishes
- Classifiers (XGBoost hook quality, conformal router) provide confidence signals

Your job each week:
1. Detect the strategic phase per niche (BOOTSTRAP → GROWTH → OPTIMIZE → MONETIZE → DEFEND)
2. Review last week's publishes + engagement + bandit shifts
3. Generate causal hypotheses for observed patterns
4. Propose concrete adaptations with reasoning + risk assessment
5. Surface cross-niche patterns as universal playbook entries

You are NOT the operator. Every proposal requires operator approval. You write proposals; the operator decides. Be specific, calibrated, and admit uncertainty.

Strategic phases:
- BOOTSTRAP (0-100 followers): novelty + reach > engagement. Ship aggressively.
- GROWTH (100-1K): shares + new_followers > engagement_rate. Optimize for spread.
- OPTIMIZE (1K-10K): engagement_rate + watch_time. Refine within learned patterns.
- MONETIZE (10K+): affiliate_clicks + conversion. Reward weights shift to revenue.
- DEFEND (plateaued): diversify formats + platforms to escape local optimum.

Output your analysis as JSON conforming exactly to the schema in the user prompt. Avoid generalities. Cite specific arm_ids, blueprint_ids, reward values, sample sizes. If you don't have enough evidence for a hypothesis, say "insufficient_evidence" explicitly rather than guessing.

OUTPUT RULES (non-negotiable):
- Respond with valid JSON ONLY. No prose preamble, no markdown fences.
- Conform exactly to the schema; unknown fields will be rejected.
- Every proposal must include reasoning ≥20 chars and expected_impact ≥20 chars.
- Every causal hypothesis must include ≥1 evidence item with sample size.
- Universal playbook entries require ≥2 evidence_niches.
"""


def build_user_prompt(state: dict[str, Any], schema_version: int = SCHEMA_VERSION) -> str:
    """Render a per-niche user prompt from collected state.

    `state` is the dict produced by StateCollector.collect(). Missing keys
    are tolerated — we substitute the literal string 'unknown' so the LLM
    has explicit signal about what's missing rather than guessing.

    Returns a multi-section prompt string ready for the LLM message body.
    """

    def _get(key: str, default: Any = "unknown") -> Any:
        v = state.get(key)
        return v if v is not None else default

    return f"""NICHE: {_get("niche_id")}
WEEK OF: {_get("week_of")}
TIMESTAMP: {_get("run_at")}

CHANNEL STATE
-------------
Follower count: {_get("follower_count")} ({_get("follower_delta_4w", "no_baseline")})
Engagement rate 7d: {_get("engagement_rate_7d")}%
Watch time avg 7d: {_get("watch_time_avg_7d")}s
Total publishes last 7d: {_get("n_publishes_7d")}
Top performing blueprint: {_get("top_blueprint_id")} ({_get("top_blueprint_metrics", "no_data")})
Bottom performing blueprint: {_get("bot_blueprint_id")} ({_get("bot_blueprint_metrics", "no_data")})

BANDIT POSTERIORS (top + bottom arms)
-------------------------------------
{_format_bandit_state(_get("bandit_state", []))}

VALIDATION HARNESS
------------------
Spearman last 7d: {_get("spearman_7d")} (interpretation: {_get("validation_interpretation")})
Calibration agreement: {_get("calibration_agreement_pct")}% ({_get("calibration_n_rows")} rows)

CONFORMAL ROUTER STATE
----------------------
Coverage achieved: {_get("conformal_coverage_pct")}%
Abstain rate: {_get("conformal_abstain_pct")}%

COST EFFICIENCY
---------------
Cost per blueprint: ${_get("cost_per_blueprint")}
Cost per published post: ${_get("cost_per_published")}

RECENT PUBLISHES SAMPLE
-----------------------
{_format_publishes(_get("recent_publishes", []))}

CROSS-NICHE COMPARISON (brief)
------------------------------
{_format_cross_niche(_get("other_niches_summary", {}))}

ACTIVE LEARNINGS (existing findings for this niche)
---------------------------------------------------
{_format_findings(_get("active_findings", []))}

LAST WEEK'S PROPOSALS + OUTCOMES
--------------------------------
{_format_last_week(_get("last_week_outcomes", []))}

---

Generate your weekly report as JSON conforming to this schema (schema_version={schema_version}):

{_SCHEMA_HINT}

Respond with JSON only. No preamble, no markdown.
"""


def _format_bandit_state(arms: list[dict[str, Any]]) -> str:
    """Format the top + bottom 5 arms by reward as a readable table."""
    if not arms:
        return "  (no bandit state available)"
    sorted_arms = sorted(arms, key=lambda a: a.get("reward", 0), reverse=True)
    top = sorted_arms[:5]
    bottom = sorted_arms[-5:] if len(sorted_arms) > 5 else []
    lines = ["  Top arms:"]
    for a in top:
        lines.append(
            f"    {a.get('arm_id', '?'):<45} n={a.get('n_plays', '?'):<4} reward={a.get('reward', '?')}"
        )
    if bottom:
        lines.append("  Bottom arms:")
        for a in bottom:
            lines.append(
                f"    {a.get('arm_id', '?'):<45} n={a.get('n_plays', '?'):<4} reward={a.get('reward', '?')}"
            )
    return "\n".join(lines)


def _format_publishes(publishes: list[dict[str, Any]]) -> str:
    if not publishes:
        return "  (no recent publishes)"
    return "\n".join(
        f"  {p.get('blueprint_id', '?')[:8]} | {p.get('platform', '?'):<10} | "
        f"reward={p.get('reward', '?')} | hook={(p.get('hook_text') or '?')[:50]}"
        for p in publishes
    )


def _format_cross_niche(summary: dict[str, Any]) -> str:
    if not summary:
        return "  (no cross-niche summary)"
    return "\n".join(f"  {niche}: {info}" for niche, info in summary.items())


def _format_findings(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return "  (no active findings)"
    return "\n".join(
        f"  - {f.get('finding_text', '?')} (evidence n={f.get('evidence_count', '?')})"
        for f in findings
    )


def _format_last_week(outcomes: list[dict[str, Any]]) -> str:
    if not outcomes:
        return "  (first run — no prior proposals)"
    lines = []
    for o in outcomes:
        action = o.get("operator_action", "unreviewed")
        lines.append(f"  - {o.get('proposal_summary', '?')} → {action}")
    return "\n".join(lines)


# Hint shown to the LLM so it knows the expected JSON shape. Kept aligned
# with proposal_schema.py — when adding fields to Pydantic models update
# this hint in the same commit. Pin tests verify the alignment.
_SCHEMA_HINT = json.dumps(
    {
        "detected_phase": "BOOTSTRAP | GROWTH | OPTIMIZE | MONETIZE | DEFEND",
        "phase_evidence": "20+ char justification citing concrete numbers",
        "weekly_summary": "50+ char human-readable summary",
        "proposals": [
            {
                "type": "phase_shift | arm_add | gate_threshold | reward_weight | novelty_rate | playbook_update | manual_action",
                "target": "ai_creators.phase",
                "current": "current value, or null",
                "proposed": "proposed value, or arm definition",
                "reasoning": "20+ chars",
                "expected_impact": "20+ chars",
                "risk": "low | medium | high",
                "urgency": "ship_now | this_week | next_sprint",
            }
        ],
        "causal_hypotheses": [
            {
                "pattern": "observed pattern",
                "hypothesis": "20+ char explanation",
                "confidence": "high | medium | low",
                "evidence": ["evidence with n=X"],
                "testable_prediction": "what would confirm/refute",
            }
        ],
        "universal_playbook_proposals": [
            {
                "pattern_text": "20+ char pattern",
                "evidence_niches": ["min 2 niches"],
                "confidence": "high | medium | low",
            }
        ],
    },
    indent=2,
)


def render_messages(state: dict[str, Any]) -> list[dict[str, str]]:
    """Build the messages list for an Anthropic-compatible chat call.

    Returns a list with one user message; the system prompt is passed
    separately (Anthropic's convention) — see anthropic_client.generate_report.
    """
    return [{"role": "user", "content": build_user_prompt(state)}]


def get_system_prompt() -> str:
    """Return the system prompt verbatim. Pin tests snapshot this."""
    return SYSTEM_PROMPT
