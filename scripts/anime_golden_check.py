#!/usr/bin/env python3
"""Golden-set regression: measure a render and diff it against the baseline.

A pipeline change that regresses the golden set does not merge. "Regress" is
defined per metric, not as "differs" -- an edit that improves smoothness should
not fail because the number moved.

Usage:
  anime_golden_check.py measure <render.mp4> <render_log.json> [-o out.json]
  anime_golden_check.py diff <candidate.json> [--baseline path]
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "genlab-core/src"))
from genlab_core.still import pacing as PA  # noqa: E402
from genlab_core.still import smoothness as SM

GOLDEN = Path(__file__).resolve().parents[1] / \
    "niches/anime/edit_lane/golden/demon_slayer_akaza"

# direction: +1 = higher is better, -1 = lower is better, 0 = must not change
DIRECTION = {
    "duration_s": 0, "width": 0, "height": 0, "fps": 0,
    "unique_per_s": +1, "freezes": -1, "alternations": -1,
    "unauthored_black": -1, "unauthored_white": -1,
    "cuts_per_min": 0, "shot_count": 0, "min_shot_s": +1,
    "noise_floor_db": +1, "loudness_lufs": 0, "true_peak_dbtp": -1,
    "plate_aspects": 0,
}
# how much a metric may move before it counts as a regression
TOLERANCE = {"duration_s": 0.10, "unique_per_s": 0.50, "noise_floor_db": 2.0,
             "loudness_lufs": 0.6, "true_peak_dbtp": 0.5, "min_shot_s": 0.05,
             "cuts_per_min": 2.0, "shot_count": 0}


def measure(video: Path, logp: Path) -> dict:
    log = json.loads(logp.read_text())
    sm = SM.measure(video, fps=24)
    det = PA.measure(video, fps=24)
    segs = log["segments"]
    pac = PA.from_segments(segs, sum(s["out_s"] for s in segs), 24)
    probe = subprocess.run(
        ["ffprobe", "-v", "0", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate", "-show_entries", "format=duration",
         "-of", "json", str(video)], capture_output=True, text=True).stdout
    pj = json.loads(probe)
    st = pj["streams"][0]
    with tempfile.TemporaryDirectory() as td:
        w = Path(td) / "a.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vn",
                        "-ar", "48000", "-ac", "1", str(w)], check=True)
        with wave.open(str(w)) as f:
            a = np.frombuffer(f.readframes(f.getnframes()),
                              dtype=np.int16).astype(np.float32) / 32768
    # Skip AAC priming, exactly as the renderer's floor gate does. Without this
    # the two measurements of the same quantity disagree by ~26 dB and the
    # harness reports a regression that is an artefact of its own instrument.
    nz = int(np.argmax(np.abs(a) > 1e-6)) if np.any(np.abs(a) > 1e-6) else 0
    a = a[nz:]
    n = len(a) // 2400
    rms = np.sqrt((a[:n * 2400].reshape(n, 2400) ** 2).mean(1))
    floor = float(20 * np.log10(np.maximum(rms, 1e-9)).min())
    e = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(video),
                        "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True).stderr
    lufs = tp = None
    for ln in reversed(e.splitlines()):
        if lufs is None and "I:" in ln and "LUFS" in ln:
            lufs = float(ln.split("I:")[1].split("LUFS")[0])
        if tp is None and "Peak:" in ln and "dBFS" in ln:
            tp = float(ln.split("Peak:")[1].split("dBFS")[0])
    auth = dict(log.get("smoothness") or {})
    if not auth.get("authored_holds"):
        # Fall back to the segment record: a segment marked black IS the
        # authored gap, and its frames are authored by construction.
        gap = [s for s in segs if s.get("aspect") and s.get("out_s", 0) <= 0.2
               and s.get("i", 0) < 0]
        frames = []
        for g in gap:
            t0 = g["reel_start"]
            k = 0
            while t0 + k / 24 < t0 + g["out_s"] + 1 / 24:
                frames.append(round(t0 + k / 24, 3)); k += 1
        auth["authored_holds"] = frames
    return {
        "duration_s": round(float(pj["format"]["duration"]), 3),
        "width": st["width"], "height": st["height"], "fps": st["r_frame_rate"],
        "unique_per_s": round(sm.unique_per_s, 2),
        "freezes": len(sm.freezes), "alternations": len(sm.alternations),
        "unauthored_black": len([t for t in sm.black
                                 if not any(abs(t - x) <= .1
                                            for x in auth.get("authored_holds", []))]),
        "unauthored_white": len(sm.white),
        "cuts_per_min": round(pac.cuts_per_min, 2),
        "shot_count": len(pac.shot_lengths),
        "min_shot_s": round(min(pac.shot_lengths, default=0), 3),
        "noise_floor_db": round(floor, 2),
        "loudness_lufs": lufs, "true_peak_dbtp": tp,
        "plate_aspects": sorted({s["aspect"] for s in segs}),
        "detector_cuts_advisory": len(det.cut_times),
    }


def diff(cand: dict, base: dict) -> tuple[list, list]:
    regress, moved = [], []
    for k, d in DIRECTION.items():
        if k not in base or k not in cand:
            continue
        b, c = base[k], cand[k]
        if isinstance(b, list) or isinstance(b, str):
            if b != c:
                regress.append(f"{k}: {b} -> {c} (must not change)")
            continue
        if b is None or c is None:
            continue
        tol = TOLERANCE.get(k, 0)
        delta = c - b
        if d == 0:
            if abs(delta) > tol:
                regress.append(f"{k}: {b} -> {c} ({delta:+.3f}, tol {tol})")
        elif d * delta < -tol:
            regress.append(f"{k}: {b} -> {c} ({delta:+.3f}, worse)")
        elif abs(delta) > tol:
            moved.append(f"{k}: {b} -> {c} ({delta:+.3f}, better)")
    return regress, moved


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 0
    if sys.argv[1] == "measure":
        m = measure(Path(sys.argv[2]), Path(sys.argv[3]))
        out = Path(sys.argv[sys.argv.index("-o") + 1]) if "-o" in sys.argv else None
        (out or Path("/dev/stdout")).write_text(json.dumps(m, indent=2))
        return 0
    if sys.argv[1] == "diff":
        cand = json.loads(Path(sys.argv[2]).read_text())
        bp = Path(sys.argv[sys.argv.index("--baseline") + 1]) \
            if "--baseline" in sys.argv else GOLDEN / "baseline.json"
        base = json.loads(bp.read_text())
        regress, moved = diff(cand, base)
        print(f"  golden: {bp}")
        for m in moved:
            print(f"    improved  {m}")
        for r in regress:
            print(f"    REGRESSED {r}")
        print(f"\n  {'REGRESSION — do not merge' if regress else 'no regression'}")
        return 1 if regress else 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
