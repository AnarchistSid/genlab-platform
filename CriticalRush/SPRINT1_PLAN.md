# Sprint 1: Video Quality Fixes (F-01, F-02, F-03, F-08)

## Questions Answered

### 1. Which files will be touched?

| File | Action | Reason |
|------|--------|--------|
| `niches/gaming/media/ffmpeg_gaming.py` | MODIFY | Fix encoding in `normalize_clip`, `concat_with_transitions`, `add_text_overlay` |
| `niches/gaming/stages/render_gaming_video.py` | MODIFY | Fix encoding in `_render_with_ffmpeg` |
| `niches/gaming/tools/clip_sourcer.py` | MODIFY | Fix encoding in `_normalize_to_h264` |
| `niches/gaming/config/platform_specs.yaml` | MODIFY | Add deprecation notice (F-08) |
| `tests/gaming/test_encoding_quality.py` | CREATE | Verify bt709 + yuv420p + CRF in all encoding calls |

### 2. What genlab-core imports will be added?

**`ffmpeg_gaming.py`** — no new imports needed. Define `_ENCODE_QUALITY_ARGS` constant inline, aligned with genlab-core's `FINAL_VIDEO_PARAMS` values (CRF 17, preset slow, yuv420p, bt709). `FINAL_VIDEO_PARAMS` is a `list[str]` (not a dict), so values are hardcoded to match.

**`render_gaming_video.py`** — no new imports. Replace hardcoded CRF 23 / preset fast with CRF 17 / preset slow / yuv420p / bt709 inline.

**`clip_sourcer.py`** — no new genlab-core imports. Apply bt709 + yuv420p + CRF 18 directly (pre-scoring codec conversion; per HARD STOP #5, this runs before VideoCompositor is available).

### 3. What is the encoding parameter source for each call site?

| Call site | Source | Rationale |
|-----------|--------|-----------|
| `ffmpeg_gaming.normalize_clip()` | `FINAL_VIDEO_PARAMS` via `_ENCODE_QUALITY_ARGS` | Intermediate compilation step — high quality baseline prevents chain degradation |
| `ffmpeg_gaming.concat_with_transitions()` | `FINAL_VIDEO_PARAMS` via `_ENCODE_QUALITY_ARGS` | Compilation concat — same baseline |
| `ffmpeg_gaming.add_text_overlay()` | `FINAL_VIDEO_PARAMS` via `_ENCODE_QUALITY_ARGS` | Final compilation encode — uses same baseline for consistency |
| `render_gaming_video._render_with_ffmpeg()` | `FINAL_VIDEO_PARAMS` directly | Legacy fallback — uses same baseline |
| `clip_sourcer._normalize_to_h264()` | Hardcoded CRF 18 + bt709 + yuv420p | Pre-scoring codec normalization — not final output, but still needs colorspace correctness |

**Why `FINAL_VIDEO_PARAMS` and NOT `PLATFORM_SPECS`?**
- The compilation pipeline produces ONE video shared across all platforms
- `PLATFORM_SPECS` are per-platform (different CRF/codec per platform) — they belong at the publish-time transcode step
- `FINAL_VIDEO_PARAMS` (CRF 17, preset slow, bt709, yuv420p) is the correct high-quality baseline for intermediate/final compilation encoding
- Per-platform transcoding from the master is a separate concern (deferred)

**`_ENCODE_QUALITY_ARGS` constant** (defined in `ffmpeg_gaming.py`):
```python
_ENCODE_QUALITY_ARGS = [
    "-crf", FINAL_VIDEO_PARAMS["-crf"],
    "-preset", FINAL_VIDEO_PARAMS["-preset"],
    "-pix_fmt", FINAL_VIDEO_PARAMS["-pix_fmt"],
    "-color_primaries", FINAL_VIDEO_PARAMS["-color_primaries"],
    "-color_trc", FINAL_VIDEO_PARAMS["-color_trc"],
    "-colorspace", FINAL_VIDEO_PARAMS["-colorspace"],
]
```
This extracts quality + colorspace params while leaving codec (`-c:v`), fps (`-r`), and audio settings to each function.

### 4. How will tests verify the changes?

New test file `tests/gaming/test_encoding_quality.py` with:

1. **`test_normalize_clip_uses_quality_args`** — mock `subprocess.run`, call `normalize_clip()`, assert FFmpeg command contains `-crf 17 -pix_fmt yuv420p -color_primaries bt709 -color_trc bt709 -colorspace bt709`
2. **`test_concat_with_transitions_uses_quality_args`** — same pattern for `concat_with_transitions()`
3. **`test_add_text_overlay_uses_quality_args`** — same pattern for `add_text_overlay()`
4. **`test_render_with_ffmpeg_uses_quality_args`** — mock `subprocess.run`, call `_render_with_ffmpeg()`, assert command has correct encoding args
5. **`test_normalize_to_h264_has_bt709_and_yuv420p`** — mock subprocess in `clip_sourcer._normalize_to_h264()`, assert bt709 + yuv420p present
6. **`test_no_crf_23_anywhere`** — grep-style test scanning all 3 source files for literal `"23"` adjacent to `crf` — fails if any remain

### 5. What is the rollback strategy?

All changes are in 3 source files + 1 YAML + 1 new test file. Rollback = `git revert <commit>`.

No database, API, or config changes. No schema changes. No public interface changes.

Encoding params are the only change — if video quality regresses, revert and the pipeline returns to its prior (lower quality) state.

### 6. Does this change any public interface?

**No.** All functions retain the same signatures:
- `normalize_clip(input_path, output_path, start, end, target_width, target_height, target_fps, mode, timeout) -> bool`
- `concat_with_transitions(clip_paths, output_path, transitions, temp_dir, timeout) -> bool`
- `add_text_overlay(input_path, output_path, overlays, timeout) -> bool`
- `_render_with_ffmpeg(clip_path, hook, output_path, story, spec) -> Optional[str]` (private method)
- `_normalize_to_h264(file_path) -> str` (module-level private)

Only the internal FFmpeg command construction changes. Callers are unaffected.

---

## Implementation Sequence

1. Add `_ENCODE_QUALITY_ARGS` to `ffmpeg_gaming.py` (import + constant)
2. Fix `normalize_clip()` — replace hardcoded args with `_ENCODE_QUALITY_ARGS`
3. Fix `concat_with_transitions()` — both audio and no-audio branches
4. Fix `add_text_overlay()` — replace hardcoded args
5. Fix `render_gaming_video._render_with_ffmpeg()` — replace CRF 23 + add bt709/yuv420p
6. Fix `clip_sourcer._normalize_to_h264()` — CRF 18 + bt709 + yuv420p
7. Add deprecation header to `platform_specs.yaml` (F-08)
8. Write `tests/gaming/test_encoding_quality.py`
9. Run all CriticalRush tests
10. Smoke test with `ffmpeg -version` sanity check
