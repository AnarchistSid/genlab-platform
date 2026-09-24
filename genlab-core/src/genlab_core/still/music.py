"""ANIME-PEAK-09 — the bed's own shape, measured, and the gates over it.

The v2 reels laid a bed under a finished cut. This module exists so the cut
can be built on the bed instead: it measures tempo, where the sub sits, and
where the drop is, so the impact can be placed ON the drop rather than
wherever the hand mark happened to fall.

Everything here is measured from the audio. Tempo reuses
``action.grid.detect_grid`` rather than re-deriving one -- that detector
already learned that flux autocorrelation peaks at the half and the double
as hard as at the true tempo, and a grid at half tempo makes every off-beat
event look on-grid.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

SR = 22050               # plenty for a sub-band and a drop
SUB_HZ = 120.0           # "sub" is everything under this
CLICK_BAND = (2000.0, 8000.0)   # where the cowbell and the click live

# Gates (§1)
TEMPO_BAND = (130.0, 165.0)
# detect_grid searches 120-180 BPM, so a reading below 120 is not reachable
# by this instrument. The v2 beds all read 150 against the packet's 152; its
# 99 reading came from a detector with a wider band or from an octave below.

MIN_SUB_SHARE = 0.25     # of spectral MAGNITUDE — see sub_share
DROP_WINDOW_S = (6.0, 10.0)     # where a usable drop sits, room for the wind-up
MIN_DROP_STEP_DB = 3.0          # a step smaller than this is not a drop


@dataclass(frozen=True)
class MusicMetrics:
    path: Path
    duration_s: float
    bpm: float
    sub_share: float
    drop_t: float | None
    drop_step_db: float
    click_flatness: float
    loudest_s: float
    lufs: float
    true_peak: float
    energy_db: list[float] = field(default_factory=list, repr=False)

    @property
    def in_tempo_band(self) -> bool:
        return TEMPO_BAND[0] <= self.bpm <= TEMPO_BAND[1]

    @property
    def has_drop(self) -> bool:
        return self.drop_t is not None

    def row(self, label: str = "") -> str:
        d = f"{self.drop_t:5.2f}s" if self.drop_t is not None else " none "
        return (f"  {label:<20}{self.bpm:6.1f} BPM  sub {self.sub_share:4.0%}  "
                f"drop {d} (+{self.drop_step_db:4.1f} dB)  "
                f"loudest {self.loudest_s:5.2f}s  {self.lufs:6.1f} LUFS")


def _decode(path: Path, *, hp: float | None = None, lp: float | None = None):
    """Mono float samples at SR, optionally band-limited."""
    import numpy as np

    af = []
    if hp:
        af.append(f"highpass=f={hp}:poles=2")
    if lp:
        af.append(f"lowpass=f={lp}:poles=2")
    cmd = ["ffmpeg", "-v", "error", "-i", str(path)]
    if af:
        cmd += ["-af", ",".join(af)]
    cmd += ["-f", "f32le", "-ac", "1", "-ar", str(SR), "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype="<f4")


def _rms_db(x, win_s: float = 0.5, hop_s: float = 0.1):
    """Windowed RMS in dB, plus the timestamp of each window."""
    import numpy as np

    w, h = int(SR * win_s), int(SR * hop_s)
    if len(x) < w:
        return np.array([]), np.array([])
    n = 1 + (len(x) - w) // h
    idx = np.arange(w)[None, :] + h * np.arange(n)[:, None]
    frames = x[idx]
    rms = np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1) + 1e-12)
    return 20 * np.log10(rms), np.arange(n) * hop_s + win_s / 2


def sub_share(path: Path) -> float:
    """Share of mean spectral MAGNITUDE under SUB_HZ.

    Two defensible definitions of "sub share" differ by 3x on the same file.
    A POWER ratio reads 41-50% on the v2 beds; a MAGNITUDE share reads
    14-17%. The packet's 15-16% observation and its 25% gate are stated
    against the magnitude definition, so that is the one implemented here --
    taking the threshold without its method would have made the gate
    unfalsifiable, since every candidate clears 25% on the power meter.

    `sub_power_share` keeps the other reading available under its own name so
    the two can never be mistaken for each other.
    """
    import numpy as np

    x = _decode(path).astype(np.float64)
    n, hop = 8192, 4096
    acc = np.zeros(n // 2 + 1)
    for i in range(0, max(1, len(x) - n), hop):
        acc += np.abs(np.fft.rfft(x[i:i + n] * np.hanning(n)))
    freqs = np.fft.rfftfreq(n, 1 / SR)
    return float(acc[freqs < SUB_HZ].sum() / (acc.sum() + 1e-12))


def sub_power_share(path: Path) -> float:
    """Share of total ENERGY under SUB_HZ. Not the gate; see `sub_share`."""
    import numpy as np

    full = _decode(path)
    sub = _decode(path, lp=SUB_HZ)
    pf = float((full.astype(np.float64) ** 2).sum()) + 1e-12
    return min(1.0, float((sub.astype(np.float64) ** 2).sum()) / pf)


def find_drop(path: Path) -> tuple[float | None, float, list[float]]:
    """The largest sustained step up in sub-weighted energy.

    A drop is not the loudest moment and not the biggest single-frame jump --
    both of those are hits. It is a LEVEL SHIFT: the seconds after it are
    louder than the seconds before it and stay that way. So the statistic is
    the difference between the median of the following 2 s and the median of
    the preceding 2 s, which a one-frame hit cannot move.
    """
    import numpy as np

    x = _decode(path, lp=400.0)     # sub-weighted: the drop is a bass event
    db, t = _rms_db(x)
    if len(db) < 60:
        return None, 0.0, []
    look = int(2.0 / 0.1)
    best_i, best_step = None, 0.0
    for i in range(look, len(db) - look):
        before = float(np.median(db[i - look:i]))
        after = float(np.median(db[i:i + look]))
        step = after - before
        if step > best_step:
            best_step, best_i = step, i
    if best_i is None or best_step < MIN_DROP_STEP_DB:
        return None, float(best_step), db.tolist()
    return float(t[best_i]), float(best_step), db.tolist()


def find_drops(path: Path, n: int = 4, min_gap_s: float = 4.0) -> list[tuple[float, float]]:
    """Every level shift worth aligning to, strongest first.

    Phonk repeats its drop -- the section comes back. Returning only the
    largest throws away alignment points the track already contains, which is
    what left two reels unservable when the generator put its first drop at
    6.7 s and the reel needed 13.5 s.
    """
    import numpy as np

    x = _decode(path, lp=400.0)
    db, t = _rms_db(x)
    if len(db) < 60:
        return []
    look = int(2.0 / 0.1)
    steps = []
    for i in range(look, len(db) - look):
        before = float(np.median(db[i - look:i]))
        after = float(np.median(db[i:i + look]))
        steps.append((after - before, float(t[i])))
    steps.sort(reverse=True)
    out: list[tuple[float, float]] = []
    for step, tt in steps:
        if step < MIN_DROP_STEP_DB:
            break
        if all(abs(tt - o[0]) >= min_gap_s for o in out):
            out.append((tt, step))
        if len(out) >= n:
            break
    return out


def loudest_second(path: Path) -> float:
    """Midpoint of the loudest 1-second window."""
    import numpy as np

    db, t = _rms_db(_decode(path), win_s=1.0, hop_s=0.1)
    if not len(db):
        return 0.0
    return float(t[int(np.argmax(db))])


def click_flatness(path: Path, at_s: float | None = None) -> float:
    """Spectral flatness in the 2-8 kHz band, where the cowbell and click sit.

    A flat band there means broadband percussion is present. A tonal pad with
    no percussion is peaky, and phonk without its cowbell is not phonk.
    """
    import numpy as np

    x = _decode(path, hp=CLICK_BAND[0], lp=CLICK_BAND[1])
    if at_s is not None:
        a, b = int(max(0.0, at_s - 0.5) * SR), int((at_s + 0.5) * SR)
        x = x[a:b]
    if len(x) < 1024:
        return 0.0
    spec = np.abs(np.fft.rfft(x.astype(np.float64) * np.hanning(len(x)))) ** 2 + 1e-12
    gm = np.exp(np.log(spec).mean())
    return float(gm / spec.mean())


def measure(path: Path) -> MusicMetrics:
    from genlab_core.action.grid import detect_grid
    from genlab_core.still.audio import measure_loudness
    from genlab_core.still.reference import duration_s

    grid = detect_grid(str(path))
    drop_t, step, db = find_drop(path)
    lufs, tp = measure_loudness(path)
    return MusicMetrics(
        path=path, duration_s=duration_s(path), bpm=float(grid.bpm),
        sub_share=sub_share(path), drop_t=drop_t, drop_step_db=step,
        click_flatness=click_flatness(path, drop_t), loudest_s=loudest_second(path),
        lufs=lufs, true_peak=tp, energy_db=db)


# ── §1 registers, candidates, and the pick ──────────────────────────────

_CFG = None


def registers() -> dict:
    """The register config. Prompts are config, never code."""
    global _CFG
    if _CFG is None:
        import yaml

        here = Path(__file__).resolve()
        for base in (here.parents[3], here.parents[4]):
            p = base / "config" / "anime_music_registers.yaml"
            if p.exists():
                _CFG = yaml.safe_load(p.read_text())
                break
        else:
            raise FileNotFoundError("anime_music_registers.yaml not found")
    return _CFG


def register_for(fight_id: str) -> str:
    cfg = registers()
    return cfg["fights"].get(fight_id, "brazilian_phonk")


def prompt_for(register: str, intro_s: float = 8.0) -> tuple[str, float]:
    r = registers()["registers"][register]
    return (r["prompt"].format(tempo=r["tempo"], intro=int(round(intro_s))).strip(),
            float(r["tempo"]))


@dataclass(frozen=True)
class Candidate:
    path: Path
    metrics: MusicMetrics
    register: str
    continuous: bool = True

    target_drop_s: float = 8.0

    @property
    def usable_drop(self) -> tuple[float, float] | None:
        """The drop this candidate offers NEAREST the reel's wind-up.

        Not the biggest one. Phonk repeats its drop, and taking only the
        largest left two reels unservable when the generator put its first
        drop at 6.7 s and the reel needed 13.5 s -- while the same file
        carried a second one at 12.75 s.
        """
        drops = find_drops(self.path)
        if not drops:
            return None
        return min(drops, key=lambda d: abs(d[0] - self.target_drop_s))

    @property
    def gates(self) -> dict[str, bool]:
        m = self.metrics
        resid = registers()["defaults"]["max_align_residual_s"]
        u = self.usable_drop
        return {
            "tempo_130_165": m.in_tempo_band,
            "sub_share_25": m.sub_share >= MIN_SUB_SHARE,
            "has_drop": u is not None,
            "drop_alignable": u is not None and abs(u[0] - self.target_drop_s) <= resid,
            "click_present": m.click_flatness >= MIN_CLICK_FLATNESS,
        }

    @property
    def passes(self) -> bool:
        return all(self.gates.values())

    def row(self, label: str) -> str:
        failed = [k for k, v in self.gates.items() if not v]
        u = self.usable_drop
        d = f"{u[0]:5.2f}s(+{u[1]:4.1f}dB)" if u else "   none      "
        return (f"  {label:<20}{self.metrics.bpm:6.1f} BPM  sub "
                f"{self.metrics.sub_share:4.0%}  usable drop {d}  "
                + ("PASS" if self.passes else "fails: " + ",".join(failed)))


#: PROVISIONAL — not yet enforced. Measured on the v2 orchestral beds (no
#: cowbell) the statistic spans 0.011-0.113, so it does not by itself
#: separate "has a cowbell" from "has cymbals and brass". It needs a positive
#: control: phonk candidates measured against those beds. Until then the gate
#: reports the value and passes.
MIN_CLICK_FLATNESS = 0.0


def generate_candidates(fight_id: str, work: Path, *, n: int | None = None,
                        duration_s: float | None = None,
                        target_drop_s: float = 8.0) -> list[Candidate]:
    """Generate and MEASURE n beds for this fight's register.

    Two prompt clauses from different packets collide here. ANIME-16 added
    CONTINUOUS_BED ("constant rhythmic pulse from start to end") because beds
    with gaps were the dead air in the mix. PEAK-09 needs a low-pass-filtered
    intro that BUILDS -- which is a dynamic arc, and may be exactly what that
    clause suppresses. Rather than pick, candidates alternate and the
    measurement decides: `continuous` is recorded per candidate.
    """
    from genlab_core.still.sound import generate_bed

    cfg = registers()
    reg = register_for(fight_id)
    prompt, _ = prompt_for(reg, intro_s=target_drop_s)
    n = n or int(cfg["defaults"]["candidates"])
    duration_s = duration_s or float(cfg["defaults"]["duration_s"])
    work.mkdir(parents=True, exist_ok=True)
    out = []
    for i in range(n):
        dest = work / f"{fight_id}_{reg}_{i}.mp3"
        # Vary the prompt per candidate: the generator is deterministic
        # enough that an identical prompt returns near-identical audio.
        p = prompt if i == 0 else f"{prompt}, variation {i + 1}"
        cont = i < max(1, n - 1)          # last candidate drops the clause
        generate_bed(p, duration_s, dest, continuous=cont)
        out.append(Candidate(dest, measure(dest), reg, cont, target_drop_s))
        logger.info("[music] %s", out[-1].row(dest.name))
    return out


def pick(candidates: list[Candidate], target_drop_s: float | None = None) -> Candidate | None:
    """The passing candidate whose drop needs the least alignment.

    Nearest to the reel's OWN wind-up, not to a fixed window: the residual is
    what align_bed has to absorb, either by trimming into the build or by
    leaving a silent opening, and both are costs worth minimising.
    """
    ok = [c for c in candidates if c.passes]
    if not ok:
        return None
    t = target_drop_s if target_drop_s is not None else ok[0].target_drop_s
    return min(ok, key=lambda c: abs((c.usable_drop or (0.0, 0.0))[0] - t))


# ── §2 the reel is anchored on the drop ─────────────────────────────────


def align_bed(bed: Path, drop_t: float, impact_rel: float, reel_s: float,
              dest: Path) -> dict:
    """Position the bed so its drop lands on the reel's impact.

    The drop is never moved -- it is the anchor. What moves is where the bed
    STARTS relative to the reel:

    * wind-up shorter than the intro (``drop_t > impact_rel``): the intro is
      trimmed from its start, so the bed begins already part-built.
    * wind-up longer than the intro (``drop_t < impact_rel``): the bed starts
      late and the opening seconds are pre-roll, carried by the show's own
      audio alone.

    Returns the offsets actually applied, because a large pre-roll is a real
    cost (a silent opening) and has to be visible in the report rather than
    inferred.
    """
    trim = max(0.0, drop_t - impact_rel)      # cut from the bed's head
    preroll = max(0.0, impact_rel - drop_t)   # silence before the bed starts
    af = []
    if trim:
        af.append(f"atrim=start={trim:.3f}")
        af.append("asetpts=PTS-STARTPTS")
    if preroll:
        af.append(f"adelay={int(preroll * 1000)}|{int(preroll * 1000)}")
    af.append(f"apad,atrim=0:{reel_s:.3f},asetpts=PTS-STARTPTS")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(bed),
                    "-af", ",".join(af), "-c:a", "aac", "-b:a", "192k",
                    "-ar", "48000", "-ac", "2", str(dest)], check=True)
    logger.info("[music] aligned: drop %.2fs -> impact %.2fs (trim %.2fs, pre-roll %.2fs)",
                drop_t, impact_rel, trim, preroll)
    return {"trim_s": round(trim, 3), "preroll_s": round(preroll, 3),
            "drop_at_reel_s": round(impact_rel, 3)}


def beat_times(bed: Path, bpm: float, drop_t: float, reel_s: float,
               offset: float) -> list[float]:
    """Beat grid in REEL time, phase-locked to the drop.

    The drop is a downbeat by construction in this genre, so the grid is
    built outward from it rather than from the file's start -- which is
    where a detector's phase error would otherwise accumulate.
    """
    period = 60.0 / bpm
    out, t = [], offset
    while t > 0:
        t -= period
    t += period
    while t < reel_s:
        out.append(round(t, 4))
        t += period
    return out


def downbeats(beats: list[float], every: int = 4, anchor: float = 0.0) -> list[float]:
    """Every `every`-th beat, counted OUTWARD from the anchor.

    The drop is a downbeat by construction, so the bar line is counted from
    it. Counting from the file's start would put the bar line wherever the
    detector's phase happened to land.
    """
    if not beats:
        return []
    period = beats[1] - beats[0] if len(beats) > 1 else 0.0
    if period <= 0:
        return list(beats)
    bar = period * every
    out, t = [], anchor
    while t > beats[0] - bar:
        t -= bar
    while t <= beats[-1] + 1e-6:
        if t >= beats[0] - 1e-6:
            out.append(round(t, 4))
        t += bar
    return out


def first_downbeat_after(t: float, beats: list[float], anchor: float = 0.0,
                         every: int = 4) -> float:
    """Where the hook slam goes: the first bar line at or after `t`.

    Not the nearest BEAT -- at 150 BPM a bar is 1.6 s, so snapping the hook
    to the nearest beat can put it three beats off the bar line, which reads
    as a stumble rather than an entrance.
    """
    dbs = [d for d in downbeats(beats, every, anchor) if d >= t - 1e-6]
    return dbs[0] if dbs else t


def snap_to_beat(t: float, beats: list[float], max_shift_s: float = 0.20) -> float:
    if not beats:
        return t
    b = min(beats, key=lambda x: abs(x - t))
    return b if abs(b - t) <= max_shift_s else t


# ── §3 the loudness arc has to run the right way ────────────────────────

LOUDEST_TOL_S = 1.0      # how near the impact the loudest second must sit
TARGET_LUFS = (-15.0, -13.0)
MAX_TRUE_PEAK = -1.0


@dataclass(frozen=True)
class MixCheck:
    sub_share: float
    loudest_s: float
    impact_s: float
    lufs: float
    true_peak: float

    @property
    def gates(self) -> dict[str, bool]:
        return {
            "sub_share_25": self.sub_share >= MIN_SUB_SHARE,
            "loudest_is_the_hit": abs(self.loudest_s - self.impact_s) <= LOUDEST_TOL_S,
            "lufs_-14_+-1": TARGET_LUFS[0] <= self.lufs <= TARGET_LUFS[1],
            "true_peak": self.true_peak <= MAX_TRUE_PEAK,
        }

    @property
    def passes(self) -> bool:
        return all(self.gates.values())

    def row(self, label: str) -> str:
        failed = [k for k, v in self.gates.items() if not v]
        return (f"  {label:<20}sub {self.sub_share:4.0%}  loudest {self.loudest_s:5.2f}s "
                f"vs impact {self.impact_s:5.2f}s  {self.lufs:6.1f} LUFS  "
                f"TP {self.true_peak:5.1f}  "
                + ("PASS" if self.passes else "fails: " + ",".join(failed)))


def check_mix(reel: Path, impact_rel: float) -> MixCheck:
    """The reel's own loudness arc, measured on the finished file.

    The gate that matters is `loudest_is_the_hit`. On the v2 reels the
    loudest second sat at 1.3-2.1 s -- the hook slam -- which is the title
    being louder than the punch.

    ALIGNING THE DROP DOES NOT SATISFY THIS GATE. A drop is where the level
    steps UP; the section's own peak arrives later. Measured on the aligned
    beds, the bed's loudest second landed 1.1-3.3 s AFTER the impact, while
    the show's own audio peaked 5.1 dB higher BEFORE it. The impact hit has
    to own the loudest second outright, with the bed and the show ducked
    under it -- the bed's job is the step, not the peak.
    """
    from genlab_core.still.audio import measure_loudness

    lufs, tp = measure_loudness(reel)
    return MixCheck(sub_share=sub_share(reel), loudest_s=loudest_second(reel),
                    impact_s=impact_rel, lufs=lufs, true_peak=tp)


#: MEASURED: `loudest_second` uses a 1 s RMS window, so the sound intended to
#: be the loudest second has to be long enough to fill one. With the impact at
#: 9.20 s in a synthetic mix, a 0.4 s and a 0.8 s hit both left the loudest
#: second at the reel's end; 1.5 s and 2.0 s hits (faded) put it at 9.70 s.
#: The hit and this gate are a matched pair -- shortening one breaks the other.
MIN_IMPACT_HIT_S = 1.5


def snap_cuts(shots: list[dict], bpm: float, impact_source_t: float,
              *, min_shot_s: float = 0.30,
              max_shift_s: float = 0.18) -> tuple[float | None, float]:
    """Snap every cut onto the beat grid; report where the impact LANDS.

    Mutates each shot's ``end`` and sets ``reel_start``. Returns
    ``(impact_rel, total_s)``.

    Three constraints that are easy to lose when this is re-described:

    * the grid is in REEL time and starts at 0. The bed is aligned to the
      result afterwards, so the grid's absolute phase does not matter --
      only that cuts land a whole number of beats apart.
    * a shot is never DROPPED for being short, only nudged. Dropping one
      silently shortens the reel, measured at 2.2 s on Luffy in PEAK-08.
    * the impact's reel time is recomputed AFTER snapping, never assumed
      before it, because snapping moves every boundary and with it the
      impact. Aligning a bed to a pre-snap impact puts the drop off the hit.
    """
    period = 60.0 / bpm
    t = 0.0
    impact_rel: float | None = None
    for sh in shots:
        d = sh["end"] - sh["start"]
        if sh["start"] <= impact_source_t < sh["end"]:
            impact_rel = t + (impact_source_t - sh["start"])
        target = round((t + d) / period) * period
        nd = target - t
        if abs(nd - d) <= max_shift_s and nd >= min_shot_s:
            sh["end"] = round(sh["start"] + nd, 3)
            d = nd
        sh["reel_start"] = round(t, 3)
        t += d
    return impact_rel, t


# ── PEAK-10 §3 — tempo, with the octave actually resolved ───────────────

TEMPO_TOLERANCE = 0.05   # of the asked tempo
WIDE_BAND = (60.0, 200.0)
#: Share of on-beat flux the intermediate beats must carry for the doubled
#: tempo to be the true one. Measured on the four v3 beds: 0.89-1.38, i.e.
#: the intermediate beats are as strong as the others and the tracks are at
#: the asked tempo, not half of it.
OFF_BEAT_RATIO = 0.50


def _flux(path: Path):
    from genlab_core.action.grid import onset_envelope

    return onset_envelope(str(path))


def _beat_score(flux, fps: float, bpm: float) -> tuple[float, float]:
    import numpy as np

    step = (60.0 / bpm) * fps
    best = (-1e9, 0.0)
    for ph in np.arange(0, step, 0.2):
        idx = np.round(np.arange(ph, len(flux) - 1, step)).astype(int)
        s = float(flux[idx].mean())
        if s > best[0]:
            best = (s, float(ph))
    return best


def off_beat_ratio(path: Path, bpm: float) -> float:
    """Strength of the INTERMEDIATE beats relative to the others at `bpm`.

    This is the measurement that resolves the octave, and nothing else in
    this file does. A flux-autocorrelation fit scores the mean onset strength
    at sampled positions, so halving the tempo can SCORE BETTER simply by
    sampling the stronger subset -- which is why a wide band reported 74 BPM
    for four beds that are demonstrably at 148/148/140/150. Widening the
    search band does not resolve an octave; it only moves where the ambiguity
    lands.
    """
    import numpy as np

    flux, fps, _ = _flux(path)
    step = (60.0 / bpm) * fps
    _, ph = _beat_score(flux, fps, bpm)
    idx = np.round(np.arange(ph, len(flux) - 1, step)).astype(int)
    if len(idx) < 4:
        return 0.0
    on = float(flux[idx[0::2]].mean())
    off = float(flux[idx[1::2]].mean())
    return off / on if abs(on) > 1e-9 else 0.0


def detect_tempo(path: Path, expected_bpm: float | None = None) -> float:
    """Tempo with the octave resolved by the intermediate-beat test."""
    from genlab_core.action.grid import detect_grid

    bpm = float(detect_grid(str(path), *WIDE_BAND).bpm)
    # Walk UP while the intermediate beats are carrying real onsets.
    for _ in range(2):
        doubled = bpm * 2
        if doubled > WIDE_BAND[1]:
            break
        if off_beat_ratio(path, doubled) >= OFF_BEAT_RATIO:
            bpm = doubled
        else:
            break
    if expected_bpm:
        logger.info("[music] tempo %.1f vs asked %.1f (%.0f%% off)", bpm, expected_bpm,
                    100 * abs(bpm - expected_bpm) / expected_bpm)
    return bpm


def tempo_ok(measured: float, asked: float, tol: float = TEMPO_TOLERANCE) -> bool:
    return abs(measured - asked) <= tol * asked


# ── PEAK-10 §1 — the window is fixed; the track adapts ──────────────────

MAX_STRETCH = 0.04       # tempo may move this much, and no more


def _atempo_chain(factor: float) -> str:
    """atempo accepts 0.5-2.0 per instance; chain for anything outside."""
    out, f = [], factor
    while f < 0.5:
        out.append("atempo=0.5")
        f /= 0.5
    while f > 2.0:
        out.append("atempo=2.0")
        f /= 2.0
    out.append(f"atempo={f:.6f}")
    return ",".join(out)


def fit_bed(bed: Path, drop_t: float, impact_rel: float, reel_s: float,
            bpm: float, dest: Path, *, bed_s: float | None = None) -> dict:
    """Make the track fit the reel. The WINDOW never moves.

    Three remedies, in the order they cost least:

    1. **stretch** the whole track by at most ±4% so its drop moves toward
       the impact. Cheap and inaudible at this size; it cannot close a large
       gap on its own.
    2. **trim** the intro from its start when the drop still lands late, or
       **pre-roll** when it lands early -- the opening seconds then carry the
       show's own audio alone.
    3. **loop** a bar-aligned section after the drop when the track runs out
       before the reel does. Bar-aligned so the grid survives the splice.

    Shortening the fight to meet the drop is not among them: that is the edit
    inverted, and PEAK-09 did it to Tanjiro before this rule existed.
    """
    from genlab_core.still.reference import duration_s

    bed_s = bed_s if bed_s is not None else duration_s(bed)
    want = impact_rel / drop_t if drop_t > 0 else 1.0
    # A stretch factor < 1 SLOWS the track (its drop arrives later).
    stretch = min(1 + MAX_STRETCH, max(1 - MAX_STRETCH, 1 / want))
    eff_drop = drop_t / stretch
    eff_len = bed_s / stretch

    trim = max(0.0, eff_drop - impact_rel)
    preroll = max(0.0, impact_rel - eff_drop)
    covered = eff_len - trim + preroll

    bar = (60.0 / bpm) * 4
    loops, loop_from, loop_len = 0, 0.0, 0.0
    if covered < reel_s - 0.05:
        # loop a whole number of bars taken from after the drop
        n_bars = max(1, int((eff_len - eff_drop) // bar))
        loop_len = n_bars * bar
        loop_from = eff_drop
        loops = int((reel_s - covered) // loop_len) + 1

    af = [_atempo_chain(stretch)] if abs(stretch - 1.0) > 1e-6 else []
    if trim:
        af += [f"atrim=start={trim:.3f}", "asetpts=PTS-STARTPTS"]
    if preroll:
        af.append(f"adelay={int(preroll * 1000)}|{int(preroll * 1000)}")
    chain = ",".join(af) if af else "anull"

    if loops:
        segs = "".join(
            f"[0:a]{chain},atrim=start={loop_from + trim:.3f}:"
            f"end={loop_from + trim + loop_len:.3f},asetpts=PTS-STARTPTS[l{i}];"
            for i in range(loops))
        ins = "".join(f"[l{i}]" for i in range(loops))
        fc = (f"[0:a]{chain}[base];{segs}[base]{ins}concat=n={loops + 1}:v=0:a=1,"
              f"apad,atrim=0:{reel_s:.3f},asetpts=PTS-STARTPTS[a]")
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(bed), "-filter_complex", fc,
               "-map", "[a]"]
    else:
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(bed), "-af",
               f"{chain},apad,atrim=0:{reel_s:.3f},asetpts=PTS-STARTPTS"]
    subprocess.run(cmd + ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                          str(dest)], check=True)

    info = {"stretch": round(stretch, 4), "stretch_pct": round((stretch - 1) * 100, 2),
            "trim_s": round(trim, 3), "preroll_s": round(preroll, 3),
            "loops": loops, "loop_bars": round(loop_len / bar) if loops else 0,
            "drop_at_reel_s": round(impact_rel, 3),
            "effective_drop_s": round(eff_drop, 3)}
    logger.info("[music] fit: stretch %+.1f%%, trim %.2fs, pre-roll %.2fs, %d loop(s)",
                info["stretch_pct"], trim, preroll, loops)
    return info


# ── PEAK-10 §4 — style match, not a copy ────────────────────────────────

CHROMA_HOP = 4096

#: MEASURED 2026-09-25 on material already in hand: five pairs of unrelated
#: generated tracks (different registers, different generations, and two
#: against the earlier orchestral beds) scored 0.281-0.411, and a track
#: against ITSELF scored 1.000. 0.55 sits clear of both. A reference-
#: conditioned generation scoring above this is reproducing the tune, not the
#: style, and is rejected.
MAX_MELODY_SIMILARITY = 0.55


def chroma(path: Path):
    """Mean-normalised 12-bin pitch-class profile over time."""
    import numpy as np

    x = _decode(path).astype(np.float64)
    n = 8192
    if len(x) < n:
        return np.zeros((12, 0))
    freqs = np.fft.rfftfreq(n, 1 / SR)
    with np.errstate(divide="ignore", invalid="ignore"):
        midi = 69 + 12 * np.log2(np.maximum(freqs, 1e-9) / 440.0)
    pc = np.mod(np.round(midi).astype(int), 12)
    usable = (freqs > 55) & (freqs < 4000)
    cols = []
    for i in range(0, len(x) - n, CHROMA_HOP):
        spec = np.abs(np.fft.rfft(x[i:i + n] * np.hanning(n)))
        v = np.zeros(12)
        for k in range(12):
            m = usable & (pc == k)
            v[k] = spec[m].sum()
        s = v.sum()
        cols.append(v / s if s > 0 else v)
    return np.array(cols).T if cols else np.zeros((12, 0))


def chroma_similarity(a: Path, b: Path) -> float:
    """Melodic/harmonic similarity in [0, 1], time-alignment invariant.

    Cross-correlates the two chroma sequences over lag and pitch rotation and
    keeps the best match, so a transposed or offset copy still scores high.
    A STYLE match shares tempo, drums and texture but not the tune, and
    should sit near the unrelated-track floor.
    """
    import numpy as np

    ca, cb = chroma(a), chroma(b)
    if ca.size == 0 or cb.size == 0:
        return 0.0
    n = min(ca.shape[1], cb.shape[1])
    ca, cb = ca[:, :n], cb[:, :n]
    best = 0.0
    for rot in range(12):
        bb = np.roll(cb, rot, axis=0)
        for lag in range(0, max(1, n // 2), max(1, n // 40)):
            x = ca[:, lag:].ravel()
            y = bb[:, :n - lag].ravel() if lag else bb.ravel()
            m = min(len(x), len(y))
            if m < 24:
                continue
            x, y = x[:m], y[:m]
            xs, ys = x - x.mean(), y - y.mean()
            d = float(np.linalg.norm(xs) * np.linalg.norm(ys))
            if d > 0:
                best = max(best, float(np.dot(xs, ys) / d))
    return max(0.0, best)
