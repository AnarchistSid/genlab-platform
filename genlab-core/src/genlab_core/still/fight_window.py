"""Where in the episode the fight is. Community metadata locates; licence supplies.

ANIME-PEAK-04 §1. The reel needs the WINDOW to about a second. Only the drawn
flash needs the frame. PEAK-03 gated on the frame and produced 25 picks for 2
marks — a firing rate, not a recall — while the thing the output actually
depends on went unmeasured.

Two sources, strictly separated:

* **Fan clips are a LOCATOR.** Their titles, durations and descriptions say
  where in the episode a fight sits. Their footage is never touched, never
  downloaded, never rendered. A fan upload is not a licensed source and no
  amount of convenience makes it one.
* **The licensed episode is the SOURCE.** Show-and-episode matched through
  ``fight_source``, which is the fix that stopped Fairy Tail 41 being used as
  Jujutsu Kaisen 41.

The locator narrows a 24-minute episode to a 2-4 minute region; the coarse
pass then ranks 6-14 s windows inside it.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

#: The locator narrows to a region this big; the marker watches only this.
REGION_MIN_S, REGION_MAX_S = 120.0, 240.0
#: The reel's window.
WINDOW_MIN_S, WINDOW_MAX_S = 6.0, 14.0
#: §1 — a proposed window counts as a hit at this overlap with the mark.
WINDOW_OVERLAP = 0.60
#: §1 — at most this many windows proposed per fight, so precision means
#: something. PEAK-03 proposed 25 impacts against 2 marks.
MAX_WINDOWS_PER_FIGHT = 2

#: "12:34", "at 1:02:03", "starts 14:20"
_TS = re.compile(r"\b(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\b")

#: "Season 2 Episode 17", "S2E17", "ep 17". Fan-clip TITLES carry the episode
#: far more reliably than descriptions carry timestamps — 0 of 4 probed clips
#: had a timestamp, while 2 of 4 named the season and episode outright.
_EP_IN_TITLE = (
    re.compile(r"season\s*(\d{1,2})\s*episode\s*(\d{1,3})", re.I),
    re.compile(r"\bs(\d{1,2})\s*e(\d{1,3})\b", re.I),
)
_EP_ONLY = re.compile(r"\bepisode\s*(\d{1,3})\b", re.I)


@dataclass
class Region:
    """Where to look, and what said so."""

    start_s: float
    end_s: float
    basis: str
    evidence: list[str] = field(default_factory=list)

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    def row(self) -> str:
        return (f"{self.start_s / 60:.1f}-{self.end_s / 60:.1f} min "
                f"({self.duration_s:.0f}s) via {self.basis}")


@dataclass
class Window:
    start_s: float
    end_s: float
    score: float
    n_onsets: int = 0
    motion: float = 0.0

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    def overlap(self, other: Window) -> float:
        """Fraction of the OTHER (the mark) that this covers."""
        lo, hi = max(self.start_s, other.start_s), min(self.end_s, other.end_s)
        inter = max(0.0, hi - lo)
        return inter / other.duration_s if other.duration_s > 0 else 0.0

    def row(self) -> str:
        return (f"  {self.start_s:7.2f}-{self.end_s:7.2f}s ({self.duration_s:4.1f}s)  "
                f"score {self.score:6.2f}  onsets {self.n_onsets:2d}  motion {self.motion:6.1f}")


#: A modal value over a handful of titles is not consensus. Measured across
#: 13 fights, unfiltered extraction produced "Maki vs the Zenin -> JJK S3E4"
#: (there is no season 3) and "Denji vs Katana Man -> ep 12" on a support of
#: ONE title out of 31. Both would have been treated as ground truth.
MIN_CONSENSUS_SUPPORT = 3
MIN_CONSENSUS_FRACTION = 0.10


def episode_consensus(titles: list[str], *, fight: str | None = None,
                      show: str | None = None) -> dict:
    """What the community says the episode number is.

    This is the locator's most useful output, and it was not what it was built
    for. Probing four Sukuna vs Mahoraga fan clips for description timestamps
    found none — but two titles read "Jujutsu Kaisen Season 2 Episode 17",
    against a catalog that recorded episode 41 / S2E18. A curated number
    entered by hand is a guess; a dozen uploaders agreeing is evidence.

    Returns the modal (season, episode) and its support, for CROSS-CHECKING
    the catalog. It never silently overwrites it — a disagreement is reported
    so it can be looked at.
    """
    # Only titles that NAME THIS FIGHT may vote. A search for one fight
    # returns plenty of clips from the same show, and their episode numbers
    # are someone else's scene. Reusing the source gate's matcher rather than
    # writing a second one.
    if fight and show:
        from genlab_core.still.fight_source import names_the_fight

        voting = [t for t in titles if names_the_fight(t, fight, show)]
    else:
        voting = list(titles)

    pairs: list[tuple[int | None, int]] = []
    for t in voting:
        matched = False
        for pat in _EP_IN_TITLE:
            m = pat.search(t or "")
            if m:
                pairs.append((int(m.group(1)), int(m.group(2))))
                matched = True
                break
        if not matched:
            m = _EP_ONLY.search(t or "")
            if m:
                pairs.append((None, int(m.group(1))))
    if not pairs:
        return {"episode": None, "season": None, "support": 0,
                "n_titles": len(titles), "n_voting": len(voting),
                "reason": "no episode reference in any title naming this fight"}
    top, n = Counter(pairs).most_common(1)[0]
    frac = n / len(voting) if voting else 0.0
    if n < MIN_CONSENSUS_SUPPORT or frac < MIN_CONSENSUS_FRACTION:
        return {"episode": None, "season": None, "support": n,
                "n_titles": len(titles), "n_voting": len(voting),
                "reason": (f"weak: {n} votes ({frac:.0%} of {len(voting)} naming titles) "
                           f"below the {MIN_CONSENSUS_SUPPORT}/{MIN_CONSENSUS_FRACTION:.0%} "
                           f"floor — a modal value over a handful is not consensus"),
                "all": dict(Counter(pairs).most_common(4))}
    return {"season": top[0], "episode": top[1], "support": n,
            "n_titles": len(titles), "n_voting": len(voting), "reason": "",
            "all": dict(Counter(pairs).most_common(4))}


def _seconds(m: re.Match) -> float:
    h, mm, ss = m.group(1), m.group(2), m.group(3)
    return (int(h) * 3600 if h else 0) + int(mm) * 60 + int(ss)


def locate(titles_and_descriptions: list[str], *, episode_runtime_s: float,
           clip_durations: list[float] | None = None) -> Region:
    """A 2-4 minute region, from metadata alone.

    Timestamps in fan-clip titles and descriptions are the strongest signal
    and are used directly. Failing that, the modal clip duration says how long
    the fight runs, and a fight of that length in an episode of this runtime
    sits — by overwhelming convention — in the last third.
    """
    stamps: list[float] = []
    for text in titles_and_descriptions:
        for m in _TS.finditer(text or ""):
            t = _seconds(m)
            if 30.0 <= t <= episode_runtime_s:
                stamps.append(t)

    if stamps:
        centre = float(np.median(stamps))
        half = REGION_MIN_S / 2
        return Region(max(0.0, centre - half), min(episode_runtime_s, centre + half),
                      basis=f"{len(stamps)} timestamps in fan-clip metadata, median "
                            f"{centre / 60:.1f} min",
                      evidence=[f"{s:.0f}s" for s in sorted(stamps)[:8]])

    # No timestamps. Fall back to convention, and SAY that it is convention.
    durs = [d for d in (clip_durations or []) if 20.0 <= d <= 600.0]
    modal = float(Counter(round(d / 30) * 30 for d in durs).most_common(1)[0][0]) if durs else 0.0
    start = episode_runtime_s * 0.60
    end = min(episode_runtime_s, start + REGION_MAX_S)
    return Region(start, end,
                  basis=("no timestamps found; the last third by convention"
                         + (f", modal fan-clip length {modal:.0f}s" if modal else "")),
                  evidence=[f"{len(durs)} clip durations"] if durs else [])


def fan_clip_metadata(queries: list[str], *, ytdlp: str = "yt-dlp",
                      per_query: int = 8) -> tuple[list[str], list[float]]:
    """Titles and durations of fan clips. METADATA ONLY — nothing downloaded.

    ``--flat-playlist`` returns the search page's entries without fetching any
    media. This function has no path that can write a video file, which is the
    point: the separation between locator and source has to be structural, not
    a rule someone remembers.
    """
    texts: list[str] = []
    durations: list[float] = []
    for q in queries:
        r = subprocess.run(
            [ytdlp, "--no-warnings", "--flat-playlist", "-J", f"ytsearch{per_query}:{q}"],
            capture_output=True, text=True, timeout=300)
        try:
            entries = json.loads(r.stdout or "{}").get("entries") or []
        except ValueError:
            continue
        for e in entries:
            if e.get("title"):
                texts.append(e["title"])
            if e.get("description"):
                texts.append(e["description"])
            if e.get("duration"):
                durations.append(float(e["duration"]))
    logger.info("[locate] %d metadata strings, %d durations from %d queries",
                len(texts), len(durations), len(queries))
    return texts, durations


def coarse_windows(video: Path, region: Region, audio: Path | None = None, *,
                   top_n: int = MAX_WINDOWS_PER_FIGHT,
                   length_s: float = 10.0) -> list[Window]:
    """Rank 6-14 s windows inside the region by audio onsets AND motion.

    Clusters rather than single frames: a fight's window is where hits are
    DENSE, and density survives a detector that cannot place any one of them.
    """
    from genlab_core.action.window import container_fps, motion_profile
    from genlab_core.still.fight_moment import audio_envelope
    from genlab_core.still.fight_moment_v2 import AUDIO_STEP_DB, _audio_onsets

    fps = container_fps(str(video))
    prof = np.array(motion_profile(str(video)).values, dtype=np.float32)
    env = audio_envelope(audio or video)
    onsets = _audio_onsets(env, 0.02, AUDIO_STEP_DB) if env.size else []
    onsets = [t for t in onsets if region.start_s <= t <= region.end_s]

    mot_norm = float(np.percentile(prof, 90)) or 1.0
    step = 1.0
    cands: list[Window] = []
    t = region.start_s
    while t + length_s <= region.end_s:
        n = sum(1 for o in onsets if t <= o <= t + length_s)
        i0, i1 = int(t * fps), int((t + length_s) * fps)
        seg = prof[i0:i1]
        m = float(seg.mean()) if seg.size else 0.0
        # Onset DENSITY carries the score; motion breaks ties. A window with
        # four hits and average motion is the fight; one with no hits and high
        # motion is a camera move.
        cands.append(Window(t, t + length_s, n * 1.0 + (m / mot_norm) * 0.5, n, m))
        t += step

    cands.sort(key=lambda w: -w.score)
    kept: list[Window] = []
    for w in cands:
        if any(w.overlap(k) > 0.25 or k.overlap(w) > 0.25 for k in kept):
            continue
        kept.append(w)
        if len(kept) == top_n:
            break
    logger.info("[locate] %d onsets in region; %d windows proposed (cap %d)",
                len(onsets), len(kept), top_n)
    for w in kept:
        logger.info("%s", w.row())
    return kept


def window_recall(picks: list[Window], marks: list[Window],
                  overlap: float = WINDOW_OVERLAP) -> dict:
    """§1's gate. A pick counts if it covers >= ``overlap`` of a mark."""
    hit = [m for m in marks if any(p.overlap(m) >= overlap for p in picks)]
    good = [p for p in picks if any(p.overlap(m) >= overlap for m in marks)]
    return {"n_marks": len(marks), "n_picks": len(picks),
            "recall": round(len(hit) / len(marks), 3) if marks else 0.0,
            "precision": round(len(good) / len(picks), 3) if picks else 0.0,
            "overlap_required": overlap,
            "missed": [[round(m.start_s, 2), round(m.end_s, 2)] for m in marks
                       if m not in hit]}
