"""Measure a reel the way the corpus measures it. One function, both sides.

ANIME-14 §9. "Loads of room" becomes a number per dimension only if OUR reel
and THEIR reel are measured by the same code with the same thresholds. Every
metric here therefore carries its detector and its threshold in the name or
in the returned record, because a cut count without its method is not a
measurement — two ffmpeg detectors disagreed 2.4-3x at nominally the same
threshold on this project's own renders.

Nothing here is a gate. It measures; ``anime_reference.json`` turns the
measurements into percentiles and the gates quote those.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

#: A HARD CUT. Scene score above this is a shot boundary.
CUT_THRESHOLD = 0.30
#: A VISUAL CHANGE. Lower, so it also catches a zoom pulse, a flash or a text
#: pop inside one shot — the "interrupt" the genre uses. Strictly a superset
#: of the cuts, which is why changes/20s is always >= cuts over the same span.
CHANGE_THRESHOLD = 0.10
#: freezedetect noise floor and minimum run, for longest_static.
FREEZE_NOISE_DB = -55
FREEZE_MIN_S = 0.5
#: OCR sampling. Dense inside the hook window, sparse after it: the hook's
#: ONSET needs 0.5s resolution, but text density is an average and 2s
#: sampling estimates it just as well. At 0.5s throughout, a 174s item cost
#: 348 tesseract calls and the 34-reel corpus projected to ~2.5 hours.
OCR_STRIDE_S = 0.5
OCR_STRIDE_AFTER_HOOK_S = 2.0
#: Hook window — the first seconds where a tension line must appear.
HOOK_WINDOW_S = 3.0


@dataclass
class ReelMetrics:
    source: str = ""
    path: str = ""
    duration_s: float = 0.0
    cuts: int = 0
    cuts_per_min: float = 0.0
    shot_lengths_s: list[float] = field(default_factory=list)
    shot_length_mean_s: float = 0.0
    shot_length_sd_s: float = 0.0
    shot_length_cv: float = 0.0
    longest_static_s: float = 0.0
    visual_changes: int = 0
    changes_per_20s: float = 0.0
    text_density_mean: float = 0.0
    text_density_max: float = 0.0
    hook_text_onset_s: float | None = None
    hook_word_count: int = 0
    hook_text: str = ""
    music_present: bool = False
    music_bpm: float = 0.0
    music_lift: float = 0.0
    ok: bool = True
    error: str = ""
    detectors: dict = field(default_factory=dict)


def _run(cmd: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def duration_s(path: Path) -> float:
    r = _run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        120,
    )
    try:
        return float((r.stdout or "0").strip())
    except ValueError:
        return 0.0


def _scene_times(path: Path, threshold: float) -> list[float]:
    """Scene-change timestamps. ``-hide_banner -nostats``, never ``-v error``:
    the latter SILENCES metadata=print, so a muted instrument reads as 'this
    clip has no cuts'."""
    r = _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-an",
            "-vf",
            f"select='gt(scene,{threshold})',metadata=print:file=-",
            "-f",
            "null",
            "-",
        ],
        900,
    )
    out = []
    for line in (r.stdout or "").splitlines():
        if "pts_time:" in line:
            try:
                out.append(float(line.split("pts_time:")[1].split()[0]))
            except (IndexError, ValueError):
                continue
    return out


def longest_static(path: Path) -> float:
    r = _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-an",
            "-vf",
            f"freezedetect=n={FREEZE_NOISE_DB}dB:d={FREEZE_MIN_S}",
            "-f",
            "null",
            "-",
        ],
        900,
    )
    runs = [
        float(ln.split("freeze_duration:")[1].split()[0])
        for ln in (r.stderr or "").splitlines()
        if "freeze_duration:" in ln
    ]
    return max(runs) if runs else 0.0


#: Languages the OCR gate reads. ANIME-16 §1: running the ENGLISH model over
#: an anime PV means Japanese broadcast furniture is invisible to it, and
#: "オープニング・テーマ" sat in three shipped frames of reel B because of it.
#: jpn_vert matters as much as jpn — the credit block is set vertically.
OCR_LANGS = "jpn+jpn_vert+eng"

#: The CORPUS was measured at eng / 540px. Those defaults are FROZEN: change
#: them and every published corpus percentile silently describes a different
#: measurement than our reels are scored against. The stricter settings are
#: opt-in and the PV window gate passes them explicitly.
CORPUS_LANGS = "eng"
CORPUS_SCALE = 540
GATE_SCALE = 1080


def ocr_frame(
    path: Path, t: float, tmp: Path, langs: str = CORPUS_LANGS, scale: int = CORPUS_SCALE
) -> tuple[float, str]:
    """(fraction of frame area covered by detected text, the text).

    Tesseract TSV so the boxes are available; a word's box area is summed and
    divided by the frame area. Confidence below 60 is dropped — anime frames
    are full of shapes tesseract will happily call letters.

    The threshold is on detected-region AREA, not on recognised characters,
    so a language the model reads badly still registers as text present.
    """
    if not shutil.which("tesseract"):
        raise RuntimeError("tesseract not installed — text density unmeasurable")
    png = tmp / f"ocr_{t:.2f}.png"
    _run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(path),
            "-ss",
            f"{t:.3f}",
            "-frames:v",
            "1",
            "-vf",
            "scale=540:-2",
            str(png),
        ],
        120,
    )
    if not png.exists():
        return 0.0, ""
    r = _run(["tesseract", str(png), "stdout", "-l", langs, "--psm", "11", "tsv"], 300)
    area = 0.0
    words: list[str] = []
    W = H = 0
    for i, line in enumerate((r.stdout or "").splitlines()):
        parts = line.split("\t")
        if i == 0 or len(parts) < 12:
            continue
        try:
            conf = float(parts[10])
            w, h = int(parts[8]), int(parts[9])
            lvl = int(parts[0])
        except ValueError:
            continue
        if lvl == 1:
            W, H = w, h
            continue
        text = parts[11].strip()
        if conf >= 60 and text and len(text) > 1:
            area += w * h
            words.append(text)
    png.unlink(missing_ok=True)
    frame_area = (W * H) if W and H else (scale * scale * 16 // 9)
    return (area / frame_area if frame_area else 0.0), " ".join(words)


def measure(
    path: Path, *, source: str = "", do_ocr: bool = True, tmp: Path | None = None
) -> ReelMetrics:
    """Every dimension the corpus compares on."""
    m = ReelMetrics(source=source, path=str(path))
    m.detectors = {
        "cuts": f"ffmpeg select='gt(scene,{CUT_THRESHOLD})'",
        "visual_changes": f"ffmpeg select='gt(scene,{CHANGE_THRESHOLD})'",
        "longest_static": f"ffmpeg freezedetect n={FREEZE_NOISE_DB}dB d={FREEZE_MIN_S}",
        "text": "tesseract --psm 11 tsv, conf>=60, boxes summed / frame area",
        "music": "genlab_core.action.grid.detect_grid, lift>0.02",
    }
    try:
        m.duration_s = duration_s(path)
        if m.duration_s <= 0:
            m.ok, m.error = False, "unreadable duration"
            return m

        cuts = _scene_times(path, CUT_THRESHOLD)
        m.cuts = len(cuts)
        m.cuts_per_min = len(cuts) * 60.0 / m.duration_s
        bounds = [0.0] + cuts + [m.duration_s]
        m.shot_lengths_s = [round(b - a, 3) for a, b in zip(bounds, bounds[1:], strict=False)]
        n = len(m.shot_lengths_s)
        mean = sum(m.shot_lengths_s) / n
        var = sum((x - mean) ** 2 for x in m.shot_lengths_s) / n
        m.shot_length_mean_s = round(mean, 3)
        m.shot_length_sd_s = round(var**0.5, 3)
        m.shot_length_cv = round((var**0.5) / mean, 3) if mean else 0.0

        m.visual_changes = len(_scene_times(path, CHANGE_THRESHOLD))
        m.changes_per_20s = m.visual_changes * 20.0 / m.duration_s
        m.longest_static_s = longest_static(path)

        if do_ocr:
            tmpd = tmp or path.parent / ".ocr"
            tmpd.mkdir(parents=True, exist_ok=True)
            densities: list[float] = []
            t = 0.0
            while t < m.duration_s:
                frac, text = ocr_frame(path, t, tmpd)
                densities.append(frac)
                if m.hook_text_onset_s is None and t <= HOOK_WINDOW_S and frac >= 0.005:
                    m.hook_text_onset_s = round(t, 2)
                    m.hook_text = text[:120]
                    m.hook_word_count = len(text.split())
                t += OCR_STRIDE_S if t < HOOK_WINDOW_S else OCR_STRIDE_AFTER_HOOK_S
            if densities:
                m.text_density_mean = round(sum(densities) / len(densities), 4)
                m.text_density_max = round(max(densities), 4)

        try:
            from genlab_core.action.grid import detect_grid

            g = detect_grid(str(path))
            m.music_bpm = round(g.bpm, 1)
            m.music_lift = round(g.lift, 4)
            m.music_present = g.lift > 0.02
        except Exception as e:  # noqa: BLE001
            logger.info("[reference] no music grid for %s: %s", path.name, e)
    except Exception as e:  # noqa: BLE001
        m.ok, m.error = False, str(e)[:200]
    return m


def percentiles(values: list[float], ps=(10, 25, 50, 75, 90)) -> dict:
    if not values:
        return {}
    s = sorted(values)

    def at(p: float) -> float:
        if len(s) == 1:
            return s[0]
        k = (len(s) - 1) * p / 100.0
        lo, hi = int(k), min(int(k) + 1, len(s) - 1)
        return s[lo] + (s[hi] - s[lo]) * (k - lo)

    return {f"p{p}": round(at(p), 4) for p in ps} | {
        "n": len(s),
        "min": round(s[0], 4),
        "max": round(s[-1], 4),
        "mean": round(sum(s) / len(s), 4),
    }


DIMENSIONS = (
    "cuts_per_min",
    "longest_static_s",
    "changes_per_20s",
    "shot_length_cv",
    "shot_length_mean_s",
    "text_density_mean",
    "text_density_max",
    "hook_text_onset_s",
    "hook_word_count",
    "duration_s",
)


def build_reference(metrics: list[ReelMetrics]) -> dict:
    good = [m for m in metrics if m.ok]
    ref = {
        "n_reels": len(good),
        "n_failed": len(metrics) - len(good),
        "detectors": good[0].detectors if good else {},
        "dimensions": {},
    }
    for dim in DIMENSIONS:
        vals = [getattr(m, dim) for m in good if getattr(m, dim) is not None]
        vals = [float(v) for v in vals if isinstance(v, (int, float))]
        ref["dimensions"][dim] = percentiles(vals)
    ref["music_present_fraction"] = (
        round(sum(1 for m in good if m.music_present) / len(good), 3) if good else 0.0
    )
    ref["hook_text_fraction"] = (
        round(sum(1 for m in good if m.hook_text_onset_s is not None) / len(good), 3)
        if good
        else 0.0
    )
    return ref


def score_against(m: ReelMetrics, ref: dict) -> dict:
    """Where one reel sits against the corpus, per dimension.

    ``higher_is_better`` is stated per dimension rather than assumed: more
    cuts is more genre-typical, a longer static hold is not.
    """
    higher_better = {
        "cuts_per_min": True,
        "changes_per_20s": True,
        "shot_length_cv": True,
        "longest_static_s": False,
        "text_density_mean": None,
        "text_density_max": None,
        "shot_length_mean_s": None,
        "duration_s": None,
        "hook_text_onset_s": False,
        "hook_word_count": None,
    }
    out = {}
    for dim, stats in ref.get("dimensions", {}).items():
        if not stats:
            continue
        v = getattr(m, dim, None)
        if v is None:
            out[dim] = {"value": None, "p50": stats.get("p50"), "verdict": "not measured"}
            continue
        p50 = stats.get("p50")
        hb = higher_better.get(dim)
        if hb is None:
            verdict = "in band" if stats["p10"] <= v <= stats["p90"] else "outside p10-p90"
        elif hb:
            verdict = "at or above P50" if v >= p50 else "below P50"
        else:
            verdict = "at or below P50" if v <= p50 else "above P50"
        out[dim] = {
            "value": round(float(v), 4),
            "p50": p50,
            "p10": stats.get("p10"),
            "p90": stats.get("p90"),
            "verdict": verdict,
        }
    return out


def save(metrics: list[ReelMetrics], ref: dict, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps({"reference": ref, "reels": [asdict(m) for m in metrics]}, indent=2))
    return dest
