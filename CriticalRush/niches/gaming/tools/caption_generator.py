"""Caption generation via faster-whisper ASR + SRT/ASS formatting.

Uses faster-whisper (CTranslate2 backend) for 4-8x faster transcription
with lower memory usage compared to openai-whisper. Lazy-imported with
graceful fallback when not installed.

Key differences from openai-whisper:
  - model.transcribe() returns a GENERATOR for segments, not a list.
    Always iterate fully before accessing results.
  - Word objects are dataclasses: word.word, word.start, word.end, word.probability
    (not dicts with string keys).
  - word.word includes leading whitespace — call .strip() when building output.
  - Silero VAD filtering (vad_filter=True) skips silent sections automatically.

Transplanted from Gaming Clips with import updates (setup_logging and
CONFIG_DIR replaced with standard logging and Path-based config loading).
"""

import logging
import subprocess
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

NICHE_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG: dict = {
    "model": {
        "default_size": "base",
        "device": "cpu",
        "compute_type": "int8",
        "size_by_duration": [],
    },
    "transcription": {
        "vad_filter": True,
        "min_word_probability": 0.5,
        "word_timestamps": True,
        "language": None,
    },
    "subtitle_style": {
        "subtitle_format": "ass",
        "font_name": "Montserrat ExtraBold",
        "font_size": 72,
        "fallback_font": "Impact",
        "highlight_color": "&H0000FFFF",
        "normal_color": "&H00FFFFFF",
        "outline_color": "&H00000000",
        "back_color": "&H80000000",
        "outline_width": 4,
        "shadow_depth": 2,
        "max_words_per_group": 5,
        "margin_bottom": 350,
        "uppercase": True,
        "min_word_confidence": 0.5,
        "alignment": 2,
        "play_res_x": 1080,
        "play_res_y": 1920,
    },
}


def load_captions_config(config_path: Path | None = None) -> dict:
    """Load captions config from YAML, falling back to defaults."""
    if config_path is None:
        config_path = NICHE_ROOT / "config" / "captions.yaml"
    config_path = Path(config_path)

    if not config_path.exists():
        logger.debug("Captions config not found at %s, using defaults", config_path)
        return _DEFAULT_CONFIG

    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (yaml.YAMLError, OSError) as exc:
        logger.warning("Failed to load captions config: %s, using defaults", exc)
        return _DEFAULT_CONFIG

    if not isinstance(data, dict):
        return _DEFAULT_CONFIG

    merged = {
        "model": {**_DEFAULT_CONFIG["model"], **data.get("model", {})},
        "transcription": {**_DEFAULT_CONFIG["transcription"], **data.get("transcription", {})},
        "subtitle_style": {**_DEFAULT_CONFIG["subtitle_style"], **data.get("subtitle_style", {})},
    }
    return merged


def select_model_size(duration_seconds: float | None, config: dict | None = None) -> str:
    """Select model size based on audio duration using config heuristic."""
    if config is None:
        config = load_captions_config()

    model_config = config.get("model", {})
    default_size = model_config.get("default_size", "base")

    if duration_seconds is None:
        return default_size

    rules = model_config.get("size_by_duration", [])
    if not isinstance(rules, list):
        return default_size

    for rule in rules:
        if not isinstance(rule, dict):
            continue
        max_sec = rule.get("max_seconds")
        model_size = rule.get("model_size")
        if max_sec is None:
            return model_size or default_size
        try:
            if duration_seconds <= float(max_sec):
                return model_size or default_size
        except (TypeError, ValueError):
            continue

    return default_size


# ---------------------------------------------------------------------------
# Transcription (faster-whisper backend)
# ---------------------------------------------------------------------------

def transcribe(
    audio_path: str,
    model_size: str | None = None,
    duration_seconds: float | None = None,
    config_path: Path | None = None,
) -> dict | None:
    """Transcribe audio using faster-whisper via shared model cache.

    Returns a dict with segments, language, language_probability.
    Returns None on failure or if faster-whisper is not installed.
    """
    try:
        from genlab_core.media.whisper_timing import get_model
    except ImportError:
        logger.warning("genlab_core.media.whisper_timing not available — captions unavailable")
        return None

    config = load_captions_config(config_path)
    model_cfg = config.get("model", {})
    tx_cfg = config.get("transcription", {})

    if model_size is None:
        model_size = select_model_size(duration_seconds, config)

    device = model_cfg.get("device", "cpu")
    compute_type = model_cfg.get("compute_type", "int8")

    try:
        model = get_model(model_size, device=device, compute_type=compute_type)

        segments_gen, info = model.transcribe(
            audio_path,
            word_timestamps=tx_cfg.get("word_timestamps", True),
            vad_filter=tx_cfg.get("vad_filter", True),
            language=tx_cfg.get("language"),
        )

        min_prob = float(tx_cfg.get("min_word_probability", 0.0))
        segments = _normalize_segments(segments_gen, min_prob)

        result = {
            "segments": segments,
            "language": info.language,
            "language_probability": info.language_probability,
        }

        logger.info(
            "Transcribed %s: %d segments, language=%s (%.0f%%), model=%s",
            audio_path,
            len(segments),
            info.language,
            info.language_probability * 100,
            model_size,
        )
        return result

    except ImportError:
        logger.warning("faster-whisper not installed — captions unavailable")
        return None
    except Exception as e:
        logger.warning("faster-whisper transcription failed: %s", e)
        return None


def _normalize_segments(segments_gen, min_word_probability: float = 0.0) -> list[dict]:
    """Convert faster-whisper segment generator to list of dicts."""
    segments: list[dict] = []

    for seg in segments_gen:
        words: list[dict] = []
        if seg.words:
            for w in seg.words:
                prob = getattr(w, "probability", 1.0)
                if prob < min_word_probability:
                    continue
                words.append({
                    "word": w.word.strip(),
                    "start": w.start,
                    "end": w.end,
                    "probability": prob,
                })

        segments.append({
            "start": seg.start,
            "end": seg.end,
            "text": seg.text.strip() if seg.text else "",
            "words": words,
        })

    return segments


# ---------------------------------------------------------------------------
# SRT formatting
# ---------------------------------------------------------------------------

def format_srt_timestamp(seconds: float) -> str:
    """Convert seconds to SRT timestamp format: HH:MM:SS,mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def generate_srt(
    transcription: dict | None,
    output_path: str,
) -> bool:
    """Convert transcription to SRT subtitle file. Returns True on success."""
    if not transcription or not transcription.get("segments"):
        return False

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            for i, seg in enumerate(transcription["segments"], 1):
                start = format_srt_timestamp(seg["start"])
                end = format_srt_timestamp(seg["end"])
                text = seg.get("text", "").strip()
                if text:
                    f.write(f"{i}\n{start} --> {end}\n{text}\n\n")
        return True
    except Exception as e:
        logger.warning("SRT generation failed: %s", e)
        return False


def burn_captions(
    video_path: str,
    subtitle_path: str,
    output_path: str,
) -> bool:
    """Burn subtitles into video via FFmpeg.

    Detects ASS vs SRT by file extension. Returns True on success.
    """
    escaped = subtitle_path.replace("\\", "\\\\").replace("'", "'\\''")

    if subtitle_path.endswith(".ass"):
        vf = f"ass='{escaped}'"
    else:
        vf = f"subtitles='{escaped}'"

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", vf,
        "-c:a", "copy",
        output_path,
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=300,
        )
        if proc.returncode != 0:
            logger.warning(
                "FFmpeg subtitle burn failed (rc=%d): %s",
                proc.returncode,
                proc.stderr.decode(errors="replace")[:500],
            )
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.warning("FFmpeg subtitle burn timed out (300s)")
        return False
    except Exception as e:
        logger.warning("FFmpeg subtitle burn error: %s", e)
        return False
