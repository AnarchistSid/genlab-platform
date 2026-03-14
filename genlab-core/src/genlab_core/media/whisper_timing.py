"""genlab_core.media.whisper_timing — Word-level timestamps from faster-whisper.

Three public functions:
  transcribe_words(audio_path)  — run faster-whisper, return word dicts
  align_words(text, whisper_words) — two-pointer alignment with interpolation
  _flatten_whisper_words(segments) — convert CriticalRush segment format

Optional dependency: faster-whisper.  All functions return None gracefully
when the library is unavailable or on error.
"""

from __future__ import annotations

import logging
import re
import string
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Optional dependency ──────────────────────────────────────────────────────

try:
    from faster_whisper import WhisperModel

    _FASTER_WHISPER_AVAILABLE = True
except ImportError:
    _FASTER_WHISPER_AVAILABLE = False

# ── Singleton model cache ────────────────────────────────────────────────────

_model_cache: dict[str, object] = {}


def get_model(
    model_size: str = "base",
    device: str = "auto",
    compute_type: str = "default",
) -> WhisperModel:
    """Return a lazily-loaded WhisperModel singleton, keyed by model_size.

    Raises:
        ImportError: If faster-whisper is not installed.
    """
    if not _FASTER_WHISPER_AVAILABLE:
        raise ImportError(
            "faster-whisper is required but not installed. "
            "Install with: pip install 'genlab-core[whisper]'"
        )
    key = f"{model_size}:{device}:{compute_type}"
    if key not in _model_cache:
        logger.info(
            "[whisper_timing] Loading WhisperModel(%s, device=%s, compute_type=%s)",
            model_size,
            device,
            compute_type,
        )
        _model_cache[key] = WhisperModel(
            model_size, device=device, compute_type=compute_type
        )
    return _model_cache[key]


# ── Text normalisation ──────────────────────────────────────────────────────

_PUNCT_RE = re.compile(f"[{re.escape(string.punctuation)}]")


def _normalize(word: str) -> str:
    """Lowercase and strip punctuation for fuzzy matching."""
    return _PUNCT_RE.sub("", word).lower().strip()


# ── Public API ───────────────────────────────────────────────────────────────


def transcribe_words(
    audio_path: Path | str,
    model_size: str = "base",
    min_confidence: float = 0.3,
) -> list[dict] | None:
    """Run faster-whisper on an audio file and return word-level timestamps.

    Args:
        audio_path: Path to a WAV/MP3/etc audio file.
        model_size: Whisper model size (tiny, base, small, medium, large-v3).
        min_confidence: Drop words below this probability threshold.

    Returns:
        List of ``{"word", "start", "end", "confidence"}`` dicts,
        or None if faster-whisper is unavailable or on error.
    """
    if not _FASTER_WHISPER_AVAILABLE:
        logger.debug("[whisper_timing] faster-whisper not installed — skipping")
        return None

    audio_path = Path(audio_path)

    try:
        model = get_model(model_size)
        segments, _info = model.transcribe(str(audio_path), word_timestamps=True)

        words: list[dict] = []
        for seg in segments:
            if not seg.words:
                continue
            for w in seg.words:
                prob = getattr(w, "probability", 1.0)
                if prob < min_confidence:
                    continue
                words.append(
                    {
                        "word": w.word.strip(),
                        "start": w.start,
                        "end": w.end,
                        "confidence": prob,
                    }
                )

        logger.debug("[whisper_timing] Transcribed %d words from %s", len(words), audio_path.name)
        return words

    except Exception as exc:
        logger.warning("[whisper_timing] Transcription failed for %s: %s", audio_path, exc)
        return None


def align_words(
    text: str,
    whisper_words: list[dict],
    mismatch_threshold: float = 0.30,
) -> list[dict] | None:
    """Align authored caption text to Whisper word-level output.

    Two-pointer walk: match authored words to Whisper words by normalised
    form (lowercase, strip punctuation).  Matched words use Whisper
    timestamps but preserve the authored word text.  Unmatched words get
    interpolated timestamps from their neighbours.

    Args:
        text: The authored caption string.
        whisper_words: List of ``{"word", "start", "end", "confidence"}`` dicts.
        mismatch_threshold: If the fraction of unmatched authored words exceeds
            this, return None (catastrophic mismatch -- caller should use WPM
            fallback).

    Returns:
        List of aligned word dicts, or None on catastrophic mismatch.
    """
    authored = text.split()
    if not authored:
        return []

    # Build aligned list with None placeholders for unmatched
    aligned: list[dict | None] = [None] * len(authored)

    wi = 0  # whisper pointer
    matched = 0

    for ai, a_word in enumerate(authored):
        a_norm = _normalize(a_word)
        if not a_norm:
            continue

        # Scan forward in whisper_words to find a match
        scan = wi
        while scan < len(whisper_words):
            w_norm = _normalize(whisper_words[scan]["word"])
            if a_norm == w_norm:
                aligned[ai] = {
                    "word": a_word,  # preserve authored text
                    "start": whisper_words[scan]["start"],
                    "end": whisper_words[scan]["end"],
                    "confidence": whisper_words[scan]["confidence"],
                }
                matched += 1
                wi = scan + 1
                break
            scan += 1
            # Don't scan too far ahead -- limit lookahead to 3 positions
            if scan - wi > 3:
                break

    # Check mismatch rate
    mismatch_rate = 1.0 - (matched / len(authored))
    if mismatch_rate > mismatch_threshold:
        logger.debug(
            "[whisper_timing] Catastrophic mismatch: %.0f%% unmatched (%d/%d)",
            mismatch_rate * 100,
            len(authored) - matched,
            len(authored),
        )
        return None

    # Interpolate gaps
    aligned = _interpolate_gaps(aligned, authored)
    return aligned


def _flatten_whisper_words(segments: list[dict]) -> list[dict]:
    """Convert CriticalRush segment format to flat word list.

    CriticalRush caption_generator produces::

        [{"words": [{"word", "start", "end", "probability"}], ...}]

    This flattens to::

        [{"word", "start", "end", "confidence"}]

    with ``probability`` renamed to ``confidence`` for cross-compatibility.
    """
    flat: list[dict] = []
    for seg in segments:
        for w in seg.get("words", []):
            flat.append(
                {
                    "word": w["word"],
                    "start": w["start"],
                    "end": w["end"],
                    "confidence": w.get("probability", w.get("confidence", 1.0)),
                }
            )
    return flat


# ── Internal helpers ─────────────────────────────────────────────────────────


def _interpolate_gaps(
    aligned: list[dict | None],
    authored: list[str],
) -> list[dict]:
    """Fill None entries in aligned with interpolated timestamps.

    For each gap, linearly interpolate between the nearest matched
    neighbours' timestamps.
    """
    n = len(aligned)
    result: list[dict] = []

    for i in range(n):
        if aligned[i] is not None:
            result.append(aligned[i])
            continue

        # Find previous matched entry
        prev_end = 0.0
        for j in range(i - 1, -1, -1):
            if aligned[j] is not None:
                prev_end = aligned[j]["end"]
                break

        # Find next matched entry
        next_start = prev_end + 0.5  # default: 0.5s after previous
        for j in range(i + 1, n):
            if aligned[j] is not None:
                next_start = aligned[j]["start"]
                break

        # Count consecutive unmatched in this gap
        gap_start_idx = i
        while gap_start_idx > 0 and aligned[gap_start_idx - 1] is None:
            gap_start_idx -= 1

        gap_end_idx = i
        while gap_end_idx < n - 1 and aligned[gap_end_idx + 1] is None:
            gap_end_idx += 1

        gap_size = gap_end_idx - gap_start_idx + 1
        pos_in_gap = i - gap_start_idx

        # Linearly distribute time across the gap
        total_duration = next_start - prev_end
        slot = total_duration / gap_size
        word_start = prev_end + pos_in_gap * slot
        word_end = word_start + slot

        result.append(
            {
                "word": authored[i],
                "start": round(word_start, 3),
                "end": round(word_end, 3),
                "confidence": 0.0,  # no Whisper match
            }
        )

    return result
