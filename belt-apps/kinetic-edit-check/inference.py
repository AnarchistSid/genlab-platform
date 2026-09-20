"""kinetic-edit-check — measure the pacing of a short-form video.

Built after measuring a reference VFX edit frame by frame and discovering that
every plausible-looking number was measuring something other than its name:

  * A cut count is meaningless without its method. Three detectors on the same
    41-second clip returned 0.32, 0.37 and 0.51 cuts/s. All three are reported.
  * "Longest static" as the gap between event ONSETS reads 1.23s on a clip whose
    longest stretch with nothing happening is 0.30s, because one continuously
    detected run collapses to a single onset. Both are reported.
  * A letterbox bar is dark AND flat AND temporally frozen. Testing brightness
    alone marks a night-time arena shot as letterboxed.

So this returns every number with the method that produced it, and never a
single headline figure that hides the disagreement.
"""
import json
import logging
import math
import os
import subprocess
from typing import List, Optional

import numpy as np
from inferencesh import (BaseApp, BaseAppOutput, BaseAppSetup, File,
                         OutputMeta, TextMeta)
from pydantic import BaseModel, Field

# Some SDK builds provide self.logger, some do not; fall back rather than crash
# in setup(), which is a poor place to learn about an attribute.
_LOG = logging.getLogger("kinetic-edit-check")

W, H = 480, 270
LUM = np.array([0.299, 0.587, 0.114], np.float32)


class AppSetup(BaseAppSetup):
    hist_cut_threshold: float = Field(
        default=0.60,
        description="Colour-histogram intersection below which a frame is a cut (canonical detector).",
    )
    framediff_threshold: float = Field(
        default=35.0,
        description="Mean absolute luma difference above which a frame is a cut (also fires on strobes).",
    )
    flash_threshold: float = Field(
        default=9.0,
        description="Rise in FRAME-MEAN luma that counts as a flash. A cut moves pixels; a flash lifts them all.",
    )
    zoom_threshold: float = Field(
        default=0.020,
        description="Deviation from scale 1.0 that counts as a zoom pulse.",
    )
    rgb_split_threshold: float = Field(
        default=2.0,
        description="Horizontal red/blue channel lag, in source pixels, that counts as an RGB split.",
    )


class RunInput(BaseModel):
    video: File = Field(description="The video to measure. Any format ffmpeg can read.")
    bpm: Optional[float] = Field(
        default=None,
        description="Known tempo. If given, events are scored against that grid instead of a fitted one.",
    )
    fps: Optional[float] = Field(
        default=None, description="Override the detected frame rate."
    )


class CutRates(BaseModel):
    hist_intersection: float = Field(description="Cuts/s by colour-histogram intersection (canonical).")
    ffmpeg_scene: float = Field(description="Cuts/s by ffmpeg's own scene filter, as an independent check.")
    frame_difference: float = Field(description="Cuts/s by mean luma difference. Also fires on strobes.")


class RunOutput(BaseAppOutput):
    frames: int = Field(description="Frames measured.")
    duration_s: float = Field(description="Duration in seconds.")
    cuts_per_second: CutRates = Field(description="Three detectors. They disagree; that is the point.")
    median_shot_s: float = Field(description="Median shot length by the canonical detector.")
    effect_events_per_second: float = Field(description="Flashes, zoom pulses and RGB splits per second.")
    flash_events: int = Field(description="Frames where the whole image brightened at once.")
    zoom_events: int = Field(description="Distinct zoom pulses.")
    rgb_split_events: int = Field(description="Distinct RGB-split events.")
    longest_quiet_run_s: float = Field(
        description="Longest run of frames with NO detector firing. The honest 'longest static'."
    )
    longest_onset_gap_s: float = Field(
        description="Longest gap between event onsets. Larger, and inflated by collapsed runs."
    )
    full_bleed_pct: float = Field(
        description="Percent of frames with no letterbox bar (dark AND flat AND frozen on an edge)."
    )
    bpm: Optional[float] = Field(default=None, description="Tempo used or fitted.")
    on_grid_pct: Optional[float] = Field(
        default=None, description="Percent of events within one frame of a beat or half-beat."
    )
    report: str = Field(description="A human-readable summary with every number and its method.")


def _probe(path: str) -> tuple:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=r_frame_rate,width,height", "-show_entries", "format=duration",
         "-of", "json", path],
        capture_output=True, text=True,
    ).stdout
    d = json.loads(out or "{}")
    st = (d.get("streams") or [{}])[0]
    num, _, den = (st.get("r_frame_rate") or "30/1").partition("/")
    fps = float(num) / float(den or 1)
    dur = float((d.get("format") or {}).get("duration") or 0.0)
    return fps, dur, int(st.get("width") or 0), int(st.get("height") or 0)


def _collapse(idx, gap: int = 3) -> list:
    out: list = []
    for i in idx:
        if not out or i - out[-1][-1] > gap:
            out.append([int(i)])
        else:
            out[-1].append(int(i))
    return out


def _best_scale(a: np.ndarray, b: np.ndarray) -> float:
    n = len(a)
    x = np.arange(n)
    c = (n - 1) / 2.0
    a = a - a.mean()
    best, bs = -2.0, 1.0
    for s in np.arange(0.94, 1.161, 0.005):
        r = np.interp(c + (x - c) / s, x, b)
        r = r - r.mean()
        v = float((a * r).sum() / (np.sqrt((a * a).sum() * (r * r).sum()) + 1e-9))
        if v > best:
            best, bs = v, float(s)
    return bs


def _lag(a: np.ndarray, b: np.ndarray, rng: int = 6) -> float:
    a = a - a.mean()
    b = b - b.mean()
    cc = [float((a[rng:len(a) - rng] * b[rng + k:len(b) - rng + k]).sum())
          for k in range(-rng, rng + 1)]
    i = int(np.argmax(cc))
    if 0 < i < len(cc) - 1:
        y0, y1, y2 = cc[i - 1], cc[i], cc[i + 1]
        d = float(np.clip((y0 - y2) / (2 * (y0 - 2 * y1 + y2) + 1e-9), -1, 1))
    else:
        d = 0.0
    return (i - rng) + d


class App(BaseApp):
    def _log(self):
        return getattr(self, "logger", None) or _LOG

    async def setup(self, config: AppSetup):
        self.cfg = config
        self._log().info("kinetic-edit-check ready (ffmpeg/ffprobe, cpu only)")

    async def run(self, input_data: RunInput) -> RunOutput:
        path = input_data.video.path
        if not os.path.exists(path):
            raise ValueError(f"video not found: {path}")
        fps, dur, sw, _sh = _probe(path)
        if input_data.fps:
            fps = float(input_data.fps)
        self._log().info(f"measuring {os.path.basename(path)} at {fps:.3f} fps")

        proc = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-i", path, "-vf", f"scale={W}:{H}",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            stdout=subprocess.PIPE,
        )
        nb = W * H * 3
        prev = None
        hist, fdif, flash, zoom, split, bleed = [], [], [], [], [], []
        while True:
            raw = proc.stdout.read(nb)
            if len(raw) < nb:
                break
            f = np.frombuffer(raw, np.uint8).reshape(H, W, 3).astype(np.float32)
            y = f @ LUM
            q = (f // 32).astype(np.int32)
            h = np.bincount((q[..., 0] * 64 + q[..., 1] * 8 + q[..., 2]).ravel(),
                            minlength=512).astype(np.float32)
            h /= max(h.sum(), 1.0)
            strips = [y[:4, :], y[-4:, :], y[:, :4], y[:, -4:]]
            if prev is None:
                bar = False
            else:
                bar = any(
                    s.mean() < 14.0 and s.std() < 5.0 and float(np.abs(s - p).mean()) < 1.0
                    for s, p in zip(strips, prev[4])
                )
            bleed.append(0.0 if bar else 1.0)
            split.append(abs(_lag(f[H // 2, :, 0], f[H // 2, :, 2])) * (max(sw, W) / W))
            col, row = y.mean(axis=0), y.mean(axis=1)
            if prev is None:
                hist.append(1.0); fdif.append(0.0); flash.append(0.0); zoom.append(1.0)
            else:
                hist.append(float(np.minimum(h, prev[5]).sum()))
                fdif.append(float(np.abs(y - prev[0]).mean()))
                flash.append(float(y.mean() - prev[1]))
                zoom.append((_best_scale(col, prev[2]) + _best_scale(row, prev[3])) / 2.0)
            prev = (y, y.mean(), col, row, strips, h)
        proc.stdout.close()
        proc.wait()

        n = len(fdif)
        if n == 0:
            raise ValueError("no frames decoded")
        dur = dur or n / fps
        hist_a = np.array(hist); fd_a = np.array(fdif)
        fl_a = np.array(flash); zo_a = np.array(zoom); sp_a = np.array(split)

        cuts_h = [r[0] for r in _collapse(np.where(hist_a < self.cfg.hist_cut_threshold)[0])]
        cuts_f = [r[0] for r in _collapse(np.where(fd_a > self.cfg.framediff_threshold)[0])]
        scene = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vf",
             "select='gt(scene,0.30)',showinfo", "-f", "null", "-"],
            capture_output=True, text=True,
        ).stderr
        cuts_s = [ln for ln in scene.splitlines() if "pts_time:" in ln]

        ev_flash = _collapse(np.where(fl_a > self.cfg.flash_threshold)[0])
        ev_zoom = _collapse(np.where(np.abs(zo_a - 1.0) > self.cfg.zoom_threshold)[0])
        ev_split = _collapse(np.where(sp_a > self.cfg.rgb_split_threshold)[0])
        n_ev = len(ev_flash) + len(ev_zoom) + len(ev_split)

        active = np.zeros(n, bool)
        for arr in (np.where(hist_a < self.cfg.hist_cut_threshold)[0],
                    np.where(fl_a > self.cfg.flash_threshold)[0],
                    np.where(np.abs(zo_a - 1.0) > self.cfg.zoom_threshold)[0],
                    np.where(sp_a > self.cfg.rgb_split_threshold)[0]):
            active[arr] = True
        best = cur = 0
        for a in active:
            cur = 0 if a else cur + 1
            best = max(best, cur)

        onsets = sorted({r[0] for r in (ev_flash + ev_zoom + ev_split)} | set(cuts_h))
        gaps = np.diff(np.array([0] + onsets + [n]))
        bounds = np.array([0.0] + [c / fps for c in cuts_h] + [dur])
        shots = np.diff(bounds)

        bpm = input_data.bpm
        on_grid = None
        if bpm:
            per = 60.0 / float(bpm) * fps / 2.0
            offs = [min(abs(o - k * per) for k in range(int(n / max(per, 1)) + 2)) for o in onsets]
            on_grid = round(100.0 * float(np.mean([o <= 1.0 for o in offs])), 1) if offs else None

        rep = [
            f"frames {n}  duration {dur:.2f}s  fps {fps:.3f}",
            "cuts/s  hist<%.2f %.3f | ffmpeg scene>0.30 %.3f | framediff>%.0f %.3f"
            % (self.cfg.hist_cut_threshold, len(cuts_h) / dur, len(cuts_s) / dur,
               self.cfg.framediff_threshold, len(cuts_f) / dur),
            "  ^ they disagree; quote the method with the number",
            f"median shot {float(np.median(shots)):.3f}s",
            f"effect events/s {n_ev / dur:.3f}  "
            f"(flash {len(ev_flash)}, zoom {len(ev_zoom)}, rgb-split {len(ev_split)})",
            f"longest quiet run {best / fps:.3f}s   longest onset gap {float(gaps.max()) / fps:.3f}s",
            f"full-bleed {100.0 * float(np.mean(bleed)):.2f}% of frames",
        ]
        if on_grid is not None:
            rep.append(f"on-grid at {bpm:.2f} BPM: {on_grid}% of events within 1 frame")

        self._log().info(" | ".join(rep[:2]))
        text = "\n".join(rep)
        return RunOutput(
            frames=n,
            duration_s=round(dur, 3),
            cuts_per_second=CutRates(
                hist_intersection=round(len(cuts_h) / dur, 4),
                ffmpeg_scene=round(len(cuts_s) / dur, 4),
                frame_difference=round(len(cuts_f) / dur, 4),
            ),
            median_shot_s=round(float(np.median(shots)), 4),
            effect_events_per_second=round(n_ev / dur, 4),
            flash_events=len(ev_flash),
            zoom_events=len(ev_zoom),
            rgb_split_events=len(ev_split),
            longest_quiet_run_s=round(best / fps, 4),
            longest_onset_gap_s=round(float(gaps.max()) / fps, 4),
            full_bleed_pct=round(100.0 * float(np.mean(bleed)), 2),
            bpm=bpm,
            on_grid_pct=on_grid,
            report=text,
            output_meta=OutputMeta(outputs=[TextMeta(character_count=len(text))]),
        )
