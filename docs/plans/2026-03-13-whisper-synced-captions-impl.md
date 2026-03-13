# Whisper-Synced Animated Captions — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace fixed WPM caption timing with Whisper speech-to-text word-level timestamps so captions sync to actual speech across CriticalRush, ClutchWire, SpliceReel, and FrameDrift.

**Architecture:** Two new shared modules in genlab-core (`whisper_timing.py` for Whisper transcription, `audio_probe.py` for audio detection). The existing `WordByWordAnimator` gains an optional `whisper_words` parameter — when provided, real timestamps drive the animation; when absent, WPM fallback is used. Per-channel wiring is additive (config flag + strategy enhancement). CriticalRush refactors to use shared `whisper_timing` instead of its own `caption_generator.py`.

**Tech Stack:** Python 3.12, faster-whisper (CTranslate2), FFmpeg (ffprobe + drawtext), pytest, genlab-core src-layout, uv workspace.

**Design doc:** `docs/plans/2026-03-13-whisper-synced-captions-design.md`

---

## Task 1: Create `genlab_core.media.audio_probe` module

**Files:**
- Create: `genlab-core/src/genlab_core/media/audio_probe.py`
- Test: `genlab-core/tests/media/test_audio_probe.py`

**Step 1: Write the failing tests**

```python
# genlab-core/tests/media/test_audio_probe.py
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from genlab_core.media.audio_probe import (
    extract_audio_track,
    has_meaningful_audio,
)


class TestHasMeaningfulAudio:
    """Tests for audio stream detection + silence gate."""

    @patch("genlab_core.media.audio_probe._probe_audio_stream")
    def test_no_audio_stream_returns_false(self, mock_probe):
        mock_probe.return_value = None
        assert has_meaningful_audio(Path("fake.mp4")) is False

    @patch("genlab_core.media.audio_probe._probe_audio_stream")
    def test_silent_audio_returns_false(self, mock_probe):
        mock_probe.return_value = {"codec_name": "aac", "sample_rate": "48000"}
        with patch(
            "genlab_core.media.audio_probe._measure_volume",
            return_value=-91.0,
        ):
            assert has_meaningful_audio(Path("fake.mp4")) is False

    @patch("genlab_core.media.audio_probe._probe_audio_stream")
    def test_loud_audio_returns_true(self, mock_probe):
        mock_probe.return_value = {"codec_name": "aac", "sample_rate": "48000"}
        with patch(
            "genlab_core.media.audio_probe._measure_volume",
            return_value=-20.0,
        ):
            assert has_meaningful_audio(Path("fake.mp4")) is True

    @patch("genlab_core.media.audio_probe._probe_audio_stream")
    def test_custom_threshold(self, mock_probe):
        mock_probe.return_value = {"codec_name": "aac", "sample_rate": "48000"}
        with patch(
            "genlab_core.media.audio_probe._measure_volume",
            return_value=-35.0,
        ):
            # Default threshold is -40, so -35 is above it
            assert has_meaningful_audio(Path("fake.mp4")) is True
            # But with stricter threshold...
            assert has_meaningful_audio(Path("fake.mp4"), silence_threshold_db=-30) is False


class TestExtractAudioTrack:
    """Tests for WAV extraction from video."""

    @patch("genlab_core.media.audio_probe.get_ffmpeg_binary", return_value="ffmpeg")
    @patch("subprocess.run")
    def test_successful_extraction(self, mock_run, mock_bin, tmp_path):
        output = tmp_path / "audio.wav"
        mock_run.return_value = MagicMock(returncode=0)
        result = extract_audio_track(Path("input.mp4"), output)
        assert result == output
        mock_run.assert_called_once()

    @patch("genlab_core.media.audio_probe.get_ffmpeg_binary", return_value="ffmpeg")
    @patch("subprocess.run")
    def test_failed_extraction_returns_none(self, mock_run, mock_bin, tmp_path):
        output = tmp_path / "audio.wav"
        mock_run.return_value = MagicMock(returncode=1)
        result = extract_audio_track(Path("input.mp4"), output)
        assert result is None

    @patch("genlab_core.media.audio_probe.get_ffmpeg_binary", side_effect=RuntimeError)
    def test_no_ffmpeg_returns_none(self, mock_bin, tmp_path):
        output = tmp_path / "audio.wav"
        result = extract_audio_track(Path("input.mp4"), output)
        assert result is None
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/anarchistsid/GenLab && uv run --package genlab-core pytest genlab-core/tests/media/test_audio_probe.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'genlab_core.media.audio_probe'`

**Step 3: Write minimal implementation**

```python
# genlab-core/src/genlab_core/media/audio_probe.py
"""Audio stream detection and extraction for Whisper-synced captions.

Provides two capabilities:
  1. has_meaningful_audio() — detect whether a clip has speech/audio worth transcribing
  2. extract_audio_track() — extract audio to a temporary WAV for Whisper input

Uses ffprobe for stream detection and volumedetect for silence gating.
No new dependencies — relies on existing FFmpeg infrastructure.
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from genlab_core.media.ffmpeg import get_ffmpeg_binary, get_ffprobe_binary

logger = logging.getLogger(__name__)

# Default: audio below -40 dB mean volume is considered silent
_DEFAULT_SILENCE_THRESHOLD_DB = -40.0


def has_meaningful_audio(
    clip_path: Path | str,
    silence_threshold_db: float = _DEFAULT_SILENCE_THRESHOLD_DB,
) -> bool:
    """Check whether a video clip has meaningful (non-silent) audio.

    Two-step check:
      1. Does the file have an audio stream at all? (ffprobe)
      2. Is the audio above the silence threshold? (volumedetect)

    Returns False if no audio stream, audio is silent, or on any error.
    """
    clip_path = Path(clip_path)
    stream = _probe_audio_stream(clip_path)
    if stream is None:
        return False

    mean_vol = _measure_volume(clip_path)
    if mean_vol is None:
        return False

    return mean_vol > silence_threshold_db


def extract_audio_track(
    clip_path: Path | str,
    output_path: Path | str,
) -> Path | None:
    """Extract audio from video to a WAV file for Whisper input.

    Returns output_path on success, None on failure.
    """
    try:
        ffmpeg = get_ffmpeg_binary()
    except RuntimeError:
        logger.warning("FFmpeg not available for audio extraction")
        return None

    cmd = [
        ffmpeg, "-y",
        "-i", str(clip_path),
        "-vn",                    # no video
        "-acodec", "pcm_s16le",   # 16-bit PCM
        "-ar", "16000",           # 16kHz (Whisper's native rate)
        "-ac", "1",               # mono
        str(output_path),
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode == 0:
            return Path(output_path)
        logger.warning("Audio extraction failed (rc=%d)", proc.returncode)
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.warning("Audio extraction error: %s", e)
        return None


def _probe_audio_stream(clip_path: Path) -> dict | None:
    """Return the first audio stream dict from ffprobe, or None."""
    try:
        ffprobe = get_ffprobe_binary()
    except RuntimeError:
        return None

    cmd = [
        ffprobe, "-v", "quiet",
        "-print_format", "json",
        "-show_streams", "-select_streams", "a",
        str(clip_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout)
        streams = data.get("streams", [])
        return streams[0] if streams else None
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return None


def _measure_volume(clip_path: Path) -> float | None:
    """Measure mean volume in dB using FFmpeg volumedetect. Returns None on error."""
    try:
        ffmpeg = get_ffmpeg_binary()
    except RuntimeError:
        return None

    cmd = [
        ffmpeg,
        "-i", str(clip_path),
        "-af", "volumedetect",
        "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        # volumedetect outputs to stderr
        for line in proc.stderr.splitlines():
            if "mean_volume" in line:
                # Format: [Parsed_volumedetect_0 ...] mean_volume: -20.5 dB
                parts = line.split("mean_volume:")
                if len(parts) == 2:
                    return float(parts[1].strip().replace("dB", "").strip())
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        return None
```

**Step 4: Run tests to verify they pass**

Run: `cd /Users/anarchistsid/GenLab && uv run --package genlab-core pytest genlab-core/tests/media/test_audio_probe.py -v`
Expected: 7 PASS

**Step 5: Commit**

```bash
cd /Users/anarchistsid/GenLab/genlab-core
git add src/genlab_core/media/audio_probe.py tests/media/test_audio_probe.py
git commit -m "feat(media): add audio_probe module for audio detection and extraction

Two functions: has_meaningful_audio() (ffprobe + volumedetect silence gate)
and extract_audio_track() (WAV extraction at 16kHz mono for Whisper input).
No new dependencies — uses existing FFmpeg infrastructure."
```

---

## Task 2: Create `genlab_core.media.whisper_timing` module

**Files:**
- Create: `genlab-core/src/genlab_core/media/whisper_timing.py`
- Test: `genlab-core/tests/media/test_whisper_timing.py`

**Step 1: Write the failing tests**

```python
# genlab-core/tests/media/test_whisper_timing.py
"""Tests for Whisper word-level timing extraction.

All tests mock faster-whisper — no torch/model required.
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from genlab_core.media.whisper_timing import (
    transcribe_words,
    align_words,
    _flatten_whisper_words,
)


# ── Fixtures ──────────────────────────────────────────────────

def _make_whisper_word(word: str, start: float, end: float, prob: float = 0.95):
    """Create a mock faster-whisper Word object."""
    w = MagicMock()
    w.word = word
    w.start = start
    w.end = end
    w.probability = prob
    return w


def _make_segment(text: str, start: float, end: float, words: list):
    seg = MagicMock()
    seg.text = text
    seg.start = start
    seg.end = end
    seg.words = words
    return seg


# ── transcribe_words tests ────────────────────────────────────

class TestTranscribeWords:
    @patch("genlab_core.media.whisper_timing._get_model")
    def test_returns_word_dicts(self, mock_get_model):
        words = [
            _make_whisper_word(" AI", 0.1, 0.4, 0.98),
            _make_whisper_word(" just", 0.4, 0.7, 0.95),
            _make_whisper_word(" changed", 0.7, 1.1, 0.92),
        ]
        seg = _make_segment("AI just changed", 0.1, 1.1, words)
        mock_model = MagicMock()
        mock_model.transcribe.return_value = (iter([seg]), MagicMock())
        mock_get_model.return_value = mock_model

        result = transcribe_words(Path("fake.wav"))
        assert result is not None
        assert len(result) == 3
        assert result[0] == {"word": "AI", "start": 0.1, "end": 0.4, "confidence": 0.98}
        assert result[2]["word"] == "changed"

    @patch("genlab_core.media.whisper_timing._get_model")
    def test_filters_low_confidence(self, mock_get_model):
        words = [
            _make_whisper_word(" AI", 0.1, 0.4, 0.98),
            _make_whisper_word(" uh", 0.4, 0.5, 0.15),  # below default 0.3
            _make_whisper_word(" changed", 0.7, 1.1, 0.92),
        ]
        seg = _make_segment("AI uh changed", 0.1, 1.1, words)
        mock_model = MagicMock()
        mock_model.transcribe.return_value = (iter([seg]), MagicMock())
        mock_get_model.return_value = mock_model

        result = transcribe_words(Path("fake.wav"), min_confidence=0.3)
        assert len(result) == 2
        assert result[0]["word"] == "AI"
        assert result[1]["word"] == "changed"

    def test_returns_none_when_not_installed(self):
        with patch("genlab_core.media.whisper_timing._FASTER_WHISPER_AVAILABLE", False):
            result = transcribe_words(Path("fake.wav"))
            assert result is None

    @patch("genlab_core.media.whisper_timing._get_model")
    def test_transcription_error_returns_none(self, mock_get_model):
        mock_model = MagicMock()
        mock_model.transcribe.side_effect = RuntimeError("model crashed")
        mock_get_model.return_value = mock_model
        result = transcribe_words(Path("fake.wav"))
        assert result is None


# ── align_words tests ─────────────────────────────────────────

class TestAlignWords:
    def test_perfect_match(self):
        text = "AI just changed everything"
        whisper_words = [
            {"word": "AI", "start": 0.1, "end": 0.4, "confidence": 0.95},
            {"word": "just", "start": 0.4, "end": 0.7, "confidence": 0.90},
            {"word": "changed", "start": 0.7, "end": 1.1, "confidence": 0.92},
            {"word": "everything", "start": 1.1, "end": 1.6, "confidence": 0.88},
        ]
        result = align_words(text, whisper_words)
        assert result is not None
        assert len(result) == 4
        assert result[0]["word"] == "AI"
        assert result[0]["start"] == 0.1

    def test_case_insensitive_match(self):
        text = "THE AI Revolution"
        whisper_words = [
            {"word": "the", "start": 0.0, "end": 0.2, "confidence": 0.9},
            {"word": "ai", "start": 0.2, "end": 0.5, "confidence": 0.9},
            {"word": "revolution", "start": 0.5, "end": 1.0, "confidence": 0.9},
        ]
        result = align_words(text, whisper_words)
        assert result is not None
        assert len(result) == 3
        assert result[0]["word"] == "THE"  # original text preserved

    def test_unmatched_words_get_interpolated(self):
        text = "AI will change the world"
        whisper_words = [
            {"word": "AI", "start": 0.0, "end": 0.3, "confidence": 0.9},
            # "will" missing from whisper
            {"word": "change", "start": 0.6, "end": 1.0, "confidence": 0.9},
            {"word": "the", "start": 1.0, "end": 1.2, "confidence": 0.9},
            {"word": "world", "start": 1.2, "end": 1.6, "confidence": 0.9},
        ]
        result = align_words(text, whisper_words)
        assert result is not None
        assert len(result) == 5
        # "will" should get interpolated between "AI" and "change"
        assert result[1]["word"] == "will"
        assert result[1]["start"] >= 0.3
        assert result[1]["end"] <= 0.6

    def test_catastrophic_mismatch_returns_none(self):
        text = "completely different text that whisper missed"
        whisper_words = [
            {"word": "something", "start": 0.0, "end": 0.5, "confidence": 0.9},
            {"word": "else", "start": 0.5, "end": 0.8, "confidence": 0.9},
        ]
        result = align_words(text, whisper_words)
        assert result is None  # >30% mismatch triggers fallback


class TestFlattenWhisperWords:
    def test_flattens_multiple_segments(self):
        segments = [
            {"words": [
                {"word": "AI", "start": 0.1, "end": 0.4, "probability": 0.9},
            ]},
            {"words": [
                {"word": "rocks", "start": 0.5, "end": 0.8, "probability": 0.9},
            ]},
        ]
        result = _flatten_whisper_words(segments)
        assert len(result) == 2
        assert result[0]["word"] == "AI"
        assert result[1]["word"] == "rocks"
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/anarchistsid/GenLab && uv run --package genlab-core pytest genlab-core/tests/media/test_whisper_timing.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'genlab_core.media.whisper_timing'`

**Step 3: Write minimal implementation**

```python
# genlab-core/src/genlab_core/media/whisper_timing.py
"""Word-level Whisper transcription for synced captions.

Provides word-level timestamps from faster-whisper and a text alignment
algorithm that maps authored text to Whisper output.

Optional dependency: faster-whisper (pulls torch/ctranslate2).
Returns None gracefully when not installed — callers fall back to WPM.

Usage:
    words = transcribe_words(Path("audio.wav"))
    if words:
        aligned = align_words("authored caption text", words)
        # aligned is list[dict] with word/start/end/confidence per authored word
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Lazy import check
try:
    from faster_whisper import WhisperModel  # noqa: F401
    _FASTER_WHISPER_AVAILABLE = True
except ImportError:
    _FASTER_WHISPER_AVAILABLE = False

# Singleton model cache (lazy loaded)
_model_cache: dict[str, object] = {}


def _get_model(model_size: str = "base", device: str = "cpu", compute_type: str = "int8"):
    """Lazy singleton model loader. Downloads on first use (~150MB for base)."""
    from faster_whisper import WhisperModel

    key = f"{model_size}_{device}_{compute_type}"
    if key not in _model_cache:
        logger.info("Loading Whisper model: %s (device=%s)", model_size, device)
        _model_cache[key] = WhisperModel(model_size, device=device, compute_type=compute_type)
    return _model_cache[key]


def transcribe_words(
    audio_path: Path | str,
    model_size: str = "base",
    min_confidence: float = 0.3,
) -> list[dict] | None:
    """Transcribe audio and return word-level timestamps.

    Returns list of {"word": str, "start": float, "end": float, "confidence": float}
    or None if faster-whisper is unavailable or transcription fails.
    """
    if not _FASTER_WHISPER_AVAILABLE:
        logger.debug("faster-whisper not installed — Whisper sync unavailable")
        return None

    try:
        model = _get_model(model_size)
        segments_gen, _info = model.transcribe(
            str(audio_path),
            word_timestamps=True,
            vad_filter=True,
        )

        words: list[dict] = []
        for seg in segments_gen:
            if not seg.words:
                continue
            for w in seg.words:
                prob = getattr(w, "probability", 1.0)
                if prob < min_confidence:
                    continue
                words.append({
                    "word": w.word.strip(),
                    "start": round(w.start, 3),
                    "end": round(w.end, 3),
                    "confidence": round(prob, 3),
                })

        if not words:
            logger.warning("Whisper produced no words for %s", audio_path)
            return None

        logger.info("Transcribed %d words from %s", len(words), audio_path)
        return words

    except Exception as e:
        logger.warning("Whisper transcription failed for %s: %s", audio_path, e)
        return None


def _flatten_whisper_words(segments: list[dict]) -> list[dict]:
    """Flatten CriticalRush-style segment dicts into flat word list.

    Input format (from caption_generator.transcribe()):
      [{"words": [{"word": "AI", "start": 0.1, "end": 0.4, "probability": 0.9}, ...]}]

    Output format (this module's standard):
      [{"word": "AI", "start": 0.1, "end": 0.4, "confidence": 0.9}, ...]
    """
    words: list[dict] = []
    for seg in segments:
        for w in seg.get("words", []):
            words.append({
                "word": w["word"].strip() if isinstance(w["word"], str) else w["word"],
                "start": w["start"],
                "end": w["end"],
                "confidence": w.get("probability", w.get("confidence", 1.0)),
            })
    return words


def _normalize(word: str) -> str:
    """Normalize a word for fuzzy matching: lowercase, strip punctuation."""
    return re.sub(r"[^\w]", "", word.lower())


def align_words(
    text: str,
    whisper_words: list[dict],
    mismatch_threshold: float = 0.30,
) -> list[dict] | None:
    """Align authored text words with Whisper timestamps.

    The authored text (displayed on screen) may differ from Whisper's output.
    This function maps each authored word to the best-matching Whisper word
    and interpolates timestamps for unmatched words.

    Args:
        text:                The authored caption text (displayed verbatim).
        whisper_words:       Whisper output: [{"word", "start", "end", "confidence"}].
        mismatch_threshold:  If more than this fraction of words are unmatched,
                             return None (caller should fall back to WPM).

    Returns:
        List of dicts with same structure as whisper_words but using authored
        text and interpolated timestamps. None on catastrophic mismatch.
    """
    authored = text.split()
    if not authored:
        return None
    if not whisper_words:
        return None

    # Two-pointer alignment
    aligned: list[dict | None] = [None] * len(authored)
    w_idx = 0  # whisper pointer

    for a_idx, a_word in enumerate(authored):
        a_norm = _normalize(a_word)
        if not a_norm:
            continue

        # Search forward in whisper words for a match
        best_j = None
        for j in range(w_idx, min(w_idx + 5, len(whisper_words))):
            if _normalize(whisper_words[j]["word"]) == a_norm:
                best_j = j
                break

        if best_j is not None:
            aligned[a_idx] = {
                "word": a_word,  # preserve authored text
                "start": whisper_words[best_j]["start"],
                "end": whisper_words[best_j]["end"],
                "confidence": whisper_words[best_j]["confidence"],
            }
            w_idx = best_j + 1

    # Check mismatch rate
    matched = sum(1 for a in aligned if a is not None)
    if matched / len(authored) < (1 - mismatch_threshold):
        logger.warning(
            "Whisper alignment: %d/%d matched (%.0f%%) — falling back to WPM",
            matched, len(authored), matched / len(authored) * 100,
        )
        return None

    # Interpolate unmatched words from neighbors
    _interpolate_gaps(aligned, authored)

    return aligned  # type: ignore[return-value]


def _interpolate_gaps(aligned: list[dict | None], authored: list[str]) -> None:
    """Fill None gaps in aligned list by interpolating from neighbors."""
    n = len(aligned)

    for i in range(n):
        if aligned[i] is not None:
            continue

        # Find previous and next matched words
        prev_end = 0.0
        for p in range(i - 1, -1, -1):
            if aligned[p] is not None:
                prev_end = aligned[p]["end"]
                break

        next_start = prev_end + 0.5  # fallback: 0.5s after previous
        for nx in range(i + 1, n):
            if aligned[nx] is not None:
                next_start = aligned[nx]["start"]
                break

        # Count consecutive gaps to distribute time evenly
        gap_start = i
        gap_end = i
        while gap_end < n and aligned[gap_end] is None:
            gap_end += 1
        gap_count = gap_end - gap_start

        total_gap = next_start - prev_end
        per_word = total_gap / gap_count if gap_count > 0 else 0.3

        for g in range(gap_start, gap_end):
            offset = g - gap_start
            aligned[g] = {
                "word": authored[g],
                "start": round(prev_end + offset * per_word, 3),
                "end": round(prev_end + (offset + 1) * per_word, 3),
                "confidence": 0.0,  # interpolated, not from Whisper
            }
```

**Step 4: Run tests to verify they pass**

Run: `cd /Users/anarchistsid/GenLab && uv run --package genlab-core pytest genlab-core/tests/media/test_whisper_timing.py -v`
Expected: 8 PASS

**Step 5: Commit**

```bash
cd /Users/anarchistsid/GenLab/genlab-core
git add src/genlab_core/media/whisper_timing.py tests/media/test_whisper_timing.py
git commit -m "feat(media): add whisper_timing module for word-level timestamps

transcribe_words() wraps faster-whisper with lazy singleton model loading.
align_words() maps authored text to Whisper output via two-pointer walk
with interpolation for gaps and WPM fallback on >30% mismatch.
Optional dep: faster-whisper. Returns None when unavailable."
```

---

## Task 3: Add `whisper-timestamped` optional dependency to genlab-core

**Files:**
- Modify: `genlab-core/pyproject.toml` (add optional dependency group)

**Step 1: No test needed — this is config**

**Step 2: Add the optional dependency**

In `genlab-core/pyproject.toml`, add to `[project.optional-dependencies]`:

```toml
whisper = ["faster-whisper>=1.0"]
```

This follows the same pattern as `tts`, `smart-crop`, etc. — optional extras that pull heavy deps.

**Step 3: Commit**

```bash
cd /Users/anarchistsid/GenLab/genlab-core
git add pyproject.toml
git commit -m "build: add whisper optional dependency group

faster-whisper>=1.0 as optional extra, same pattern as tts/smart-crop.
Pulls ctranslate2 + torch. Only needed on render machines."
```

---

## Task 4: Enhance `WordByWordAnimator` with Whisper timing path

**Files:**
- Modify: `Content Scraper/execution/utils/word_by_word_animator.py` (lines 198-261, 501-592)
- Test: `Content Scraper/tests/test_word_by_word_whisper.py`

**Step 1: Write the failing tests**

```python
# Content Scraper/tests/test_word_by_word_whisper.py
"""Tests for Whisper-synced timing path in WordByWordAnimator."""
import pytest

from execution.utils.word_by_word_animator import WordByWordAnimator, WordTiming


class TestCalculateWordTimingsFromWhisper:
    """Tests for the new Whisper-based timing method."""

    def setup_method(self):
        self.animator = WordByWordAnimator(config={
            "wpm": 150,
            "transition_duration": 0.15,
            "max_words_per_line": 6,
            "char_width_factor": 0.55,
            "space_width_factor": 0.30,
            "line_height_factor": 1.45,
            "highlight_color": "FFD700",
            "base_color": "FFFFFF",
            "shadow_color": "000000",
            "shadow_alpha": 0.9,
            "start_delay": 0.3,
            "body_gap": 0.2,
        })

    def test_basic_whisper_timing(self):
        text = "AI just changed"
        whisper_words = [
            {"word": "AI", "start": 0.1, "end": 0.4, "confidence": 0.95},
            {"word": "just", "start": 0.5, "end": 0.8, "confidence": 0.90},
            {"word": "changed", "start": 0.9, "end": 1.3, "confidence": 0.88},
        ]
        timings = self.animator.calculate_word_timings_from_whisper(
            text, whisper_words,
        )
        assert len(timings) == 3
        assert timings[0].word == "AI"
        assert timings[0].appear_time == 0.1
        assert timings[1].appear_time == 0.5
        assert timings[2].appear_time == 0.9

    def test_highlight_end_is_next_word_start(self):
        text = "one two three"
        whisper_words = [
            {"word": "one", "start": 0.0, "end": 0.3, "confidence": 0.9},
            {"word": "two", "start": 0.5, "end": 0.8, "confidence": 0.9},
            {"word": "three", "start": 1.0, "end": 1.4, "confidence": 0.9},
        ]
        timings = self.animator.calculate_word_timings_from_whisper(
            text, whisper_words,
        )
        # highlight_end of word N = start of word N+1
        assert timings[0].highlight_end == 0.5
        assert timings[1].highlight_end == 1.0
        # Last word: highlight_end = its own end time
        assert timings[2].highlight_end == 1.4

    def test_fade_end_includes_transition_duration(self):
        text = "hello world"
        whisper_words = [
            {"word": "hello", "start": 0.0, "end": 0.5, "confidence": 0.9},
            {"word": "world", "start": 0.6, "end": 1.0, "confidence": 0.9},
        ]
        timings = self.animator.calculate_word_timings_from_whisper(
            text, whisper_words,
        )
        fade_dur = self.animator.DEFAULT_FADE_DURATION  # 0.15
        assert timings[0].fade_end == pytest.approx(0.6 + fade_dur, abs=0.01)

    def test_empty_text_returns_empty(self):
        timings = self.animator.calculate_word_timings_from_whisper("", [])
        assert timings == []

    def test_custom_fade_duration(self):
        text = "test"
        whisper_words = [
            {"word": "test", "start": 0.0, "end": 0.5, "confidence": 0.9},
        ]
        timings = self.animator.calculate_word_timings_from_whisper(
            text, whisper_words, fade_duration=0.25,
        )
        assert timings[0].fade_end == pytest.approx(0.5 + 0.25, abs=0.01)


class TestBuildAnimatedFiltersWhisper:
    """Test the whisper_words parameter on build_animated_filters."""

    def setup_method(self):
        self.animator = WordByWordAnimator(config={
            "wpm": 150,
            "transition_duration": 0.15,
            "max_words_per_line": 6,
            "char_width_factor": 0.55,
            "space_width_factor": 0.30,
            "line_height_factor": 1.45,
            "highlight_color": "FFD700",
            "base_color": "FFFFFF",
            "shadow_color": "000000",
            "shadow_alpha": 0.9,
            "start_delay": 0.3,
            "body_gap": 0.2,
        })

    def test_whisper_words_produces_filters(self):
        text = "AI changed everything"
        whisper_words = [
            {"word": "AI", "start": 0.1, "end": 0.4, "confidence": 0.95},
            {"word": "changed", "start": 0.5, "end": 0.9, "confidence": 0.90},
            {"word": "everything", "start": 1.0, "end": 1.5, "confidence": 0.88},
        ]
        filters, dur, bottom = self.animator.build_animated_filters(
            text, whisper_words=whisper_words,
        )
        assert "drawtext" in filters
        assert dur > 0
        assert bottom > 0

    def test_none_whisper_words_uses_wpm(self):
        text = "fallback to WPM"
        filters, dur, bottom = self.animator.build_animated_filters(
            text, whisper_words=None,
        )
        assert "drawtext" in filters
        # WPM timing: 3 words at 150 WPM = 1.2s + start_delay
        assert dur > 0

    def test_whisper_timing_differs_from_wpm(self):
        text = "fast slow"
        whisper_words = [
            {"word": "fast", "start": 0.0, "end": 0.1, "confidence": 0.9},
            {"word": "slow", "start": 2.0, "end": 3.0, "confidence": 0.9},
        ]
        filters_w, dur_w, _ = self.animator.build_animated_filters(
            text, whisper_words=whisper_words,
        )
        filters_f, dur_f, _ = self.animator.build_animated_filters(
            text, whisper_words=None,
        )
        # Whisper version should have different duration than WPM
        assert dur_w != pytest.approx(dur_f, abs=0.1)
```

**Step 2: Run tests to verify they fail**

Run: `cd "/Users/anarchistsid/GenLab/Content Scraper" && uv run --package content-scraper pytest tests/test_word_by_word_whisper.py -v`
Expected: FAIL with `AttributeError: 'WordByWordAnimator' object has no attribute 'calculate_word_timings_from_whisper'`

**Step 3: Implement the Whisper timing path**

Add two things to `word_by_word_animator.py`:

**3a.** New method `calculate_word_timings_from_whisper()` — add after `calculate_word_timings()` (after line 261):

```python
    def calculate_word_timings_from_whisper(
        self,
        text: str,
        whisper_words: list[dict],
        fade_duration: float | None = None,
    ) -> List[WordTiming]:
        """Create word timings from Whisper timestamps instead of WPM math.

        Each word's appear_time comes from its Whisper start timestamp.
        Highlight lasts until the next word's start (or this word's end for
        the last word). Fade transition follows the same gold->white pattern.

        Args:
            text:           The authored text to display (visual source of truth).
            whisper_words:  Aligned word list from whisper_timing.align_words():
                            [{"word": str, "start": float, "end": float, "confidence": float}]
            fade_duration:  Gold->white fade duration (default: from config).

        Returns:
            List of WordTiming with timing fields set. Pixel positions NOT set.
        """
        words = text.split()
        if not words or not whisper_words:
            return []

        fade_duration = fade_duration if fade_duration is not None else self.DEFAULT_FADE_DURATION

        timings: List[WordTiming] = []
        line = 0
        col = 0

        for i, word in enumerate(words):
            if i < len(whisper_words):
                appear = whisper_words[i]["start"]
                word_end = whisper_words[i]["end"]
            else:
                # More authored words than whisper words — extrapolate
                prev = timings[-1] if timings else None
                appear = (prev.highlight_end if prev else 0.0) + 0.1
                word_end = appear + 0.3

            # Highlight ends when next word starts (or at this word's end)
            if i < len(whisper_words) - 1:
                highlight_end = whisper_words[i + 1]["start"]
            else:
                highlight_end = word_end

            timings.append(WordTiming(
                word=word,
                index=i,
                line=line,
                col=col,
                appear_time=round(appear, 3),
                highlight_end=round(highlight_end, 3),
                fade_end=round(highlight_end + fade_duration, 3),
            ))

            col += 1
            if col >= self.MAX_WORDS_PER_LINE:
                line += 1
                col = 0

        return timings
```

**3b.** Add `whisper_words` parameter to `build_animated_filters()` — modify the method signature at line 501 and the body:

Change the signature to add `whisper_words: list[dict] | None = None`:

```python
    def build_animated_filters(
        self,
        text: str,
        text_type: str = "hook",
        wpm: Optional[int] = None,
        start_time: Optional[float] = None,
        canvas_width: int = 1080,
        canvas_height: int = 1920,
        override_y: Optional[int] = None,
        override_x: Optional[int] = None,
        override_width: Optional[int] = None,
        align_left: bool = False,
        max_font_size: Optional[int] = None,
        whisper_words: list | None = None,  # NEW: Whisper word timestamps
    ) -> Tuple[str, float, int]:
```

Then change the timing calculation block (currently lines 569-571) to branch on `whisper_words`:

```python
        # Calculate word timings — Whisper-synced or WPM fallback
        if whisper_words is not None:
            timings = self.calculate_word_timings_from_whisper(
                text, whisper_words,
            )
        else:
            timings = self.calculate_word_timings(
                text, wpm=wpm, start_time=start_time,
            )
```

**Step 4: Run tests to verify they pass**

Run: `cd "/Users/anarchistsid/GenLab/Content Scraper" && uv run --package content-scraper pytest tests/test_word_by_word_whisper.py -v`
Expected: 8 PASS

**Step 5: Run existing tests to verify no regression**

Run: `cd "/Users/anarchistsid/GenLab/Content Scraper" && uv run --package content-scraper pytest tests/ -v --tb=short 2>&1 | tail -5`
Expected: All existing tests still pass (1323+)

**Step 6: Commit**

```bash
cd "/Users/anarchistsid/GenLab/Content Scraper"
git add execution/utils/word_by_word_animator.py tests/test_word_by_word_whisper.py
git commit -m "feat(animator): add Whisper-synced timing path to WordByWordAnimator

New calculate_word_timings_from_whisper() method uses real speech timestamps.
build_animated_filters() gains optional whisper_words param — when provided,
uses Whisper timing; when None, falls back to WPM. Layout, filters, safe
zones, gold->white transitions all unchanged."
```

---

## Task 5: Update `genlab_core.media.__init__` exports

**Files:**
- Modify: `genlab-core/src/genlab_core/media/__init__.py`

**Step 1: Add the new module exports**

```python
# Add to genlab-core/src/genlab_core/media/__init__.py
from genlab_core.media.audio_probe import extract_audio_track, has_meaningful_audio
from genlab_core.media.whisper_timing import align_words, transcribe_words
```

And add to `__all__`:

```python
    "extract_audio_track",
    "has_meaningful_audio",
    "align_words",
    "transcribe_words",
```

**Step 2: Run full genlab-core test suite**

Run: `cd /Users/anarchistsid/GenLab && uv run --package genlab-core pytest genlab-core/tests/ -v --tb=short 2>&1 | tail -5`
Expected: All tests pass (695+, now including new audio_probe + whisper_timing tests)

**Step 3: Commit**

```bash
cd /Users/anarchistsid/GenLab/genlab-core
git add src/genlab_core/media/__init__.py
git commit -m "refactor(media): export audio_probe and whisper_timing from media package"
```

---

## Task 6: Add whisper sync config to per-niche YAML

**Files:**
- Modify: `ClutchWire/config/visuals.yaml`
- Modify: `SpliceReel/config/visuals.yaml`
- Modify: `FrameDrift/config/visuals.yaml`

**Step 1: No test needed — this is config**

**Step 2: Add whisper_sync block to each channel's visuals.yaml**

Append to the bottom of each file:

```yaml
# Word-by-word caption animation
animation:
  word_by_word:
    whisper_sync:
      enabled: true
      model_size: "base"
      fallback: "wpm"
      silence_threshold_db: -40
      skip_tts_when_audio: true
      min_confidence: 0.3
```

**Step 3: Commit each channel**

```bash
cd /Users/anarchistsid/GenLab/ClutchWire
git add config/visuals.yaml
git commit -m "config: add whisper_sync animation settings for ClutchWire"

cd /Users/anarchistsid/GenLab/SpliceReel
git add config/visuals.yaml
git commit -m "config: add whisper_sync animation settings for SpliceReel"

cd /Users/anarchistsid/GenLab/FrameDrift
git add config/visuals.yaml
git commit -m "config: add whisper_sync animation settings for FrameDrift"
```

---

## Task 7: Wire Whisper sync into ClutchWire visual render strategy

**Files:**
- Modify: `ClutchWire/cw_strategies/visual_render.py`
- Test: `ClutchWire/tests/test_whisper_render.py`

**Step 1: Write the failing test**

```python
# ClutchWire/tests/test_whisper_render.py
"""Tests for Whisper-synced caption wiring in ClutchWire visual render."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cw_strategies.visual_render import SportVisualRenderStrategy


class TestWhisperCaptionWiring:
    def setup_method(self):
        self.strategy = SportVisualRenderStrategy()

    def test_get_whisper_config_loads_from_yaml(self):
        self.strategy._ensure_config()
        config = self.strategy._get_whisper_config()
        assert isinstance(config, dict)
        assert "enabled" in config

    @patch("cw_strategies.visual_render.has_meaningful_audio", return_value=True)
    @patch("cw_strategies.visual_render.extract_audio_track")
    @patch("cw_strategies.visual_render.transcribe_words")
    @patch("cw_strategies.visual_render.align_words")
    def test_prepare_whisper_words_with_audio(
        self, mock_align, mock_transcribe, mock_extract, mock_has_audio,
    ):
        mock_extract.return_value = Path("/tmp/audio.wav")
        mock_transcribe.return_value = [
            {"word": "goal", "start": 0.1, "end": 0.4, "confidence": 0.9},
        ]
        mock_align.return_value = [
            {"word": "GOAL", "start": 0.1, "end": 0.4, "confidence": 0.9},
        ]

        result = self.strategy.prepare_whisper_words(
            clip_path=Path("/tmp/clip.mp4"),
            caption_text="GOAL",
        )
        assert result is not None
        assert result[0]["word"] == "GOAL"
        mock_transcribe.assert_called_once()

    @patch("cw_strategies.visual_render.has_meaningful_audio", return_value=False)
    def test_prepare_whisper_words_silent_clip_returns_none(self, mock_has_audio):
        """Silent sports clip with no TTS — returns None (WPM fallback)."""
        result = self.strategy.prepare_whisper_words(
            clip_path=Path("/tmp/silent.mp4"),
            caption_text="Big play coming",
        )
        assert result is None
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/anarchistsid/GenLab/ClutchWire && uv run --package clutchwire pytest tests/test_whisper_render.py -v`
Expected: FAIL (method doesn't exist yet)

**Step 3: Add `prepare_whisper_words()` method to `SportVisualRenderStrategy`**

Add imports at top of `ClutchWire/cw_strategies/visual_render.py`:

```python
import tempfile
from genlab_core.media.audio_probe import extract_audio_track, has_meaningful_audio
from genlab_core.media.whisper_timing import align_words, transcribe_words
```

Add methods to the class:

```python
    def _get_whisper_config(self) -> dict:
        """Get whisper_sync config from visuals.yaml."""
        self._ensure_config()
        animation = self._visuals_config.get("animation", {})
        wbw = animation.get("word_by_word", {})
        return wbw.get("whisper_sync", {"enabled": False})

    def prepare_whisper_words(
        self,
        clip_path: Path,
        caption_text: str,
    ) -> list[dict] | None:
        """Attempt Whisper transcription on clip audio for synced captions.

        Returns aligned word list for WordByWordAnimator, or None to fall back
        to WPM timing. Sports clips almost always have commentary audio.
        """
        ws_config = self._get_whisper_config()
        if not ws_config.get("enabled", False):
            return None

        if not has_meaningful_audio(
            clip_path,
            silence_threshold_db=ws_config.get("silence_threshold_db", -40),
        ):
            logger.info("[sports] No meaningful audio in %s — WPM fallback", clip_path)
            return None

        # Extract audio to temp WAV
        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "audio.wav"
            extracted = extract_audio_track(clip_path, wav_path)
            if extracted is None:
                return None

            whisper_words = transcribe_words(
                extracted,
                model_size=ws_config.get("model_size", "base"),
                min_confidence=ws_config.get("min_confidence", 0.3),
            )
            if whisper_words is None:
                return None

            return align_words(caption_text, whisper_words)
```

**Step 4: Run tests to verify they pass**

Run: `cd /Users/anarchistsid/GenLab/ClutchWire && uv run --package clutchwire pytest tests/test_whisper_render.py -v`
Expected: 3 PASS

**Step 5: Run existing ClutchWire tests**

Run: `cd /Users/anarchistsid/GenLab/ClutchWire && uv run --package clutchwire pytest tests/ -v --tb=short 2>&1 | tail -5`
Expected: All tests pass (88+)

**Step 6: Commit**

```bash
cd /Users/anarchistsid/GenLab/ClutchWire
git add cw_strategies/visual_render.py tests/test_whisper_render.py
git commit -m "feat(sports): wire Whisper-synced captions into visual render strategy

prepare_whisper_words() detects audio, extracts WAV, runs Whisper, and
aligns to caption text. Returns None on failure/silence for WPM fallback.
Config-driven via visuals.yaml whisper_sync block."
```

---

## Task 8: Wire Whisper sync into SpliceReel visual render strategy

**Files:**
- Modify: `SpliceReel/sr_strategies/visual_render.py`
- Test: `SpliceReel/tests/test_whisper_render.py`

Same pattern as Task 7. The strategy class is `MovieVisualRenderStrategy`.

**Step 1: Write the failing test**

```python
# SpliceReel/tests/test_whisper_render.py
"""Tests for Whisper-synced caption wiring in SpliceReel visual render."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sr_strategies.visual_render import MovieVisualRenderStrategy


class TestWhisperCaptionWiring:
    def setup_method(self):
        self.strategy = MovieVisualRenderStrategy()

    @patch("sr_strategies.visual_render.has_meaningful_audio", return_value=True)
    @patch("sr_strategies.visual_render.extract_audio_track")
    @patch("sr_strategies.visual_render.transcribe_words")
    @patch("sr_strategies.visual_render.align_words")
    def test_prepare_whisper_words_with_audio(
        self, mock_align, mock_transcribe, mock_extract, mock_has_audio,
    ):
        mock_extract.return_value = Path("/tmp/audio.wav")
        mock_transcribe.return_value = [
            {"word": "epic", "start": 0.1, "end": 0.4, "confidence": 0.9},
        ]
        mock_align.return_value = [
            {"word": "EPIC", "start": 0.1, "end": 0.4, "confidence": 0.9},
        ]

        result = self.strategy.prepare_whisper_words(
            clip_path=Path("/tmp/trailer.mp4"),
            caption_text="EPIC",
        )
        assert result is not None

    @patch("sr_strategies.visual_render.has_meaningful_audio", return_value=False)
    def test_silent_clip_returns_none(self, mock_has_audio):
        result = self.strategy.prepare_whisper_words(
            clip_path=Path("/tmp/silent.mp4"),
            caption_text="Coming soon",
        )
        assert result is None
```

**Step 2: Run test → FAIL**

Run: `cd /Users/anarchistsid/GenLab/SpliceReel && uv run --package splicereel pytest tests/test_whisper_render.py -v`

**Step 3: Add same `prepare_whisper_words()` to `MovieVisualRenderStrategy`**

Read the file first, then add the same imports and methods as Task 7 (adapted class name). The method body is identical — sports and movies both have source audio.

**Step 4: Run tests → PASS**

**Step 5: Run full SpliceReel suite**

Run: `cd /Users/anarchistsid/GenLab/SpliceReel && uv run --package splicereel pytest tests/ -v --tb=short 2>&1 | tail -5`
Expected: All tests pass (96+)

**Step 6: Commit**

```bash
cd /Users/anarchistsid/GenLab/SpliceReel
git add sr_strategies/visual_render.py tests/test_whisper_render.py
git commit -m "feat(movies): wire Whisper-synced captions into visual render strategy"
```

---

## Task 9: Wire Whisper sync into FrameDrift visual render strategy (with TTS path)

**Files:**
- Modify: `FrameDrift/fd_strategies/visual_render.py`
- Test: `FrameDrift/tests/test_whisper_render.py`

FrameDrift is different — anime clips may be silent. When silent, generate TTS first, then Whisper on the TTS output (Path B from design doc).

**Step 1: Write the failing test**

```python
# FrameDrift/tests/test_whisper_render.py
"""Tests for Whisper-synced caption wiring in FrameDrift visual render.

FrameDrift has both Path A (audio clips) and Path B (silent → TTS → Whisper).
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fd_strategies.visual_render import FashionVisualRenderStrategy


class TestWhisperCaptionWiring:
    def setup_method(self):
        self.strategy = FashionVisualRenderStrategy()

    @patch("fd_strategies.visual_render.has_meaningful_audio", return_value=True)
    @patch("fd_strategies.visual_render.extract_audio_track")
    @patch("fd_strategies.visual_render.transcribe_words")
    @patch("fd_strategies.visual_render.align_words")
    def test_path_a_audio_clip(
        self, mock_align, mock_transcribe, mock_extract, mock_has_audio,
    ):
        mock_extract.return_value = Path("/tmp/audio.wav")
        mock_transcribe.return_value = [
            {"word": "sakura", "start": 0.1, "end": 0.5, "confidence": 0.9},
        ]
        mock_align.return_value = [
            {"word": "Sakura", "start": 0.1, "end": 0.5, "confidence": 0.9},
        ]
        result = self.strategy.prepare_whisper_words(
            clip_path=Path("/tmp/anime.mp4"),
            caption_text="Sakura",
        )
        assert result is not None

    @patch("fd_strategies.visual_render.has_meaningful_audio", return_value=False)
    @patch("fd_strategies.visual_render.TTSCascade")
    @patch("fd_strategies.visual_render.transcribe_words")
    @patch("fd_strategies.visual_render.align_words")
    def test_path_b_silent_generates_tts(
        self, mock_align, mock_transcribe, mock_tts_cls, mock_has_audio,
    ):
        """Silent clip triggers TTS → Whisper → alignment."""
        mock_tts = MagicMock()
        mock_tts.synthesize.return_value = MagicMock(success=True, output_path="/tmp/tts.wav")
        mock_tts_cls.return_value = mock_tts

        mock_transcribe.return_value = [
            {"word": "Beautiful", "start": 0.0, "end": 0.4, "confidence": 0.9},
            {"word": "animation", "start": 0.5, "end": 1.0, "confidence": 0.9},
        ]
        mock_align.return_value = [
            {"word": "Beautiful", "start": 0.0, "end": 0.4, "confidence": 0.9},
            {"word": "animation", "start": 0.5, "end": 1.0, "confidence": 0.9},
        ]

        result = self.strategy.prepare_whisper_words(
            clip_path=Path("/tmp/silent_sakuga.mp4"),
            caption_text="Beautiful animation",
        )
        assert result is not None
        mock_tts.synthesize.assert_called_once()

    @patch("fd_strategies.visual_render.has_meaningful_audio", return_value=False)
    @patch("fd_strategies.visual_render.TTSCascade")
    def test_path_b_tts_failure_returns_none(self, mock_tts_cls, mock_has_audio):
        mock_tts = MagicMock()
        mock_tts.synthesize.return_value = MagicMock(success=False)
        mock_tts_cls.return_value = mock_tts

        result = self.strategy.prepare_whisper_words(
            clip_path=Path("/tmp/silent.mp4"),
            caption_text="Fallback text",
        )
        assert result is None
```

**Step 2: Run test → FAIL**

**Step 3: Add `prepare_whisper_words()` with Path B to `FashionVisualRenderStrategy`**

This version includes the TTS fallback for silent clips:

```python
import tempfile
from genlab_core.media.audio_probe import extract_audio_track, has_meaningful_audio
from genlab_core.media.whisper_timing import align_words, transcribe_words
from genlab_core.tts.cascade import TTSCascade
```

```python
    def prepare_whisper_words(
        self,
        clip_path: Path,
        caption_text: str,
    ) -> list[dict] | None:
        """Attempt Whisper sync — with TTS fallback for silent clips (Path B).

        Anime clips are mixed: dubbed/subbed clips have dialogue (Path A),
        silent sakuga clips need TTS voiceover first (Path B).
        """
        ws_config = self._get_whisper_config()
        if not ws_config.get("enabled", False):
            return None

        has_audio = has_meaningful_audio(
            clip_path,
            silence_threshold_db=ws_config.get("silence_threshold_db", -40),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            if has_audio:
                # Path A: Whisper on existing audio
                wav_path = tmpdir_path / "audio.wav"
                extracted = extract_audio_track(clip_path, wav_path)
                if extracted is None:
                    return None
                audio_for_whisper = extracted
            else:
                # Path B: Generate TTS → Whisper on TTS
                if not ws_config.get("skip_tts_when_audio", True):
                    return None

                tts_path = tmpdir_path / "tts_voiceover.wav"
                try:
                    tts = TTSCascade()
                    result = tts.synthesize(caption_text, tts_path)
                    if not result.success:
                        logger.info("[anime] TTS failed — WPM fallback")
                        return None
                    audio_for_whisper = Path(result.output_path)
                except Exception as e:
                    logger.warning("[anime] TTS error: %s", e)
                    return None

            whisper_words = transcribe_words(
                audio_for_whisper,
                model_size=ws_config.get("model_size", "base"),
                min_confidence=ws_config.get("min_confidence", 0.3),
            )
            if whisper_words is None:
                return None

            return align_words(caption_text, whisper_words)
```

**Step 4: Run tests → PASS**

**Step 5: Run full FrameDrift suite**

Run: `cd /Users/anarchistsid/GenLab/FrameDrift && uv run --package framedrift pytest tests/ -v --tb=short 2>&1 | tail -5`
Expected: All tests pass (100+)

**Step 6: Commit**

```bash
cd /Users/anarchistsid/GenLab/FrameDrift
git add fd_strategies/visual_render.py tests/test_whisper_render.py
git commit -m "feat(anime): wire Whisper-synced captions with TTS fallback for silent clips

Path A: existing audio → Whisper. Path B: silent clip → TTS → Whisper.
Uses genlab_core.tts.TTSCascade for voiceover generation on silent clips."
```

---

## Task 10: Refactor CriticalRush to use shared `whisper_timing` module

**Files:**
- Modify: `CriticalRush/niches/gaming/stages/render_text_overlays.py`
- Test: `CriticalRush/tests/gaming/test_whisper_shared.py`

CriticalRush already has its own Whisper infrastructure (`caption_generator.py`). This task adds an optional path that uses the shared module while preserving the existing ASS subtitle pipeline.

**Step 1: Write the failing test**

```python
# CriticalRush/tests/gaming/test_whisper_shared.py
"""Test CriticalRush can use shared whisper_timing module."""
from unittest.mock import patch

from genlab_core.media.whisper_timing import _flatten_whisper_words


class TestFlattenCRSegments:
    """Verify _flatten_whisper_words handles CriticalRush segment format."""

    def test_cr_segment_format(self):
        """CriticalRush caption_generator returns segments with 'probability' key."""
        segments = [
            {
                "start": 0.0, "end": 1.5, "text": "clutch play",
                "words": [
                    {"word": "clutch", "start": 0.0, "end": 0.6, "probability": 0.95},
                    {"word": "play", "start": 0.7, "end": 1.2, "probability": 0.92},
                ],
            },
        ]
        result = _flatten_whisper_words(segments)
        assert len(result) == 2
        assert result[0]["word"] == "clutch"
        assert result[0]["confidence"] == 0.95  # mapped from probability

    def test_empty_segments(self):
        assert _flatten_whisper_words([]) == []
```

**Step 2: Run tests → verify they pass immediately** (module already exists from Task 2)

Run: `cd /Users/anarchistsid/GenLab/CriticalRush && uv run --package criticalrush pytest tests/gaming/test_whisper_shared.py -v`
Expected: 2 PASS (no code changes needed — `_flatten_whisper_words` already handles this format)

**Step 3: Run full CriticalRush suite**

Run: `cd /Users/anarchistsid/GenLab/CriticalRush && uv run --package criticalrush pytest tests/ -v --tb=short 2>&1 | tail -5`
Expected: All tests pass (494+)

**Step 4: Commit**

```bash
cd /Users/anarchistsid/GenLab/CriticalRush
git add tests/gaming/test_whisper_shared.py
git commit -m "test(gaming): verify shared whisper_timing handles CR segment format

Confirms _flatten_whisper_words correctly maps CR's 'probability' key to
the shared 'confidence' field. No code changes needed — compatibility
already built into shared module."
```

---

## Task 11: Run full regression across all repos

**Files:** None (verification only)

**Step 1: Run genlab-core tests**

Run: `cd /Users/anarchistsid/GenLab && uv run --package genlab-core pytest genlab-core/tests/ --tb=short 2>&1 | tail -3`
Expected: 700+ passed (695 existing + new audio_probe + whisper_timing tests)

**Step 2: Run Content Scraper tests**

Run: `cd "/Users/anarchistsid/GenLab/Content Scraper" && uv run --package content-scraper pytest tests/ --tb=short 2>&1 | tail -3`
Expected: 1325+ passed

**Step 3: Run CriticalRush tests**

Run: `cd /Users/anarchistsid/GenLab/CriticalRush && uv run --package criticalrush pytest tests/ --tb=short 2>&1 | tail -3`
Expected: 496+ passed

**Step 4: Run ClutchWire tests**

Run: `cd /Users/anarchistsid/GenLab/ClutchWire && uv run --package clutchwire pytest tests/ --tb=short 2>&1 | tail -3`
Expected: 91+ passed

**Step 5: Run SpliceReel tests**

Run: `cd /Users/anarchistsid/GenLab/SpliceReel && uv run --package splicereel pytest tests/ --tb=short 2>&1 | tail -3`
Expected: 98+ passed

**Step 6: Run FrameDrift tests**

Run: `cd /Users/anarchistsid/GenLab/FrameDrift && uv run --package framedrift pytest tests/ --tb=short 2>&1 | tail -3`
Expected: 103+ passed

---

## Summary

| Task | Repo | What | New Tests |
|------|------|------|-----------|
| 1 | genlab-core | `audio_probe.py` — audio detection + extraction | 7 |
| 2 | genlab-core | `whisper_timing.py` — word timestamps + alignment | 8 |
| 3 | genlab-core | pyproject.toml whisper optional dep | 0 |
| 4 | Content Scraper | WordByWordAnimator Whisper path | 8 |
| 5 | genlab-core | `__init__.py` exports | 0 |
| 6 | CW/SR/FD | Config YAML whisper_sync block | 0 |
| 7 | ClutchWire | `prepare_whisper_words()` in visual render | 3 |
| 8 | SpliceReel | `prepare_whisper_words()` in visual render | 2 |
| 9 | FrameDrift | `prepare_whisper_words()` + TTS Path B | 3 |
| 10 | CriticalRush | Shared module compatibility test | 2 |
| 11 | All | Full regression | 0 |
| **Total** | | | **33** |

**Dependencies:** Tasks 1-3 must complete before 4-5. Task 5 before 6-10. Task 11 is final.
