# YouTube Auto-Landscape Conversion — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** When videos >180s are uploaded to YouTube as regular videos, auto-convert to landscape (16:9) if the pre-rendered landscape file is missing.

**Architecture:** Reuse the existing `_auto_render_landscape()` from `publish_all_platforms.py` (proven for Twitter/Facebook) as a fallback inside `publish_youtube.py`'s regular video path. The render uses `render_clean_landscape_from_source()` which produces clean 1920×1080 H.264 High profile CRF 17 output — highest practical quality for YouTube.

**Tech Stack:** Python, FFmpeg (via render_text_overlays.py), pytest

---

### Task 1: Write failing tests for auto-landscape fallback

**Files:**
- Modify: `tests/test_publish_youtube_strict.py`

**Step 1: Write the failing tests**

Add three tests to `tests/test_publish_youtube_strict.py`:

```python
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from execution.publish_youtube import publish_youtube_post


def test_regular_video_auto_renders_landscape_when_missing(tmp_path):
    """When duration >180s and no landscape file exists, auto-render one."""
    mp4_path = tmp_path / "clip_reel.mp4"
    mp4_path.write_bytes(b"fake-mp4")
    landscape_path = tmp_path / "clip_reel_landscape.mp4"

    fields = {
        "format": "reel",
        "hook": "AI demo",
        "caption": "Long video",
        "hashtags": "#AI",
        "visual_paths": json.dumps([str(mp4_path)]),
        "youtube_content": json.dumps({"community_post_text": "Hello"}),
    }

    def fake_auto_render(portrait_path):
        # Simulate successful landscape render
        landscape_path.write_bytes(b"fake-landscape-mp4")
        return str(landscape_path)

    with patch("execution.publish_youtube.probe_video_duration", return_value=300.0), \
         patch("execution.publish_youtube._auto_render_landscape", side_effect=fake_auto_render) as mock_render, \
         patch("execution.publish_youtube._publish_regular_video", return_value="yt_video_id") as mock_pub:
        result = publish_youtube_post(fields, {"youtube": {"enabled": True}}, dry_run=False)

    mock_render.assert_called_once_with(str(mp4_path))
    # Should have published with the landscape file, not the portrait
    assert mock_pub.call_args[0][3] == str(landscape_path)
    assert result == "yt_video_id"


def test_regular_video_falls_back_to_portrait_when_auto_render_fails(tmp_path):
    """When auto-render fails, fall back to portrait with warning."""
    mp4_path = tmp_path / "clip_reel.mp4"
    mp4_path.write_bytes(b"fake-mp4")

    fields = {
        "format": "reel",
        "hook": "AI demo",
        "caption": "Long video",
        "hashtags": "#AI",
        "visual_paths": json.dumps([str(mp4_path)]),
        "youtube_content": json.dumps({"community_post_text": "Hello"}),
    }

    with patch("execution.publish_youtube.probe_video_duration", return_value=300.0), \
         patch("execution.publish_youtube._auto_render_landscape", return_value=None) as mock_render, \
         patch("execution.publish_youtube._publish_regular_video", return_value="yt_video_id") as mock_pub:
        result = publish_youtube_post(fields, {"youtube": {"enabled": True}}, dry_run=False)

    mock_render.assert_called_once_with(str(mp4_path))
    # Should fall back to portrait path
    assert mock_pub.call_args[0][3] == str(mp4_path)
    assert result == "yt_video_id"


def test_regular_video_uses_existing_landscape_without_auto_render(tmp_path):
    """When landscape file already exists in visual_paths, skip auto-render."""
    mp4_path = tmp_path / "clip_reel.mp4"
    mp4_path.write_bytes(b"fake-mp4")
    landscape_path = tmp_path / "clip_reel_landscape.mp4"
    landscape_path.write_bytes(b"fake-landscape-mp4")

    fields = {
        "format": "reel",
        "hook": "AI demo",
        "caption": "Long video",
        "hashtags": "#AI",
        "visual_paths": json.dumps([str(mp4_path), str(landscape_path)]),
        "youtube_content": json.dumps({"community_post_text": "Hello"}),
    }

    with patch("execution.publish_youtube.probe_video_duration", return_value=300.0), \
         patch("execution.publish_youtube._auto_render_landscape") as mock_render, \
         patch("execution.publish_youtube._publish_regular_video", return_value="yt_video_id") as mock_pub:
        result = publish_youtube_post(fields, {"youtube": {"enabled": True}}, dry_run=False)

    # Should NOT call auto-render since landscape already exists
    mock_render.assert_not_called()
    # Should publish with the existing landscape
    assert mock_pub.call_args[0][3] == str(landscape_path)
    assert result == "yt_video_id"
```

**Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_publish_youtube_strict.py -v -x`
Expected: FAIL — `_auto_render_landscape` not importable from `publish_youtube`

**Step 3: Commit the failing tests**

```bash
git add tests/test_publish_youtube_strict.py
git commit -m "test: add failing tests for YouTube auto-landscape fallback"
```

---

### Task 2: Implement auto-landscape fallback in publish_youtube.py

**Files:**
- Modify: `execution/publish_youtube.py:178-186`

**Step 1: Add import for _auto_render_landscape**

At the top of `execution/publish_youtube.py`, after the existing imports (line 29), add:

```python
from execution.publish_all_platforms import _auto_render_landscape
```

Note: This creates a runtime import dependency from publish_youtube → publish_all_platforms.
If this causes a circular import (publish_all_platforms imports publish_youtube), use a
lazy import inside the function instead. Check by running:
`venv/bin/python -c "from execution.publish_youtube import publish_youtube_post; print('OK')"`

If circular, change to lazy import inside the `else` branch instead:
```python
from execution.publish_all_platforms import _auto_render_landscape
```

**Step 2: Replace the regular video fallback (lines 178-186)**

Replace:
```python
    else:
        # Use landscape version for regular YouTube video
        landscape_path = _find_landscape_mp4(blueprint_fields)
        if landscape_path:
            logger.info("  YouTube: %.1fs > %ds — uploading as regular video (landscape)", duration, int(yt_max_duration))
            return _publish_regular_video(blueprint_fields, yt_content, config, landscape_path, dry_run)
        else:
            logger.info("  YouTube: %.1fs > %ds — no landscape version, uploading vertical as regular video", duration, int(yt_max_duration))
            return _publish_regular_video(blueprint_fields, yt_content, config, mp4_path, dry_run)
```

With:
```python
    else:
        # Use landscape version for regular YouTube video
        landscape_path = _find_landscape_mp4(blueprint_fields)

        # Auto-render landscape if pre-rendered file is missing
        if not landscape_path:
            logger.info("  YouTube: landscape missing — auto-rendering from portrait source")
            landscape_path = _auto_render_landscape(mp4_path)

        if landscape_path:
            logger.info(
                "  YouTube: %.1fs > %ds — uploading as regular video (landscape: %s)",
                duration, int(yt_max_duration), Path(landscape_path).name,
            )
            return _publish_regular_video(blueprint_fields, yt_content, config, landscape_path, dry_run)
        else:
            logger.warning(
                "  YouTube: %.1fs > %ds — landscape unavailable, uploading portrait as regular video",
                duration, int(yt_max_duration),
            )
            return _publish_regular_video(blueprint_fields, yt_content, config, mp4_path, dry_run)
```

**Step 3: Handle potential circular import**

Run: `venv/bin/python -c "from execution.publish_youtube import publish_youtube_post; print('OK')"`

If it fails with ImportError, move the import inside the `else` block:
```python
    else:
        from execution.publish_all_platforms import _auto_render_landscape
        # ... rest of code
```

**Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest tests/test_publish_youtube_strict.py -v -x`
Expected: All 5 tests PASS (2 existing + 3 new)

**Step 5: Run full test suite**

Run: `venv/bin/python -m pytest tests/ -x -q --tb=short`
Expected: 952+ passed, 0 failures

**Step 6: Commit**

```bash
git add execution/publish_youtube.py
git commit -m "feat: auto-render landscape for YouTube regular videos when pre-rendered file missing"
```

---

### Task 3: Verify import and end-to-end dry run

**Step 1: Verify imports**

Run:
```bash
venv/bin/python -c "from execution.publish_youtube import publish_youtube_post, _find_landscape_mp4; print('OK')"
venv/bin/python -c "from execution.publish_all_platforms import _auto_render_landscape; print('OK')"
```
Expected: Both print `OK`

**Step 2: Run full regression**

Run: `venv/bin/python -m pytest tests/ -x -q --tb=short`
Expected: 955+ passed, 0 failures

**Step 3: Commit (if any test fixups were needed)**

```bash
git add -A && git commit -m "fix: test fixups for YouTube auto-landscape"
```

---

## Execution Summary

| Task | Files | Tests Added | Commits |
|------|-------|-------------|---------|
| 1. Failing tests | test_publish_youtube_strict.py | 3 | 1 |
| 2. Implementation | publish_youtube.py | — | 1 |
| 3. Verification | — | — | 0-1 |
| **Total** | **2 files** | **3 tests** | **2-3** |
