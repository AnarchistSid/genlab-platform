"""The STILL path's audio: narration, alignment, captions, bed, loudness.

Order matters and is not negotiable: the voice is synthesised first, the
word timings come from the RENDERED voice rather than from a prediction, and
everything else is placed against those timings. Captions written against
predicted timings drift the moment the TTS delivers at a different rate than
the config claims -- which is exactly what the measured-vs-recorded wpm gap
turned out to be (recorded 141, measured ~162 at rate 1.0).
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from genlab_core.capabilities import select
from genlab_core.llm.output_integrity import artifacts

logger = logging.getLogger(__name__)

#: rule #25 — a WAF fingerprints the default Python-urllib UA and returns 403.
_UA = {"User-Agent": "GenLab/1.0 (+https://github.com/AnarchistSid/genlab-platform)"}

_LUFS_TOLERANCE = 1.0


class NarrationRejected(RuntimeError):
    """The script must not be spoken."""


@dataclass(frozen=True)
class Narration:
    path: Path
    duration_s: float
    words: int
    measured_wpm: float
    cost_usd: float
    ref: str


@dataclass(frozen=True)
class Word:
    text: str
    start_s: float
    end_s: float


def sanitize_for_speech(script: str) -> str:
    """What the voice actually reads.

    Hashtags, URLs and emoji are caption furniture. A narrator reading
    "hashtag anime" aloud is the failure this prevents, and it is why the
    pipeline asserts ``spoken_text == sanitize_for_speech(script)`` rather
    than trusting the two to match.
    """
    import re

    out = re.sub(r"https?://\S+", "", script)
    out = re.sub(r"#\w+", "", out)
    out = re.sub(r"[\U0001F300-\U0001FAFF☀-➿]", "", out)
    return re.sub(r"\s+", " ", out).strip()


def _duration(path: Path) -> float:
    return float(
        subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )


def synthesize(
    script: str,
    out_path: Path,
    *,
    narration: dict[str, Any],
    context: str = "fire",
    timeout_s: int = 300,
) -> Narration:
    """Speak the script. Refuses a script the corruption gate rejects."""
    text = sanitize_for_speech(script)
    if not text:
        raise NarrationRejected("nothing to speak after sanitising")
    bad = artifacts(text)
    if bad:
        raise NarrationRejected(
            f"llm_output_corrupt:narration — splice artifacts {bad[:6]}; "
            "a garbled script must never reach a voice"
        )

    cap = select("tts", context=context)
    payload = {
        "text": text,
        "voice_id": narration.get("voice_id") or "Sarah",
        "audio_encoding": "MP3",
        "sample_rate_hertz": 44100,
        "speaking_rate": float(narration.get("speaking_rate", 1.0)),
    }
    sub = subprocess.run(
        [
            "belt",
            "app",
            "run",
            cap.ref,
            "--no-input",
            "--json",
            "--no-wait",
            "--input",
            json.dumps(payload),
        ],
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    task_id = json.loads(sub.stdout or "{}").get("id")
    if not task_id:
        raise NarrationRejected(f"tts submit returned no id: {sub.stdout[:200]}")

    deadline = time.monotonic() + timeout_s
    result: dict[str, Any] = {}
    while time.monotonic() < deadline:
        got = subprocess.run(
            ["belt", "task", "get", str(task_id), "--json"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        try:
            result = json.loads(got.stdout or "{}")
        except ValueError:
            result = {}
        if result.get("status_text") in ("completed", "failed", "cancelled"):
            break
        time.sleep(2)
    if result.get("status_text") != "completed":
        raise NarrationRejected(f"tts task {task_id} ended {result.get('status_text')!r}")

    out = result.get("output") or {}
    url = next((v for v in out.values() if isinstance(v, str) and v.startswith("http")), None)
    if not url:
        raise NarrationRejected(f"tts completed with no audio; keys {sorted(out)}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=120) as resp, open(out_path, "wb") as fh:
        fh.write(resp.read())

    dur = _duration(out_path)
    n_words = len(text.split())
    measured = n_words / dur * 60.0 if dur else 0.0
    logger.info(
        "[still] narration %.2fs, %d words, MEASURED %.1f wpm (config predicts %s)",
        dur,
        n_words,
        measured,
        narration.get("wpm"),
    )
    return Narration(
        path=out_path,
        duration_s=dur,
        words=n_words,
        measured_wpm=measured,
        cost_usd=cap.cost_per_unit_usd or 0.0,
        ref=cap.ref,
    )


def align(audio: Path, *, model: str = "base") -> list[Word]:
    """Word timings from the RENDERED voice, not from a prediction."""
    from faster_whisper import WhisperModel

    whisper = WhisperModel(model, device="cpu", compute_type="int8")
    segments, _info = whisper.transcribe(str(audio), word_timestamps=True)
    words: list[Word] = []
    for seg in segments:
        for w in seg.words or []:
            words.append(Word(text=w.word.strip(), start_s=float(w.start), end_s=float(w.end)))
    logger.info("[still] aligned %d words from %s", len(words), audio.name)
    return words


def measure_loudness(path: Path) -> tuple[float, float]:
    """(integrated LUFS, true peak dBTP) via ffmpeg's loudnorm analysis."""
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-af",
            "loudnorm=I=-14:TP=-1:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    blob = proc.stderr[proc.stderr.rfind("{") : proc.stderr.rfind("}") + 1]
    data = json.loads(blob)
    return float(data["input_i"]), float(data["input_tp"])
