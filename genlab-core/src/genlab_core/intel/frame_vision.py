"""ANIME-PEAK-12 §1 — ask something that can SEE what is on screen.

Eleven packets of heuristics failed at one layer. Luma, motion energy, onset
density, colour-seed area and matte fraction were all proxies for questions
they cannot answer: who is this, where is their face, is this the moment.
Each proxy was measured, each was rejected against a hand-labelled set, and
the next one was a different proxy for the same unanswerable question.

This asks a model that can look at the frame.

The contract is deliberately narrow. The model returns STRUCTURED JSON per
frame and nothing else; a frame whose JSON fails validation is retried once
and then recorded as `unknown` rather than guessed. `unknown` is a value the
shot planner can see and a person can review -- silently defaulting it would
put us back where the proxies were.

`response_format` is NOT used: it is an OpenAI-ism that has broken every
belt json_mode caller in this project. The schema is stated in the prompt and
the response is parsed defensively.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

VISION_APP = "anthropic/claude-haiku-4-5"  # overridable per call
FRAME_W = 512
SAMPLE_FPS = 2.0
BATCH = 6                    # frames per call
SHOT_TYPES = ("windup", "clash", "impact", "reaction", "debris_or_effect", "other")

_SYSTEM = (
    "You label frames from an anime fight for a video editor. You answer with "
    "JSON only -- no prose, no code fence. You are precise about WHICH "
    "character is visible and you never guess a character who is not there."
)

_SCHEMA = """Return a JSON array with one object per image, in order:
[{"i": <index as given>,
  "characters": [<names present, from the allowed list; [] if none>],
  "faces": [{"name": <name>, "box": [x0,y0,x1,y1]}],   // 0-1 fractions of the frame
  "shot_type": one of ["windup","clash","impact","reaction","debris_or_effect","other"],
  "motion_blurred": true|false,
  "text_or_watermark": [{"what": <"watermark"|"subtitle"|"credit">, "box": [x0,y0,x1,y1]}]
}]
shot_type meanings: windup = a fighter gathering/preparing; clash = fighters
engaged or a strike in flight; impact = the moment of contact or its flash;
reaction = after the hit, a fighter recoiling/falling/looking; debris_or_effect
= the frame is mostly effect, dust or rubble with no readable character;
other = anything else, including establishing shots and end cards."""


@dataclass
class FrameRecord:
    i: int
    t: float
    characters: list[str] = field(default_factory=list)
    faces: list[dict] = field(default_factory=list)
    shot_type: str = "unknown"
    motion_blurred: bool = False
    text_or_watermark: list[dict] = field(default_factory=list)
    ok: bool = True

    @property
    def has_character(self) -> bool:
        return bool(self.characters)


def sample_frames(path: Path, start: float, end: float, dest: Path,
                  fps: float = SAMPLE_FPS, width: int = FRAME_W) -> list[tuple[int, float, Path]]:
    dest.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start}", "-t", f"{end - start}",
                    "-i", str(path), "-vf", f"fps={fps},scale={width}:-2",
                    "-y", str(dest / "f%04d.png")], check=True)
    out = []
    for n, p in enumerate(sorted(dest.glob("f*.png"))):
        out.append((n, round(start + n / fps, 3), p))
    return out


def _belt_vision(images: list[Path], text: str, timeout: int = 600) -> str:
    payload = {"system_prompt": _SYSTEM, "text": text,
               "images": [str(p) for p in images], "temperature": 0.0,
               "max_tokens": 4000, "stream": False}
    p = subprocess.run(["belt", "app", "run", VISION_APP, "--no-input", "--json",
                        "--no-wait", "--input", json.dumps(payload)],
                       capture_output=True, text=True, timeout=300)
    tid = json.loads(p.stdout or "{}").get("id")
    if not tid:
        raise RuntimeError((p.stdout or p.stderr)[:200])
    end = time.monotonic() + timeout
    d = {}
    while time.monotonic() < end:
        d = json.loads(subprocess.run(["belt", "task", "get", str(tid), "--json"],
                                      capture_output=True, text=True,
                                      timeout=120).stdout or "{}")
        if d.get("status_text") in ("completed", "failed", "cancelled"):
            break
        time.sleep(3)
    if d.get("status_text") != "completed":
        raise RuntimeError(f"{tid}: {d.get('status_text')} {str(d.get('error'))[:160]}")
    out = d.get("output") or {}
    for k in ("text", "response", "content", "message"):
        if isinstance(out.get(k), str) and out[k].strip():
            return out[k]
    return json.dumps(out)


def _parse(blob: str) -> list[dict]:
    """Pull the JSON array out of whatever came back."""
    m = re.search(r"\[.*\]", blob, re.S)
    if not m:
        raise ValueError(f"no JSON array in response: {blob[:160]!r}")
    return json.loads(m.group(0))


def _validate(rec: dict, allowed: tuple[str, ...]) -> bool:
    if not isinstance(rec, dict):
        return False
    if rec.get("shot_type") not in SHOT_TYPES:
        return False
    chars = rec.get("characters")
    if not isinstance(chars, list) or any(c not in allowed for c in chars):
        return False
    return True


def label_frames(frames: list[tuple[int, float, Path]], characters: tuple[str, ...],
                 *, batch: int = BATCH) -> list[FrameRecord]:
    """One call per batch of adjacent frames; a bad record is retried once."""
    allowed = tuple(characters)
    # Built by concatenation, not str.format: _SCHEMA is full of JSON braces
    # and format() reads them as fields.
    head = f"Allowed character names: {list(allowed)}.\n{_SCHEMA}\n"
    out: list[FrameRecord] = []
    for k in range(0, len(frames), batch):
        chunk = frames[k:k + batch]
        idx = [c[0] for c in chunk]
        text = head + f"There are {len(chunk)} images, with indices {idx} in order."
        recs = {}
        for attempt in (1, 2):
            try:
                parsed = _parse(_belt_vision([c[2] for c in chunk], text))
                recs = {r.get("i"): r for r in parsed if _validate(r, allowed)}
                if len(recs) == len(chunk):
                    break
            except Exception as e:  # noqa: BLE001
                logger.warning("[vision] batch %s attempt %s failed: %s", idx, attempt, e)
        for i, t, _ in chunk:
            r = recs.get(i)
            if r is None:
                out.append(FrameRecord(i=i, t=t, shot_type="unknown", ok=False))
                continue
            out.append(FrameRecord(
                i=i, t=t, characters=list(r.get("characters") or []),
                faces=list(r.get("faces") or []), shot_type=r["shot_type"],
                motion_blurred=bool(r.get("motion_blurred")),
                text_or_watermark=list(r.get("text_or_watermark") or [])))
        logger.info("[vision] batch %s: %d/%d labelled", idx, len(recs), len(chunk))
    return out


def write_frames_json(records: list[FrameRecord], dest: Path) -> dict:
    payload = {"app": VISION_APP, "sample_fps": SAMPLE_FPS, "frame_width": FRAME_W,
               "n": len(records), "unknown": sum(1 for r in records if not r.ok),
               "frames": [asdict(r) for r in records]}
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=1))
    return payload
