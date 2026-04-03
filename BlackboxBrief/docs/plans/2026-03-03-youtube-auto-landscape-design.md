# YouTube Auto-Landscape Conversion — Design Doc

**Date:** 2026-03-03
**Status:** Approved

---

## Problem

When a video exceeds 180s (YouTube Shorts limit), it should upload as a regular
YouTube video in landscape (16:9). The render pipeline already produces a
`_landscape.mp4` alongside the portrait `_reel.mp4`, and `publish_youtube.py`
already looks for it via `_find_landscape_mp4()`.

However, if the landscape render fails silently (non-fatal at render time) or the
file is missing from `visual_paths`, the system falls back to uploading the
portrait file as a regular YouTube video — resulting in a 9:16 video with ugly
black sidebars on desktop YouTube. Unlike Twitter/Facebook, the YouTube regular
video path has **no auto-render fallback**.

## Solution: Approach B — Smarter fallback in publish_youtube.py

Add an auto-render landscape fallback to `publish_youtube.py`'s regular video
path. Reuse the existing `_auto_render_landscape()` function from
`publish_all_platforms.py` (already proven for Twitter/Facebook).

### Decision flow (>180s videos)

```
duration > 180s → regular video path
  ├─ _find_landscape_mp4() in visual_paths
  │   └─ Found & exists? → Upload landscape ✅
  ├─ _auto_render_landscape(portrait_path)
  │   └─ Rendered & valid dimensions? → Upload landscape ✅
  └─ Final fallback → Upload portrait + log WARNING ⚠️
```

### Shorts (≤180s) — unchanged

Portrait 9:16, no landscape conversion, `#Shorts` in title/tags.

### Quality preservation

The existing `render_clean_landscape_from_source()` already uses the highest
practical quality settings for YouTube:

| Setting | Value | Why |
|---------|-------|-----|
| CRF | 17 | Visually lossless; YouTube recommends 16-18 |
| Profile | H.264 High | Max quality YouTube accepts for 1080p |
| Preset | slow | Better compression without quality loss |
| Color space | bt709 | Broadcast standard |
| Audio | AAC 256k @ 48kHz | YouTube's recommended audio |
| Resolution | 1920×1080 | Standard HD |
| Branding | None | Clean video, no overlays |

YouTube re-encodes all uploads regardless, so going above CRF 17 increases
file size without visible improvement.

## Files changed

| File | Change |
|------|--------|
| `execution/publish_youtube.py` | Import `_auto_render_landscape` + `_is_landscape_dimensions`; add auto-render fallback in regular video path |
| `tests/test_publish_youtube.py` | 3 new tests: auto-render triggered, auto-render fails gracefully, landscape found directly |

## What stays the same

- Shorts (≤180s) → always portrait 9:16
- Render pipeline → still produces both portrait + landscape
- Instagram → unchanged (portrait only)
- Twitter/Facebook → unchanged (existing auto-render)
- Config → no publishing.yaml changes
