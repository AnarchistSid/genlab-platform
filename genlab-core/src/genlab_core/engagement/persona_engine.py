"""Persona-aware reply generation via LLM.

The persona is defined in niches/*/config/persona.yaml and controls
voice, vocabulary, and appropriate topics. The LLM generates the
creative reply; the persona YAML constrains its style.
"""
from __future__ import annotations

import logging
from typing import Optional

from genlab_core.engagement.persona_schema import NichePersona
from genlab_core.engagement.toxicity_gate import ToxicityGate

logger = logging.getLogger(__name__)


class PersonaEngine:
    """Generate replies matching a niche persona."""

    def __init__(
        self,
        persona: NichePersona,
        toxicity_gate: Optional[ToxicityGate] = None,
    ) -> None:
        self._persona = persona
        self._toxicity_gate = toxicity_gate

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
        import anthropic

        client = anthropic.Anthropic()
        system = self._build_system_prompt()

        user_content = f'Comment on {platform}:\n"{comment}"'
        if post_context:
            user_content += f"\n\nOriginal post was about: {post_context}"
        user_content += "\n\nWrite a single reply (no quotes, no explanation):"

        for attempt in range(max_retries + 1):
            try:
                resp = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=150,
                    system=system,
                    messages=[{"role": "user", "content": user_content}],
                )
                reply = resp.content[0].text.strip()

                if self._toxicity_gate and not self._toxicity_gate.is_clean_outbound(reply):
                    logger.warning(
                        "[PERSONA] Reply failed toxicity check (attempt %d): %s...",
                        attempt + 1,
                        reply[:50],
                    )
                    continue

                return reply

            except Exception as e:
                logger.warning(
                    "[PERSONA] Reply generation failed (attempt %d): %s",
                    attempt + 1, e,
                )
                if attempt >= max_retries:
                    return None

        logger.warning(
            "[PERSONA] All %d reply attempts failed toxicity gate", max_retries + 1
        )
        return None
