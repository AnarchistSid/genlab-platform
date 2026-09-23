"""The bed, the hits, and where they sit.

ANIME-14 §5. The bed is chosen FIRST, because everything else is locked to
its grid: cuts land on beats, the fact slams land on downbeats, and the hits
land on the same frames as the cuts. A bed added last is decoration; a bed
added first is the timing.

Levels are stated once, here:

* narration is the reference, normalised to the kit's target LUFS
* the bed sits 6-10 dB under it (kit ``music_duck_db``)
* every effect sits 18 dB under the narration — audible as impact, never as
  a competing sound
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_UA = {"User-Agent": "GenLab/1.0 (+https://github.com/AnarchistSid/genlab-platform)"}

#: Effects sit this far under the narration.
SFX_DB = -18.0

#: The four cues the packet names, with the prompt each is generated from.
CUE_PROMPTS = {
    "riser": "tense cinematic riser building to an impact, no music, dry",
    "hit": "deep cinematic impact hit, short, punchy, no tail",
    "sub_drop": "deep sub bass drop, short, no melody",
    "whoosh": "fast whoosh transition, short, airy",
}


@dataclass(frozen=True)
class SoundCue:
    kind: str
    at_s: float
    path: Path
    gain_db: float = SFX_DB

    def row(self) -> str:
        return f"  {self.kind:<9} @{self.at_s:6.2f}s  {self.gain_db:+.0f} dB"


def _belt(ref: str, payload: dict, timeout: int = 600) -> str:
    p = subprocess.run(
        [
            "belt",
            "app",
            "run",
            ref,
            "--no-input",
            "--json",
            "--no-wait",
            "--input",
            json.dumps(payload),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    tid = json.loads(p.stdout or "{}").get("id")
    if not tid:
        raise RuntimeError(f"{ref}: no task id — {(p.stdout or p.stderr)[:160]}")
    end = time.monotonic() + timeout
    d: dict = {}
    while time.monotonic() < end:
        d = json.loads(
            subprocess.run(
                ["belt", "task", "get", str(tid), "--json"],
                capture_output=True,
                text=True,
                timeout=120,
            ).stdout
            or "{}"
        )
        if d.get("status_text") in ("completed", "failed", "cancelled"):
            break
        time.sleep(3)
    if d.get("status_text") != "completed":
        raise RuntimeError(f"{ref} task {tid}: {d.get('status_text')}")
    return next(
        v for v in (d.get("output") or {}).values() if isinstance(v, str) and v.startswith("http")
    )


def _fetch(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size:
        return dest
    with urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=180) as r:
        dest.write_bytes(r.read())
    return dest


def generate_bed(prompt: str, duration_s: float, dest: Path) -> Path:
    """The music bed. Generated first so its grid can drive the edit."""
    if dest.exists() and dest.stat().st_size:
        logger.info("[sound] cached bed %s", dest.name)
        return dest
    # The app's own schema: duration_seconds + prompt. Checked with
    # `belt app sample elevenlabs/music` rather than assumed — an earlier
    # draft passed music_length_ms, which the app does not have.
    url = _belt("elevenlabs/music", {"prompt": prompt, "duration_seconds": int(duration_s)})
    return _fetch(url, dest)


def generate_sfx(kind: str, dest: Path, *, duration_s: float = 2.0) -> Path:
    if dest.exists() and dest.stat().st_size:
        return dest
    prompt = CUE_PROMPTS.get(kind)
    if not prompt:
        raise ValueError(f"unknown cue {kind!r}; known: {sorted(CUE_PROMPTS)}")
    url = _belt("elevenlabs/sound-effects", {"text": prompt, "duration_seconds": duration_s})
    return _fetch(url, dest)


def snap(t: float, beats: list[float], *, max_shift_s: float = 0.25) -> float:
    """Nearest beat, if one is close enough; otherwise ``t`` unchanged.

    Returning ``t`` rather than the nearest beat at any distance is the point:
    snapping a cut 0.9 s to reach a beat does not make it on-beat, it makes it
    a different cut.
    """
    if not beats:
        return t
    nearest = min(beats, key=lambda b: abs(b - t))
    return nearest if abs(nearest - t) <= max_shift_s else t


def plan_cues(
    *,
    hook_flash_s: float,
    date_slam_s: float | None,
    character_reveal_s: float | None,
    card_s: float,
    beats: list[float],
    sfx_dir: Path,
) -> list[SoundCue]:
    """The four cues the packet names, each snapped to the grid.

    A cue whose anchor does not exist in this reel is SKIPPED, not moved to a
    plausible time — a whoosh on no reveal is a noise with no referent.
    """
    cues: list[SoundCue] = []

    def add(kind: str, at: float | None, *, dur: float = 2.0) -> None:
        if at is None:
            logger.info("[sound] no anchor for %s in this reel — skipped", kind)
            return
        cues.append(
            SoundCue(
                kind,
                snap(at, beats),
                generate_sfx(kind, sfx_dir / f"{kind}.mp3", duration_s=dur),
                SFX_DB,
            )
        )

    add("sub_drop", hook_flash_s, dur=1.5)
    if date_slam_s is not None:
        add("riser", max(0.0, date_slam_s - 1.2), dur=1.5)
        add("hit", date_slam_s, dur=1.0)
    add("whoosh", character_reveal_s, dur=1.0)
    for c in cues:
        logger.info("%s", c.row())
    return cues


def build_mix(
    *,
    narration: Path,
    bed: Path | None,
    cues: list[SoundCue],
    out: Path,
    video_s: float,
    target_lufs: float,
    true_peak: float,
    duck_db: float,
    timeout_s: int = 600,
) -> bool:
    """Narration over a ducked bed, with the effects on the grid.

    One filter_complex rather than successive passes: each re-encode of an AAC
    intermediate costs quality, and the levels are easier to reason about when
    they are all stated in one graph.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    inputs = ["-i", str(narration)]
    parts = []
    mix_labels = ["[vo]"]
    parts.append(f"[0:a]apad,atrim=0:{video_s:.3f},asetpts=PTS-STARTPTS[vo]")

    idx = 1
    if bed is not None:
        inputs += ["-stream_loop", "-1", "-i", str(bed)]
        parts.append(f"[{idx}:a]volume={duck_db}dB,atrim=0:{video_s:.3f},asetpts=PTS-STARTPTS[bed]")
        mix_labels.append("[bed]")
        idx += 1

    for n, c in enumerate(cues):
        inputs += ["-i", str(c.path)]
        parts.append(
            f"[{idx}:a]volume={c.gain_db}dB,adelay={int(c.at_s * 1000)}|{int(c.at_s * 1000)},"
            f"apad,atrim=0:{video_s:.3f},asetpts=PTS-STARTPTS[fx{n}]"
        )
        mix_labels.append(f"[fx{n}]")
        idx += 1

    parts.append(
        "".join(mix_labels) + f"amix=inputs={len(mix_labels)}:duration=first:normalize=0,"
        f"loudnorm=I={target_lufs}:TP={true_peak}:LRA=11,"
        f"apad,atrim=0:{video_s:.3f}[a]"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        *inputs,
        "-filter_complex",
        ";".join(parts),
        "-map",
        "[a]",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-ac",
        "2",
        str(out),
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    if p.returncode != 0:
        logger.warning("[sound] mix failed: %s", p.stderr[-400:])
        return False

    # Second loudnorm pass. Single-pass is a live estimator and lands within
    # ~2 LU, outside a +/-1 gate.
    from genlab_core.still.audio import measure_loudness

    i, tp = measure_loudness(out)
    if abs(i - target_lufs) <= 0.5:
        return True
    tmp = out.with_suffix(".p2" + out.suffix)
    p2 = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(out),
            "-af",
            f"loudnorm=I={target_lufs}:TP={true_peak}:LRA=11:measured_I={i}:"
            f"measured_TP={tp}:measured_LRA=11:measured_thresh={i - 10:.2f}:linear=true,"
            f"apad,atrim=0:{video_s:.3f}",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            str(tmp),
        ],
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    if p2.returncode == 0 and tmp.exists():
        tmp.replace(out)
    else:
        logger.warning("[sound] loudnorm pass 2 failed, keeping pass 1")
    return True
