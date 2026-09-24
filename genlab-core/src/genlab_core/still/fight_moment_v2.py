"""Impact detection anchored on sound, confirmed by picture.

ANIME-PEAK-03 §2. The v1 detector scored a short luma spike against a ±0.5 s
median. It verified perfectly on One Piece and returned recall 0.00 on
Jujutsu Kaisen, because it had encoded one studio's convention: a 1-3 frame
impact frame. Hollow Purple is a sustained bright FIELD, and v1 rejects
sustained rises by design.

The redesign anchors on the hit's SOUND and lets the picture confirm in any
of three ways. Every threshold below carries the dev-set measurement that set
it. The dev set is Zoro vs King, Luffy vs Kaido and Gojo vs Jogo — all three
contaminated by earlier inspection, which is exactly why they are the dev set
and never the test set.

DEV-SET RESULT AT FREEZE (Zoro, Luffy, Gojo — 9 marks, +/-0.3 s):

    anchor="audio"  recall 0.22   precision 0.04
    anchor="any"    recall 0.56   precision 0.07

Below the 0.8 bar either way, and the failure is concentrated: Gojo is 0/3 in
both. The remaining Gojo cause is NOT the one v1 had. The sustained-field
confirmation does fire on that clip, but only on the RISING EDGE of the
field, and the marks were placed at the visible burst inside it. For a flash
lasting half a second, "the impact frame" is ambiguous — onset or peak — and
a +/-0.3 s tolerance cannot span the difference. That is a definition
problem between marker and detector, not a threshold.

WHERE THE DEV SET DISAGREES WITH THE BRIEF. §2 calls audio "universal across
studios". Measured at the nine dev marks, the audio step is:

    zoro   4.16  5.98  7.13
    luffy  6.73  7.62  6.03
    gojo   1.67  3.49 -2.17

Six of nine clear 4 dB. Gojo's three do not, and one is NEGATIVE — the audio
gets quieter as Hollow Purple lands. A hard audio bracket therefore cannot
find Gojo's impacts at all. ``anchor`` is a parameter for that reason:
``"audio"`` implements the brief, ``"any"`` lets a strong video signal stand
alone, and the difference is reported rather than chosen silently.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from genlab_core.still.fight_moment import Sequence, audio_envelope, luma_series

logger = logging.getLogger(__name__)

# ── thresholds, each with its dev-set measurement ────────────────────────
#: Audio step, dB above the preceding half-second's median. Six of nine dev
#: marks measured 4.16-7.62; the three that did not are all Gojo.
AUDIO_STEP_DB = 4.0
#: The hit's sound leads or trails the visible impact by this much.
BRACKET_BEFORE_S, BRACKET_AFTER_S = 0.5, 0.2
#: (a) SHORT SPIKE — v1's signal. Dev marks on One Piece measured 1-5 frames
#: above the clip's 95th percentile.
SPIKE_JUMP = 12.0
SPIKE_MAX_FRAMES = 3
#: (b) SUSTAINED FIELD — Gojo's two bright marks measured 12 and 9 frames over
#: the clip's p95, against 1-5 for every One Piece mark. Four frames is the
#: separating value.
FIELD_MIN_FRAMES = 4
FIELD_PERCENTILE = 95
#: (c) MOTION — frame-difference peak, as a percentile of the clip's own.
MOTION_PERCENTILE = 90
#: Audio and picture must agree within this.
AGREE_FRAMES = 3
#: An impact with no aftermath inside this window is unresolved.
AFTERMATH_WINDOW_S = 2.0


@dataclass(frozen=True)
class Impact:
    t: float
    audio_step: float
    confirmed_by: str          # spike | field | motion
    detail: float
    bracket: tuple[float, float]

    def row(self) -> str:
        return (f"  t={self.t:7.2f}s  audio+{self.audio_step:5.2f}dB  "
                f"{self.confirmed_by:<7} ({self.detail:6.1f})")


def _audio_onsets(env: np.ndarray, hop: float, step_db: float) -> list[float]:
    """Times where the level steps up. The hit's onset, not its peak."""
    out: list[float] = []
    for k in range(25, len(env) - 5):
        pre = env[k - 25:k]
        post = env[k:k + 5]
        if not pre.size or not post.size:
            continue
        if float(post.max() - np.median(pre)) >= step_db and env[k] > env[k - 1]:
            t = k * hop
            if not out or t - out[-1] > 0.35:
                out.append(t)
    return out


def find_impacts(video: Path, audio: Path | None = None, *,
                 anchor: str = "audio", top_n: int = 12) -> list[Impact]:
    """Impacts, anchored on sound and confirmed by picture.

    ``anchor="audio"`` is the brief: only frames inside an audio bracket are
    considered. ``anchor="any"`` also admits a video signal that stands alone,
    which is what Gojo's marks need.
    """
    from genlab_core.action.window import motion_profile

    luma, fps = luma_series(video)
    prof = np.array(motion_profile(str(video)).values, dtype=np.float32)
    env = audio_envelope(audio or video)
    hop = 0.02
    onsets = _audio_onsets(env, hop, AUDIO_STEP_DB) if env.size else []

    p95 = float(np.percentile(luma, FIELD_PERCENTILE))
    mot_p = float(np.percentile(prof, MOTION_PERCENTILE)) if prof.size else 1e9
    half = max(1, int(0.5 * fps))
    pad = np.pad(luma, (half, half), mode="edge")
    base = np.array([np.median(pad[i:i + 2 * half + 1]) for i in range(len(luma))])
    jump = luma - base

    def audio_step_at(t: float) -> float:
        if not env.size:
            return 0.0
        k = int(t / hop)
        pre, post = env[max(0, k - 25):max(1, k)], env[k:k + 10]
        return float(post.max() - np.median(pre)) if pre.size and post.size else 0.0

    def confirm(i: int) -> tuple[str, float] | None:
        """(a) short spike, (b) sustained field, (c) motion — any one."""
        if jump[i] >= SPIKE_JUMP and any(
                jump[min(i + k, len(jump) - 1)] < jump[i] * 0.4
                for k in range(1, SPIKE_MAX_FRAMES + 1)):
            return "spike", float(jump[i])
        run = luma[i:i + FIELD_MIN_FRAMES * 3]
        n_over = int((run > p95).sum())
        if n_over >= FIELD_MIN_FRAMES and (i == 0 or luma[i] > luma[i - 1]):
            return "field", float(n_over)
        if i < len(prof) and prof[i] >= mot_p:
            return "motion", float(prof[i])
        return None

    found: list[Impact] = []
    if anchor == "audio":
        windows = [(max(0.0, t - BRACKET_BEFORE_S), t + BRACKET_AFTER_S) for t in onsets]
    else:
        windows = [(0.0, len(luma) / fps)]

    #: Two impacts closer together than this are one impact.
    refractory_s = 0.35
    for a, b in windows:
        i0, i1 = int(a * fps), min(int(b * fps), len(luma) - 1)
        i = max(0, i0)
        end = max(0, i1)
        while i <= end:
            c = confirm(i)
            if not c:
                i += 1
                continue
            t = i / fps
            if not found or t - found[-1].t >= refractory_s:
                found.append(Impact(t, audio_step_at(t), c[0], c[1], (a, b)))
                if anchor == "audio":
                    # EARLIEST agreeing frame in the bracket, then next bracket.
                    break
            i += int(refractory_s * fps)

    found.sort(key=lambda x: (-x.audio_step, -x.detail))
    logger.info("[impact] %s: %d audio onsets, %d impacts (anchor=%s)",
                video.name, len(onsets), len(found), anchor)
    return found[:top_n]


def has_aftermath(video: Path, t: float, *, window_s: float = AFTERMATH_WINDOW_S) -> bool:
    """A cut, a camera move, or a luma settle within ``window_s`` of the hit.

    Required for the WINDOW, not for the impact frame: a hit with nothing
    after it is a frame, not a moment.
    """
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{t:.3f}", "-t", f"{window_s:.3f}",
         "-i", str(video), "-an", "-vf",
         "select='gt(scene,0.20)',metadata=print:file=-", "-f", "null", "-"],
        capture_output=True, text=True, timeout=300)
    return "pts_time:" in (r.stdout or "")


def window_for(imp: Impact, duration_s: float) -> Sequence:
    from genlab_core.still.fight_moment import Beat, sequence_for

    b = Beat(imp.t, imp.detail, imp.audio_step, 0.0, imp.audio_step)
    return sequence_for(b, duration_s)


# ── ANIME-PEAK-06 §2: one pick per window, and the flash is gated on it ──

#: Two impacts closer than this inside one window are one impact.
NMS_SEPARATION_S = 2.0


@dataclass(frozen=True)
class WindowPick:
    """The single impact chosen for a marked window, and whether it is trusted."""

    t: float | None
    confirmed: bool
    confirmed_by: str = ""
    audio_step: float = 0.0
    anchor_t: float = 0.0        # what the speed ramp anchors on
    reason: str = ""

    def row(self) -> str:
        if self.confirmed:
            return (f"impact {self.t:6.2f}s  CONFIRMED by {self.confirmed_by} "
                    f"(audio +{self.audio_step:.1f} dB) -> drawn flash ON")
        return (f"impact {('%.2fs' % self.t) if self.t is not None else '   none'}  "
                f"UNCONFIRMED -> flash: none (impact_unconfirmed); "
                f"ramp anchors on {self.anchor_t:.2f}s — {self.reason}")


def pick_for_window(video: Path, audio: Path | None, window: tuple[float, float],
                    impact_intervals: list[list[float]] | None = None) -> WindowPick:
    """One impact for this window. The drawn flash is gated on the mark.

    PEAK-03 produced 25 picks for 2 marks — a firing rate, not a recall. One
    pick per window is what makes precision mean anything, and NMS at 2 s is
    what makes it one.

    The flash is only drawn when the pick lands inside a marked impact
    interval. A missing flash beats a wrong one: an unconfirmed pick still
    gives the speed ramp an anchor (the strongest audio onset in the window),
    and the shot list says which happened.
    """
    a, b = window
    cands = [i for i in find_impacts(video, audio, anchor="any", top_n=60)
             if a <= i.t <= b]
    env = audio_envelope(audio or video)
    hop = 0.02
    onsets = [t for t in (_audio_onsets(env, hop, AUDIO_STEP_DB) if env.size else [])
              if a <= t <= b]

    def strength(i: Impact) -> float:
        return (min(max(i.audio_step, 0.0) / 8.0, 1.0) * 1.4
                + min(i.detail / 60.0, 1.0) * 1.0
                + (0.3 if i.confirmed_by == "spike" else 0.0))

    kept: list[Impact] = []
    for i in sorted(cands, key=strength, reverse=True):
        if all(abs(i.t - k.t) >= NMS_SEPARATION_S for k in kept):
            kept.append(i)
    best = kept[0] if kept else None

    # The ramp always has an anchor, confirmed or not.
    anchor = (max(onsets, key=lambda t: t) if onsets else (best.t if best else (a + b) / 2))
    if onsets:
        anchor = sorted(onsets, key=lambda t: -_step_at(env, hop, t))[0]

    if best is None:
        return WindowPick(None, False, anchor_t=anchor,
                          reason="no candidate impact inside the marked window")

    intervals = impact_intervals or []
    inside = any(lo - 0.3 <= best.t <= hi + 0.3 for lo, hi in intervals)
    if inside:
        return WindowPick(best.t, True, best.confirmed_by, best.audio_step, best.t,
                          reason="inside a marked impact interval")
    return WindowPick(best.t, False, best.confirmed_by, best.audio_step, anchor,
                      reason=(f"pick {best.t:.2f}s is outside every marked interval "
                              f"{intervals}"))


def _step_at(env, hop: float, t: float) -> float:
    k = int(t / hop)
    pre, post = env[max(0, k - 25):max(1, k)], env[k:k + 10]
    if not len(pre) or not len(post):
        return 0.0
    return float(post.max() - np.median(pre))
