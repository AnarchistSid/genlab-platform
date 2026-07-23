"""Weekly agent-memory scratchpad — Opus reads episodic_events → markdown.

The 2026-06-22 'make-the-agent-smarter' research synthesis identified
persistent agent memory (Category 3 from Agent D) as the lever that
unlocks the next architectural tier. Today the agent has:

- Episodic memory: every operator review, edit, bandit pick, bombed
  post recorded to ``episodic_events`` (Postgres-backed)
- Working memory: per-run ``reasoning_trace`` for cross-stage signal
- Decision traces: on-disk JSONL for debug + retraining

What's missing: a layer that reads ALL of this and synthesizes
operator-readable insights the LLM can use as context for the NEXT
generation. This module is that layer.

How it works
------------
Once per week (systemd timer), this module:

1. Pulls 7 days of episodic_events from Postgres (recent operator
   reviews, edits, bombed posts, bandit picks)
2. Builds a compact summary dict via ``summarize_events``
3. Calls Claude Opus 4.7 (via DecisionRouter cross_run_reflection
   tier — uses the 1M context window for the full event stream)
4. Asks Opus to identify patterns + write actionable markdown
5. Writes to ``$GENLAB_PROJECT_ROOT/.tmp/agent_memory.md``

Hook generator, gate, and other LLM call sites read the markdown
and prepend it to their system prompts as "what we learned this
week" context.

Why Opus specifically
---------------------
Cross-run reflection needs:
- 1M token context window (read 5000+ events of payload metadata)
- Strong reasoning to spot patterns operators wouldn't notice
- Synthesis — turn noise (5000 events) into signal (10 actionable
  insights)

Haiku is too lossy on this task; Sonnet is too short on context.
This is the only Gen Lab decision surface that genuinely needs Opus.
Cost: 1 call/week × ~$0.50/call = ~$2/month. Worth the spend.

Fail-OPEN
---------
Every error path is caught. Missing DATABASE_URL → no events →
fallback markdown ("no data yet"). LLM API down → log + write
empty file. The hook generator handles missing scratchpad
gracefully (just skips the prepend).

Future PRs in this thread
-------------------------
- Wire scratchpad into reply_critic + gate_judge (currently only
  hook generator consumes)
- Add per-niche scratchpads (separate file per niche)
- Add monthly REFLECTION job (deeper analysis, propose prompt updates)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_DEFAULT_LOOKBACK_DAYS = 7
_DEFAULT_MAX_EVENTS = 5000


def _output_path() -> Path:
    """Resolve the scratchpad output path.

    Defaults to ``<GenLab-root>/.tmp/agent_memory.md``. Override via
    ``AGENT_MEMORY_PATH`` for tests or alt deployments.
    """
    custom = os.environ.get("AGENT_MEMORY_PATH")
    if custom:
        return Path(custom)
    here = Path(__file__).resolve()
    genlab_root = here.parents[4]
    return genlab_root / ".tmp" / "agent_memory.md"


def _build_prompt(events_summary: dict, sample_events: list[dict]) -> str:
    """Build the LLM prompt — events summary + sample, ask for patterns.

    2026-07-14 (injection-defense audit F2): sanitize event payload
    string fields before compacting into the prompt. Events include
    story titles, captions, comment text — all attacker-influenceable.
    A crafted title reaching this Opus reflection prompt could hijack
    the "week's contract" markdown the agent uses as system-prompt
    context for the NEXT generation — a learning-loop poisoning
    vector amplifying prior class-of-bug patterns.
    """
    # Compact JSON for the prompt — Opus parses well; saves tokens
    import json as _json

    try:
        from genlab_core.cache.text_sanitizer import (
            check_for_injection,
            sanitize_text,
        )

        def _clean(value: Any) -> Any:
            """Recursively sanitize string values in event payloads."""
            if isinstance(value, str):
                cleaned = sanitize_text(value, max_length=2000)
                if check_for_injection(cleaned):
                    logger.warning(
                        "[scratchpad] injection heuristic hit in event payload "
                        "(len=%d) — dropping value",
                        len(cleaned),
                    )
                    return ""
                return cleaned
            if isinstance(value, dict):
                return {k: _clean(v) for k, v in value.items()}
            if isinstance(value, list):
                return [_clean(v) for v in value]
            return value

        sample_events = [_clean(e) for e in sample_events]
    except Exception as exc:  # noqa: BLE001 — never break the reflection call
        logger.warning("[scratchpad] sanitize step failed: %s", exc)

    sample_compact = _json.dumps(sample_events[:200], default=str, indent=None)[:50000]
    return (
        "You are the Gen Lab agent's weekly reflection function. The agent "
        "publishes short-form video content across 5 niches (gaming, sports, "
        "movies, anime, ai_creators). Below is structured data from the past "
        "week: aggregate event counts + a sample of recent events.\n\n"
        f"## Summary\n```\n{events_summary}\n```\n\n"
        f"## Sample events (most recent first, up to 200)\n```\n{sample_compact}\n```\n\n"
        "## Your task\n"
        "Write a concise markdown summary the agent's hook generator + gate "
        "evaluator will read as system-prompt context for the NEXT generation. "
        "Output 4-6 sections in this order:\n\n"
        "1. **Top wins this week** — which posts/hooks performed best, what "
        "structural element they shared\n"
        "2. **Recurring failures** — which hook patterns operators rejected "
        "most + the categorical reason\n"
        "3. **Operator taste signals** — what operator EDITS revealed about "
        "preferred voice (active/passive, specificity, length)\n"
        "4. **Per-niche notes** — 1-2 concrete observations per niche\n"
        "5. **Recommendations** — 3-5 specific changes the next hook should "
        "consider (e.g. 'avoid the X just Y opener for gaming', 'prefer "
        "question hooks for ai_creators on Tuesdays')\n"
        "6. **What to ignore** — patterns that LOOK like signal but are "
        "noise (small N, no clear effect)\n\n"
        "Be concrete. Cite specific examples from the events. Avoid "
        "hedging — the agent will treat your output as the WEEK'S CONTRACT. "
        "Max 800 words. Markdown only."
    )


def _fallback_markdown(reason: str) -> str:
    """Write a 'no data yet' markdown that's still useful as context."""
    ts = datetime.now(UTC).isoformat(timespec="seconds")
    return (
        f"# Agent Memory — Week of {ts}\n\n"
        f"_No reflective insights this week ({reason})._\n\n"
        "The agent generates content using the standard system prompts. "
        "When episodic_events accumulates enough data, weekly reflections "
        "will populate this file with operator-taste patterns + recent "
        "failure modes.\n"
    )


def generate_weekly_scratchpad(
    *,
    lookback_days: int = _DEFAULT_LOOKBACK_DAYS,
    max_events: int = _DEFAULT_MAX_EVENTS,
    output_path: Path | None = None,
) -> bool:
    """Generate the weekly scratchpad. Returns True on success.

    Steps:
    1. Pull recent episodic events
    2. Summarize + sample
    3. Call Opus (via DecisionRouter cross_run_reflection tier)
    4. Write markdown to output_path

    Fail-OPEN: returns False + writes a fallback markdown on any error
    so the hook generator's consumer always finds a readable file.
    """
    output = output_path or _output_path()
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("[scratchpad] could not create output dir: %s", exc)
        return False

    # Pull events from episodic memory
    try:
        from genlab_core.learning.episodic_memory import (
            _get_default_backend,
            filter_events,
            summarize_events,
        )

        backend = _get_default_backend()
        if backend is None:
            output.write_text(_fallback_markdown("episodic backend unavailable"))
            logger.info("[scratchpad] no backend; wrote fallback to %s", output)
            return False

        since = datetime.now(UTC) - timedelta(days=lookback_days)
        # Backend.query already filters by niche when set; we want cross-
        # niche so omit. The query caps at 5000 rows already; we apply
        # the since filter via filter_events for consistency.
        raw_events = backend.query()
        events = filter_events(raw_events, since=since, limit=max_events)
        events_summary = summarize_events(events)
    except Exception as exc:
        logger.warning("[scratchpad] event fetch failed: %s", exc)
        output.write_text(_fallback_markdown(f"event fetch failed: {exc}"))
        return False

    if not events:
        output.write_text(_fallback_markdown("no events in lookback window"))
        logger.info("[scratchpad] no events; wrote fallback to %s", output)
        return False

    # Render sample as plain dicts for the prompt
    sample_dicts = [
        {
            "type": e.event_type,
            "niche": e.niche_id,
            "ts": e.timestamp,
            "payload": e.payload,
        }
        for e in events[:200]
    ]

    # Build prompt + call Opus
    prompt = _build_prompt(events_summary, sample_dicts)
    try:
        import anthropic

        from genlab_core.cost.decision_router import route_decision

        model = route_decision("cross_run_reflection")
        client = anthropic.Anthropic()

        _system = (
            "You are a strategic reflection function for an autonomous "
            "content agent. Be concrete + concise. Treat absent data as "
            "absent — don't invent patterns."
        )

        def _llm_call():
            return client.messages.create(
                model=model,
                max_tokens=2000,
                system=_system,
                messages=[{"role": "user", "content": prompt}],
            )

        from genlab_core.http.circuit_breaker import ANTHROPIC_CB

        # 2026-07-21: OpenAI fallback on Anthropic exhaustion. Scratchpad
        # runs weekly and drives strategic prompt-injection for other
        # LLM sites (auto_approval_gate, hook_generator, persona_engine)
        # — losing it means those sites lose their "recent learnings"
        # context for the week.
        from genlab_core.llm.fallback import (
            call_openai_fallback as _call_openai_fallback,
            cb_is_open as _cb_is_open,
            cb_record_exhaustion as _cb_record_exhaustion,
            cb_record_success as _cb_record_success,
            fallback_enabled as _fallback_enabled,
            should_fallback as _should_fallback,
        )

        _openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
        text = ""

        if _fallback_enabled() and _cb_is_open() and _openai_key:
            text = _call_openai_fallback(_system, prompt, 2000, 0.7, _openai_key)
        else:
            try:
                resp = ANTHROPIC_CB.call(_llm_call)
            except Exception as anthropic_exc:
                if _fallback_enabled() and _should_fallback(anthropic_exc) and _openai_key:
                    _cb_record_exhaustion()
                    # 2026-07-23: classify for attribution.
                    from genlab_core.llm.errors import classify_llm_error

                    logger.warning(
                        "[scratchpad] Anthropic failed (reason=%s) → OpenAI fallback",
                        classify_llm_error(anthropic_exc),
                    )
                    text = _call_openai_fallback(_system, prompt, 2000, 0.7, _openai_key)
                else:
                    raise
            else:
                _cb_record_success()
                text = resp.content[0].text.strip() if resp.content else ""

                # Cost tracking (best-effort)
                try:
                    from genlab_core.intelligence.cost_accumulator import (
                        record_anthropic_usage,
                    )

                    record_anthropic_usage(model, resp)
                except Exception:  # noqa: BLE001 — cost tracking failure must not block
                    pass

        text = text.strip() if text else ""

        if not text:
            output.write_text(_fallback_markdown("LLM returned empty response"))
            logger.warning("[scratchpad] empty LLM response; wrote fallback")
            return False

        # Header with metadata for operator audit trail
        ts = datetime.now(UTC).isoformat(timespec="seconds")
        full = (
            f"# Agent Memory — Generated {ts}\n\n"
            f"_Source: {events_summary.get('total_events', 0)} episodic events "
            f"from last {lookback_days} days. Model: {model}._\n\n"
            "---\n\n"
            f"{text}\n"
        )
        output.write_text(full)
        logger.info(
            "[scratchpad] wrote %d chars to %s (from %d events)",
            len(full),
            output,
            events_summary.get("total_events", 0),
        )
        return True
    except Exception as exc:  # noqa: BLE001 — fail-OPEN
        logger.warning("[scratchpad] LLM call failed: %s", exc)
        output.write_text(_fallback_markdown(f"LLM call failed: {exc}"))
        return False


def read_scratchpad(max_chars: int = 4000) -> str:
    """Read the current scratchpad. Returns '' if missing/error.

    Used by consumers (hook generator, gate judge) to prepend to
    system prompts. Truncated to keep token cost bounded — full
    insights are typically <2000 chars; 4000 cap absorbs occasional
    longer reflections without prompt bloat.

    Fail-OPEN: any error returns empty string. Consumers should
    treat empty as 'no scratchpad available' and proceed without it.
    """
    try:
        path = _output_path()
        if not path.exists():
            return ""
        text = path.read_text()
        if len(text) > max_chars:
            text = text[:max_chars] + "\n\n... [truncated]"
        return text
    except Exception as exc:  # noqa: BLE001 — fail-OPEN
        logger.debug("[scratchpad] read failed (non-fatal): %s", exc)
        return ""


def main(argv: list[str] | None = None) -> int:
    """CLI entry: ``python -m genlab_core.learning.scratchpad``."""
    parser = argparse.ArgumentParser(
        prog="scratchpad",
        description="Generate the agent's weekly reflection markdown.",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=_DEFAULT_LOOKBACK_DAYS,
        help=f"Days of episodic_events to read (default {_DEFAULT_LOOKBACK_DAYS}).",
    )
    parser.add_argument(
        "--max-events",
        type=int,
        default=_DEFAULT_MAX_EVENTS,
        help=f"Cap on event count (default {_DEFAULT_MAX_EVENTS}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Override output path (default $GENLAB_PROJECT_ROOT/.tmp/agent_memory.md).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    ok = generate_weekly_scratchpad(
        lookback_days=args.lookback_days,
        max_events=args.max_events,
        output_path=args.output,
    )
    print(f"Scratchpad generation: {'OK' if ok else 'fallback'}")
    return 0  # Always exit 0 — fallback markdown is still a valid output


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
