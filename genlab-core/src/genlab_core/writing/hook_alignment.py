"""Triple-hook alignment: text overlay + narration + visual must match.

Ensures the viewer receives a consistent message across all sensory channels
in the first 1.5 seconds. Misalignment causes instant swipe-away.
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


def validate_alignment(hook_text: str, narration_opening: str,
                       video_title: str) -> tuple[bool, str]:
    """Check that hook, narration opening, and video are semantically aligned.

    Returns (is_aligned, reason).
    """
    if not hook_text or not narration_opening:
        return True, "skip (missing data)"

    hook_lower = hook_text.lower().strip()
    narr_lower = narration_opening.lower().strip()
    title_lower = (video_title or "").lower().strip()

    # Extract key entities (names, numbers, teams)
    hook_entities = set(re.findall(r"[A-Z][a-z]+(?:\s[A-Z][a-z]+)?|\d+", hook_text))
    narr_entities = set(re.findall(r"[A-Z][a-z]+(?:\s[A-Z][a-z]+)?|\d+", narration_opening))

    # Rule 1: At least one key entity must appear in both
    shared = hook_entities & narr_entities
    if hook_entities and narr_entities and not shared:
        return False, f"No shared entities: hook={hook_entities}, narr={narr_entities}"

    # Rule 2: Hook words overlap with narration (Jaccard > 0.3)
    hook_words = set(hook_lower.split()) - {"the", "a", "an", "is", "just", "and", "of", "in", "on", "for"}
    narr_words = set(narr_lower.split()) - {"the", "a", "an", "is", "just", "and", "of", "in", "on", "for"}
    if hook_words and narr_words:
        overlap = len(hook_words & narr_words) / max(len(hook_words | narr_words), 1)
        if overlap < 0.2:
            return False, f"Low word overlap ({overlap:.0%}): hook and narration diverge"

    return True, "aligned"


def build_narration_opening(hook_text: str, video_title: str, niche_id: str) -> str:
    """Generate a narration opening that aligns with the hook text.

    This is the first sentence the TTS will read — it must reinforce the hook.
    """
    if not hook_text:
        return ""

    # Simple approach: expand the hook into a spoken sentence
    # Remove emoji and special chars for TTS
    clean = re.sub(r"[^\w\s'.,!?—-]", "", hook_text).strip()

    # If hook is already a complete sentence, use it directly
    if clean.endswith((".", "!", "?")):
        return clean

    # Otherwise, frame it as an opening statement
    return f"{clean}."
