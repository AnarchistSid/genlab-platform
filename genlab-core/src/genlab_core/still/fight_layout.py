"""ANIME-PEAK-11 §1 — full-screen, one character at a time.

The band was the amateur layout. It existed for a structural reason: the
action box had to CONTAIN every fighter, and two fighters staged apart make
a box wider than any 9:16 crop of a 16:9 source, so the frame had to shrink
to fit them. Committing to ONE fighter per shot removes that constraint
entirely -- the crop needs a CENTRE, not a containing box -- and the other
fighter becomes their own shot, cut to on a beat.

Magnification is capped at 2.0x. The full-bleed crop of a 1920x1080 source
is 1.778x (607 px wide), so the usable range is a narrow 540-607 px: every
shot fills the frame, and "tighter" means at most 12% tighter than full
bleed. There is no band.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_MAG = 2.0
OUT_W, OUT_H = 1080, 1920


@dataclass(frozen=True)
class SubjectShot:
    start: float
    end: float
    subject: str              # which fighter this shot follows
    centre_x: float           # in source pixels
    crop_w: int
    crop_x: int
    src_w: int
    src_h: int
    magnification: float
    located: bool             # False when the centre came from motion, not a seed

    def row(self) -> str:
        return (f"  {self.start:6.2f}-{self.end:<6.2f}{self.subject:<10}"
                f"cx={self.centre_x:6.0f}  crop {self.crop_w}@{self.crop_x:<5}"
                f"{self.magnification:.2f}x  {'seed' if self.located else 'motion'}")


def crop_for_subject(centre_x: float, subject_w: float, src_w: int, src_h: int,
                     *, headroom: float = 1.15) -> tuple[int, int, float]:
    """A full-screen 9:16 crop SIZED TO THE SUBJECT, not just centred on them.

    Sizing matters more than centring and this is the second time the lesson
    has cost a render. A colour seed locates a GARMENT; a crop chosen
    independently of the subject's extent frames cloth. At 1.99x the crop was
    542 px wide while the fighters' boxes measured 500-900 px, so the crop
    was NARROWER than the fighter and a partial view was guaranteed -- the
    proof frames came back as Akaza's stripes and Tanjiro's haori pattern
    filling the screen.

    The crop is therefore at least the subject's width plus headroom, still
    clamped to full bleed at the wide end (there is no wider 9:16 rectangle)
    and to MAX_MAG at the tight end.
    """
    full_bleed_w = int(src_h * OUT_W // OUT_H)
    fb_mag = OUT_H / src_h
    # There is no 9:16 rectangle wider than full bleed, so the tightest crop
    # can never exceed it. On a 720p source full bleed is ALREADY 2.667x --
    # the MAX_MAG cap is unsatisfiable there and the source is simply smaller
    # than the output. Clamping rather than pretending keeps the crop legal.
    tightest = min(full_bleed_w, int(round(full_bleed_w * fb_mag / MAX_MAG)))
    crop_w = int(round(max(tightest, min(full_bleed_w, subject_w * headroom))))
    crop_w -= crop_w % 2
    x = int(round(min(max(0.0, centre_x - crop_w / 2), src_w - crop_w)))
    return crop_w, x, OUT_W / crop_w


def subject_boxes(path: Path, t: float, seeds: dict[str, dict]) -> dict[str, tuple[float, float]]:
    """(centre_x, width) per fighter a seed can locate at `t`."""
    from genlab_core.still import action_box as AB

    out = {}
    for name, spec in seeds.items():
        b = AB.colour_box(path, t, spec, name)
        if b is not None:
            p = b.padded(w=AB.probe_dims(path)[0], h=AB.probe_dims(path)[1])
            out[name] = (p.cx, p.w)
    return out


def plan_subject_shots(path: Path, shots: list[dict],
                       seeds: dict[str, dict]) -> list[SubjectShot]:
    """One subject per shot, alternating between fighters across cuts.

    Alternation is the point: the grammar is A, then B, then A. When only one
    fighter is locatable the shot follows them; when neither is, the crop
    centres on the motion region, which is still a full-screen crop -- never
    a band.
    """
    from genlab_core.still import action_box as AB

    src_w, src_h = AB.probe_dims(path)
    out: list[SubjectShot] = []
    last: str | None = None
    for sh in shots:
        t = (sh["start"] + sh["end"]) / 2
        boxes = subject_boxes(path, t, seeds)
        pick, located = None, True
        if boxes:
            others = [n for n in boxes if n != last]
            pick = others[0] if others else next(iter(boxes))
        else:
            mb = AB.motion_box(path, t)
            located = False
            pick = "motion"
            boxes = {"motion": (mb.cx if mb else src_w / 2,
                                mb.w if mb else src_h * OUT_W / OUT_H)}
        cx, bw = boxes[pick]
        crop_w, x, m = crop_for_subject(cx, bw, src_w, src_h)
        out.append(SubjectShot(sh["start"], sh["end"], pick, cx, crop_w, x,
                               src_w, src_h, m, located))
        if located:
            last = pick
    return out


def shot_filter(s: SubjectShot, *, shake_px: float = 3.0, shake_hz: float = 2.0,
                pulse: float = 0.0, pulse_period_s: float = 0.4,
                rgb_split_at: tuple[float, ...] = (), split_px: float = 2.0) -> str:
    """The crop, plus the movement the genre puts on every shot.

    Shake and pulse are small on purpose: 2-4 px at 2 Hz reads as handheld
    energy, and a zoom pulse of a couple of percent on the beat reads as the
    track. Anything larger reads as a mistake.
    """
    # Crop wider than needed so the shake has somewhere to move into.
    pad = int(shake_px * 2) + 4
    cw = min(s.crop_w + pad, s.src_w)
    cx = max(0, min(s.crop_x - pad // 2, s.src_w - cw))
    f = [f"crop={cw}:{s.src_h}:{cx}:0"]
    zoom = f"1+{pulse:.4f}*abs(sin(PI*t/{pulse_period_s:.4f}))" if pulse else "1"
    # Scale so the crop lands at OUT_W plus just enough margin for the shake
    # to move into. Scaling to OUT_W*2 and then cropping OUT_W x OUT_H from
    # the centre discards half the width AND half the height -- a silent 2x
    # zoom on top of the intended crop, which is what filled the frame with
    # haori pattern while crop_w read a correct 606 px.
    # The shake pad widens the CROP, so the scale has to follow the CONTENT
    # width or the padded frame scales down and the 9:16 crop no longer fits:
    # scaling a 616 px padded crop to 1090 gives 1911 px of height against a
    # 1920 px crop, and ffmpeg writes no packets at all.
    sw = int(round(OUT_W * cw / s.crop_w))
    sw -= sw % 2
    f.append(
        f"scale={sw}:-2:flags=lanczos,"
        f"crop=w='{OUT_W}':h='{OUT_H}'"
        f":x='(iw-{OUT_W})/2+{shake_px:.1f}*sin(2*PI*{shake_hz}*t)'"
        f":y='(ih-{OUT_H})/2+{shake_px * 0.6:.1f}*cos(2*PI*{shake_hz * 1.3}*t)'")
    if pulse:
        f.append(f"scale=w='iw*({zoom})':h='ih*({zoom})':eval=frame,"
                 f"crop={OUT_W}:{OUT_H}")
    if rgb_split_at:
        # rgbashift's shift options are integers evaluated ONCE -- they are not
        # per-frame expressions and the filter has no eval option. The split is
        # gated by timeline `enable=` instead, which is what the filter does
        # support.
        cond = "+".join(f"between(t,{a:.3f},{a + 2 / 24:.3f})" for a in rgb_split_at)
        f.append(f"rgbashift=rh={int(split_px)}:bh=-{int(split_px)}:"
                 f"enable='{cond}'")
    f.append("setsar=1")
    return ",".join(f)
