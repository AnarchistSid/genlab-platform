"""BB writing strategy — LLM content generation for AI creator stories.

Migrated to BaseWritingStrategy (Sprint 69). Inherits the shared LLM +
template-fallback writing pipeline with AI-specific config from
config/writing.yaml and config/templates.yaml.

Previous version (115 lines) called write_video_content() directly and
was missing TikTok/Threads content, extra_instructions, template fallback
on LLM failure, and caption length enforcement.
"""

from __future__ import annotations

from pathlib import Path

from genlab_core.strategies import BaseWritingStrategy

BB_ROOT = Path(__file__).resolve().parent.parent


class BBWritingStrategy(BaseWritingStrategy):
    """Generate written content for AI creator stories.

    Identical to the shared path — all AI-specific config lives in
    config/writing.yaml (banned phrases, hook examples, tone notes)
    and config/templates.yaml (hook formulas, CTA library, hashtag pool).
    """

    def __init__(self) -> None:
        super().__init__(niche_id="ai_creators", niche_root=BB_ROOT)
