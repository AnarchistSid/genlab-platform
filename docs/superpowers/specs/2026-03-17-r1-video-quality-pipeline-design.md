# R1: Video Quality Pipeline

**Goal**: Enable VMAF gate + per-platform transcode tree. Use the existing MASTER_SPEC/PLATFORM_SPECS from ffmpeg.py (currently dead code).
**Effort**: ~3h

## Problem

`video_compositor.py` hardcodes `_ENCODE_ARGS` with libx264 CRF18 for all platforms. Meanwhile, `ffmpeg.py` already defines `MASTER_SPEC` (FFV1 lossless) and `PLATFORM_SPECS` (H.265 for YT, H.264 CRF15 for IG/TikTok, etc.) — but these are never imported or used. VMAF gate exists (194 LOC) but `run_vmaf=False` everywhere.

## Changes

### 1. Replace `_ENCODE_ARGS` in video_compositor.py with PLATFORM_SPECS

Import from `ffmpeg.py` and use platform-specific encode args:

```python
from genlab_core.media.ffmpeg import PLATFORM_SPECS, Platform

# Replace hardcoded _ENCODE_ARGS with:
def _get_encode_args(platform: str = "instagram") -> list[str]:
    spec = PLATFORM_SPECS.get(Platform(platform), PLATFORM_SPECS[Platform.INSTAGRAM])
    return spec.build_ffmpeg_args()
```

### 2. Enable VMAF gate in ValidateVideos stage

In `genlab-core/src/genlab_core/pipeline/stages/validate_videos.py`, set `run_vmaf=True` by default. On VMAF < 85, re-encode at CRF-3 (down to CRF 12 minimum), then reject if still failing.

### 3. Add platform_encode_specs.yaml for business-tunable overrides

```yaml
youtube:
  codec: libx265
  crf: 18
  preset: medium
instagram:
  codec: libx264
  crf: 15
  preset: slow
```

## Files

| File | Change |
|---|---|
| `genlab-core/src/genlab_core/media/video_compositor.py` | Replace _ENCODE_ARGS with PLATFORM_SPECS import |
| `genlab-core/src/genlab_core/pipeline/stages/validate_videos.py` | Enable VMAF, add re-encode logic |
| `genlab-core/config/platform_encode_specs.yaml` | NEW — overridable encode specs |
| `genlab-core/tests/media/test_video_quality_pipeline.py` | NEW — tests |
