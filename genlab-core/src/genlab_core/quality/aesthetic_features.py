"""Aesthetic composition features from a video keyframe (Phase 4.B session 1).

Extracts ~15 hand-crafted features per keyframe. Session 2 trains
logistic regression on top-20/bottom-20 reward labels; session 3
uses trained coefficients to score new renders pre-publish.

## Feature list

**Rule of thirds** (0-1 higher = better composition)
  * rot_horizontal_score — content concentration near 1/3, 2/3 h-lines
  * rot_vertical_score — content concentration near 1/3, 2/3 v-lines

**Symmetry** (0-1 higher = more symmetric)
  * horizontal_symmetry — pixel-mirror similarity across v-axis
  * vertical_symmetry — pixel-mirror similarity across h-axis

**Edge density** (0-1 higher = busier composition)
  * edge_density — fraction of pixels above local-gradient threshold

**Color harmony** (0-1 higher = more harmonious)
  * hue_variance — inverted; low hue variance = tight palette
  * saturation_mean — average saturation
  * saturation_variance — 1-normalized

**Brightness / contrast**
  * brightness_mean — average luma
  * brightness_variance — normalized
  * brightness_entropy — histogram entropy (higher = full dynamic range)

**Composition / focus**
  * center_weight — brightness weighted toward center vs edges
  * top_bottom_balance — luma balance top vs bottom half
  * left_right_balance — luma balance left vs right half

**Frame metadata**
  * aspect_ratio — width/height (informational, ~0.56 for 9:16)

## Design constraints (same as sessions 1-3 of 4.A)

Zero heavy CV deps. FFmpeg extracts a scaled-down (64×64) keyframe
as raw RGB bytes → pure-Python computation. 64×64 gives 4096 pixels
per feature — enough for rank statistics without heap pressure.
"""
from __future__ import annotations

import logging
import math
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Keyframe grid — small enough for stdlib-only computation but
# large enough to preserve composition structure.
_GRID = 64
_MIDFRAME_SECS = 1.0  # extract at 1s in (past intro, before outro)


@dataclass(frozen=True)
class AestheticFeatures:
    """15-feature vector. All floats [0, 1] except aspect_ratio.
    ``ok`` flags whether extraction succeeded end-to-end."""
    ok: bool
    reason: str = ""
    rot_horizontal_score: float = 0.0
    rot_vertical_score: float = 0.0
    horizontal_symmetry: float = 0.0
    vertical_symmetry: float = 0.0
    edge_density: float = 0.0
    hue_variance: float = 0.0
    saturation_mean: float = 0.0
    saturation_variance: float = 0.0
    brightness_mean: float = 0.0
    brightness_variance: float = 0.0
    brightness_entropy: float = 0.0
    center_weight: float = 0.0
    top_bottom_balance: float = 0.0
    left_right_balance: float = 0.0
    aspect_ratio: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def _ffmpeg_binary() -> str:
    from genlab_core.media.ffmpeg import get_ffmpeg_binary
    return get_ffmpeg_binary()


def _extract_keyframe_rgb(video_path: Path) -> bytes | None:
    """Extract 1 keyframe from ``_MIDFRAME_SECS`` in, scaled to
    64x64, as raw RGB. Returns None on any failure."""
    try:
        cmd = [
            _ffmpeg_binary(), "-nostdin", "-hide_banner",
            "-ss", str(_MIDFRAME_SECS),
            "-i", str(video_path),
            "-vf", f"scale={_GRID}:{_GRID}:force_original_aspect_ratio=disable,format=rgb24",
            "-frames:v", "1",
            "-f", "rawvideo", "-",
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=15)
        if proc.returncode != 0:
            return None
        expected = _GRID * _GRID * 3
        if len(proc.stdout) != expected:
            return None
        return proc.stdout
    except Exception as exc:
        logger.warning("[aesthetic] frame extract failed %s: %s", video_path, exc)
        return None


def _probe_aspect(video_path: Path) -> float:
    """width/height for the informational aspect_ratio feature."""
    try:
        from genlab_core.media.ffmpeg import get_ffprobe_binary
        proc = subprocess.run(
            [
                get_ffprobe_binary(), "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=p=0",
                str(video_path),
            ],
            capture_output=True, text=True, timeout=10,
        )
        parts = proc.stdout.strip().split(",")
        if len(parts) >= 2:
            w = int(parts[0])
            h = int(parts[1])
            return w / h if h > 0 else 0.0
    except Exception:
        return 0.0
    return 0.0


# ── Pure-Python feature computation ──────────────────────────────


def _pixels(rgb: bytes) -> list[tuple[int, int, int]]:
    """Slice raw RGB into (r, g, b) tuples."""
    return [
        (rgb[i], rgb[i + 1], rgb[i + 2])
        for i in range(0, len(rgb), 3)
    ]


def _luma(px: tuple[int, int, int]) -> float:
    """Rec.709 luma weights."""
    return 0.2126 * px[0] + 0.7152 * px[1] + 0.0722 * px[2]


def _hsv(px: tuple[int, int, int]) -> tuple[float, float, float]:
    """RGB → (hue [0,360), sat [0,1], val [0,1])."""
    r, g, b = px[0] / 255.0, px[1] / 255.0, px[2] / 255.0
    cmax = max(r, g, b)
    cmin = min(r, g, b)
    delta = cmax - cmin
    if delta == 0:
        h = 0.0
    elif cmax == r:
        h = (60 * ((g - b) / delta) + 360) % 360
    elif cmax == g:
        h = (60 * ((b - r) / delta) + 120) % 360
    else:
        h = (60 * ((r - g) / delta) + 240) % 360
    s = 0.0 if cmax == 0 else delta / cmax
    v = cmax
    return h, s, v


def _rule_of_thirds_horizontal(luma_grid: list[list[float]]) -> float:
    """Higher when brighter/darker pixels concentrate near
    y=1/3 and y=2/3 (both directions from average). 0 when
    uniform across rows."""
    row_means = [sum(row) / len(row) for row in luma_grid]
    total_mean = sum(row_means) / len(row_means)
    third1 = int(_GRID / 3)
    third2 = int(2 * _GRID / 3)
    n = _GRID
    third_dev = 0.0
    other_dev = 0.0
    band = max(1, n // 12)  # ~5px band around each 1/3 line
    for i, m in enumerate(row_means):
        dev = abs(m - total_mean)
        near_third = (
            abs(i - third1) <= band or abs(i - third2) <= band
        )
        if near_third:
            third_dev += dev
        else:
            other_dev += dev
    denom = third_dev + other_dev
    return third_dev / denom if denom > 0 else 0.0


def _rule_of_thirds_vertical(luma_grid: list[list[float]]) -> float:
    """Column-version of _rule_of_thirds_horizontal."""
    col_means = [
        sum(luma_grid[r][c] for r in range(_GRID)) / _GRID
        for c in range(_GRID)
    ]
    total_mean = sum(col_means) / len(col_means)
    third1 = int(_GRID / 3)
    third2 = int(2 * _GRID / 3)
    band = max(1, _GRID // 12)
    third_dev = 0.0
    other_dev = 0.0
    for i, m in enumerate(col_means):
        dev = abs(m - total_mean)
        near_third = (
            abs(i - third1) <= band or abs(i - third2) <= band
        )
        if near_third:
            third_dev += dev
        else:
            other_dev += dev
    denom = third_dev + other_dev
    return third_dev / denom if denom > 0 else 0.0


def _horizontal_symmetry(luma_grid: list[list[float]]) -> float:
    """Mirror across vertical axis. 1 = perfect symmetry."""
    total = 0.0
    count = 0
    for r in range(_GRID):
        for c in range(_GRID // 2):
            l = luma_grid[r][c]
            mirror = luma_grid[r][_GRID - 1 - c]
            total += 1 - abs(l - mirror) / 255.0
            count += 1
    return total / count if count > 0 else 0.0


def _vertical_symmetry(luma_grid: list[list[float]]) -> float:
    total = 0.0
    count = 0
    for r in range(_GRID // 2):
        for c in range(_GRID):
            l = luma_grid[r][c]
            mirror = luma_grid[_GRID - 1 - r][c]
            total += 1 - abs(l - mirror) / 255.0
            count += 1
    return total / count if count > 0 else 0.0


def _edge_density(luma_grid: list[list[float]]) -> float:
    """Fraction of pixels where local gradient exceeds threshold.
    Sobel-like: horizontal + vertical neighbor diffs."""
    edges = 0
    total = 0
    threshold = 30.0  # luma diff threshold
    for r in range(1, _GRID - 1):
        for c in range(1, _GRID - 1):
            gx = abs(luma_grid[r][c + 1] - luma_grid[r][c - 1])
            gy = abs(luma_grid[r + 1][c] - luma_grid[r - 1][c])
            grad = math.sqrt(gx * gx + gy * gy)
            if grad > threshold:
                edges += 1
            total += 1
    return edges / total if total > 0 else 0.0


def _brightness_entropy(luma_flat: list[float]) -> float:
    """Shannon entropy of 16-bin luma histogram, normalized to
    [0, 1] where 1.0 = uniform distribution across bins."""
    bins = [0] * 16
    for l in luma_flat:
        idx = min(15, int(l / 16))
        bins[idx] += 1
    total = sum(bins)
    if total == 0:
        return 0.0
    h = 0.0
    for c in bins:
        if c > 0:
            p = c / total
            h -= p * math.log2(p)
    return h / 4.0  # max entropy for 16 bins is log2(16)=4


def _center_weight(luma_grid: list[list[float]]) -> float:
    """Ratio of mean luma in central 1/2 quadrant vs edge regions.
    >0.5 = subject is centered; <0.5 = subject is edge-weighted."""
    q = _GRID // 4
    center_sum = 0.0
    center_count = 0
    edge_sum = 0.0
    edge_count = 0
    for r in range(_GRID):
        for c in range(_GRID):
            l = luma_grid[r][c]
            in_center = (q <= r < 3 * q) and (q <= c < 3 * q)
            if in_center:
                center_sum += l
                center_count += 1
            else:
                edge_sum += l
                edge_count += 1
    if center_count == 0 or edge_count == 0:
        return 0.5
    center_mean = center_sum / center_count
    edge_mean = edge_sum / edge_count
    total = center_mean + edge_mean
    return center_mean / total if total > 0 else 0.5


def _balance_score(a: float, b: float) -> float:
    """1 = perfect balance between two half-frames. 0 = one entirely dark."""
    if a + b == 0:
        return 0.0
    diff = abs(a - b) / (a + b)
    return 1.0 - diff


def extract_aesthetic_features(video_path: Path) -> AestheticFeatures:
    """Full pipeline: extract keyframe → compute 15 features. Returns
    ``AestheticFeatures(ok=False, reason=...)`` on any failure."""
    if not video_path.exists():
        return AestheticFeatures(ok=False, reason="file_not_found")

    rgb = _extract_keyframe_rgb(video_path)
    if rgb is None:
        return AestheticFeatures(ok=False, reason="keyframe_extract_failed")

    pixels = _pixels(rgb)
    if len(pixels) != _GRID * _GRID:
        return AestheticFeatures(
            ok=False, reason=f"unexpected_pixel_count:{len(pixels)}",
        )

    luma_flat = [_luma(p) for p in pixels]
    luma_grid: list[list[float]] = [
        luma_flat[r * _GRID:(r + 1) * _GRID] for r in range(_GRID)
    ]

    # HSV-derived features
    hues = []
    sats = []
    for p in pixels:
        h, s, v = _hsv(p)
        if s > 0.05:  # skip near-gray pixels — hue is undefined
            hues.append(h)
        sats.append(s)
    if hues:
        hue_mean_ang = sum(math.sin(math.radians(h)) for h in hues) / len(hues), \
                       sum(math.cos(math.radians(h)) for h in hues) / len(hues)
        # Circular variance in [0, 1] where 0 = tight hue distribution
        circular_var = 1 - math.sqrt(hue_mean_ang[0] ** 2 + hue_mean_ang[1] ** 2)
        hue_variance_score = 1.0 - min(1.0, max(0.0, circular_var))
    else:
        hue_variance_score = 0.0

    sat_mean = sum(sats) / len(sats) if sats else 0.0
    sat_variance = 0.0
    if len(sats) > 1:
        sat_variance = sum((s - sat_mean) ** 2 for s in sats) / len(sats)
    sat_var_norm = min(1.0, sat_variance * 4.0)  # empirical scale

    # Brightness statistics
    bright_mean = (sum(luma_flat) / len(luma_flat)) / 255.0
    bright_var = 0.0
    if len(luma_flat) > 1:
        m = sum(luma_flat) / len(luma_flat)
        bright_var = sum((l - m) ** 2 for l in luma_flat) / len(luma_flat)
    bright_var_norm = min(1.0, bright_var / (127 ** 2))

    bright_entropy = _brightness_entropy(luma_flat)

    # Balance features
    top_half = sum(luma_grid[r][c] for r in range(_GRID // 2) for c in range(_GRID))
    bot_half = sum(
        luma_grid[r][c] for r in range(_GRID // 2, _GRID) for c in range(_GRID)
    )
    left_half = sum(
        luma_grid[r][c] for r in range(_GRID) for c in range(_GRID // 2)
    )
    right_half = sum(
        luma_grid[r][c] for r in range(_GRID) for c in range(_GRID // 2, _GRID)
    )

    return AestheticFeatures(
        ok=True,
        rot_horizontal_score=_rule_of_thirds_horizontal(luma_grid),
        rot_vertical_score=_rule_of_thirds_vertical(luma_grid),
        horizontal_symmetry=_horizontal_symmetry(luma_grid),
        vertical_symmetry=_vertical_symmetry(luma_grid),
        edge_density=_edge_density(luma_grid),
        hue_variance=hue_variance_score,
        saturation_mean=sat_mean,
        saturation_variance=sat_var_norm,
        brightness_mean=bright_mean,
        brightness_variance=bright_var_norm,
        brightness_entropy=bright_entropy,
        center_weight=_center_weight(luma_grid),
        top_bottom_balance=_balance_score(top_half, bot_half),
        left_right_balance=_balance_score(left_half, right_half),
        aspect_ratio=_probe_aspect(video_path),
    )
