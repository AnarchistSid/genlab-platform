"""Pacing gate — is the reel cut too fast, too choppy, or too flashy?

Smoothness asks whether motion is continuous WITHIN a shot. This asks whether
the shots themselves are of a sane length and whether punctuation is being
spent sensibly. The two fail independently: v21 passes smoothness and fails
this, because a reel can be perfectly fluid inside every shot and still be an
unreadable strobe.

Thresholds are the anime reference corpus's own, not invented: the cuts/min
ceiling is the corpus p75.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

CUTS_PER_MIN_MAX = 58.7      # anime_reference.json, 29 reels, p75
# Total-variation distance between consecutive luma histograms. Calibrated
# against two controls: a genuinely cut-free shot rendered with the reel's own
# tight 9:16 crop fires ZERO times at this threshold, and the delivered reel
# fires ~ its authored boundary count. `select='gt(scene,N)'` cannot be used
# here -- on a tight crop of a fast-moving wide shot it reported six "cuts"
# spaced exactly two frames apart in a region the source has no cut in at all,
# which read as a shot 0.04 s long.
CUT_HIST_DISTANCE = 0.15
MIN_SHOT_S = 0.5
MAX_ONE_FRAMERS = 2
MAX_WHITE_FLASHES = 3


@dataclass
class Pacing:
    duration_s: float
    fps: float
    cut_times: list[float] = field(default_factory=list)
    shot_lengths: list[float] = field(default_factory=list)
    white_flashes: list[float] = field(default_factory=list)
    black_frames: list[float] = field(default_factory=list)

    @property
    def cuts_per_min(self) -> float:
        return len(self.cut_times) * 60.0 / max(self.duration_s, 1e-6)

    def short_shots(self, floor: float = MIN_SHOT_S,
                    authored: tuple[float, ...] = ()) -> list[tuple[float, float]]:
        """Shots under the floor. An AUTHORED short section -- the black gap,
        the loop-back tail -- is a deliberate punctuation mark and is not a
        shot; counting it as one makes the gate unpassable by construction."""
        out, t = [], 0.0
        for d in self.shot_lengths:
            if d < floor and not any(abs(t - a) <= 0.20 for a in authored):
                out.append((round(t, 3), round(d, 3)))
            t += d
        return out

    def one_framers(self, authored: tuple[float, ...] = ()) -> list[tuple[float, float]]:
        return self.short_shots(floor=2.5 / self.fps, authored=authored)

    def verdict(self, authored_impacts: tuple[float, ...] = (),
                authored_boundaries: tuple[float, ...] = ()) -> dict:
        def near(t):
            return any(abs(t - x) <= 0.25 for x in authored_impacts)

        ones = self.one_framers(authored_boundaries)
        short = [s for s in self.short_shots(authored=authored_boundaries)
                 if s not in ones]
        stray = [t for t in self.white_flashes if not near(t)]
        rules = {
            "cuts_per_min": {
                "value": round(self.cuts_per_min, 2), "limit": CUTS_PER_MIN_MAX,
                "pass": self.cuts_per_min <= CUTS_PER_MIN_MAX},
            "min_shot_0.5s": {
                "value": len(short), "limit": 0, "pass": not short,
                "offenders": short[:12]},
            "one_framers": {
                "value": len(ones), "limit": MAX_ONE_FRAMERS,
                "pass": len(ones) <= MAX_ONE_FRAMERS, "offenders": ones[:12]},
            "white_flashes": {
                "value": len(self.white_flashes), "limit": MAX_WHITE_FLASHES,
                "pass": len(self.white_flashes) <= MAX_WHITE_FLASHES,
                "at": [round(t, 3) for t in self.white_flashes]},
            "flashes_on_impacts_only": {
                "value": len(stray), "limit": 0, "pass": not stray,
                "offenders": [round(t, 3) for t in stray]},
        }
        rules["PASS"] = all(r["pass"] for r in rules.values() if isinstance(r, dict))
        return rules

    def row(self) -> str:
        return (f"{self.cuts_per_min:5.1f} cuts/min  {len(self.shot_lengths):3d} shots  "
                f"min {min(self.shot_lengths, default=0):.2f}s  "
                f"short {len(self.short_shots())}  white {len(self.white_flashes)}")


def from_segments(segments: list[dict], duration_s: float, fps: float,
                  white_flashes: list[float] | None = None,
                  black_frames: list[float] | None = None) -> Pacing:
    """Pacing measured from the AUTHORED cut list rather than from a detector.

    This gate exists to judge editing decisions, and those are known exactly --
    they are the segment boundaries. Three detectors were tried first and each
    failed in a different direction on this footage: `select='gt(scene,N)'`
    reported six cuts two frames apart inside a shot the source never cuts;
    a per-frame histogram distance still fired on fast anime motion; a
    block-stability test stopped firing on motion but then missed 8 of 22 real
    cuts, because a punch-in between two crops of one shot barely changes the
    histogram. A detector that cannot see a cut I authored is the wrong
    instrument for a question about cuts I authored.

    Source-internal cuts -- ones inside a shot, which I did not author -- stay
    the detector's job and are reported ADVISORY, never blocking.
    """
    return Pacing(duration_s=duration_s, fps=fps,
                  cut_times=[round(s["reel_start"], 3) for s in segments[1:]],
                  shot_lengths=[s["out_s"] for s in segments],
                  white_flashes=white_flashes or [],
                  black_frames=black_frames or [])


def _luma(path: Path, fps: float, w: int = 96) -> np.ndarray:
    h = int(round(w * 16 / 9))
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"fps={fps},scale={w}:{h}",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True)
    a = np.frombuffer(r.stdout, dtype=np.uint8)
    n = a.size // (w * h)
    return a[:n * w * h].reshape(n, h, w).astype(np.float32)


def _hist_distance(g: np.ndarray, bins: int = 32) -> np.ndarray:
    hs = np.stack([np.histogram(f, bins=bins, range=(0, 255))[0] for f in g])
    hs = hs.astype(float)
    hs /= hs.sum(1, keepdims=True) + 1e-9
    return np.abs(np.diff(hs, axis=0)).sum(1) / 2.0


def measure(path: Path, fps: float = 24.0,
            cut_threshold: float = CUT_HIST_DISTANCE) -> Pacing:
    dur = float(subprocess.run(
        ["ffprobe", "-v", "0", "-show_entries", "format=duration", "-of", "csv=p=0",
         str(path)], capture_output=True, text=True).stdout.strip().strip(","))
    g = _luma(path, fps)
    d = _hist_distance(g)
    cuts = [round(float(i + 1) / fps, 3) for i in np.where(d > cut_threshold)[0]]
    edges = [0.0] + cuts + [dur]
    shots = [b - a for a, b in zip(edges, edges[1:], strict=False) if b > a]

    m = g.mean((1, 2))
    white, black = [], []
    for i in range(1, len(m) - 1):
        if m[i] > 150 and m[i] - m[i - 1] > 45 and m[i] - m[i + 1] > 45:
            white.append(i / fps)
        if m[i] < 18 and m[i - 1] - m[i] > 35 and m[i + 1] - m[i] > 35:
            black.append(i / fps)
    return Pacing(duration_s=dur, fps=fps, cut_times=cuts, shot_lengths=shots,
                  white_flashes=white, black_frames=black)


# --- effective cut rate -------------------------------------------------
# The authored cut list is what the editor controls; it is not what the viewer
# sees. A block compressed 3.4x carries the SOURCE's own cuts at 3.4x, so a
# reel can pass the authored ceiling and still strobe. Measured on MHA reel v1:
# authored 26.7/min (pass), effective 74.7/min (over the same 58.7 ceiling),
# with one block at 131/min. The gate could not see any of it, by construction.
#
# Rate is source cuts divided by the block's ON-SCREEN length, which is
# algebraically the source's own cut rate multiplied by the ramp factor.
# The entry cut is a boundary between blocks, not inside one, so it is
# reported separately rather than folded in.

@dataclass
class Block:
    """One authored segment, with the source cuts it carries."""
    name: str
    src_s: float
    ramp: float
    source_cuts: int

    @property
    def on_s(self) -> float:
        return self.src_s / max(self.ramp, 1e-6)

    @property
    def effective_cuts_per_min(self) -> float:
        return self.source_cuts * 60.0 / max(self.on_s, 1e-6)

    @property
    def source_cuts_per_min(self) -> float:
        return self.source_cuts * 60.0 / max(self.src_s, 1e-6)

    def as_dict(self) -> dict:
        return {"block": self.name, "src_s": round(self.src_s, 3),
                "ramp": self.ramp, "on_s": round(self.on_s, 3),
                "source_cuts": self.source_cuts,
                "source_cuts_per_min": round(self.source_cuts_per_min, 1),
                "effective_cuts_per_min": round(self.effective_cuts_per_min, 1),
                "pass": self.effective_cuts_per_min <= CUTS_PER_MIN_MAX}


def effective_verdict(blocks: list[Block], authored_cuts: int,
                      duration_s: float) -> dict:
    """Per-block effective rate plus the reel-level figure, same ceiling.

    Reported per block because the reel-level number hides where the problem
    is: MHA v1 reads 74.7 overall, which is a mild overshoot, while the block
    that actually strobes reads 131.
    """
    rows = [b.as_dict() for b in blocks]
    total_src = sum(b.source_cuts for b in blocks)
    reel = (authored_cuts + total_src) * 60.0 / max(duration_s, 1e-6)
    over = [r for r in rows if not r["pass"]]
    return {
        "per_block": rows,
        "reel_effective_cuts_per_min": round(reel, 1),
        "limit": CUTS_PER_MIN_MAX,
        "reel_pass": reel <= CUTS_PER_MIN_MAX,
        "blocks_over": [r["block"] for r in over],
        "worst": max(rows, key=lambda r: r["effective_cuts_per_min"]) if rows else None,
        "PASS": reel <= CUTS_PER_MIN_MAX and not over,
    }


def cuts_inside(cuts: list[float], a: float, b: float, fps: float = 24000/1001,
                frames: float = 1.0) -> int:
    """Source cuts strictly INSIDE a block, excluding those that ARE its joins.

    A cut within a frame of a block boundary is the boundary. Counting it as an
    inside cut charges the same visible cut twice -- once as the authored join,
    once against the effective rate. Measured on MHA v2: transcribing segment
    edges at 2 dp moved four boundary cuts inside their blocks and pushed 4.0
    from 44.8 to 89.6 with no change to a single rendered frame, because a 5 ms
    shift is 0.12 of a frame and `trim` selects the same frame either way.
    """
    eps = frames / fps
    return len([c for c in cuts if a + eps < c < b - eps])
