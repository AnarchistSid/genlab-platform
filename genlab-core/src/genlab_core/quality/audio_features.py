"""Audio feature extraction from rendered videos (Phase 4.A session 2).

Three extractors, mirroring the shape of ``visual_features``:
FeatureResult dataclass, fail-open on every path, ffmpeg-only
(no numpy / no librosa / no whisper — same 4GB VPS ceiling
reasoning as session 1).

## Signals

  * ``audio_energy_variance`` — variance of per-frame RMS. High =
    dynamic content (drops, inflection). Low = flat drone or
    silence. Uses ``astats=metadata=1:reset=1`` + parses RMS_level
    from stderr metadata lines.

  * ``dialogue_density`` — fraction of the video that has AUDIBLE
    content (not silence). Higher = talky/musical, lower = long
    silent stretches. Uses ``silencedetect=noise=-30dB:d=0.5`` +
    parses silence_start / silence_end windows.

  * ``music_to_voice_ratio`` — proxy for whether the mix is
    music-heavy or voice-heavy. Compares total-signal RMS to
    voice-band-filtered (300-3400 Hz) RMS. Score is normalized
    to 0-1 where 0.5 = balanced, closer to 1 = music-heavy,
    closer to 0 = voice-heavy.

## Design contract

Identical to visual_features:
  * ``FeatureResult(ok=True/False, score=[0,1], raw=<stat>, reason='')``
  * Never raises — every failure path returns ok=False with
    a diagnostic reason string
  * Reuses ``genlab_core.media.ffmpeg.get_{ffmpeg,ffprobe}_binary``
    to share binary-path caching
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeatureResult:
    """Return of every audio extractor. Same shape as
    visual_features.FeatureResult — deliberate so the joint-score
    module in session 3 can treat both uniformly."""
    ok: bool
    score: float | None = None
    raw: float | None = None
    reason: str = ""


# ── ffmpeg wrappers ──────────────────────────────────────────────


def _ffmpeg_binary() -> str:
    from genlab_core.media.ffmpeg import get_ffmpeg_binary
    return get_ffmpeg_binary()


def _ffprobe_binary() -> str:
    from genlab_core.media.ffmpeg import get_ffprobe_binary
    return get_ffprobe_binary()


def _run_ffmpeg_audio(
    video_path: Path, filter_spec: str, timeout_sec: int = 30,
) -> tuple[bool, str, str]:
    """Run ``ffmpeg -i <path> -af <filter> -f null -``. Returns
    (ok, stdout, stderr). Audio-side stats land in stderr just like
    the video-side signalstats."""
    try:
        cmd = [
            _ffmpeg_binary(), "-nostdin", "-hide_banner",
            "-i", str(video_path),
            "-af", filter_spec,
            "-f", "null", "-",
        ]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_sec,
        )
        return proc.returncode == 0, proc.stdout, proc.stderr
    except FileNotFoundError:
        return False, "", "ffmpeg_missing"
    except subprocess.TimeoutExpired:
        return False, "", f"timeout after {timeout_sec}s"
    except Exception as exc:
        return False, "", f"ffmpeg_crashed: {exc}"


def _get_duration_seconds(video_path: Path) -> float | None:
    try:
        proc = subprocess.run(
            [
                _ffprobe_binary(), "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0:
            return None
        return float(proc.stdout.strip())
    except Exception:
        return None


def _has_audio_stream(video_path: Path) -> bool:
    """True when the video actually carries an audio track. Videos
    that were re-encoded video-only would produce noise from every
    audio filter otherwise."""
    try:
        proc = subprocess.run(
            [
                _ffprobe_binary(), "-v", "error",
                "-select_streams", "a",
                "-show_entries", "stream=codec_type",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True, text=True, timeout=10,
        )
        return proc.returncode == 0 and "audio" in proc.stdout.lower()
    except Exception:
        return False


def _parse_rms_levels(stderr: str) -> list[float]:
    """Extract per-frame RMS_level values from astats metadata
    output. astats emits lines like:
       [Parsed_astats_1 @ 0x...] lavfi.astats.Overall.RMS_level=-24.5
    """
    values: list[float] = []
    for line in stderr.splitlines():
        m = re.search(r"lavfi\.astats\.Overall\.RMS_level=(-?[\d.]+)", line)
        if m:
            try:
                v = float(m.group(1))
            except ValueError:
                continue
            # astats emits '-inf' as a very negative sentinel; skip
            # since it means "no audio in this frame" and would
            # dominate the variance calc.
            if v > -100:
                values.append(v)
    return values


# ── Feature extractors ───────────────────────────────────────────


def extract_audio_energy_variance(video_path: Path) -> FeatureResult:
    """Higher score = more RMS variance across frames = more dynamic
    audio (music drops, voice inflection, sound-effect punches).
    Low = flat drone or silence.

    Variance normalization: divide by 400 (empirical: healthy
    dynamic audio has RMS variance in the 50-200 dB^2 range).
    """
    if not video_path.exists():
        return FeatureResult(ok=False, reason="file_not_found")
    if not _has_audio_stream(video_path):
        return FeatureResult(ok=False, reason="no_audio_stream")

    ok, _stdout, stderr = _run_ffmpeg_audio(
        video_path, "astats=metadata=1:reset=1,ametadata=print",
    )
    if not ok:
        return FeatureResult(ok=False, reason=stderr[:100])
    values = _parse_rms_levels(stderr)
    if len(values) < 2:
        return FeatureResult(ok=False, reason=f"insufficient_frames:{len(values)}")

    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    score = min(1.0, variance / 400.0)
    return FeatureResult(ok=True, score=score, raw=variance)


def extract_dialogue_density(video_path: Path) -> FeatureResult:
    """Fraction of the video's duration that has AUDIBLE content
    (i.e., is NOT silence). Uses silencedetect to find silence
    windows and subtracts.

    A truly silent video → 0. Constant audio → 1.

    Note: this doesn't distinguish speech from music — the
    naming ``dialogue_density`` follows the roadmap wording but
    the actual signal is "audible-content density". The
    music-vs-voice split is the separate extractor below.
    """
    if not video_path.exists():
        return FeatureResult(ok=False, reason="file_not_found")
    if not _has_audio_stream(video_path):
        return FeatureResult(ok=False, reason="no_audio_stream")

    duration = _get_duration_seconds(video_path)
    if duration is None or duration <= 0:
        return FeatureResult(ok=False, reason="no_duration")

    ok, _stdout, stderr = _run_ffmpeg_audio(
        video_path, "silencedetect=noise=-30dB:d=0.3",
    )
    if not ok:
        return FeatureResult(ok=False, reason=stderr[:100])

    # silencedetect lines look like:
    #   [silencedetect @ 0x...] silence_start: 1.23
    #   [silencedetect @ 0x...] silence_end: 3.45 | silence_duration: 2.22
    silence_total = 0.0
    for line in stderr.splitlines():
        m = re.search(r"silence_duration:\s*([\d.]+)", line)
        if m:
            try:
                silence_total += float(m.group(1))
            except ValueError:
                continue
    audible_ratio = max(0.0, min(1.0, (duration - silence_total) / duration))
    return FeatureResult(
        ok=True, score=audible_ratio, raw=silence_total,
    )


def extract_music_to_voice_ratio(video_path: Path) -> FeatureResult:
    """Proxy for whether the mix is music-dominated or voice-dominated.

    Compare TOTAL signal RMS to VOICE-BAND (300-3400 Hz, standard
    telephony band that captures human speech fundamentals + first
    formant) RMS. Score is normalized around 0.5:

      * ~0.5 = balanced mix (voice + music at similar levels)
      * > 0.5 = music-heavy (voice band is a small fraction of total)
      * < 0.5 = voice-heavy (voice band dominates)

    Returns raw as the (music_rms - voice_rms) delta in dB.
    """
    if not video_path.exists():
        return FeatureResult(ok=False, reason="file_not_found")
    if not _has_audio_stream(video_path):
        return FeatureResult(ok=False, reason="no_audio_stream")

    # Full-band RMS
    ok, _stdout, stderr = _run_ffmpeg_audio(
        video_path,
        "astats=metadata=1:reset=1,ametadata=print",
    )
    if not ok:
        return FeatureResult(ok=False, reason=f"full:{stderr[:60]}")
    total_rms_values = _parse_rms_levels(stderr)
    if not total_rms_values:
        return FeatureResult(ok=False, reason="no_total_rms")
    total_rms = sum(total_rms_values) / len(total_rms_values)

    # Voice-band RMS: bandpass 300-3400 Hz then astats
    # 1850 Hz center, 3100 Hz bandwidth (300-3400 range)
    voice_filter = (
        "highpass=f=300,lowpass=f=3400,"
        "astats=metadata=1:reset=1,ametadata=print"
    )
    ok, _stdout, stderr = _run_ffmpeg_audio(video_path, voice_filter)
    if not ok:
        return FeatureResult(ok=False, reason=f"voice:{stderr[:60]}")
    voice_rms_values = _parse_rms_levels(stderr)
    if not voice_rms_values:
        return FeatureResult(ok=False, reason="no_voice_rms")
    voice_rms = sum(voice_rms_values) / len(voice_rms_values)

    # Delta in dB. Positive = full-band is louder than voice-band
    # (music present outside voice range). Negative = voice-band
    # louder (which shouldn't happen because voice-band is a
    # subset — but numeric noise can flip it).
    delta_db = total_rms - voice_rms
    # Normalize: delta of ~6dB is meaningful (2x power).
    # score = 0.5 + delta/12 (0dB → 0.5, +6dB → 1.0, -6dB → 0.0)
    score = max(0.0, min(1.0, 0.5 + delta_db / 12.0))
    return FeatureResult(ok=True, score=score, raw=delta_db)
