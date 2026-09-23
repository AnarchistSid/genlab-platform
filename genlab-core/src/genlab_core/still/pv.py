"""The show's own PV, and the moments worth cutting out of it.

ANIME-13 §2. For anime the PV is the footage, exactly as the league highlight
is the footage for sports: an official studio/publisher upload, used with an
in-frame attribution slate, under the same stop rule on any claim.

Why this module exists at all: the 2026-09-21 reels were built from generated
pictures because the story record held no video for the show. It held a
``trailer.id`` all along -- AniList returns it in the response the fetcher
already makes -- and nothing downloaded it.

Selection reuses ``action.window``'s measured primitives (``motion_profile``,
``cut_times``, ``container_fps``) rather than re-deriving them. That module
learned three things the hard way and they all apply here:

* ``-v error`` silences ``metadata=print``, so a muted instrument reads as
  "this clip has no motion". Use ``-hide_banner -nostats``.
* The motion profile is per FRAME. Index it at the CONTAINER's fps, never at
  ``len(values)/duration``.
* A window picked on motion alone returns a replay montage. Motion AND zero
  cuts is what picks a usable continuous moment.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from genlab_core.action.window import container_fps, cut_times, duration_s, motion_profile

logger = logging.getLogger(__name__)

#: A PV's first seconds are usually a distributor/studio logo card, and its
#: last seconds a broadcast-date slate. Neither is a moment.
_HEAD_SKIP_S = 2.0
_TAIL_SKIP_S = 1.0

#: Shot lengths the anime edit grammar asks for.
MIN_MOMENT_S = 0.8
MAX_MOMENT_S = 2.0

PEAK = "peak"
COLD_OPEN = "cold_open"
TEXT_SLATE = "text_slate"


class PVUnavailable(RuntimeError):
    """The PV could not be downloaded. Callers fall back to stills."""


@dataclass(frozen=True)
class PVMoment:
    """One window of the PV, with the numbers that chose it."""

    start_s: float
    duration_s: float
    motion: float
    kind: str = PEAK

    @property
    def end_s(self) -> float:
        return self.start_s + self.duration_s

    def overlaps(self, other: PVMoment, pad_s: float = 0.25) -> bool:
        return self.start_s < other.end_s + pad_s and other.start_s < self.end_s + pad_s

    def row(self) -> str:
        return (
            f"  {self.kind:<10} t={self.start_s:6.2f}s  "
            f"{self.duration_s:4.2f}s  motion={self.motion:6.2f}"
        )


def download_pv(trailer_id: str, dest: Path, *, timeout_s: int = 300) -> Path:
    """Download the official PV by its YouTube id.

    Goes through ``media.download_top_videos._download_video`` rather than
    calling yt-dlp here: that function carries the cookie resolution, the
    player-client order and the WARP interaction that three separate outages
    were spent getting right. A second yt-dlp invocation in this repo would
    be a second place for those to drift.
    """
    from genlab_core.media.download_top_videos import _download_video

    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        logger.info("[pv] cached %s", dest.name)
        return dest
    url = f"https://www.youtube.com/watch?v={trailer_id}"
    # VIDEO ONLY. The reel's audio is narration over a bed; ``cut_moment``
    # passes -an and the PV's own track is never heard. Asking for muxed
    # audio is what capped this at 360p -- the default selector's high-res
    # tiers all require `bestaudio[ext=m4a]`, which YouTube did not offer for
    # either PV, so every one of them failed on its audio half.
    res = _download_video(
        url,
        str(dest),
        format_selector=(
            "bestvideo[height>=1080]/bestvideo[height>=720]/bestvideo/best[height>=720]/best"
        ),
        # "" = let yt-dlp pick its clients. Two independent caps held this
        # download at 640x360 and each looked like the other's fault: the
        # audio-codec predicate above, and the pinned player_client list here.
        player_clients="",
    )
    if not res.get("success") or not dest.exists() or dest.stat().st_size == 0:
        raise PVUnavailable(f"{trailer_id}: {res.get('error') or 'no file produced'}")
    logger.info("[pv] %s -> %s (%.1fs)", trailer_id, dest.name, res.get("duration", 0.0))
    return dest


def _windows(path: str, length_s: float, stride_s: float) -> list[tuple[float, float, int]]:
    """(start, mean motion, cuts inside) for every candidate window."""
    prof = motion_profile(path)
    fps = container_fps(path)  # CONTAINER fps -- see module docstring
    cuts = cut_times(path)
    total = prof.duration_s

    out: list[tuple[float, float, int]] = []
    t = _HEAD_SKIP_S
    last = total - _TAIL_SKIP_S - length_s
    while t <= last:
        i0, i1 = int(t * fps), int((t + length_s) * fps)
        seg = prof.values[i0:i1]
        if seg:
            n_cuts = sum(1 for c in cuts if t < c < t + length_s)
            out.append((t, sum(seg) / len(seg), n_cuts))
        t += stride_s
    logger.info(
        "[pv] %.1fs, %d frames @ %.2f fps, %d cuts, %d windows of %.2fs",
        total,
        len(prof.values),
        fps,
        len(cuts),
        len(out),
        length_s,
    )
    return out


def peak_moments(
    path: str | Path,
    *,
    n: int = 5,
    length_s: float = 1.4,
    stride_s: float = 0.25,
    exclude: list[PVMoment] | None = None,
) -> list[PVMoment]:
    """The PV's ``n`` most dynamic CONTINUOUS windows, non-overlapping.

    Motion AND zero cuts. Motion alone returns the densest montage in the
    trailer -- maximum luma change, no continuity, nothing a viewer can read
    as one moment. In a PV, which is cut far faster than a highlight reel, the
    zero-cut constraint does nearly all of the selection work.

    Falls back to allowing ONE cut only if zero-cut windows cannot supply
    ``n``, and says so, because an anime PV can legitimately have no 1.4s
    stretch without a cut.
    """
    path = str(path)
    if not (MIN_MOMENT_S <= length_s <= MAX_MOMENT_S):
        raise ValueError(f"length_s {length_s} outside {MIN_MOMENT_S}-{MAX_MOMENT_S}")
    cand = _windows(path, length_s, stride_s)
    if not cand:
        return []

    taken = list(exclude or [])

    def take(pool: list[tuple[float, float, int]]) -> list[PVMoment]:
        chosen: list[PVMoment] = []
        for start, motion, _ in sorted(pool, key=lambda r: -r[1]):
            m = PVMoment(start, length_s, motion, PEAK)
            # `exclude` keeps the same footage from appearing twice: the cold
            # open is chosen first and is usually the single highest-motion
            # stretch, so without this the first peak lands on top of it.
            if any(m.overlaps(c) for c in chosen + taken):
                continue
            chosen.append(m)
            if len(chosen) == n:
                break
        return chosen

    clean = [c for c in cand if c[2] == 0]
    moments = take(clean)
    if len(moments) < n:
        relaxed = take([c for c in cand if c[2] <= 1])
        logger.warning(
            "[pv] only %d zero-cut windows of %.2fs; relaxing to <=1 cut gave %d. "
            "A PV cut faster than %.2fs per shot has no continuous window of "
            "that length -- this is the clip's grammar, not a failure.",
            len(moments),
            length_s,
            len(relaxed),
            length_s,
        )
        if len(relaxed) > len(moments):
            moments = relaxed
    return sorted(moments, key=lambda m: m.start_s)


def cold_open(path: str | Path, *, length_s: float = 1.0, top_k: int = 12) -> PVMoment:
    """The PV's strongest READABLE second — the reel's first frame.

    Not simply the maximum. In an anime PV the highest luma-difference window
    is a whip pan or a flash transition, and the frame it yields is motion
    blur: measured on TOUGEN ANKI, the top-motion second (42.09) was an
    unreadable green-and-maroon smear, while a zero-cut peak two seconds away
    was the protagonist mid-shout, sharp and on-model.

    So: take the top ``top_k`` windows by motion, then pick the SHARPEST of
    them by edge density. Energy chooses the shortlist; legibility chooses the
    shot. This is the same lesson as "motion alone returns a replay montage",
    one level down — the metric that ranks candidates is not the metric that
    should pick the winner.
    """
    cand = _windows(str(path), length_s, 0.2)
    if not cand:
        raise PVUnavailable(f"{path}: no measurable window for a cold open")
    shortlist = sorted(cand, key=lambda r: -r[1])[:top_k]
    scored = [(_edge_density(str(path), s + length_s / 2), s, m) for s, m, _ in shortlist]
    edge, start, motion = max(scored, key=lambda r: r[0])
    logger.info(
        "[pv] cold open t=%.2fs motion=%.2f edge=%.2f (top motion was %.2f at %.2fs "
        "— rejected as blur if it is not this one)",
        start,
        motion,
        edge,
        shortlist[0][1],
        shortlist[0][0],
    )
    return PVMoment(start, length_s, motion, COLD_OPEN)


def _edge_density(path: str, t: float) -> float:
    """Mean edge energy of one frame. A title card is mostly strokes on flat."""
    r = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-ss",
            f"{t:.3f}",
            "-i",
            path,
            "-frames:v",
            "1",
            "-vf",
            "edgedetect=low=0.1:high=0.3,signalstats,metadata=print:key=lavfi.signalstats.YAVG",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    vals = [float(x.rsplit("=", 1)[1]) for x in (r.stderr or "").splitlines() if "YAVG=" in x]
    return vals[-1] if vals else 0.0


def text_slate(
    path: str | Path, *, min_edge: float = 4.0, max_motion: float = 2.0
) -> PVMoment | None:
    """The PV's end slate, if it has one. ``None`` if it does not.

    Named for what it FINDS, not for what was asked for. ANIME-13 asks for
    "the cut with the show's title card"; what this detector actually lands on
    in both real PVs measured is the BROADCAST-DATE slate — the frame that
    says which channel and which Friday. It is the one shot in a PV that is
    still and covered in strokes, which is exactly the thing the heuristic
    scores, and it is legitimately the show's own material. Calling it
    ``title_card`` would have shipped a name that quietly disagreed with the
    pixels.

    Such a slate MUST be letterboxed rather than cropped — see ``cut_moment``.

    Returns None rather than a best guess. "If present" is load-bearing: a
    wrong frame labelled as the show's card would be the most confidently
    wrong shot in the reel, and plenty of PVs have no such frame.
    """
    path = str(path)
    total = duration_s(path)
    if total <= 0:
        return None
    prof = motion_profile(path)
    fps = container_fps(path)

    best: tuple[float, float, float] | None = None  # (edge, t, motion)
    t = max(_HEAD_SKIP_S, total * 0.66)
    while t < total - _TAIL_SKIP_S:
        i0, i1 = int(t * fps), int((t + 0.5) * fps)
        seg = prof.values[i0:i1]
        motion = sum(seg) / len(seg) if seg else 999.0
        if motion <= max_motion:
            edge = _edge_density(path, t + 0.25)
            if best is None or edge > best[0]:
                best = (edge, t, motion)
        t += 0.5

    if best is None or best[0] < min_edge:
        logger.info(
            "[pv] no end slate: best still frame scored %.2f edge against a %.2f "
            "floor. Reporting absence rather than a guess.",
            best[0] if best else 0.0,
            min_edge,
        )
        return None
    edge, t, motion = best
    logger.info("[pv] end slate at %.2fs (edge %.2f, motion %.2f)", t, edge, motion)
    return PVMoment(t, 1.2, motion, TEXT_SLATE)


def cut_moment(
    src: Path, moment: PVMoment, out: Path, *, pad_colour: str = "#101018", timeout_s: int = 180
) -> bool:
    """Cut one moment to 1080x1920 bt709. Full-bleed, unless it carries TEXT.

    Going 16:9 -> 9:16 full-bleed discards about two thirds of the width. For
    an action shot that is the right trade; for a slate it cut the broadcast
    date through the middle of its characters, which reads as a broken render
    rather than as a crop. Text slates are therefore FITTED and padded on the
    kit's card colour.

    ``-ss`` goes AFTER ``-i`` so the seek is frame-accurate: before ``-i`` it
    snaps to the nearest keyframe, and two starts 33 ms apart decoded the
    SAME frame when that was last got wrong.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    if moment.kind == TEXT_SLATE:
        # Fit the slate whole, then fill the bars with a BLURRED, darkened
        # copy of the same frame rather than flat colour. Letterboxing 16:9
        # into 9:16 leaves two thirds of the frame empty; flat bars read as a
        # broken export, the blurred fill reads as a deliberate vertical
        # treatment and keeps every character of the date intact.
        vf = (
            "split=2[bg][fg];"
            "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,gblur=sigma=42,eq=brightness=-0.12[bgb];"
            "[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fgs];"
            "[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )
    else:
        vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"
    p = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(src),
            "-ss",
            f"{moment.start_s:.3f}",
            "-t",
            f"{moment.duration_s:.3f}",
            "-vf",
            vf,
            "-an",
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-crf",
            "20",
            "-preset",
            "fast",
            "-pix_fmt",
            "yuv420p",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    if p.returncode != 0:
        logger.warning("[pv] cut failed at %.2fs: %s", moment.start_s, p.stderr[-300:])
        return False
    return True


# ── ANIME-14 §4: a PV frame can carry the PV's own text ──────────────────

#: A window whose frames carry more than this fraction of detected text is
#: not usable under our own overlays. 3% of frame area is roughly one line of
#: broadcast furniture at 1080x1920.
MAX_BAKED_TEXT_FRACTION = 0.03


def window_text_fraction(
    path: str | Path, moment: PVMoment, *, stride_s: float = 0.5
) -> tuple[float, str]:
    """(worst text-area fraction across the window, the text seen).

    OCR rather than a heuristic because the thing being detected IS text.
    Sampled every ``stride_s`` because a PV's furniture — the title lockup,
    the streaming-service bug, the date line — appears and leaves inside a
    single shot.
    """
    from genlab_core.still.reference import ocr_frame

    tmp = Path(path).parent / ".ocr_windows"
    tmp.mkdir(parents=True, exist_ok=True)
    worst, seen = 0.0, ""
    t = moment.start_s
    while t < moment.end_s:
        frac, text = ocr_frame(Path(path), t, tmp)
        if frac > worst:
            worst, seen = frac, text
        t += stride_s
    return worst, seen[:120]


def without_baked_text(
    path: str | Path,
    moments: list[PVMoment],
    *,
    max_fraction: float = MAX_BAKED_TEXT_FRACTION,
    keep_min: int = 1,
) -> tuple[list[PVMoment], list[dict]]:
    """Drop windows carrying the PV's own text. Returns (kept, rejected).

    ``keep_min`` is a floor, not a courtesy: a PV that is title-carded
    end-to-end would otherwise return nothing and the reel would have no
    footage at all. When the floor engages it is LOGGED as a warning, because
    "we used a texty window" and "there were no clean windows" must not look
    the same afterwards.
    """
    scored = []
    for m in moments:
        frac, text = window_text_fraction(path, m)
        scored.append((m, frac, text))
    kept = [m for m, f, _ in scored if f <= max_fraction]
    rejected = [
        {"start_s": m.start_s, "text_fraction": round(f, 4), "text": t}
        for m, f, t in scored
        if f > max_fraction
    ]
    if len(kept) < keep_min:
        scored.sort(key=lambda r: r[1])
        kept = [r[0] for r in scored[:keep_min]]
        logger.warning(
            "[pv] every window carries baked text above %.1f%%; keeping the %d "
            "cleanest (%.1f%%) so the reel has footage at all. The overlays will "
            "sit on top of the PV's own text.",
            max_fraction * 100,
            keep_min,
            scored[0][1] * 100 if scored else 0.0,
        )
        rejected = [r for r in rejected if r["start_s"] not in {m.start_s for m in kept}]
    logger.info(
        "[pv] text gate: %d kept, %d rejected at <=%.1f%% frame area",
        len(kept),
        len(rejected),
        max_fraction * 100,
    )
    return kept, rejected


# ── ANIME-15 §2: dark windows are rejected, not graded up ────────────────

#: Mean luma floor for any PV frame that reaches the reel.
LUMA_FLOOR = 40.0
#: The reveal frame must be BRIGHT — it is the reel's loudest second.
LUMA_FLOOR_REVEAL = 80.0


def window_luma(path: str | Path, moment: PVMoment, *, stride_s: float = 0.5) -> float:
    """Lowest mean Y across the window.

    The MINIMUM rather than the mean of means: a window that is bright for a
    second and black for half of it still puts a black half-second on screen,
    and averaging hides exactly that.
    """
    vals: list[float] = []
    t = moment.start_s
    while t < moment.end_s:
        r = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostats",
                "-ss",
                f"{t:.3f}",
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-vf",
                "signalstats,metadata=print:key=lavfi.signalstats.YAVG",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        got = [float(x.rsplit("=", 1)[1]) for x in (r.stderr or "").splitlines() if "YAVG=" in x]
        if got:
            vals.append(got[-1])
        t += stride_s
    return min(vals) if vals else 0.0


def bright_enough(
    path: str | Path, moments: list[PVMoment], *, floor: float = LUMA_FLOOR, keep_min: int = 1
) -> tuple[list[PVMoment], list[dict]]:
    """Drop windows below the luma floor. Returns (kept, rejected).

    Rejected, never graded up: raising the level of a frame that was shot dark
    raises its noise with it, and an anime PV's dark frames are dark because
    the scene is — the information is not there to recover.
    """
    scored = [(m, window_luma(path, m)) for m in moments]
    kept = [m for m, y in scored if y >= floor]
    rejected = [{"start_s": m.start_s, "min_luma": round(y, 1)} for m, y in scored if y < floor]
    if len(kept) < keep_min and scored:
        scored.sort(key=lambda r: -r[1])
        kept = [r[0] for r in scored[:keep_min]]
        logger.warning(
            "[pv] every window is below the %.0f luma floor; keeping the %d "
            "brightest (min Y %.1f). The reel will carry a dark shot.",
            floor,
            keep_min,
            scored[0][1],
        )
        rejected = [r for r in rejected if r["start_s"] not in {m.start_s for m in kept}]
    logger.info(
        "[pv] luma gate: %d kept, %d rejected below Y=%.0f", len(kept), len(rejected), floor
    )
    return kept, rejected


def brightest(path: str | Path, moments: list[PVMoment]) -> PVMoment | None:
    """The brightest window — the reveal's home, per §5."""
    if not moments:
        return None
    return max(moments, key=lambda m: window_luma(path, m))
