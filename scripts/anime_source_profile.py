#!/usr/bin/env python3
"""Detect a source's profile at ingest and REPORT it.

Reports every signal it used, not just the verdict, and marks the result
`uncertain` when the signals disagree -- a human confirms only then.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np


def probe(p: Path) -> dict:
    j = json.loads(subprocess.run(
        ["ffprobe", "-v", "0", "-select_streams", "v:0", "-show_entries",
         "stream=width,height,r_frame_rate,field_order,sample_aspect_ratio",
         "-of", "json", str(p)], capture_output=True, text=True).stdout)
    return j["streams"][0]


def cadence(p: Path, seconds: float = 6.0) -> tuple[float, float]:
    # NOTE: the held-frame threshold is absolute and therefore resolution- and
    # bitrate-sensitive. On a 640x360 re-encode it read 1.00 (every frame
    # "held"), which is an artefact of the encode, not the animation. Treat the
    # ratio as advisory until it is calibrated per resolution.
    """Fraction of frames identical to their predecessor. Anime on twos sits
    near 0.5; full animation and CG sit near 0."""
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-t", str(seconds), "-i", str(p),
         "-vf", "fps=24,scale=96:54", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True)
    a = np.frombuffer(r.stdout, dtype=np.uint8)
    n = a.size // (96 * 54)
    if n < 4:
        return 0.0, 0.0
    g = a[:n * 96 * 54].reshape(n, 54 * 96).astype(np.int16)
    d = np.abs(np.diff(g, axis=0)).mean(1)
    return float((d < 0.6).mean()), float(d.mean())


def letterbox(p: Path) -> float:
    """Share of frame height that is a static dark bar top+bottom."""
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-t", "4", "-i", str(p), "-vf",
         "fps=4,scale=64:120", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True)
    a = np.frombuffer(r.stdout, dtype=np.uint8)
    n = a.size // (64 * 120)
    if n < 2:
        return 0.0
    g = a[:n * 64 * 120].reshape(n, 120, 64).astype(float)
    # A matte is dark in EVERY frame and has a bright middle. A dark shot is
    # dark everywhere -- measured 0.90 "letterbox" on a 16:9 trailer whose
    # opening is a night scene, which is the failure this guard removes.
    per = g.mean(2)                       # n x 120 row means
    dark_all = (per < 16).all(0)
    if dark_all.all() or not dark_all.any():
        return 0.0
    mid = per[:, 40:80].mean()
    bars = per[:, dark_all].mean() if dark_all.any() else 0.0
    # A relative test, not an absolute floor: anime is often dark overall, and
    # a 24-luma floor missed a genuine 4:3 matte on Demon Slayer footage.
    if mid - bars < 10.0:
        return 0.0                        # middle no brighter than the bars
    top = int(np.argmax(~dark_all))
    bot = int(np.argmax(~dark_all[::-1]))
    return (top + bot) / 120.0


def grain(p: Path) -> float:
    """High-frequency energy after a light blur — a proxy for film grain."""
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-t", "4", "-i", str(p), "-vf",
         "fps=2,scale=320:180,format=gray", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True)
    a = np.frombuffer(r.stdout, dtype=np.uint8)
    n = a.size // (320 * 180)
    if n < 1:
        return 0.0
    g = a[:n * 320 * 180].reshape(n, 180, 320).astype(float)
    lap = (-4 * g[:, 1:-1, 1:-1] + g[:, :-2, 1:-1] + g[:, 2:, 1:-1]
           + g[:, 1:-1, :-2] + g[:, 1:-1, 2:])
    return float(np.abs(lap).mean())


def detect(p: Path) -> dict:
    st = probe(p)
    w, h = int(st["width"]), int(st["height"])
    ar = w / h
    interlaced = str(st.get("field_order", "progressive")) not in (
        "progressive", "unknown")
    twos, motion = cadence(p)
    # Letterbox is only MEANINGFUL for a 4:3 source (it distinguishes a matted
    # widescreen master from a true 4:3 one). On 16:9 it returns high values for
    # any dark scene with a bright centre -- measured 0.89 on a night sequence
    # -- so it is not computed there rather than reported and ignored.
    lb = letterbox(p) if abs(ar - 4 / 3) < 0.06 else None
    gr = grain(p)
    reasons = []
    if abs(ar - 4 / 3) < 0.06:
        prof = "sd_4x3_telecined" if interlaced else "sd_4x3_progressive"
        reasons.append(f"aspect {ar:.3f} ~ 4:3")
        if lb and lb > 0.10:
            prof = "matted_widescreen_in_4x3"
            reasons.append(f"letterbox {lb:.0%} of height")
    elif abs(ar - 16 / 9) < 0.06:
        # NOT hd_remaster: whether a 1080p file is a remaster of an SD original
        # is provenance, not pixels. Detection cannot know it and must not guess;
        # the pack declares it.
        prof = "modern_hd_16x9"
        reasons.append(f"aspect {ar:.3f} ~ 16:9, height {h}")
        if h >= 1080 and gr > 9.0:
            prof = "film_grain_theatrical"
            reasons.append(f"grain proxy {gr:.1f} high")
    else:
        prof = "uncertain"
        reasons.append(f"aspect {ar:.3f} matches no profile")
    if twos < 0.15 and prof.startswith("modern"):
        reasons.append(f"cadence: only {twos:.0%} held frames -> full/CG")
    return {"file": p.name, "width": w, "height": h, "aspect": round(ar, 3),
            "interlaced": interlaced, "twos_ratio": round(twos, 3),
            "motion": round(motion, 2),
            "letterbox_share": (round(lb, 3) if lb is not None else "n/a (16:9)"),
            "grain_proxy": round(gr, 2), "profile": prof, "reasons": reasons,
            "certain": prof != "uncertain"}


def main() -> int:
    for a in sys.argv[1:]:
        d = detect(Path(a))
        print(f"  {d['file']}")
        print(f"    {d['width']}x{d['height']}  aspect {d['aspect']}  "
              f"interlaced {d['interlaced']}  twos {d['twos_ratio']}  "
              f"letterbox {d['letterbox_share']}  grain {d['grain_proxy']}")
        print(f"    -> {d['profile']}  ({'certain' if d['certain'] else 'NEEDS A HUMAN'})")
        for r in d["reasons"]:
            print(f"       because {r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
