"""Content quality intelligence (Phase 4).

Purpose: extract multi-modal features from rendered videos so the
bandit reward can be multiplied by an intrinsic-quality score
rather than depending solely on downstream engagement (which is
sparse + high-variance for cold-start blueprints).

Modules:

  * ``visual_features`` — Phase 4.A session 1 (2026-08-14):
    color palette dominance, motion energy, cut frequency,
    brand-color consistency. Uses ffprobe/ffmpeg stats only —
    no OpenCV or heavy CV deps (4 GB VPS ceiling per CLAUDE.md).

  * ``audio_features`` — Phase 4.A session 2 (planned):
    energy variance, dialogue density, music-to-voice ratio.

  * ``joint_score`` — Phase 4.A session 3 (planned):
    combines hook_score × visual_score × audio_score into a
    single 0-1 quality metric persisted to
    ``content_quality_scores`` table.

## Fail-open discipline

Every extractor returns a `FeatureResult` with `ok: bool`. On
failure (ffprobe missing, file corrupt, filter unsupported), the
extractor returns `FeatureResult(ok=False, reason='...')` — never
raises. Downstream code treats missing scores as "unknown" and
falls back to unit reward multiplier so a broken extractor doesn't
regress the pipeline.
"""
