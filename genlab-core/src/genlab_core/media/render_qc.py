"""Post-render video QC via frame-ensemble vision judge (Lever G3).

Lever G2 (PR #432) ships ``vision_judge`` — picks one frame, judges
publish-readiness via Claude vision. That works for a single-frame
thumbnail check but misses problems that only appear at specific
moments: a logo crop at the 10s mark, hook text obscured by burned-
in captions at the 45s mark, NSFW content sneaking in mid-clip.

Lever G3 extends the judge to the FULL RENDERED VIDEO by:

1. Extracting 3 frames at fixed time positions (start / middle / end)
2. Running each frame through ``vision_judge.judge_frame``
3. Ensembling the 3 verdicts into a single ``RenderQCReport``

The ensemble exposes:
- ``min_quality_score``: the worst frame's score (the bottleneck)
- ``avg_quality_score``: average across all judged frames
- ``unique_issues``: deduplicated issue tags across all frames
- ``frame_verdicts``: per-frame VisionVerdicts for drill-down

Combined with G2 (single-frame pre-publish gate), G3 catches the
"video looks fine at the cover frame but breaks at 0:30" failure
mode that operators currently only spot by manual review.

This is the QC primitive. Wire-up (render pipeline auto-runs G3 on
every newly-rendered MP4, surfaces verdict in the Focus Review top
bar) is a follow-up PR.

Opt-in via ``GENLAB_RENDER_QC_ENABLED=1``. Default disabled = no
behavior change. Cost: 3× G2 call per video = ~$0.003 per render.
At 1 reel × 5 niches × ~$0.003 = ~$0.015/day at full activation.

Run via:
    python -m genlab_core.media.render_qc --help
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)


# Frame extraction time positions, as fractions of video duration.
# Start / middle / end covers the three failure-mode windows:
# - Start (0.1): cover frame quality (CTR proxy)
# - Middle (0.5): mid-video content drift
# - End (0.9): closing-frame branding / CTA
# 0.1 / 0.9 instead of 0.0 / 1.0 avoids ffmpeg edge-frame artifacts
# (some encoders produce black frames at exact 0 or duration boundaries).
_DEFAULT_FRAME_POSITIONS: Final[list[float]] = [0.1, 0.5, 0.9]

# Worst-frame score threshold for a "publishable" verdict. Below this
# the report's ``recommendation`` defaults to "regenerate". Tunable
# per-niche via the kwarg on ``qc_render``.
_DEFAULT_MIN_PUBLISHABLE_SCORE: Final[float] = 5.0


@dataclass(frozen=True)
class RenderQCReport:
    """Ensemble verdict across multiple frames of one rendered video.

    The downstream consumer (typically the render pipeline) reads
    ``min_quality_score`` to gate publication and ``unique_issues``
    to surface specific problems on the dashboard. ``frame_verdicts``
    is for operator drill-down — "which frame at which timestamp had
    the issue?"
    """

    video_path: str
    blueprint_id: str
    frames_judged: int  # number of frames successfully extracted + judged
    min_quality_score: float  # worst frame's score (bottleneck)
    avg_quality_score: float  # mean across frames
    unique_issues: list[str]  # deduplicated whitelisted tags
    publishable: bool  # min_quality_score >= threshold
    recommendation: str  # "publish" | "regenerate" | "unknown"
    frame_verdicts: list[dict] = field(default_factory=list)
    computed_at: str = ""


def is_enabled() -> bool:
    """Single source of truth for the opt-in flag."""
    from genlab_core.settings import env_true

    return env_true("GENLAB_RENDER_QC_ENABLED")


def extract_frame(
    video_path: str | Path,
    output_path: str | Path,
    *,
    time_offset_sec: float,
    timeout_sec: int = 10,
) -> bool:
    """Extract a single frame from a video at the given time offset.

    Uses ffmpeg via subprocess. Returns True on success, False on any
    failure (file missing, ffmpeg not installed, timeout, etc.).

    Fail-quiet — caller treats False as "couldn't get this frame" and
    proceeds with fewer frames in the ensemble.
    """
    video_p = Path(video_path)
    output_p = Path(output_path)
    if not video_p.is_file():
        logger.debug("[render_qc] video missing: %s", video_p)
        return False

    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",  # overwrite output
                "-ss",
                str(time_offset_sec),
                "-i",
                str(video_p),
                "-frames:v",
                "1",
                "-q:v",
                "2",  # high JPEG quality
                str(output_p),
            ],
            check=False,
            capture_output=True,
            timeout=timeout_sec,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.debug("[render_qc] ffmpeg failed for %s @ %.1fs: %s", video_p, time_offset_sec, exc)
        return False

    if result.returncode != 0:
        logger.debug(
            "[render_qc] ffmpeg returncode=%d for %s @ %.1fs",
            result.returncode,
            video_p,
            time_offset_sec,
        )
        return False

    return output_p.is_file() and output_p.stat().st_size > 0


def probe_video_duration(video_path: str | Path, *, timeout_sec: int = 5) -> float | None:
    """Return video duration in seconds, or None on probe failure.

    Uses ffprobe via subprocess. Fail-quiet contract.
    """
    p = Path(video_path)
    if not p.is_file():
        return None

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(p),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.debug("[render_qc] ffprobe failed for %s: %s", p, exc)
        return None

    if result.returncode != 0:
        return None

    raw = result.stdout.strip()
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def aggregate_verdicts(
    video_path: str,
    blueprint_id: str,
    verdicts: list,
    *,
    min_publishable: float = _DEFAULT_MIN_PUBLISHABLE_SCORE,
) -> RenderQCReport:
    """Combine per-frame VisionVerdicts into a single RenderQCReport.

    Pure function — no I/O. Splits aggregation from extraction +
    LLM call so callers can test the aggregation logic against
    pre-built verdict lists.

    Edge case: empty verdicts list → report with ``frames_judged=0``,
    scores=0, ``recommendation="unknown"``. Caller treats this as
    "no signal" and falls back to operator review.
    """
    from datetime import UTC, datetime

    if not verdicts:
        return RenderQCReport(
            video_path=str(video_path),
            blueprint_id=blueprint_id,
            frames_judged=0,
            min_quality_score=0.0,
            avg_quality_score=0.0,
            unique_issues=[],
            publishable=False,
            recommendation="unknown",
            frame_verdicts=[],
            computed_at=datetime.now(UTC).isoformat(),
        )

    scores = [v.quality_score for v in verdicts]
    min_score = min(scores)
    avg_score = sum(scores) / len(scores)

    # Deduplicate issues across frames, preserving order of first
    # appearance for operator readability (frame 1 issues first, etc.)
    seen: set[str] = set()
    unique_issues: list[str] = []
    for v in verdicts:
        for tag in v.issues:
            if tag not in seen:
                seen.add(tag)
                unique_issues.append(tag)

    publishable = min_score >= min_publishable
    recommendation = "publish" if publishable else "regenerate"

    return RenderQCReport(
        video_path=str(video_path),
        blueprint_id=blueprint_id,
        frames_judged=len(verdicts),
        min_quality_score=round(min_score, 2),
        avg_quality_score=round(avg_score, 2),
        unique_issues=unique_issues,
        publishable=publishable,
        recommendation=recommendation,
        frame_verdicts=[
            {
                "quality_score": v.quality_score,
                "issues": list(v.issues),
                "confidence": v.confidence,
            }
            for v in verdicts
        ],
        computed_at=datetime.now(UTC).isoformat(),
    )


def qc_render(
    video_path: str | Path,
    blueprint_id: str,
    *,
    hook_text: str = "",
    niche_id: str = "",
    positions: list[float] | None = None,
    min_publishable: float = _DEFAULT_MIN_PUBLISHABLE_SCORE,
    tmp_dir: str | Path | None = None,
) -> RenderQCReport | None:
    """Run the full render QC ensemble on a video.

    Returns ``None`` when:
    - ``GENLAB_RENDER_QC_ENABLED`` is not "1"
    - ffprobe can't read the video duration
    - All frame extractions fail
    - ``vision_judge`` returns None for all judged frames

    Fail-open by design: caller treats a None report as "no signal"
    and proceeds with operator review.

    Cost: ~3× ``vision_judge.judge_frame`` calls = ~$0.003 per video
    at default Haiku 4.5 vision pricing.
    """
    if not is_enabled():
        return None

    duration = probe_video_duration(video_path)
    if duration is None or duration <= 0:
        logger.debug("[render_qc] duration probe failed for %s", video_path)
        return None

    target_positions = positions if positions is not None else _DEFAULT_FRAME_POSITIONS
    tmp_root = Path(tmp_dir) if tmp_dir else Path("/tmp/genlab_render_qc")
    tmp_root.mkdir(parents=True, exist_ok=True)

    # Lazy import — keeps the module loadable in envs without
    # the vision_judge dep
    try:
        from genlab_core.media.vision_judge import judge_frame
    except ImportError:
        return None

    verdicts = []
    for idx, pos in enumerate(target_positions):
        time_offset = duration * pos
        frame_path = tmp_root / f"{blueprint_id}_frame_{idx}.jpg"

        if not extract_frame(video_path, frame_path, time_offset_sec=time_offset):
            continue

        v = judge_frame(
            str(frame_path),
            blueprint_id=blueprint_id,
            hook_text=hook_text,
            niche_id=niche_id,
        )
        if v is not None:
            verdicts.append(v)

        # Clean up the frame file — we have the verdict now
        try:
            frame_path.unlink(missing_ok=True)
        except OSError:
            pass

    if not verdicts:
        logger.debug(
            "[render_qc] no verdicts produced for %s (extraction + judge both failed)",
            video_path,
        )
        return None

    report = aggregate_verdicts(
        str(video_path),
        blueprint_id,
        verdicts,
        min_publishable=min_publishable,
    )
    logger.info(
        "[render_qc] bp=%s frames=%d min=%.1f avg=%.1f issues=%s rec=%s",
        blueprint_id,
        report.frames_judged,
        report.min_quality_score,
        report.avg_quality_score,
        ",".join(report.unique_issues) or "none",
        report.recommendation,
    )
    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Post-render video QC via frame-ensemble vision judge (Lever G3)"
    )
    parser.add_argument(
        "--enabled-check", action="store_true", help="Print whether opt-in env is set"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.enabled_check:
        print(f"GENLAB_RENDER_QC_ENABLED: {is_enabled()}")
        if not is_enabled():
            print("Set GENLAB_RENDER_QC_ENABLED=1 to activate the render QC layer.")
        else:
            print(f"Default frame positions: {_DEFAULT_FRAME_POSITIONS}")
            print(f"Default min-publishable score: {_DEFAULT_MIN_PUBLISHABLE_SCORE}")
    else:
        print("Use --enabled-check to verify opt-in env state.")
        print("Programmatic usage:")
        print("  from genlab_core.media.render_qc import qc_render")
        print("  report = qc_render('/path/to/video.mp4', blueprint_id='bp_001')")
