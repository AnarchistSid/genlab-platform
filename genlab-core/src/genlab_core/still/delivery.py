"""Delivery marks: the script carries the performance.

ANIME-13 §4. "A voice with no delivery marks is monotone by construction" —
a TTS given flat prose has nothing to vary, so it varies nothing. The script
therefore carries the performance in ONE markup, and two renderers consume it:
one for the voice, one for the screen.

    {excited} Satoko has everything except *time* -- her illness gives her
    mere months.

    to_speech  -> "[excited] Satoko has everything except TIME... her illness
                   gives her mere months."
    to_caption -> "Satoko has everything except time - her illness gives her
                   mere months."

Keeping one marked source and deriving both is what stops the two drifting.
The alternative — a spoken string and a caption string maintained side by
side — is the shape where a late edit lands in one and not the other.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

#: {emotion} at the head of a sentence.
_EMOTION = re.compile(r"\{([a-z_]+)\}\s*")
#: *stressed word or phrase*
_STRESS = re.compile(r"\*([^*]+)\*")
#: ` -- ` : a beat of silence before a turn.
_PAUSE = re.compile(r"\s*--\s*")

#: Emotions this kit uses, mapped to ElevenLabs v3 audio tags. A tag outside
#: this set is DROPPED rather than passed through: v3 renders an unrecognised
#: bracket as spoken text, so "[dramatic]" would be read aloud.
_TAGS = {
    "excited": "[excited]",
    "curious": "[curious]",
    "serious": "[serious]",
    "sad": "[sad]",
    "whisper": "[whispers]",
    "laughs": "[laughs]",
    "sarcastic": "[sarcastic]",
}


class DeliveryError(ValueError):
    """The script's marks are malformed."""


def validate(text: str) -> None:
    """Refuse marks a renderer cannot honour, rather than silently dropping."""
    if text.count("*") % 2:
        raise DeliveryError(f"unbalanced * in: {text[:80]!r}")
    for name in _EMOTION.findall(text):
        if name not in _TAGS:
            raise DeliveryError(
                f"unknown emotion {{{name}}} — v3 speaks an unrecognised tag aloud. "
                f"Known: {sorted(_TAGS)}"
            )


def to_speech(text: str, *, engine: str = "elevenlabs") -> str:
    """The string handed to the TTS.

    ElevenLabs v3 takes audio tags and responds to capitalisation for stress
    and to an ellipsis for a beat. Inworld takes neither — it has no tag
    vocabulary — so for that engine the emotion is dropped and only the
    punctuation-level marks survive. That is the honest difference between
    the two engines and the reason the packet names ElevenLabs first.
    """
    validate(text)
    out = text
    if engine == "elevenlabs":
        out = _EMOTION.sub(lambda m: _TAGS[m.group(1)] + " ", out)
        out = _STRESS.sub(lambda m: m.group(1).upper(), out)
    else:
        out = _EMOTION.sub("", out)
        # Without a tag vocabulary, capitals are read as an acronym by some
        # engines. Leave the word alone and keep only the pause.
        out = _STRESS.sub(lambda m: m.group(1), out)
    out = _PAUSE.sub("... ", out)
    return re.sub(r"\s+", " ", out).strip()


def to_caption(text: str) -> str:
    """The string the captions are built from. Marks removed, words intact."""
    validate(text)
    out = _EMOTION.sub("", text)
    out = _STRESS.sub(lambda m: m.group(1), out)
    out = _PAUSE.sub(" - ", out)
    return re.sub(r"\s+", " ", out).strip()


def words(text: str) -> list[str]:
    """The caption's words, in order. What the burned captions must say."""
    return to_caption(text).split()


def is_marked(text: str) -> bool:
    """True if this line carries any delivery mark at all."""
    return bool(_EMOTION.search(text) or _STRESS.search(text) or _PAUSE.search(text))


def coverage(beats: list[str]) -> float:
    """Fraction of beats carrying at least one mark.

    A script where this is 0 will be read flat no matter which engine renders
    it, and that is a script problem rather than a voice problem.
    """
    return sum(1 for b in beats if is_marked(b)) / len(beats) if beats else 0.0
