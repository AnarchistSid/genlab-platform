"""Joint quality score — combines session 1's visual features +
session 2's audio features into a single 0-1 metric (Phase 4.A s3).

## Fusion strategy

Per-modality: WEIGHTED GEOMETRIC MEAN. Rationale — a geometric
mean requires ALL sub-scores to be moderately high to yield a high
result. If any sub-score collapses (e.g., cut_frequency=0 on a
single-clip reel), the geometric mean drops sharply, which reflects
the underlying quality reality better than an arithmetic mean
would (a mean would let one high sub-score mask a broken one).

Weights per sub-score (visual):
  * color_palette_dominance:  0.20  (visual richness)
  * motion_energy:            0.30  (dynamic content)
  * cut_frequency:            0.30  (editing rhythm)
  * brand_consistency:        0.20  (brand identity)

Weights per sub-score (audio):
  * audio_energy_variance:    0.35  (dynamic sound)
  * dialogue_density:         0.35  (not-silent)
  * music_to_voice_ratio:     0.30  (mix character — less critical)

Joint = visual^0.6 × audio^0.4 (video-first platform per CLAUDE.md).

## Missing-sub-score handling

Any sub-score that came back ``ok=False`` is EXCLUDED from the
geometric mean rather than treated as 0. Rationale: missing signal
means "we don't know" — treating it as 0 would penalize the score
for our own extractor's inability rather than for the content's
quality.

If EVERY sub-score in a modality failed, that modality's score
is None → falls through to a NULL joint_score. Runner persists
NULL and moves on; downstream reward-multiplier treats NULL as
unit multiplier (1.0) — same fail-open contract as elsewhere.
"""
from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


# Fusion weights — pinned in tests so a drift here is caught.
_VISUAL_WEIGHTS = {
    "color_palette_dominance": 0.20,
    "motion_energy": 0.30,
    "cut_frequency": 0.30,
    "brand_consistency": 0.20,
}
_AUDIO_WEIGHTS = {
    "audio_energy_variance": 0.35,
    "dialogue_density": 0.35,
    "music_to_voice_ratio": 0.30,
}
# Visual : Audio in the final joint (video-first platform).
_VISUAL_JOINT_WEIGHT = 0.60
_AUDIO_JOINT_WEIGHT = 0.40


@dataclass(frozen=True)
class JointQualityScore:
    """Aggregate result for one video. All floats are [0, 1] or
    None (extractor failed). ``failed_extractors`` lists the sub-
    score names that came back ok=False."""
    video_path: str
    video_hash: str
    # Visual sub-scores
    color_palette_dominance: float | None
    motion_energy: float | None
    cut_frequency: float | None
    brand_consistency: float | None
    # Audio sub-scores
    audio_energy_variance: float | None
    dialogue_density: float | None
    music_to_voice_ratio: float | None
    # Fusion
    visual_score: float | None
    audio_score: float | None
    joint_score: float | None
    failed_extractors: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return asdict(self)


def _weighted_geometric_mean(
    scores: dict[str, float | None], weights: dict[str, float],
) -> float | None:
    """Weighted geometric mean over the non-None subset. Weights
    are renormalized to sum to 1 across the present sub-scores.

    Returns None when every score is None. Returns 0.0 (not None)
    when at least one score is present and equals 0 — that's a
    valid geometric-mean output signalling one collapsed dimension."""
    present = {k: v for k, v in scores.items() if v is not None}
    if not present:
        return None
    # Renormalize weights over the present subset
    present_weights = {k: weights[k] for k in present if k in weights}
    weight_sum = sum(present_weights.values())
    if weight_sum <= 0:
        return None
    normalized = {k: w / weight_sum for k, w in present_weights.items()}
    # Guard: geometric mean explodes on zero. Clip to 1e-6 so
    # log() doesn't hit -inf. The score still collapses toward 0
    # which is the correct signal.
    log_sum = 0.0
    for k, v in present.items():
        if k not in normalized:
            continue
        clipped = max(1e-6, min(1.0, v))
        log_sum += normalized[k] * math.log(clipped)
    return math.exp(log_sum)


def _combine_joint(
    visual_score: float | None, audio_score: float | None,
) -> float | None:
    """Two-modality weighted geometric mean. Video-first platform
    weights visual higher (0.60 vs 0.40)."""
    parts: dict[str, float | None] = {
        "visual": visual_score, "audio": audio_score,
    }
    weights = {"visual": _VISUAL_JOINT_WEIGHT, "audio": _AUDIO_JOINT_WEIGHT}
    return _weighted_geometric_mean(parts, weights)


def _hash_video(video_path: Path, chunk_bytes: int = 65536) -> str:
    """SHA-256 hash of the video file for the (blueprint, video)
    UNIQUE key. Cheap-ish: reads a single 64KB head chunk +
    file size — full-file hash would take seconds on multi-MB files.

    Format: ``<hex-head>-<size>``. Different sizes or head bytes
    → different hash → allowed to re-score if content changed."""
    try:
        size = video_path.stat().st_size
        with video_path.open("rb") as f:
            head = f.read(chunk_bytes)
        h = hashlib.sha256(head).hexdigest()[:16]
        return f"{h}-{size}"
    except OSError as exc:
        logger.warning("[joint_score] hash failed for %s: %s", video_path, exc)
        return "unhashable-0"


def compute_joint_score(
    video_path: Path, brand_hex_color: str,
) -> JointQualityScore:
    """Run every extractor + fuse. Fail-open contract holds through:
    missing extractors → excluded from mean, empty modality → None
    for that modality, empty joint → None.

    ``brand_hex_color`` is passed straight to
    :func:`extract_brand_consistency` — caller reads from the
    niche's visuals.yaml.
    """
    from genlab_core.quality.audio_features import (
        extract_audio_energy_variance,
        extract_dialogue_density,
        extract_music_to_voice_ratio,
    )
    from genlab_core.quality.visual_features import (
        extract_brand_consistency,
        extract_color_palette_dominance,
        extract_cut_frequency,
        extract_motion_energy,
    )

    def _score_or_none(fn, *args) -> tuple[float | None, bool]:
        try:
            r = fn(*args)
            return (r.score if r.ok else None, r.ok)
        except Exception as exc:  # noqa: BLE001 — extractor bug shouldn't crash runner
            logger.warning(
                "[joint_score] extractor %s crashed: %s", fn.__name__, exc,
            )
            return (None, False)

    visual_scores: dict[str, float | None] = {}
    audio_scores: dict[str, float | None] = {}
    failed: list[str] = []

    for name, fn in (
        ("color_palette_dominance", extract_color_palette_dominance),
        ("motion_energy", extract_motion_energy),
        ("cut_frequency", extract_cut_frequency),
    ):
        score, ok = _score_or_none(fn, video_path)
        visual_scores[name] = score
        if not ok:
            failed.append(name)

    brand_score, brand_ok = _score_or_none(
        extract_brand_consistency, video_path, brand_hex_color,
    )
    visual_scores["brand_consistency"] = brand_score
    if not brand_ok:
        failed.append("brand_consistency")

    for name, fn in (
        ("audio_energy_variance", extract_audio_energy_variance),
        ("dialogue_density", extract_dialogue_density),
        ("music_to_voice_ratio", extract_music_to_voice_ratio),
    ):
        score, ok = _score_or_none(fn, video_path)
        audio_scores[name] = score
        if not ok:
            failed.append(name)

    visual_score = _weighted_geometric_mean(visual_scores, _VISUAL_WEIGHTS)
    audio_score = _weighted_geometric_mean(audio_scores, _AUDIO_WEIGHTS)
    joint = _combine_joint(visual_score, audio_score)

    return JointQualityScore(
        video_path=str(video_path),
        video_hash=_hash_video(video_path),
        color_palette_dominance=visual_scores["color_palette_dominance"],
        motion_energy=visual_scores["motion_energy"],
        cut_frequency=visual_scores["cut_frequency"],
        brand_consistency=visual_scores["brand_consistency"],
        audio_energy_variance=audio_scores["audio_energy_variance"],
        dialogue_density=audio_scores["dialogue_density"],
        music_to_voice_ratio=audio_scores["music_to_voice_ratio"],
        visual_score=visual_score,
        audio_score=audio_score,
        joint_score=joint,
        failed_extractors=tuple(failed),
    )
