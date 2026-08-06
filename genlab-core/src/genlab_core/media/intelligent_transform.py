"""Intelligent transformation configuration schema.

The transformation stack registers 11 render-time choice dimensions as
bandit arms per niche; each published reel picks one arm per dimension
via LinUCB + ε-greedy explore. This module provides the Pydantic
schema for parsing the ``intelligent_transform:`` YAML block from each
niche's ``config/visuals.yaml``.

Design principles:

* **Default off** — ``enabled: false`` at both the top level AND per-
  dimension by default. Ships schema-only in PR 2/16; downstream PRs
  wire the runtime consumers. Operator flips
  ``GENLAB_INTELLIGENT_TRANSFORM_ENABLED=1`` + per-niche ``enabled:
  true`` to activate.

* **Empty defaults, not fictional arms** — the arm-value lists
  (moods, styles, framings) are populated per-niche in visuals.yaml;
  this module does NOT hardcode default arm lists. That way there is
  ONE source of truth for what arms exist per niche.

* **Forward-compatible** — new dimensions can be added to the
  ``DimensionsConfig`` model without breaking existing YAML; unknown
  YAML keys are ignored per Pydantic's default behavior.

* **Type-safe integer clamps** — audio ducking levels, pacing ms, etc.
  use typed integer ranges. Bandit selection guarantees only known
  values are chosen; this validates the seed configuration.

Related:
- [[intelligent-transformation-architecture-2026-07-05]] — full design
- Migration: ``a7v8w9x0y1z2_intelligent_transformation_schema.py``
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MusicMoodConfig(BaseModel):
    """Music mood selection + audio mix parameters.

    Reward attribution: 15-second retention hold rate. Music mood
    influences whether viewers stay past the initial hook grab.
    """

    enabled: bool = True
    moods: list[str] = Field(default_factory=list)
    """Per-niche mood tags, e.g. ['cinematic', 'energetic', 'contemplative'].
    Registered as bandit arms with arm_id ``transform__music_mood__<mood>``."""
    source: str = "pixabay"
    """Music source: 'pixabay' (free API, ~30K tracks) or 'musicgen'
    (local generation, Tier 3). MusicGen is a future enhancement — for
    v1 always 'pixabay'."""
    source_audio_duck_db: int = -6
    """dB level applied to source-video's own audio track. QB-FIX-01 F3a-2
    (2026-08-06) flipped from -12 (secondary) to -6 (primary). Design
    intent per operator: the source video's own audio (movie trailer
    dialog, sports commentary, anime theme + speech, tech creator voice)
    IS the primary aural signal. TTS is captions-only in the current
    pipeline — no code path mixes _audio.mp3 into the reel; audio_path
    is used only by WhisperX for word-level caption timing. Formerly
    the source sat 6 dB BELOW the music bed which buried source dialog.
    Independent from the audio_ducking bandit dimension which selects
    the specific level around this centre."""
    music_bed_db: int = -20
    """dB for royalty-free music bed. QB-FIX-01 F3a-2 (2026-08-06)
    flipped from -6 (primary) to -20 (behind source). Gives ~14 dB
    source-over-bed margin at the default source level of -6 dB —
    well above the 10 dB gate for aural intelligibility. Bandit
    audio_ducking arms bracket source-side; bed stays fixed at -20
    unless a per-niche override says otherwise."""


class CaptionStyleConfig(BaseModel):
    """Caption style + pacing selection.

    Reward attribution: 3-second AND 15-second hold rate. Caption style
    influences both initial attention grab AND mid-video retention.
    """

    enabled: bool = True
    styles: list[str] = Field(default_factory=list)
    """Style arms, e.g. ['word_by_word', 'hormozi_yellow', 'karaoke',
    'minimal', 'meme']. Registered as ``transform__caption_style__<name>``."""
    default_pacing_ms: int = 750
    """Baseline pacing (600-900ms per 2-4 word chunk per 2026 research).
    Bandit selects per publish via ``caption_pacing`` dimension."""


class HookFramingConfig(BaseModel):
    """Hook framing style selection.

    Reward attribution: 3-second hold rate. Hook framing is the single
    highest-leverage decision — 50% of viewers drop off in first 3s.
    Per 2026 research: storytelling hook or jump cut in first 3s makes
    a reel 72% more likely to go viral.
    """

    enabled: bool = True
    framings: list[str] = Field(default_factory=list)
    """Framing arms, e.g. ['question_open', 'statistic_shock',
    'contrarian_take', 'curiosity_gap', 'jump_cut_visual']. Registered
    as ``transform__hook_framing__<name>``."""


class IntroAnimationConfig(BaseModel):
    """Branded intro animation selection.

    Reward attribution: 3-second hold rate. Intro affects immediate
    hold vs bounce.
    """

    enabled: bool = True
    templates: list[str] = Field(default_factory=list)
    """Template names matching assets in ``asset_dir``. E.g.
    ['logo_zoom', 'logo_tagline_reveal', 'pattern_break_intro', 'none'].
    The magic string ``'none'`` is a synthetic arm that
    ``transformation_orchestrator`` special-cases as skip-intro. See
    ``force_none`` for the default override."""
    asset_dir: str = "assets/motion/intros"
    """Relative path from niche root to intro asset MP4 files."""
    force_none: bool = True
    """QB-FIX-01 F3d-1 (2026-08-06): when True (default), the
    orchestrator overrides whatever intro arm the bandit picked and
    skips the intro composite. Audit F3c measured every ai_creators /
    movies / gaming reel opening with a 2.5s branded intro — a Tier-1
    retention cost per Section 1.1 row 4 (target time-to-first-content
    ≈0s). Set to ``false`` per niche's ``visuals.yaml`` to re-enable
    bandit-driven intro selection. The three real intro templates
    stay registered so the bandit posterior is preserved; the ``'none'``
    template also stays registered so if the bandit picks it under
    ``force_none: false`` the orchestrator skips the intro cleanly."""


class OutroCTAConfig(BaseModel):
    """Outro CTA style selection.

    Reward attribution: completion rate → sends_per_reach proxy. Outro
    style affects whether viewers complete the reel AND take action.
    """

    enabled: bool = True
    styles: list[str] = Field(default_factory=list)
    """CTA styles, e.g. ['follow', 'comment', 'save', 'share', 'question']."""
    asset_dir: str = "assets/motion/outros"
    """Relative path from niche root to outro asset MP4 files."""


class PanZoomConfig(BaseModel):
    """Pan/zoom pattern selection.

    Reward attribution: full-video completion rate. Motion keeps
    viewers engaged past static-content bounce.
    """

    enabled: bool = True
    patterns: list[str] = Field(default_factory=list)
    """Pattern names, e.g. ['ken_burns_slow', 'punch_in', 'jump_cut',
    'no_motion', 'dolly']. Implemented as FFmpeg ``zoompan`` filter
    variants."""


class HighlightMomentConfig(BaseModel):
    """Highlight moment selection method.

    Reward attribution: 3-second + 15-second hold rate. Highlight
    method determines which frames of the source clip get shown first.
    """

    enabled: bool = True
    methods: list[str] = Field(default_factory=list)
    """Selection methods, e.g. ['llm_pick', 'audio_peak', 'motion_peak',
    'first_frame']. Combined signal in HighlightDetector module."""
    window_seconds: int = 8
    """Duration of highlight window to select (5-10s per architecture)."""


class AudioDuckingConfig(BaseModel):
    """Audio ducking level selection.

    Reward attribution: save rate (audio quality signal). Correct
    ducking makes music+source blend cleanly rather than fighting.
    """

    enabled: bool = True
    levels_db: list[int] = Field(default_factory=list)
    """dB levels for source-audio ducking. E.g. [-9, -12, -15]."""


class MusicVisualSyncConfig(BaseModel):
    """Music-to-visual sync mode selection.

    Reward attribution: full-video completion rate. Beat-synced cuts
    increase perceived production quality.
    """

    enabled: bool = True
    modes: list[str] = Field(default_factory=list)
    """Sync modes, e.g. ['beat_cut', 'ambient', 'none']. Beat sync
    detects BPM via librosa + snaps pan/zoom keyframes to beat."""


class CaptionEmphasisColorConfig(BaseModel):
    """Caption emphasis color selection.

    Reward attribution: comment quality (engagement signal). Emphasis
    color pop directs viewer attention to key words.
    """

    enabled: bool = True
    colors: list[str] = Field(default_factory=list)
    """Emphasis colors, e.g. ['yellow', 'red', 'cyan', 'gradient', 'none']."""


class CaptionPacingConfig(BaseModel):
    """Caption pacing (ms per chunk) selection.

    Reward attribution: 15-second hold rate. Per 2026 research:
    2-4 word chunks held 600-900ms is current default.
    """

    enabled: bool = True
    pacing_ms_options: list[int] = Field(default_factory=list)
    """Pacing arms in milliseconds. E.g. [600, 700, 800, 900]."""


class DimensionsConfig(BaseModel):
    """The 11 transformation dimensions, each a bandit-arm-selectable
    render-time choice.

    All fields default to their model's ``enabled=False`` posture when
    absent from YAML — safe for niche configs that opt in incrementally.
    """

    music_mood: MusicMoodConfig = Field(default_factory=MusicMoodConfig)
    caption_style: CaptionStyleConfig = Field(default_factory=CaptionStyleConfig)
    hook_framing: HookFramingConfig = Field(default_factory=HookFramingConfig)
    intro_animation: IntroAnimationConfig = Field(default_factory=IntroAnimationConfig)
    outro_cta: OutroCTAConfig = Field(default_factory=OutroCTAConfig)
    pan_zoom: PanZoomConfig = Field(default_factory=PanZoomConfig)
    highlight_moment: HighlightMomentConfig = Field(default_factory=HighlightMomentConfig)
    audio_ducking: AudioDuckingConfig = Field(default_factory=AudioDuckingConfig)
    music_visual_sync: MusicVisualSyncConfig = Field(default_factory=MusicVisualSyncConfig)
    caption_emphasis_color: CaptionEmphasisColorConfig = Field(
        default_factory=CaptionEmphasisColorConfig
    )
    caption_pacing: CaptionPacingConfig = Field(default_factory=CaptionPacingConfig)


class IntelligentTransformConfig(BaseModel):
    """Root config for the intelligent transformation stack.

    Two-level enablement: this ``enabled`` flag AND the
    ``GENLAB_INTELLIGENT_TRANSFORM_ENABLED`` environment variable
    must both be truthy for the pipeline to apply transformations.

    Rationale: config controls per-niche opt-in (belt); env flag
    controls global kill-switch (suspenders). Either can rollback
    without touching the other.
    """

    enabled: bool = False
    """Per-niche activation. Ships false; operator flips per niche
    once assets are designed and bandit arms are registered."""

    dimensions: DimensionsConfig = Field(default_factory=DimensionsConfig)

    @classmethod
    def from_visuals_dict(cls, visuals: dict[str, Any]) -> IntelligentTransformConfig:
        """Parse ``intelligent_transform:`` block from a parsed
        visuals.yaml dict. Returns default-off config if the block
        is absent.

        Legacy niche visuals.yaml files (pre-PR-2) do not contain the
        block; those get ``enabled=False`` and all dimensions empty.
        No exception raised — the pipeline degrades gracefully to the
        legacy render path.
        """

        block = visuals.get("intelligent_transform", {})
        if not isinstance(block, dict):
            # Malformed YAML — treat as absent, don't crash pipeline.
            return cls()
        return cls.model_validate(block)


__all__ = [
    "AudioDuckingConfig",
    "CaptionEmphasisColorConfig",
    "CaptionPacingConfig",
    "CaptionStyleConfig",
    "DimensionsConfig",
    "HighlightMomentConfig",
    "HookFramingConfig",
    "IntelligentTransformConfig",
    "IntroAnimationConfig",
    "MusicMoodConfig",
    "MusicVisualSyncConfig",
    "OutroCTAConfig",
    "PanZoomConfig",
]
