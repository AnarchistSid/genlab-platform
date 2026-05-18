# Whisper-Synced Animated Captions — Design Document

**Date:** 2026-03-13
**Scope:** CriticalRush, ClutchWire, SpliceReel, FrameDrift (BB excluded)
**Approach:** A — Whisper timestamps into existing WordByWordAnimator

## Problem

Current word-by-word caption animation uses fixed WPM cadence (150 WPM = 0.4s per word).
Words appear at mathematically uniform intervals regardless of actual speech pace. This
produces captions that drift from audio — pauses, emphasis, and fast segments are ignored.

Competitors (MrBeast, Hormozi, evolving.ai) use speech-synced captions where each word
appears exactly when spoken. This drives engagement via the "karaoke effect" — viewers
read along with audio, increasing watch time.

## Solution

Run Whisper speech-to-text on clip audio to get word-level timestamps. Feed real timestamps
into the existing animation pipeline instead of WPM math. Two paths based on source audio:

- **Path A (clip has audio):** Whisper on existing audio → captions sync to actual speech.
  Skip TTS. Applies to: sports broadcasts, movie trailers, gaming commentary, anime dialogue.
- **Path B (clip is silent):** Generate TTS voiceover → Whisper on TTS → captions sync to
  generated narration. Merge TTS into final video. Applies to: stock footage, silent sakuga.

Both paths converge at: Whisper word timestamps → WordByWordAnimator → FFmpeg drawtext filters.

## Architecture

### New Modules (genlab-core)

**`genlab_core.media.whisper_timing`**
- `transcribe_words(audio_path, model_size="base") -> list[dict] | None`
- Returns `[{"word": "AI", "start": 0.1, "end": 0.4, "confidence": 0.95}, ...]`
- Lazy singleton model loading (base model ~150MB, downloads on first use)
- Optional dependency: `whisper-timestamped` in `[project.optional-dependencies]`
- Returns None if unavailable → caller falls back to WPM

**`genlab_core.media.audio_probe`**
- `has_meaningful_audio(clip_path, silence_threshold_db=-40) -> bool`
- FFprobe: check for audio stream + volumedetect silence gate
- `extract_audio_track(clip_path, output_path) -> Path | None`
- Uses existing ffmpeg_utils infrastructure, no new dependencies

### WordByWordAnimator Enhancement

New method on existing class (BlackboxBrief/execution/utils/word_by_word_animator.py):

```python
def calculate_word_timings_from_whisper(
    self,
    text: str,
    whisper_words: list[dict],
    fade_duration: float | None = None,
) -> list[WordTiming]:
```

Updated `build_animated_filters()` signature:

```python
def build_animated_filters(
    self,
    text: str,
    ...,
    whisper_words: list[dict] | None = None,  # NEW
) -> tuple[str, float, int]:
```

When `whisper_words` is provided, uses real timestamps. When None, falls back to WPM.
Layout, filter generation, safe zones, gold→white transitions — all unchanged.

### Word Alignment Strategy

Whisper transcription may differ from authored text. Alignment:
1. Split both original text and Whisper output into word lists
2. Two-pointer walk, match by normalized form (lowercase, strip punctuation)
3. Matched words: use Whisper start/end timestamps
4. Unmatched words: interpolate timestamps from neighbors
5. Catastrophic mismatch (>30%): fall back to WPM entirely

Visual text always shows the authored version — Whisper is only used for timing.

### Pipeline Flow

```
Source clip
    │
    ├─ has_meaningful_audio() == True
    │   ├─ Skip TTS generation
    │   ├─ extract_audio_track() → temp WAV
    │   ├─ transcribe_words() → word timestamps
    │   └─ Render with synced captions (existing audio preserved)
    │
    └─ has_meaningful_audio() == False
        ├─ Generate TTS voiceover (TTSCascade)
        ├─ transcribe_words() on TTS output → word timestamps
        ├─ Render with synced captions
        └─ Merge TTS audio into video
```

### Per-Channel Wiring

**CriticalRush (Gaming):** Already has Whisper + ASS captions via `captions.yaml`.
Refactor to use shared `whisper_timing.py` module. Existing ASS output preserved.
Stage ordering: GenerateGamingAudio → RenderTextOverlays (sequential, not parallel).

**ClutchWire (Sports):** New pipeline stage or integration into visual render strategy.
Source clips are broadcast highlights — almost always have commentary audio.
Whisper on existing audio. No TTS needed for most clips.

**SpliceReel (Movies):** Same pattern. Trailers and clips have dialogue/narration.
Whisper on existing audio. No TTS for most clips.

**FrameDrift (Anime):** Mixed — some clips have dialogue (subbed/dubbed), some are
silent sakuga. Audio probe detects per-clip. Path A for dialogue clips, Path B for silent.

**BB/BlackboxBrief:** Excluded from this sprint. No changes.

## Configuration

New keys in per-niche config (or shared instagram_specs.yaml):

```yaml
animation:
  word_by_word:
    whisper_sync:
      enabled: true
      model_size: "base"       # tiny/base/small/medium
      fallback: "wpm"          # fallback when whisper fails
      silence_threshold_db: -40
      skip_tts_when_audio: true
      min_confidence: 0.3
```

Env var toggle: `GENLAB_WHISPER_SYNC=true|false` (default: true when installed).

## Safety Guarantees

**Untouched:**
- Sandwich layout (12% top / 18% bottom bars)
- Logo position and sizing
- Hook text in top bar
- Canvas dimensions (1080×1920)
- Encoding params (H.264, CRF 18, yuv420p, BT.709, 48kHz AAC)
- Safe zones (top 250px, bottom 320px, left 60px, right 120px)
- Platform-specific specs (IG, YT, TikTok, FB, X, Threads)
- validate_videos.py runs after render (safety net)

**Changed (scoped):**
- Timing source for word animation (WPM → Whisper, with WPM fallback)
- Audio detection before render (new, additive)
- TTS generation for silent clips only (new, conditional)

## Dependencies

- `whisper-timestamped>=1.15` — optional extra in genlab-core pyproject.toml
- Pulls torch (~2GB). Only needed on render machines.
- `base` model (~150MB) auto-downloads to `~/.cache/whisper/`
- CI/tests: mock transcription, no torch required

## Testing

| Test | Location | Requires torch |
|------|----------|---------------|
| whisper_timing.py unit tests | genlab-core/tests/media/ | No (mocked) |
| audio_probe.py unit tests | genlab-core/tests/media/ | No (ffprobe only) |
| word alignment edge cases | genlab-core/tests/media/ | No (fixture data) |
| WordByWordAnimator whisper path | CS/tests/ or shared | No (fixture data) |
| Integration: full render | Manual | Yes |

## Rollout

1. **Phase 1 (this sprint):** Shared modules (whisper_timing, audio_probe) in genlab-core.
   WordByWordAnimator enhancement. Wire into CW/SR/FD visual render strategies. Feature-flagged.
2. **Phase 2:** Align CriticalRush to use shared whisper_timing module (refactor, not rewrite).
3. **Phase 3:** A/B test synced vs WPM captions, measure engagement lift.
