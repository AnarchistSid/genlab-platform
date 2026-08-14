"""Visual feature extraction from rendered videos (Phase 4.A session 1).

Four extractors, each returning a `FeatureResult` with a normalised
0-1 score + raw stat + `ok` flag. Fail-open per module contract —
callers treat `ok=False` as "unknown, fall back to unit multiplier".

## Design

All extractors run **ffmpeg/ffprobe subprocesses**. No numpy, no
OpenCV, no PIL. Rationale: the Hetzner VPS has a 4 GB RAM ceiling
(CLAUDE.md video-quality-pipeline note) and CV deps would push
memory past OOM on concurrent renders. FFmpeg's built-in filters
give us enough signal:

  * ``signalstats`` filter → per-frame color statistics (Y/U/V)
  * ``select='gt(scene,X)'`` → cut detection
  * scene-change detection accumulator → motion energy proxy

## What each score means (0-1 higher = better)

  * ``color_palette_dominance``
    High = one dominant color scheme (brand-consistent, striking).
    Low = washed-out or muddy palette. Computed as max(color-channel
    variance) / total-variance across frames.
  * ``motion_energy``
    High = dynamic content (action, camera movement, cuts). Low =
    static talking-head with no cuts. Computed from mean scene-
    change score across the video.
  * ``cut_frequency``
    Sweet spot around 1-3 cuts/sec (short-form ideal). Too low = boring,
    too high = disorienting. Score peaks at 2/sec, tapers at both ends.
  * ``brand_consistency``
    Similarity between the video's dominant hue and the niche's
    brand accent color. Score = 1 - normalized_hue_distance.
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeatureResult:
    """Return of every extractor. ``score`` is 0-1 normalized;
    ``raw`` carries the underlying stat for debugging. ``ok=False``
    means the extractor couldn't compute (missing binary, corrupt
    file, filter unsupported)."""
    ok: bool
    score: float | None = None
    raw: float | None = None
    reason: str = ""


# ── ffprobe/ffmpeg wrappers ───────────────────────────────────────


def _ffmpeg_binary() -> str:
    """Use the shared discovery from media.ffmpeg. Cached there."""
    from genlab_core.media.ffmpeg import get_ffmpeg_binary
    return get_ffmpeg_binary()


def _ffprobe_binary() -> str:
    from genlab_core.media.ffmpeg import get_ffprobe_binary
    return get_ffprobe_binary()


def _run_ffmpeg_stats(
    video_path: Path, filter_spec: str, timeout_sec: int = 30,
) -> tuple[bool, str, str]:
    """Run ``ffmpeg -i <path> -vf <filter> -f null -``. Returns
    (ok, stdout, stderr). ffmpeg's signalstats + related filters
    write metrics to stderr — we parse from there."""
    try:
        cmd = [
            _ffmpeg_binary(), "-nostdin", "-hide_banner",
            "-i", str(video_path),
            "-vf", filter_spec,
            "-f", "null", "-",
        ]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_sec,
        )
        return proc.returncode == 0, proc.stdout, proc.stderr
    except FileNotFoundError:
        return False, "", "ffmpeg_missing"
    except subprocess.TimeoutExpired:
        return False, "", f"timeout after {timeout_sec}s"
    except Exception as exc:
        return False, "", f"ffmpeg_crashed: {exc}"


def _get_duration_seconds(video_path: Path) -> float | None:
    """ffprobe duration in seconds. None on failure."""
    try:
        proc = subprocess.run(
            [
                _ffprobe_binary(), "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0:
            return None
        return float(proc.stdout.strip())
    except Exception:
        return None


# ── Feature extractors ────────────────────────────────────────────


def extract_color_palette_dominance(video_path: Path) -> FeatureResult:
    """Higher score = more color variance across the video (visual
    richness). Uses signalstats ``YAVG``/``UAVG``/``VAVG`` means +
    parses the ``YSTATS`` line for min/max range.

    Simple proxy: (Y range × U range × V range) / (255^3). Values
    near 1 mean the video uses the full color space. Near 0 means
    a washed-out palette.
    """
    if not video_path.exists():
        return FeatureResult(ok=False, reason="file_not_found")

    ok, _stdout, stderr = _run_ffmpeg_stats(
        video_path, "signalstats,metadata=print",
    )
    if not ok:
        return FeatureResult(ok=False, reason=stderr[:100])

    # signalstats emits lines like:
    #   [Parsed_metadata_1 @ 0x...] frame:N pts:X pts_time:Y
    #   [Parsed_metadata_1 @ 0x...] lavfi.signalstats.YMIN=0
    #   ... .YMAX=255 .YAVG=127.5 ...
    y_ranges: list[float] = []
    u_ranges: list[float] = []
    v_ranges: list[float] = []
    ymin: float | None = None
    umin: float | None = None
    vmin: float | None = None
    for line in stderr.splitlines():
        if ".YMIN=" in line:
            m = re.search(r"YMIN=([\d.]+)", line)
            if m:
                ymin = float(m.group(1))
        elif ".YMAX=" in line and ymin is not None:
            m = re.search(r"YMAX=([\d.]+)", line)
            if m:
                y_ranges.append(float(m.group(1)) - ymin)
                ymin = None
        elif ".UMIN=" in line:
            m = re.search(r"UMIN=([\d.]+)", line)
            if m:
                umin = float(m.group(1))
        elif ".UMAX=" in line and umin is not None:
            m = re.search(r"UMAX=([\d.]+)", line)
            if m:
                u_ranges.append(float(m.group(1)) - umin)
                umin = None
        elif ".VMIN=" in line:
            m = re.search(r"VMIN=([\d.]+)", line)
            if m:
                vmin = float(m.group(1))
        elif ".VMAX=" in line and vmin is not None:
            m = re.search(r"VMAX=([\d.]+)", line)
            if m:
                v_ranges.append(float(m.group(1)) - vmin)
                vmin = None

    if not y_ranges:
        return FeatureResult(ok=False, reason="no_signalstats_output")

    avg_y_range = sum(y_ranges) / len(y_ranges)
    avg_u_range = (sum(u_ranges) / len(u_ranges)) if u_ranges else 0
    avg_v_range = (sum(v_ranges) / len(v_ranges)) if v_ranges else 0
    # Normalize to 0-1 (255^3 max)
    raw = (avg_y_range * avg_u_range * avg_v_range)
    normalized = min(1.0, raw / (255 ** 3))
    # Boost via cube-root to spread the low-end (most videos land
    # in the 0.001-0.01 range on the naive metric)
    score = normalized ** (1 / 3)
    return FeatureResult(ok=True, score=score, raw=raw)


def extract_motion_energy(video_path: Path) -> FeatureResult:
    """Higher = more visual motion (action, camera movement, cuts).
    Uses ffmpeg's scene-change detection accumulated as a
    time-averaged score.

    Threshold 0.0 so we see every frame's diff; sum & normalize."""
    if not video_path.exists():
        return FeatureResult(ok=False, reason="file_not_found")

    # showinfo emits scene-change metadata per frame
    ok, _stdout, stderr = _run_ffmpeg_stats(
        video_path, "select='gt(scene,0.0)',showinfo",
    )
    if not ok:
        return FeatureResult(ok=False, reason=stderr[:100])

    # showinfo lines: [Parsed_showinfo_1 @ 0x...] n:X pts:Y ... type:I
    # We approximate motion via count of non-I-frame-selected-frames per second.
    duration = _get_duration_seconds(video_path)
    if duration is None or duration <= 0:
        return FeatureResult(ok=False, reason="no_duration")

    # Count showinfo lines = frames that had gt(scene,0) match =
    # frames with any detected scene-change delta. Divide by duration.
    frame_count = sum(
        1 for line in stderr.splitlines() if "Parsed_showinfo" in line and "n:" in line
    )
    frames_per_sec = frame_count / duration
    # A short-form vertical typically renders 30fps. Score = frames-with-motion
    # per second / 30, capped at 1.
    score = min(1.0, frames_per_sec / 30.0)
    return FeatureResult(ok=True, score=score, raw=frames_per_sec)


def extract_cut_frequency(video_path: Path) -> FeatureResult:
    """Score peaks at 2 cuts/sec (short-form ideal). Uses
    ``select='gt(scene,0.4)'`` for hard-cut detection (higher
    threshold than motion_energy).

    Piecewise score:
      * <0.5 cuts/sec: boring, score = raw × 2 (0-1 for 0-0.5/s)
      * 0.5-3 cuts/sec: sweet spot, score = 1.0
      * >3 cuts/sec: disorienting, score = max(0, 1 - (raw-3)*0.3)
    """
    if not video_path.exists():
        return FeatureResult(ok=False, reason="file_not_found")

    ok, _stdout, stderr = _run_ffmpeg_stats(
        video_path, "select='gt(scene,0.4)',showinfo",
    )
    if not ok:
        return FeatureResult(ok=False, reason=stderr[:100])

    duration = _get_duration_seconds(video_path)
    if duration is None or duration <= 0:
        return FeatureResult(ok=False, reason="no_duration")

    cut_count = sum(
        1 for line in stderr.splitlines() if "Parsed_showinfo" in line and "n:" in line
    )
    cuts_per_sec = cut_count / duration

    if cuts_per_sec < 0.5:
        score = cuts_per_sec * 2  # 0 → 0, 0.5 → 1.0
    elif cuts_per_sec <= 3.0:
        score = 1.0
    else:
        score = max(0.0, 1.0 - (cuts_per_sec - 3.0) * 0.3)
    return FeatureResult(ok=True, score=score, raw=cuts_per_sec)


def extract_brand_consistency(
    video_path: Path, brand_hex_color: str,
) -> FeatureResult:
    """Similarity between the video's average color and the niche's
    brand accent color. brand_hex_color like '#00D4FF'.

    Score = 1 - min(hue_distance / 180, 1). Values near 1 mean the
    video's dominant hue matches the brand; near 0 means opposite
    hue.
    """
    if not video_path.exists():
        return FeatureResult(ok=False, reason="file_not_found")

    brand_hex = brand_hex_color.lstrip("#")
    if len(brand_hex) != 6:
        return FeatureResult(ok=False, reason="bad_hex")
    try:
        br = int(brand_hex[0:2], 16)
        bg = int(brand_hex[2:4], 16)
        bb = int(brand_hex[4:6], 16)
    except ValueError:
        return FeatureResult(ok=False, reason="bad_hex")

    # Get average RGB via signalstats YAVG then convert to RGB approx.
    # Simpler: scale the video to 1×1 and read the pixel.
    try:
        proc = subprocess.run(
            [
                _ffmpeg_binary(), "-nostdin", "-hide_banner",
                "-i", str(video_path),
                "-vf", "scale=1:1,format=rgb24",
                "-frames:v", "1", "-f", "rawvideo", "-",
            ],
            capture_output=True, timeout=15,
        )
        if proc.returncode != 0 or len(proc.stdout) < 3:
            return FeatureResult(
                ok=False, reason=f"scale1x1_failed: {proc.stderr[:80].decode(errors='replace')}",
            )
        vr, vg, vb = proc.stdout[0], proc.stdout[1], proc.stdout[2]
    except Exception as exc:
        return FeatureResult(ok=False, reason=f"probe_failed: {exc}")

    brand_hue = _rgb_to_hue(br, bg, bb)
    video_hue = _rgb_to_hue(vr, vg, vb)
    diff = abs(brand_hue - video_hue)
    hue_distance = min(diff, 360 - diff)  # circular hue
    score = max(0.0, 1.0 - hue_distance / 180.0)
    return FeatureResult(ok=True, score=score, raw=hue_distance)


def _rgb_to_hue(r: int, g: int, b: int) -> float:
    """RGB to hue in [0, 360). Standard HSL conversion — hue only."""
    rf, gf, bf = r / 255.0, g / 255.0, b / 255.0
    cmax = max(rf, gf, bf)
    cmin = min(rf, gf, bf)
    delta = cmax - cmin
    if delta == 0:
        return 0.0
    if cmax == rf:
        h = ((gf - bf) / delta) % 6
    elif cmax == gf:
        h = (bf - rf) / delta + 2
    else:
        h = (rf - gf) / delta + 4
    return h * 60
