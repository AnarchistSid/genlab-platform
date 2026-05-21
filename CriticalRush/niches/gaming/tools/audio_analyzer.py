"""Audio analysis utilities wrapping librosa.

Provides RMS energy, energy variance, and peak moment detection for
entertainment value scoring in the SCORE_CLIPS pipeline stage.

Uses lazy import pattern — librosa is imported inside function bodies
so the module loads even when librosa is not installed.

All functions return None on any failure (missing librosa, bad file, etc.).
Results are cached in-memory by file path within the same process.
"""

import logging

import numpy as np

logger = logging.getLogger(__name__)

# In-memory cache: path -> (y, sr)
_audio_cache: dict[str, tuple] = {}

DEFAULT_SR = 22050
DEFAULT_HOP_LENGTH = 512


def clear_cache() -> None:
    """Clear the in-memory audio cache."""
    _audio_cache.clear()


def _load_audio(path: str) -> tuple | None:
    """Load audio from a video/audio file using librosa.

    Returns (y, sr) tuple or None on failure.
    Caches results by file path.
    """
    if path in _audio_cache:
        return _audio_cache[path]

    try:
        import librosa

        y, sr = librosa.load(path, sr=DEFAULT_SR, mono=True)
        _audio_cache[path] = (y, sr)
        return (y, sr)
    except ImportError:
        logger.warning("librosa not installed — audio analysis unavailable")
        return None
    except Exception as e:
        logger.warning("Failed to load audio from %s: %s", path, e)
        return None


def get_rms_energy(path: str) -> float | None:
    """Get mean RMS energy of the audio track.

    Returns a float (typically 0.0-1.0 range) or None on failure.
    """
    result = _load_audio(path)
    if result is None:
        return None

    try:
        import librosa

        y, sr = result
        rms = librosa.feature.rms(y=y, hop_length=DEFAULT_HOP_LENGTH)
        return float(np.mean(rms[0]))
    except Exception as e:
        logger.warning("RMS energy failed for %s: %s", path, e)
        return None


def get_energy_variance(path: str) -> float | None:
    """Get variance of RMS energy across frames.

    Higher variance = more dynamic/exciting audio (peaks and valleys).
    Returns float or None on failure.
    """
    result = _load_audio(path)
    if result is None:
        return None

    try:
        import librosa

        y, sr = result
        rms = librosa.feature.rms(y=y, hop_length=DEFAULT_HOP_LENGTH)
        return float(np.var(rms[0]))
    except Exception as e:
        logger.warning("Energy variance failed for %s: %s", path, e)
        return None


def get_peak_moments(path: str, threshold: float = 0.5) -> list[float] | None:
    """Find timestamps where audio energy exceeds threshold.

    Useful for beat-syncing transitions in compilations.
    Returns list of timestamps in seconds, or None on failure.
    """
    result = _load_audio(path)
    if result is None:
        return None

    try:
        import librosa

        y, sr = result
        rms = librosa.feature.rms(y=y, hop_length=DEFAULT_HOP_LENGTH)[0]
        peak_frames = np.where(rms > threshold)[0]
        if len(peak_frames) == 0:
            return []
        peak_times = librosa.frames_to_time(
            peak_frames,
            sr=sr,
            hop_length=DEFAULT_HOP_LENGTH,
        )
        return [float(t) for t in peak_times]
    except Exception as e:
        logger.warning("Peak detection failed for %s: %s", path, e)
        return None
