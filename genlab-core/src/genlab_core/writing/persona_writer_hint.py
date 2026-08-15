"""Persona hint for the video content writer (2026-08-15).

Phase 4.D drift detector scores every published hook against
`persona.yaml` (voice knobs, style examples, topics to avoid).
Prior to this module the writer never READ persona.yaml — it used
the hardcoded `NICHE_VOICE` dict which encodes a DIFFERENT persona
than the auditor judges against. Live drift scores:

  * anime 0.05 — hook was plot synopsis with no sakuga vocabulary
  * gaming 0.35 — hook lacked expected emoji density
  * movies 0.35 — read as fan-editorial not cinephile analysis
  * ai_creators 0.45 — casual anecdotal, not technical

This module bridges: it renders the SAME persona.yaml the auditor
uses into a prompt section the writer sees. The writer now knows
the exact knobs it will be scored on.

## Rollout

Injection is per-niche via `GENLAB_PERSONA_HINT_NICHES` env var:

  * unset / empty / "0" / "false" — no injection anywhere
  * `anime` — canary on anime only
  * `anime,movies` — two-niche expansion
  * `all` or `*` — every niche

Fail-open at every layer — a broken persona.yaml or missing env
never breaks writer generation.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Final

logger = logging.getLogger(__name__)


_ROLLOUT_ENV: Final[str] = "GENLAB_PERSONA_HINT_NICHES"
_ALL_TOKENS: Final[set[str]] = {"all", "*"}
_OFF_TOKENS: Final[set[str]] = {"", "0", "false", "no", "off"}


def is_enabled_for(niche_id: str) -> bool:
    """True when the persona hint should be injected for ``niche_id``.

    Value semantics:
      * unset / empty / "0" / "false" — off everywhere
      * "all" / "*" — on for every niche
      * comma-list of niche_ids — on only for those niches
    """
    raw = (os.environ.get(_ROLLOUT_ENV) or "").strip().lower()
    if raw in _OFF_TOKENS:
        return False
    if raw in _ALL_TOKENS:
        return True
    allowed = {p.strip() for p in raw.split(",") if p.strip()}
    return niche_id in allowed


def _emoji_line(density: str) -> str | None:
    """Turn `voice.emoji_density` into a concrete instruction the
    LLM can act on. Unknown densities → None (skip line)."""
    return {
        "none": "  - Emoji density: NONE — do not add emoji to the hook.",
        "low": "  - Emoji density: LOW — zero or one emoji in the hook.",
        "medium": "  - Emoji density: MEDIUM — include 1-2 relevant emoji in the hook.",
        "high": "  - Emoji density: HIGH — include 2-3 relevant emoji; expected for this brand.",
    }.get(density.strip().lower())


def _formality_line(f: Any) -> str | None:
    """0.0-0.4 casual insider, 0.5-0.6 neutral (no line), 0.7-1.0
    professional/analytical. Non-numeric → None."""
    try:
        v = float(f)
    except (TypeError, ValueError):
        return None
    if v < 0.4:
        return (
            f"  - Formality {v}: casual insider fan-voice; NO journalese "
            "or press-release phrasing."
        )
    if v >= 0.7:
        return (
            f"  - Formality {v}: professional / analytical tone; avoid "
            "slang and reaction-emoji spam."
        )
    return None


def _enthusiasm_line(e: Any) -> str | None:
    try:
        v = float(e)
    except (TypeError, ValueError):
        return None
    if v >= 0.7:
        return (
            f"  - Enthusiasm {v}: high passion / reaction energy — "
            "not a neutral headline."
        )
    if v < 0.3:
        return (
            f"  - Enthusiasm {v}: measured tone — avoid hype language."
        )
    return None


def format_persona_prompt_section(persona: dict[str, Any] | None) -> str:
    """Render a persona.yaml dict into a writer system-prompt block.

    Returns "" when persona is None or has NO usable content — the
    caller then adds an empty string to the prompt (no-op).

    The block echoes the exact knobs the Phase 4.D drift auditor
    scores against so writer + auditor stay in sync when the
    operator edits persona.yaml.
    """
    if not persona:
        return ""

    voice = persona.get("voice") or {}
    style_examples = persona.get("style_examples") or []
    topics_avoid = persona.get("topics_to_avoid") or []
    topics_engage = persona.get("topics_to_engage") or []

    lines: list[str] = []

    vocab = str(voice.get("vocabulary") or "").strip()
    if vocab:
        lines.append(
            f"  - Vocabulary style: {vocab!r} — use domain-specific terms "
            "(see style exemplars below); NEVER generic pop-culture phrasing."
        )

    emoji_line = _emoji_line(str(voice.get("emoji_density") or ""))
    if emoji_line:
        lines.append(emoji_line)

    formality_line = _formality_line(voice.get("formality"))
    if formality_line:
        lines.append(formality_line)

    enthusiasm_line = _enthusiasm_line(voice.get("enthusiasm"))
    if enthusiasm_line:
        lines.append(enthusiasm_line)

    if topics_avoid:
        avoid_str = ", ".join(str(t) for t in topics_avoid[:6])
        lines.append(
            f"  - Topics to AVOID entirely: {avoid_str}"
        )

    if topics_engage:
        engage_str = ", ".join(str(t) for t in topics_engage[:6])
        lines.append(
            f"  - Preferred topics for this brand: {engage_str}"
        )

    if style_examples:
        exemplars = "\n".join(f"    - {ex!r}" for ex in style_examples[:4])
        lines.append(
            "  - Style exemplars (mimic the vocabulary + energy):\n"
            f"{exemplars}"
        )

    if not lines:
        return ""

    return (
        "\nPERSONA VOICE MANDATE — the Phase 4.D drift auditor scores "
        "your hook against these knobs:\n"
        + "\n".join(lines)
        + "\n  (Hooks that ignore these get drift_score < 0.6 and trigger "
        "an alert.)\n"
    )


def build_hint_for(niche_id: str) -> str:
    """End-to-end: check rollout flag → load persona → format hint.

    Every layer fails to empty string so the writer works exactly
    as before when the flag is off / persona.yaml is missing / any
    exception fires."""
    if not is_enabled_for(niche_id):
        return ""
    try:
        from genlab_core.quality.persona_drift import load_persona
        persona = load_persona(niche_id)
    except Exception as exc:
        logger.debug(
            "[persona_hint] load failed niche=%s: %s", niche_id, exc,
        )
        return ""
    try:
        return format_persona_prompt_section(persona)
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug(
            "[persona_hint] format failed niche=%s: %s", niche_id, exc,
        )
        return ""
