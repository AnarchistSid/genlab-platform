"""Pydantic schema for niche persona YAML configs."""
from __future__ import annotations

from pydantic import BaseModel, Field


class VoiceConfig(BaseModel):
    formality: float = 0.5
    enthusiasm: float = 0.5
    emoji_density: str = "low"
    vocabulary: str = "casual"


class ReplyConstraints(BaseModel):
    max_length_chars: int = 280
    language: str = "en"
    always_include_cta: bool = False


class NichePersona(BaseModel):
    """Schema for niches/*/config/persona.yaml."""

    name: str = "GenLab"
    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    style_examples: list[str] = Field(default_factory=list)
    topics_to_engage: list[str] = Field(default_factory=list)
    topics_to_avoid: list[str] = Field(default_factory=list)
    reply_constraints: ReplyConstraints = Field(default_factory=ReplyConstraints)
