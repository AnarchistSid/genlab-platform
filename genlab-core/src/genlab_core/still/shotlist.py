"""ANIME-PEAK-12 §2 — the shot list, built from what a model SAW, reviewed by a person.

Runs of consecutive frames sharing the same characters and shot type become
shots. Each shot's crop anchor is the tracked FACE BOX of its subject, so a
Tanjiro shot follows Tanjiro's face and an Akaza shot follows Akaza's -- the
anchor is no longer a colour blob's centroid, which is what framed haori
pattern for two packets.

The list is written out and looked at BEFORE anything renders. That is the
point of the section: thirty seconds of a person's eye in place of an hour of
gates that each measured a proxy.
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

MIN_SHOT_S = 0.5
EFFECT_TYPES = ("debris_or_effect",)
MAX_EFFECT_SHARE = 0.25
REQUIRED_BEATS = ("windup", "clash", "impact", "reaction")


@dataclass
class Shot:
    start: float
    end: float
    subject: str | None
    characters: list[str]
    shot_type: str
    anchor: tuple[float, float] | None = None     # face centre, 0-1 of frame
    anchor_source: str = "none"
    watermark: list[list[float]] = field(default_factory=list)
    remap: float = 1.0

    @property
    def duration(self) -> float:
        return self.end - self.start

    def row(self) -> str:
        a = f"({self.anchor[0]:.2f},{self.anchor[1]:.2f})" if self.anchor else "   —      "
        return (f"  {self.start:6.2f}-{self.end:<6.2f}{self.duration:5.2f}s  "
                f"{(self.subject or '—'):<9}{self.shot_type:<17}{a:<12}"
                f"{self.anchor_source:<8}{self.remap:>5.2f}x")


def _face_centre(frame: dict, name: str | None) -> tuple[tuple[float, float], str] | None:
    """A face centre, or None. Boxes are RANGE-checked, not just shape-checked.

    The model returned a box whose centre sat at y = 1.32 -- outside the
    frame entirely. Validating that a list has four numbers says nothing
    about whether they are coordinates.
    """
    for f in frame.get("faces") or []:
        if name is not None and f.get("name") != name:
            continue
        b = f.get("box") or []
        if len(b) != 4:
            continue
        try:
            x0, y0, x1, y1 = (float(v) for v in b)
        except (TypeError, ValueError):
            continue
        if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
            logger.warning("[shotlist] face box outside the frame, ignored: %s", b)
            continue
        return ((x0 + x1) / 2, (y0 + y1) / 2), "face"
    return None


def build(frames: list[dict], *, min_shot_s: float = MIN_SHOT_S) -> list[Shot]:
    """Runs of frames with the same characters and shot type."""
    if not frames:
        return []
    shots: list[Shot] = []
    cur = [frames[0]]

    def key(f):
        return (tuple(sorted(f.get("characters") or [])), f.get("shot_type"))

    for f in frames[1:]:
        if key(f) == key(cur[-1]):
            cur.append(f)
        else:
            shots.append(_close(cur))
            cur = [f]
    shots.append(_close(cur))

    # fold anything shorter than the floor into its neighbour rather than
    # dropping it -- a dropped shot silently shortens the reel.
    out: list[Shot] = []
    for s in shots:
        if out and s.duration < min_shot_s:
            out[-1] = Shot(out[-1].start, s.end, out[-1].subject, out[-1].characters,
                           out[-1].shot_type, out[-1].anchor, out[-1].anchor_source,
                           out[-1].watermark, out[-1].remap)
        else:
            out.append(s)
    return out


def _close(run: list[dict]) -> Shot:
    chars = list(run[0].get("characters") or [])
    stype = run[0].get("shot_type") or "unknown"
    mid = run[len(run) // 2]
    subject = chars[0] if len(chars) == 1 else None
    anchor, asrc = None, "none"
    if len(chars) == 1:
        got = _face_centre(mid, chars[0])
        if got:
            anchor, asrc = got
    if anchor is None:
        got = _face_centre(mid, None)
        if got:
            anchor, asrc = got[0], "face_any"
            if subject is None:
                for f in mid.get("faces") or []:
                    if f.get("name") in chars:
                        subject = f["name"]
                        break
    wm = [w["box"] for w in (mid.get("text_or_watermark") or [])
          if len(w.get("box") or []) == 4]
    dur_end = run[-1]["t"] + (run[-1]["t"] - run[-2]["t"] if len(run) > 1 else 0.5)
    return Shot(run[0]["t"], round(dur_end, 3), subject, chars, stype,
                anchor, asrc, wm)


@dataclass(frozen=True)
class PlanCheck:
    beats_present: dict[str, bool]
    effect_share: float
    unanchored: int
    total_s: float

    @property
    def ok(self) -> bool:
        return (all(self.beats_present.values())
                and self.effect_share <= MAX_EFFECT_SHARE)

    def row(self) -> str:
        missing = [b for b, v in self.beats_present.items() if not v]
        return (f"  beats: {'all present' if not missing else 'MISSING ' + ','.join(missing)}"
                f"   effect {self.effect_share:.0%} (cap {MAX_EFFECT_SHARE:.0%})"
                f"   unanchored shots {self.unanchored}"
                f"   total {self.total_s:.1f}s")


def check(shots: list[Shot]) -> PlanCheck:
    total = sum(s.duration for s in shots) or 1.0
    eff = sum(s.duration for s in shots if s.shot_type in EFFECT_TYPES)
    present = {b: any(s.shot_type == b for s in shots) for b in REQUIRED_BEATS}
    return PlanCheck(present, eff / total,
                     sum(1 for s in shots if s.anchor is None), total)


def contact_sheet(video: Path, shots: list[Shot], dest: Path, *,
                  cols: int = 5, width: int = 320) -> Path:
    """One frame per shot, labelled, so the list can be read in one look."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        for i, s in enumerate(shots):
            t = s.start + s.duration / 2
            label = (f"{i}  {s.shot_type}  {s.subject or 'both/none'}  "
                     f"{s.start:.1f}-{s.end:.1f}s")
            subprocess.run(
                ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video),
                 "-frames:v", "1", "-vf",
                 f"scale={width}:-2,drawtext=text='{label}':x=5:y=4:fontsize=16:"
                 f"fontcolor=yellow:box=1:boxcolor=black@0.8",
                 "-y", str(d / f"{i:03d}.png")], check=True)
        rows = (len(shots) + cols - 1) // cols
        subprocess.run(["ffmpeg", "-v", "error", "-framerate", "1", "-i",
                        str(d / "%03d.png"), "-frames:v", "1", "-vf",
                        f"tile={cols}x{rows}:margin=4:padding=4", "-y", str(dest)],
                       check=True)
    return dest


def write(shots: list[Shot], dest: Path) -> dict:
    payload = {"n": len(shots), "shots": [asdict(s) for s in shots],
               "check": asdict(check(shots))}
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=1, default=list))
    return payload
