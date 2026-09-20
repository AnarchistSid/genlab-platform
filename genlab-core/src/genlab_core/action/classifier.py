"""TALK / ACTION / STILL — which treatment a clip can carry (TREAT-01).

The three treatments want opposite things from a clip, so sending one down the
wrong path wastes the whole render:

  TALK    a person speaking to camera. Wants faces held across the clip and
          audible speech; the reel is speech-led and everything ducks to it.
  ACTION  a physical moment. Wants motion and a trackable subject; the reel is
          music-led, cut to a beat grid.
  STILL   no usable footage, or footage too static to cut. The caption engine
          and motion-on-stills carry it.

Signals, all measured from the clip rather than taken from metadata -- a title
saying "INTERVIEW" is not evidence about the pixels:

  speech_ratio      share of the clip where speech-band energy dominates
  motion_energy     mean frame-to-frame luma difference
  face_persistence  share of sampled frames with a face, weighted by how
                    steady its position is (a crowd shot has faces; a talking
                    head has the SAME face in the SAME place)

Ordering matters. STILL is decided first because "there is nothing to cut" is a
fact about the footage that no amount of speech or faces can overcome; TALK
before ACTION because a press conference has real motion in it (hands, crowd)
and would otherwise be treated as a fight.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Thresholds. Every one is a measurement from the retained corpus; see
# docs/treat-01-corpus.md for the clip each came from.
STILL_MOTION_MAX = 2.0  # below this there is nothing to cut to
TALK_SPEECH_MIN = 0.45  # sustained speech, not a shout over crowd noise
TALK_FACE_MIN = 0.55  # the same face, held
ACTION_MOTION_MIN = 6.0  # a physical moment, not a lectern
ACTION_FACE_MAX = 0.70  # a face held THIS steadily is an interview


@dataclass(frozen=True)
class Signals:
    speech_ratio: float
    motion_energy: float
    face_persistence: float


@dataclass(frozen=True)
class Verdict:
    treatment: str
    confidence: float
    reason: str
    signals: Signals


def classify(sig: Signals) -> Verdict:
    """Decide the treatment. Deterministic, and it explains itself."""
    if sig.motion_energy < STILL_MOTION_MAX:
        return Verdict(
            "STILL",
            _conf(STILL_MOTION_MAX - sig.motion_energy, 2.0),
            f"motion {sig.motion_energy:.2f} < {STILL_MOTION_MAX} — nothing to cut to",
            sig,
        )

    if sig.speech_ratio >= TALK_SPEECH_MIN and sig.face_persistence >= TALK_FACE_MIN:
        return Verdict(
            "TALK",
            _conf(
                min(sig.speech_ratio - TALK_SPEECH_MIN, sig.face_persistence - TALK_FACE_MIN), 0.3
            ),
            f"speech {sig.speech_ratio:.2f} and a held face {sig.face_persistence:.2f}",
            sig,
        )

    if sig.motion_energy >= ACTION_MOTION_MIN and sig.face_persistence <= ACTION_FACE_MAX:
        return Verdict(
            "ACTION",
            _conf(sig.motion_energy - ACTION_MOTION_MIN, 8.0),
            f"motion {sig.motion_energy:.2f} with no held face ({sig.face_persistence:.2f})",
            sig,
        )

    # Between the two: enough motion to not be STILL, not enough of either
    # signature to commit. STILL is the safe landing -- its treatment works on
    # any footage, where ACTION on a lectern produces a fight that isn't there.
    return Verdict(
        "STILL",
        0.35,
        f"ambiguous: motion {sig.motion_energy:.2f}, speech "
        f"{sig.speech_ratio:.2f}, face {sig.face_persistence:.2f}",
        sig,
    )


def _conf(margin: float, scale: float) -> float:
    return round(min(1.0, 0.5 + 0.5 * max(margin, 0.0) / scale), 3)


def measure(
    path: str,
    *,
    speech_fn: Callable[[str], float],
    motion_fn: Callable[[str], float],
    face_fn: Callable[[str], float],
) -> Signals:
    """Signal extraction, injected so this module stays free of ffmpeg and cv2."""
    return Signals(
        speech_ratio=float(speech_fn(path)),
        motion_energy=float(motion_fn(path)),
        face_persistence=float(face_fn(path)),
    )


def classify_clip(path: str, **fns) -> Verdict:
    v = classify(measure(path, **fns))
    logger.info("[classifier] %s -> %s (%.2f): %s", path, v.treatment, v.confidence, v.reason)
    return v
