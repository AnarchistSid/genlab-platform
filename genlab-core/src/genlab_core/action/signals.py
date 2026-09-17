"""`speech_fn` and `face_fn` -- the other two signals the classifier needs.

WHY THIS FILE EXISTS
--------------------
`classifier.measure()` takes all three signals INJECTED, and none of the three
had an implementation anywhere in the tree. RENDER-01 Part 4 asked for a
canonical `motion_fn` (now in `motion.py`); the corpus gate it unblocks needs
`speech_fn` and `face_fn` as well, or a clip cannot be classified at all.

So each threshold in `classifier.py` sat in an undefined unit, not just the
motion one. The units are stated here, in the functions that produce them.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# ── speech ──────────────────────────────────────────────────────────────────

SPEECH_LO_HZ, SPEECH_HI_HZ = 300.0, 3400.0  # the telephony band; where speech lives
SPEECH_SR = 16000
SPEECH_WIN = 0.050  # 50 ms, ~a phoneme
SPEECH_DOMINANCE = 0.45  # share of band energy that counts as speech
SILENCE_FLOOR_DB = -45.0  # below this a window is silence, not speech
MAX_SAMPLE_SECONDS = 20.0


def speech_ratio(path: str) -> float:
    """Share of the clip where speech-band energy DOMINATES.

    Unit: a fraction in [0, 1] of non-silent 50 ms windows whose 300-3400 Hz
    energy is at least `SPEECH_DOMINANCE` of total spectral energy.

    Two details carry the measurement. Silent windows are excluded rather than
    counted as non-speech, or a clip with a long quiet opening scores low for
    being quiet instead of for lacking speech. And it is a DOMINANCE ratio, not
    absolute band energy -- crowd noise and music both put energy in the speech
    band, so only the share distinguishes a voice from a stadium.
    """
    raw = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-v",
            "error",
            "-i",
            path,
            "-t",
            str(MAX_SAMPLE_SECONDS),
            "-ac",
            "1",
            "-ar",
            str(SPEECH_SR),
            "-f",
            "s16le",
            "-",
        ],
        capture_output=True,
    ).stdout
    a = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    n = int(SPEECH_SR * SPEECH_WIN)
    if len(a) < n * 4:
        logger.warning("speech_ratio: %s has no usable audio (%d samples)", path, len(a))
        return 0.0

    frames = a[: len(a) // n * n].reshape(-1, n)
    win = np.hanning(n).astype(np.float32)
    spec = np.abs(np.fft.rfft(frames * win, axis=1)) ** 2
    freqs = np.fft.rfftfreq(n, 1.0 / SPEECH_SR)
    band = (freqs >= SPEECH_LO_HZ) & (freqs <= SPEECH_HI_HZ)

    total = spec.sum(axis=1) + 1e-12
    rms_db = 20 * np.log10(np.sqrt((frames**2).mean(axis=1)) + 1e-12)
    voiced = rms_db > SILENCE_FLOOR_DB
    if not voiced.any():
        return 0.0
    return float(((spec[:, band].sum(axis=1) / total)[voiced] >= SPEECH_DOMINANCE).mean())


# ── faces ───────────────────────────────────────────────────────────────────

_MODEL = Path(__file__).resolve().parents[3] / "models" / "yunet.onnx"
FACE_SAMPLES = 16
FACE_CONF = 0.60


def _detector(w: int, h: int):
    import cv2

    if not _MODEL.exists():
        raise FileNotFoundError(f"YuNet model not at {_MODEL}")
    return cv2.FaceDetectorYN.create(str(_MODEL), "", (w, h), FACE_CONF, 0.3, 5000)


def face_persistence(path: str, samples: int = FACE_SAMPLES) -> float:
    """Share of sampled frames with a face, weighted by how STEADY it is.

    Unit: a fraction in [0, 1]. `detection_rate * steadiness`, where steadiness
    is `1 - (spread of face centres / frame diagonal)`, clamped at 0.

    The weighting is the whole point. A crowd shot has faces in most frames and
    a talking head has THE SAME face in THE SAME PLACE -- detection rate alone
    scores them identically, and that is precisely the pair the TALK gate has to
    separate. Position spread is what distinguishes them without needing
    identity.
    """
    import cv2

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        logger.warning("face_persistence: cannot open %s", path)
        return 0.0
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 0
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 0
        if total < 2 or not w or not h:
            return 0.0
        det = _detector(w, h)
        idx = np.linspace(0, total - 1, min(samples, total)).astype(int)
        centres, hits = [], 0
        for i in idx:
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
            ok, frame = cap.read()
            if not ok:
                continue
            _, faces = det.detect(frame)
            if faces is None or not len(faces):
                continue
            hits += 1
            x, y, fw, fh = faces[0][:4]  # the most confident detection
            centres.append((x + fw / 2, y + fh / 2))
        if not centres:
            return 0.0
        rate = hits / len(idx)
        pts = np.asarray(centres, np.float32)
        spread = float(np.hypot(*(pts.max(axis=0) - pts.min(axis=0))))
        steadiness = max(0.0, 1.0 - spread / float(np.hypot(w, h)))
        return round(rate * steadiness, 4)
    finally:
        cap.release()


def speech_fn(path: str) -> float:
    return speech_ratio(path)


def face_fn(path: str) -> float:
    return face_persistence(path)
