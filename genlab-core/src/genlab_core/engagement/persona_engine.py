"""Persona-aware reply generation via LLM.

The persona is defined in niches/*/config/persona.yaml and controls
voice, vocabulary, and appropriate topics. The LLM generates the
creative reply; the persona YAML constrains its style.
"""

from __future__ import annotations

import logging

from genlab_core.engagement.persona_schema import NichePersona
from genlab_core.engagement.toxicity_gate import ToxicityGate
from genlab_core.http.circuit_breaker import ANTHROPIC_CB, CircuitOpenError
from genlab_core.llm.prompt_cache import with_prompt_cache

logger = logging.getLogger(__name__)


class PersonaEngine:
    """Generate replies matching a niche persona."""

    @property
    def persona(self) -> NichePersona:
        """Public accessor for the persona — used by the engagement worker
        to derive downstream limits like ``reply_constraints.max_length_chars``
        without poking the private ``_persona`` attribute."""
        return self._persona

    def __init__(
        self,
        persona: NichePersona,
        toxicity_gate: ToxicityGate | None = None,
    ) -> None:
        self._persona = persona
        self._toxicity_gate = toxicity_gate
        self._client = None  # Lazy-initialized Anthropic client
        # 2026-06-21 (perf): build the system prompt ONCE per engine instance.
        # Was rebuilt on every ``generate_reply`` call even though it's a pure
        # function of ``persona`` (~200 tokens of string concatenation). At the
        # caller's cache layer (one PersonaEngine per niche) this means every
        # niche pays the prompt-build cost exactly once for its worker lifetime
        # instead of once per reply.
        self._system_prompt = self._build_system_prompt()

    def _build_system_prompt(self) -> str:
        p = self._persona
        formality = p.voice.formality
        enthusiasm = p.voice.enthusiasm

        formality_desc = (
            "very casual and conversational"
            if formality < 0.3
            else (
                "balanced, friendly but professional"
                if formality < 0.7
                else "professional and measured"
            )
        )
        enthusiasm_desc = (
            "enthusiastic and energetic, using caps and exclamations naturally"
            if enthusiasm > 0.7
            else "calm and considered"
        )

        style_block = "\n".join(f'  - "{ex}"' for ex in p.style_examples[:3])
        avoid_block = "\n".join(f"  - {t}" for t in p.topics_to_avoid)
        max_chars = p.reply_constraints.max_length_chars

        return (
            f"You are the community manager for {p.name}, a social media account.\n"
            f"Your job is to reply to viewer comments in a way that feels genuine and human.\n\n"
            f"Voice: {formality_desc}, {enthusiasm_desc}.\n"
            f"Emoji usage: {p.voice.emoji_density} (use emojis naturally, not mechanically).\n"
            f"Vocabulary style: {p.voice.vocabulary}.\n\n"
            f"Good reply examples:\n{style_block}\n\n"
            f"Never engage with:\n{avoid_block}\n\n"
            f"Constraints:\n"
            f"- Maximum {max_chars} characters\n"
            f"- Never use markdown formatting (no **bold**, no bullet points)\n"
            f"- Never mention you are AI\n"
            f"- Reply to the specific comment content — don't give generic responses\n"
            f"- If the comment is a question, actually answer it\n"
        )

    def generate_reply(
        self,
        comment: str,
        platform: str,
        post_context: str = "",
        max_retries: int = 2,
    ) -> str | None:
        """Generate a reply to a comment using the niche persona.

        Retries if the generated reply fails outbound toxicity.
        Returns None if all retries fail.
        """
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        client = self._client
        # Reuse the system prompt cached in ``__init__`` (see comment there).
        system = self._system_prompt

        user_content = f'Comment on {platform}:\n"{comment}"'
        if post_context:
            user_content += f"\n\nOriginal post was about: {post_context}"
        user_content += "\n\nWrite a single reply (no quotes, no explanation):"

        for attempt in range(max_retries + 1):
            try:

                def _llm_call():
                    # U-01: with_prompt_cache wraps the persona system
                    # prompt for caching when it's long enough. The
                    # PersonaEngine is reused across many reply
                    # generations within a 5-min poller window
                    # (YouTube poller fires every 30 min, Twitter
                    # every 15 min) — calls 2-N within a poll cycle
                    # become cache reads at ~10% input cost.
                    return client.messages.create(
                        model="claude-haiku-4-5-20251001",
                        max_tokens=150,
                        system=with_prompt_cache(system),
                        messages=[{"role": "user", "content": user_content}],
                    )

                resp = ANTHROPIC_CB.call(_llm_call)
                from genlab_core.intelligence.cost_accumulator import record_anthropic_usage

                record_anthropic_usage("claude-haiku-4-5-20251001", resp)  # U-03
                reply = resp.content[0].text.strip()

                if self._toxicity_gate and not self._toxicity_gate.is_clean_outbound(reply):
                    logger.warning(
                        "[PERSONA] Reply failed toxicity check (attempt %d): %s...",
                        attempt + 1,
                        reply[:50],
                    )
                    continue

                return reply

            except CircuitOpenError:
                logger.warning("[PERSONA] Anthropic circuit open — cannot generate reply")
                return None
            except Exception as e:
                logger.warning(
                    "[PERSONA] Reply generation failed (attempt %d): %s",
                    attempt + 1,
                    e,
                )
                if attempt >= max_retries:
                    return None

        logger.warning("[PERSONA] All %d reply attempts failed toxicity gate", max_retries + 1)
        return None

    def validate_reply(
        self,
        comment: str,
        reply: str,
        platform: str,
        post_context: str = "",
    ) -> tuple[float, str]:
        """LLM-as-judge: score a generated reply on on-topic + on-voice + engaging.

        Returns ``(score, reason)`` where:
        - ``score`` ∈ [0.0, 1.0]. 0.0 = bad reply, 1.0 = great. Mid-range
          (0.5-0.7) = borderline.
        - ``reason`` is a one-line explanation suitable for audit logs.

        The 2026-06-22 autonomy audit found that auto-replies are the
        last-mile of unattended operation but were never self-validated.
        ``generate_reply`` checks only toxicity; semantic mismatches
        (off-topic, wrong tone, generic) flowed straight to the platform.
        One bad auto-reply can damage channel reputation; this judge
        catches them before they post.

        Cost: one Haiku call per validation (~80 tokens system + 80
        user + 60 output = ~$0.0002 per call). Budget impact: 5 niches ×
        10 auto-candidate replies/day = ~$0.01/day total at full
        coverage.

        Failure modes:
        - Anthropic circuit open: return (1.0, "judge_unavailable") —
          fail-OPEN so the engagement system keeps moving. The
          conservative alternative (fail-closed at 0.0) would freeze
          all auto-replies on a transient API outage.
        - Parse failure: return (0.5, "judge_parse_error") — neutral
          mid-range routes the reply to review queue (per
          ``classify_reply_action`` rules).
        - Genuine "this reply is bad": low score, descriptive reason.
        """
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        client = self._client

        # 2026-06-22 — scratchpad context. The weekly Opus reflection
        # (PR #474) synthesizes 7 days of operator reviews + edits +
        # bombed posts into actionable markdown. Engagement reply
        # quality is the brand-trust surface — operator-taste signal
        # belongs here as much as in hook generation. Same pattern
        # as PR #471 (hook gen) + PR #476 (gate judge): prepend the
        # scratchpad BEFORE the task definition. Fail-OPEN: missing
        # scratchpad = empty string = no prepend.
        try:
            from genlab_core.learning.scratchpad import read_scratchpad

            scratchpad = read_scratchpad()
        except Exception:  # noqa: BLE001 — scratchpad is augmentation
            scratchpad = ""
        scratchpad_block = (
            "## Recent learnings (scratchpad)\n\n" + scratchpad + "\n\n---\n\n"
            if scratchpad
            else ""
        )

        system = (
            scratchpad_block + "You are a strict quality judge for social-media replies. "
            "Score the reply 0-100 on three dimensions:\n"
            "1. on-topic: does the reply directly address the comment?\n"
            "2. on-voice: does the reply match the niche persona?\n"
            "3. engaging: would a viewer want to interact further?\n\n"
            "Output ONLY a single line: SCORE: NN | REASON: <one sentence>\n"
            "Score 80-100 = ship it. 50-79 = borderline (route to human). "
            "Below 50 = bad reply (discard)."
        )
        user_content = (
            f"Platform: {platform}\n"
            f'Comment: "{comment}"\n'
            f"Post context: {post_context or '(none)'}\n"
            f'Generated reply: "{reply}"'
        )

        try:

            def _llm_call():
                # U-01: cache the judge's system prompt (scratchpad +
                # rubric). Scratchpad-augmented prompt is typically
                # 2000-4000 chars; the helper auto-skips caching when
                # below the 4000-char threshold (zero cost penalty).
                return client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=80,
                    system=with_prompt_cache(system),
                    messages=[{"role": "user", "content": user_content}],
                )

            resp = ANTHROPIC_CB.call(_llm_call)
            from genlab_core.intelligence.cost_accumulator import record_anthropic_usage

            record_anthropic_usage("claude-haiku-4-5-20251001", resp)
            raw = resp.content[0].text.strip()

            # Parse "SCORE: NN | REASON: ..." format. Tolerant of LLM
            # variation — match the first integer in the response as
            # the score.
            import re as _re

            m = _re.search(r"SCORE:\s*(-?\d{1,3})", raw, _re.IGNORECASE)
            if not m:
                logger.warning(
                    "[PERSONA-JUDGE] could not parse score from reply: %r",
                    raw[:120],
                )
                return 0.5, "judge_parse_error"
            score_int = max(0, min(100, int(m.group(1))))
            score = score_int / 100.0
            reason_match = _re.search(r"REASON:\s*(.+)$", raw, _re.IGNORECASE)
            reason = reason_match.group(1).strip()[:200] if reason_match else f"score={score_int}"
            return score, reason
        except CircuitOpenError:
            # Fail-OPEN: don't freeze all auto-replies on transient API outage.
            logger.warning("[PERSONA-JUDGE] Anthropic circuit open — passing through")
            return 1.0, "judge_unavailable"
        except Exception as exc:  # noqa: BLE001 — fail-safe to neutral
            logger.warning("[PERSONA-JUDGE] validation raised: %s", exc)
            return 0.5, "judge_exception"
