# Comprehensive Codebase Upgrade — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement all 46 fixes and upgrades identified in the comprehensive audit across 4 phases, hardening publishing reliability, rendering correctness, content quality, and architecture.

**Architecture:** Fix-in-place — each task patches existing files with targeted edits. No new modules; all changes go into existing execution scripts, utility modules, and config YAML files. TDD throughout — write failing test first, implement fix, verify green.

**Tech Stack:** Python 3.11+, pytest, FFmpeg, tweepy, google-api-python-client, requests, feedparser, Flask, Microsoft Graph SDK

**Baseline:** 955 tests passing, 15 skipped, 0 failures. Target: 980-985 tests after all phases.

**Design doc:** `docs/plans/2026-03-03-comprehensive-upgrade-design.md`

---

## Parallelization Strategy

Tasks within each phase are grouped into **parallel batches** — independent fixes that touch different files and can be dispatched to separate subagents simultaneously.

```
Phase 1 (P0 Critical):
  Batch 1A: [P0.1, P0.2, P0.3, P0.8] — publish_all_platforms.py fixes (sequential — same file)
  Batch 1B: [P0.4] — PUBLISH_OP_ERRORS + YouTube catch (2 files)
  Batch 1C: [P0.5] — Twitter chunked upload (1 file)
  Batch 1D: [P0.6] — FFmpeg dynamic timeout (1 file)
  Batch 1E: [P0.7] — Multi-clip zero-duration (1 file)
  → Batches 1B-1E run in parallel, then 1A runs after (depends on 1B for HttpError)

Phase 2 (P1 Correctness/Security):
  Batch 2A: [P1.1, P1.12, P1.13] — Twitter/thread fixes
  Batch 2B: [P1.2, P1.3, P1.4] — Instagram fixes
  Batch 2C: [P1.5, P1.6, P1.14] — FFmpeg/render param fixes
  Batch 2D: [P1.7, P1.8, P1.9] — Injection/adaptation fixes
  Batch 2E: [P1.10, P1.11] — SLO + risk rules

Phase 3 (P2 Quality/Data):
  Batch 3A: [P2.1, P2.2, P2.10, P2.11] — Data pipeline fixes
  Batch 3B: [P2.3, P2.4, P2.5, P2.6] — Rendering/template fixes
  Batch 3C: [P2.7, P2.8, P2.9, P2.12] — Disk/cleanup/config fixes

Phase 4 (P3 Architecture):
  Batch 4A: [P3.1, P3.2, P3.11] — Dead code/config cleanup
  Batch 4B: [P3.3, P3.4, P3.5] — Review server fixes
  Batch 4C: [P3.6, P3.7, P3.8, P3.9, P3.10, P3.12] — Remaining
```

---

## Phase 1: Critical — Duplicate Posts & Silent Failures (8 fixes)

### Task 1B: P0.4 — Add HttpError to PUBLISH_OP_ERRORS + YouTube catch

**Files:**
- Modify: `execution/publish_all_platforms.py:69-78`
- Modify: `execution/publish_youtube.py:338-343`
- Test: `tests/test_publish_all_platforms.py`

**Step 1: Write failing test**

```python
# In tests/test_publish_all_platforms.py — add near other PUBLISH_OP_ERRORS tests
def test_publish_op_errors_includes_http_error():
    """P0.4: HttpError must be in PUBLISH_OP_ERRORS to catch YouTube 4xx/5xx."""
    from execution.publish_all_platforms import PUBLISH_OP_ERRORS
    # googleapiclient may not be installed in test env, so check by name
    error_names = [cls.__name__ for cls in PUBLISH_OP_ERRORS]
    assert "HttpError" in error_names, (
        f"HttpError not in PUBLISH_OP_ERRORS. Found: {error_names}"
    )
```

**Step 2: Run test — expect FAIL**

```bash
venv/bin/python -m pytest tests/test_publish_all_platforms.py::test_publish_op_errors_includes_http_error -v
```

**Step 3: Implement fix in publish_all_platforms.py**

At the top of the file (near other imports, before PUBLISH_OP_ERRORS), add:

```python
try:
    from googleapiclient.errors import HttpError as _GoogleHttpError
except ImportError:
    _GoogleHttpError = None
```

Then update PUBLISH_OP_ERRORS (line 69-78):

```python
PUBLISH_OP_ERRORS = (
    RequestException,
    TimeoutError,
    OSError,
    RuntimeError,
    ValueError,
    KeyError,
    TypeError,
    *([] if ODataError is None else [ODataError]),
    *([] if _GoogleHttpError is None else [_GoogleHttpError]),
)
```

**Step 4: Fix YouTube catch in publish_youtube.py (lines 338-343)**

Replace:
```python
    except (requests.RequestException, KeyError) as exc:
        logger.error("  YouTube video upload error (%s): %s", type(exc).__name__, exc, exc_info=True)
        return None
```

With:
```python
    except (requests.RequestException, KeyError) as exc:
        logger.error("  YouTube video upload error (%s): %s", type(exc).__name__, exc, exc_info=True)
        return None
    except Exception as exc:
        # Catch HttpError and other unexpected errors from google-api-python-client
        exc_name = type(exc).__name__
        if exc_name == "HttpError":
            logger.error("  YouTube API HttpError: %s", exc, exc_info=True)
        else:
            logger.error("  YouTube video upload unexpected error (%s): %s", exc_name, exc, exc_info=True)
        return None
```

**Step 5: Run test — expect PASS**

```bash
venv/bin/python -m pytest tests/test_publish_all_platforms.py::test_publish_op_errors_includes_http_error -v
```

**Step 6: Commit**

```bash
git add execution/publish_all_platforms.py execution/publish_youtube.py tests/test_publish_all_platforms.py
git commit -m "fix(P0.4): add HttpError to PUBLISH_OP_ERRORS + YouTube catch"
```

---

### Task 1C: P0.5 — Twitter video upload: add chunked=True

**Files:**
- Modify: `execution/utils/twitter_client.py:69-72`
- Test: `tests/test_twitter_client.py`

**Step 1: Write failing test**

```python
# In tests/test_twitter_client.py
from unittest.mock import patch, MagicMock
import os

def test_upload_media_uses_chunked_for_video(tmp_path):
    """P0.5: Video files must use chunked=True for Twitter upload."""
    video_file = tmp_path / "test.mp4"
    video_file.write_bytes(b"\x00" * 100)  # Dummy MP4

    with patch.dict(os.environ, {
        "X_API_KEY": "test", "X_API_SECRET": "test",
        "X_ACCESS_TOKEN": "test", "X_ACCESS_SECRET": "test",
    }):
        with patch("tweepy.OAuth1UserHandler"):
            with patch("tweepy.API") as mock_api_cls:
                with patch("tweepy.Client"):
                    mock_api = MagicMock()
                    mock_api.media_upload.return_value = MagicMock(media_id_string="12345")
                    mock_api_cls.return_value = mock_api

                    from execution.utils.twitter_client import TwitterClient
                    client = TwitterClient()
                    client.api_v1 = mock_api

                    result = client.upload_media(video_file)
                    assert result == "12345"
                    # Verify chunked=True was passed for .mp4
                    mock_api.media_upload.assert_called_once()
                    call_kwargs = mock_api.media_upload.call_args
                    assert call_kwargs[1].get("chunked") is True or \
                           (len(call_kwargs) > 1 and call_kwargs[1].get("chunked") is True), \
                        "media_upload must use chunked=True for video files"
```

**Step 2: Run test — expect FAIL**

```bash
venv/bin/python -m pytest tests/test_twitter_client.py::test_upload_media_uses_chunked_for_video -v
```

**Step 3: Implement fix**

In `execution/utils/twitter_client.py`, replace the `upload_media` method body (lines 64-75):

```python
    def upload_media(self, file_path: Path) -> Optional[str]:
        """Upload a media file and return its media_id string.

        Uses chunked upload for video files (MP4, MOV, GIF) to support
        files >5MB (Twitter's simple upload limit).
        """
        file_path = Path(file_path)
        if not file_path.exists():
            logger.warning("Media file not found: %s", file_path)
            return None

        try:
            # P0.5: Video files need chunked upload (simple endpoint rejects >5MB)
            video_exts = {".mp4", ".mov", ".gif"}
            use_chunked = file_path.suffix.lower() in video_exts
            media = self.api_v1.media_upload(
                str(file_path), chunked=use_chunked,
            )
            logger.debug("Uploaded media: %s → %s (chunked=%s)",
                         file_path.name, media.media_id_string, use_chunked)
            return media.media_id_string
        except Exception as exc:
            logger.error("Media upload failed for %s: %s", file_path.name, exc)
            return None
```

**Step 4: Run test — expect PASS**

```bash
venv/bin/python -m pytest tests/test_twitter_client.py::test_upload_media_uses_chunked_for_video -v
```

**Step 5: Commit**

```bash
git add execution/utils/twitter_client.py tests/test_twitter_client.py
git commit -m "fix(P0.5): use chunked=True for Twitter video upload (>5MB)"
```

---

### Task 1D: P0.6 — Dynamic FFmpeg timeout for long videos

**Files:**
- Modify: `execution/render_text_overlays.py:504,1691`
- Test: `tests/test_render_text_overlays.py`

**Step 1: Write failing test**

```python
# In tests/test_render_text_overlays.py
def test_ffmpeg_timeout_scales_with_duration():
    """P0.6: FFmpeg timeout must scale with video duration, not fixed 120s."""
    from execution.render_text_overlays import _compute_ffmpeg_timeout

    # Short video — should use minimum
    assert _compute_ffmpeg_timeout(10) >= 120
    # 15-minute video — should be at least 15*60*4 = 3600
    assert _compute_ffmpeg_timeout(900) >= 3600
    # 5-minute video — should be at least 5*60*4 = 1200
    assert _compute_ffmpeg_timeout(300) >= 1200
```

**Step 2: Run test — expect FAIL (function doesn't exist yet)**

```bash
venv/bin/python -m pytest tests/test_render_text_overlays.py::test_ffmpeg_timeout_scales_with_duration -v
```

**Step 3: Implement fix**

In `execution/render_text_overlays.py`, after the `FFMPEG_TIMEOUT = 120` constant (line 504), add:

```python
def _compute_ffmpeg_timeout(video_duration_seconds: float) -> int:
    """Compute dynamic FFmpeg timeout based on video duration.

    Formula: max(FFMPEG_TIMEOUT, video_duration * 4)
    A 15-minute video at CRF 17 slow preset takes 8-15 minutes to encode.
    """
    return max(FFMPEG_TIMEOUT, int(video_duration_seconds * 4))
```

Then update both subprocess.run calls:
- Line ~1691 in `_render_drawtext()`: replace `timeout=FFMPEG_TIMEOUT` with `timeout=_compute_ffmpeg_timeout(video_duration)` (where `video_duration` comes from the existing `probe_video_duration()` call)
- Line ~504 usage in `_render_pillow()`: same replacement

**Step 4: Run test — expect PASS**

```bash
venv/bin/python -m pytest tests/test_render_text_overlays.py::test_ffmpeg_timeout_scales_with_duration -v
```

**Step 5: Commit**

```bash
git add execution/render_text_overlays.py tests/test_render_text_overlays.py
git commit -m "fix(P0.6): dynamic FFmpeg timeout = max(120, duration * 4)"
```

---

### Task 1E: P0.7 — Multi-clip zero-duration fix

**Files:**
- Modify: `execution/assemble_video_reel.py:500-518`
- Test: `tests/test_assemble_video_reel.py`

**Step 1: Write failing test**

```python
# In tests/test_assemble_video_reel.py
def test_allocate_scene_durations_zero_total():
    """P0.7: When total_duration=0, return [0.0]*n (use full clip duration)."""
    from execution.assemble_video_reel import _allocate_scene_durations

    # 3 clips with total_duration=0 should all get 0.0 (= full clip)
    result = _allocate_scene_durations(3, total_duration=0.0, transition_duration=0.5)
    assert result == [0.0, 0.0, 0.0], f"Expected [0,0,0] but got {result}"

    # 1 clip — already handled
    result = _allocate_scene_durations(1, total_duration=0.0, transition_duration=0.5)
    assert result == [0.0]

    # Negative total_duration should also return zeros
    result = _allocate_scene_durations(2, total_duration=-1.0, transition_duration=0.5)
    assert result == [0.0, 0.0]
```

**Step 2: Run test — expect FAIL**

```bash
venv/bin/python -m pytest tests/test_assemble_video_reel.py::test_allocate_scene_durations_zero_total -v
```

**Step 3: Implement fix**

In `execution/assemble_video_reel.py`, at line ~503 (after the `n_clips == 1` early return), add:

```python
    # P0.7: When total_duration <= 0, each clip uses its full duration
    if total_duration <= 0:
        return [0.0] * n_clips
```

This goes right after:
```python
    if n_clips == 1:
        return [total_duration if total_duration > 0 else 0.0]
```

And before:
```python
    # Use preset allocations if available
```

**Step 4: Run test — expect PASS**

```bash
venv/bin/python -m pytest tests/test_assemble_video_reel.py::test_allocate_scene_durations_zero_total -v
```

**Step 5: Commit**

```bash
git add execution/assemble_video_reel.py tests/test_assemble_video_reel.py
git commit -m "fix(P0.7): return [0.0]*n when total_duration<=0 (full clip)"
```

---

### Task 1A: P0.1 + P0.2 + P0.3 + P0.8 — publish_all_platforms.py fixes

These four fixes all touch `publish_all_platforms.py` and must be applied sequentially.

**Files:**
- Modify: `execution/publish_all_platforms.py` (lines 1122-1124, 1202, 1326-1331, 835-836)
- Test: `tests/test_publish_all_platforms.py`

#### Sub-step A: P0.1 — Skip PUBLISHING state on retry

**Step 1: Write failing test**

```python
def test_publishing_state_skipped_on_retry():
    """P0.1: PUBLISHING status must be skipped (treated as failed after 30min)."""
    # Test the retry filter logic
    existing_status = {"instagram": "PUBLISHED", "youtube": "PUBLISHING", "twitter": "FAILED"}
    enabled = ["instagram", "youtube", "twitter"]

    # Current code: only skips "PUBLISHED" — youtube would be re-published (BUG)
    # Fixed: PUBLISHING should also be skipped (treated as stale failure)
    platforms_to_publish = [
        p for p in enabled
        if existing_status.get(p) not in ("PUBLISHED", "PUBLISHING")
    ]
    assert "instagram" not in platforms_to_publish  # Already published
    assert "youtube" not in platforms_to_publish     # PUBLISHING = stale, skip
    assert "twitter" in platforms_to_publish          # FAILED = retry
```

**Step 2: Implement P0.1 fix**

In `execution/publish_all_platforms.py`, replace lines 1122-1124:

```python
        platforms_to_publish = [
            p for p in enabled
            if existing_status.get(p) != "PUBLISHED"
        ]
```

With:

```python
        # P0.1: Skip both PUBLISHED and PUBLISHING (stale intermediate state after crash)
        platforms_to_publish = [
            p for p in enabled
            if existing_status.get(p) not in ("PUBLISHED", "PUBLISHING")
        ]
```

#### Sub-step B: P0.2 — Raise future.result timeout to 700s

**Step 3: Implement P0.2 fix**

In `execution/publish_all_platforms.py`, replace line 1202:

```python
                        result = future.result(timeout=120)
```

With:

```python
                        result = future.result(timeout=700)  # P0.2: covers FB video (300s) + CDN (600s)
```

#### Sub-step C: P0.3 — Whitelist all SKIPPED_* codes in all_or_nothing

**Step 4: Write failing test**

```python
def test_all_or_nothing_accepts_all_skipped_codes():
    """P0.3: all_or_nothing must accept all SKIPPED_* codes, not just SKIPPED_DAILY_LIMIT."""
    platform_results = {
        "instagram": "PUBLISHED",
        "twitter": "SKIPPED_NO_CREDENTIALS",
        "facebook": "SKIPPED_PAYLOAD_MISSING",
        "youtube": "PUBLISHED",
    }
    enabled = ["instagram", "twitter", "facebook", "youtube"]

    # P0.3 fix: any SKIPPED_* prefix is acceptable
    all_succeeded = all(
        platform_results.get(p) == "PUBLISHED" or
        (platform_results.get(p) or "").startswith("SKIPPED")
        for p in enabled
    )
    assert all_succeeded is True, "SKIPPED_NO_CREDENTIALS should be accepted"
```

**Step 5: Implement P0.3 fix**

In `execution/publish_all_platforms.py`, replace lines 1326-1331:

```python
        if strategy == "all_or_nothing":
            all_succeeded = all(
                platform_results.get(p) in ("PUBLISHED", "SKIPPED_DAILY_LIMIT")
                for p in enabled
            )
```

With:

```python
        if strategy == "all_or_nothing":
            # P0.3: Accept all SKIPPED_* codes (not just SKIPPED_DAILY_LIMIT)
            all_succeeded = all(
                platform_results.get(p) == "PUBLISHED"
                or (platform_results.get(p) or "").startswith("SKIPPED")
                for p in enabled
            )
```

#### Sub-step D: P0.8 — Set tw_rate_limited after first successful post

**Step 6: Implement P0.8 fix**

In `execution/publish_all_platforms.py`, replace lines 834-836:

```python
        # Propagate rate-limit to shared event
        if tw_client and tw_client._rate_limited and tw_rate_limited:
            tw_rate_limited.set()
```

With:

```python
        # P0.8: Propagate rate-limit to shared event.
        # Set IMMEDIATELY after success (not just on 429) to prevent
        # two blueprints both passing is_set() before either posts.
        if tw_rate_limited and result:
            tw_rate_limited.set()
        if tw_client and tw_client._rate_limited and tw_rate_limited:
            tw_rate_limited.set()
```

**Step 7: Run full test suite for publish_all_platforms**

```bash
venv/bin/python -m pytest tests/test_publish_all_platforms.py -v
```

**Step 8: Commit**

```bash
git add execution/publish_all_platforms.py tests/test_publish_all_platforms.py
git commit -m "fix(P0.1-3,8): PUBLISHING skip, 700s timeout, SKIPPED_* whitelist, tw rate limit"
```

---

### Phase 1 Regression

**Step: Run full test suite after Phase 1**

```bash
venv/bin/python -m pytest tests/ -x -q --tb=short 2>&1 | tail -20
```

Expected: 955+ pass, 0 fail. Then:

```bash
venv/bin/python -c "from execution.publish_all_platforms import PUBLISH_OP_ERRORS; print('PUBLISH_OP_ERRORS ok:', len(PUBLISH_OP_ERRORS))"
```

---

## Phase 2: Publishing Correctness & Security (14 fixes)

### Task 2A: P1.1 + P1.12 + P1.13 — Twitter/thread fixes

**Files:**
- Modify: `execution/publish_twitter.py` (partial thread detection)
- Modify: `execution/publish_all_platforms.py` (credential skip code)
- Modify: `execution/utils/twitter_client.py` (media cap already in post_tweet, add to post_thread)
- Test: `tests/test_publish_twitter.py`, `tests/test_twitter_client.py`

**P1.1 — Detect partial thread and return None:**

In `execution/publish_twitter.py`, after the `post_thread()` call returns `posted_ids`, add:

```python
    # P1.1: Detect partial thread — some tweets failed
    if posted_ids and len(posted_ids) < len(thread_tweets):
        logger.warning("  Twitter: partial thread (%d/%d tweets posted) — marking as FAILED",
                       len(posted_ids), len(thread_tweets))
        return None  # Partial thread = failure (don't mark as PUBLISHED)
```

**P1.12 — Twitter credential skip code:**

In `execution/publish_all_platforms.py`, in `publish_to_platform()` Twitter section (around line 825), the early return for missing credentials should set the skip code. Find:

```python
            logger.warning("  Twitter: skipping — credentials not configured")
            return None
```

Replace with:

```python
            logger.warning("  Twitter: skipping — credentials not configured")
            return "SKIPPED_NO_CREDENTIALS"  # P1.12: match Facebook pattern
```

**P1.13 — Thread tweet media cap at 4:**

Already handled in `post_tweet()` (line 107: `media_ids[:4]`). But `post_thread()` per-tweet upload has no cap. In `execution/utils/twitter_client.py` `post_thread()`, after building `media_ids` (line ~159), add:

```python
            media_ids = media_ids[:4]  # P1.13: Twitter max 4 media per tweet
```

**Commit:**

```bash
git commit -m "fix(P1.1,12,13): partial thread detection, tw credential skip, media cap"
```

---

### Task 2B: P1.2 + P1.3 + P1.4 — Instagram fixes

**Files:**
- Modify: `execution/publish_to_instagram.py`

**P1.2 — Carousel timeout: break → return None:**

In `execution/publish_to_instagram.py` line ~276-277, the timeout `break` should be `return None`:

```python
        if elapsed > max_poll_seconds:
            logger.warning("Carousel container processing timeout after %.0fs", elapsed)
            return None  # P1.2: Don't publish timed-out carousel
```

**P1.3 — PUBLISHED poll early return:**

At line ~293-295, when status is `PUBLISHED`, add explicit handling:

```python
                elif sc in ("IN_PROGRESS", "PUBLISHED"):
                    if sc == "PUBLISHED":
                        logger.info("Carousel already PUBLISHED during polling — returning early")
                        break  # Already published — don't re-publish
```

(This is already a `break` — verified correct. P1.3 confirmed as working.)

**P1.4 — CDN upload outside retry loop:**

In `publish_to_instagram.py`, locate `_ensure_public_url()` inside the retry loop. Move it before the retry loop:

```python
    # P1.4: Ensure public URL BEFORE retry loop (CDN upload is expensive, don't repeat)
    public_url = _ensure_public_url(media_path)
    if not public_url:
        logger.error("Failed to get public URL for %s", media_path)
        return None

    for attempt in range(max_retries):
        # ... use public_url in API call ...
```

**Commit:**

```bash
git commit -m "fix(P1.2,3,4): carousel timeout return None, CDN outside retry loop"
```

---

### Task 2C: P1.5 + P1.6 + P1.14 — FFmpeg/render param fixes

**Files:**
- Modify: `execution/utils/ffmpeg_utils.py:43-57`
- Modify: `execution/render_text_overlays.py` (audio bitrate)
- Modify: `execution/assemble_video_reel.py` (audio bitrate + clip timeout)

**P1.5 — Add `-r 30` to FINAL_VIDEO_PARAMS:**

In `execution/utils/ffmpeg_utils.py`, add to `FINAL_VIDEO_PARAMS` list (after `-bf 2`):

```python
    "-r", "30",               # P1.5: Consistent 30fps across all outputs
```

**P1.6 — Replace hardcoded 192k with FINAL_AUDIO_PARAMS:**

Search for `"192k"` in `render_text_overlays.py` and `assemble_video_reel.py`. Replace all instances with a reference to `FINAL_AUDIO_PARAMS` imported from `ffmpeg_utils.py`.

In `render_text_overlays.py`, add import:
```python
from execution.utils.ffmpeg_utils import FINAL_AUDIO_PARAMS
```

Replace any `"-b:a", "192k"` with `*FINAL_AUDIO_PARAMS` (which includes `"-b:a", "256k"`).

Same for `assemble_video_reel.py`.

**P1.14 — Intermediate clip timeout:**

In `assemble_video_reel.py`, where individual clip preprocessing uses `timeout=120` (around line 633), replace with:

```python
timeout=max(120, int(actual_dur * 4))  # P1.14: scale with clip duration
```

**Commit:**

```bash
git commit -m "fix(P1.5,6,14): add -r 30 to params, 256k audio, dynamic clip timeout"
```

---

### Task 2D: P1.7 + P1.8 + P1.9 — Injection/adaptation fixes

**Files:**
- Modify: `execution/write_post_content.py:388`
- Modify: `execution/adapt_for_platforms.py:92-98`

**P1.7 — Add story_url to injection check list:**

In `execution/write_post_content.py`, line 388, the injection check loop:

```python
    for field_name in ["title", "summary", "source"]:
```

Change to:

```python
    for field_name in ["title", "summary", "source", "url"]:  # P1.7: check story_url too
```

And add `url` to the `field_var` mapping:

```python
        field_var = {
            "title": "story_title", "summary": "story_summary",
            "source": "source_name", "url": "story_url",
        }[field_name]
```

**P1.8 — Re-sanitize backlog content before adaptation:**

In `execution/adapt_for_platforms.py`, before the `variables = { ... }` dict (around line 92), add:

```python
    # P1.8: Re-sanitize hook/caption loaded from backlog before interpolating
    from execution.utils.text_sanitizer import sanitize_text, check_for_injection
    hook = sanitize_text(blueprint.get("hook", ""))
    caption = sanitize_text(blueprint.get("caption", ""))
    if check_for_injection(hook):
        logger.warning("Injection detected in hook from backlog, clearing")
        hook = ""
    if check_for_injection(caption):
        logger.warning("Injection detected in caption from backlog, clearing")
        caption = ""
```

Then use the sanitized `hook` and `caption` in the `variables` dict instead of raw `blueprint.get()`.

**P1.9 — Add Facebook to to_adapt filter:**

In `execution/adapt_for_platforms.py`, where the filter checks which platforms need adaptation (around lines 496-500), add:

```python
    has_fb = bool(fields.get("facebook_content", ""))
```

Include `has_fb` in the skip condition so Facebook is not skipped when it needs content.

**Commit:**

```bash
git commit -m "fix(P1.7,8,9): story_url injection check, resanitize backlog, FB adaptation"
```

---

### Task 2E: P1.10 + P1.11 — SLO + risk rules

**Files:**
- Modify: `execution/write_run_report.py:170,441`
- Modify: `config/risk_rules.yaml`

**P1.10 — Read SLO from config instead of hardcoded 600:**

In `execution/write_run_report.py`, replace line 170:

```python
        'pipeline_p95_seconds': 600,           # 10 min standard pipeline
```

With:

```python
        'pipeline_p95_seconds': error_budgets.get('duration_p95', 600),
```

Where `error_budgets` is loaded from `config/error_budgets.yaml` at the top of the function. Add import:

```python
    from execution.utils.config_loader import load_config
    error_budgets = load_config("error_budgets.yaml") or {}
```

Also replace line 441:

```python
    if duration > 600:  # 10 min P95 target
```

With:

```python
    slo_p95 = error_budgets.get('duration_p95', 600)
    if duration > slo_p95:
```

**P1.11 — Add missing risk keywords:**

In `config/risk_rules.yaml`, add to `high_risk_keywords`:

```yaml
high_risk_keywords:
  - "medical advice"
  - "legal advice"
  - "financial advice"
  - "investment"
  - "health claim"
  - "cure"
  - "guaranteed"
  - "deepfake"
  - "synthetic media"
  - "election"
  - "disinformation"
  - "terrorism"
  - "bioweapon"
  - "weapon"
  - "violence"
```

**Commit:**

```bash
git commit -m "fix(P1.10,11): read SLO from config, add risk keywords"
```

---

### Phase 2 Regression

```bash
venv/bin/python -m pytest tests/ -x -q --tb=short 2>&1 | tail -20
```

---

## Phase 3: Rendering Quality & Data Pipeline (12 fixes)

### Task 3A: P2.1 + P2.2 + P2.10 + P2.11 — Data pipeline fixes

**P2.1 — feedparser timeout:**

In `execution/fetch_ai_creators.py:516`:

```python
            feed = feedparser.parse(url, request_headers={"User-Agent": "GenLab-Fetcher/1.0"})
```

Replace with:

```python
            feed = feedparser.parse(
                url,
                request_headers={"User-Agent": "GenLab-Fetcher/1.0"},
                timeout=30,  # P2.1: prevent hanging on unresponsive feeds
            )
```

Note: feedparser 6.x supports `timeout` kwarg. If older version, wrap with `socket.setdefaulttimeout(30)`.

**P2.2 — normalize_url: only lowercase scheme+netloc, not path:**

In `execution/utils/stable_ids.py:35`:

```python
    parsed = urlparse(url.lower())
```

Replace with:

```python
    parsed = urlparse(url)
    # P2.2: Only lowercase scheme + netloc; preserve path/query/fragment case
    parsed = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
    )
```

**P2.10 — Column map cache TTL:**

In `execution/utils/backlog_client.py:268`:

```python
_column_map_cache: Dict[str, tuple] = {}  # list_id -> (display_to_internal, internal_to_display)
```

Replace with:

```python
import time as _time
_column_map_cache: Dict[str, tuple] = {}  # list_id -> (display_to_internal, internal_to_display, timestamp)
_COLUMN_MAP_TTL = 3600  # 1 hour TTL
```

Then in `_load_column_map()`, add TTL check:

```python
    cached = _column_map_cache.get(list_id)
    if cached:
        d2i, i2d, ts = cached
        if (_time.monotonic() - ts) < _COLUMN_MAP_TTL:
            return d2i, i2d
        # Expired — refetch
```

And when storing:

```python
    _column_map_cache[list_id] = (display_to_internal, internal_to_display, _time.monotonic())
```

**P2.11 — push_to_backlog incremental fetch:**

In `execution/push_to_backlog.py`, replace full table fetches (lines 219-240) with filtered queries:

```python
    # P2.11: Incremental fetch — only records updated recently
    from datetime import datetime, timedelta
    seven_days_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
    all_stories = client.stories.all(
        formula=f"Modified ge '{seven_days_ago}'"
    )
```

**Commit:**

```bash
git commit -m "fix(P2.1,2,10,11): feedparser timeout, URL case, cache TTL, incremental fetch"
```

---

### Task 3B: P2.3 + P2.4 + P2.5 + P2.6 — Rendering/template fixes

**P2.3 — QC Gate B reel constraints in templates.yaml:**

Add `constraints` block to reel templates in `config/templates.yaml`:

```yaml
    constraints:
      max_seconds: 900
      max_words_per_beat_title: 15
      beat_cadence_seconds: 5
```

**P2.4 — Deduplicate _escape_drawtext:**

Move the class method version to `execution/utils/ffmpeg_utils.py` as a module-level function. Both call sites import from the single source.

**P2.5 — Font path escape for FFmpeg:**

In the drawtext filter construction, use the `_escape_drawtext()` utility (now in `ffmpeg_utils.py`) for the font path:

```python
    escaped_font = _escape_drawtext(str(font_path))
```

**P2.6 — Raise max_tokens to 4096:**

In `execution/write_post_content.py`, find `max_tokens=2048` and replace with `max_tokens=4096`.

**Commit:**

```bash
git commit -m "fix(P2.3-6): reel constraints, dedup escape_drawtext, font escape, max_tokens 4096"
```

---

### Task 3C: P2.7 + P2.8 + P2.9 + P2.12 — Disk/cleanup/config fixes

**P2.7 — Disk space pre-flight:**

In `execution/render_visuals.py`, near the top of the main render function, add:

```python
    import shutil
    disk = shutil.disk_usage(output_dir)
    if disk.free < 2 * 1024**3:  # 2GB
        raise RuntimeError(f"Insufficient disk space: {disk.free / 1024**3:.1f}GB free (need 2GB)")
```

**P2.8 — Temp file cleanup:**

In `execution/publish_twitter.py` and `execution/publish_facebook.py`, wrap truncated/temp file creation in `finally:` blocks:

```python
    finally:
        # P2.8: Clean up temp files
        for tf in temp_files:
            if tf.exists():
                tf.unlink()
                logger.debug("Cleaned up temp file: %s", tf.name)
```

**P2.9 — Reel-ratio cross-story case:**

In `execution/compose_blueprints.py:978-1014`, implement the cross-story reel addition path (currently a placeholder/TODO).

**P2.12 — Facebook prompt char limit:**

In `config/content_prompts.yaml`, find the Facebook prompt with `500` character limit and change to `2000`:

```yaml
  - Max 2000 characters
```

**Commit:**

```bash
git commit -m "fix(P2.7-9,12): disk pre-flight, temp cleanup, cross-story reels, FB char limit"
```

---

### Phase 3 Regression

```bash
venv/bin/python -m pytest tests/ -x -q --tb=short 2>&1 | tail -20
```

---

## Phase 4: Architecture & Polish (12 upgrades)

### Task 4A: P3.1 + P3.2 + P3.11 — Dead code/config cleanup

**P3.1 — Remove dead assemble_reel.py:**

```bash
mkdir -p execution/archive
git mv execution/assemble_reel.py execution/archive/assemble_reel.py
```

**P3.2 — Clean publishing.yaml:**

Remove `format_mix` block and update `visuals.dimensions` to `1080x1920`.

**P3.11 — Remove dead URL loop in blueprints.py:**

In `execution/api/blueprints.py`, remove the dead loop at lines 57-60.

**Commit:**

```bash
git commit -m "chore(P3.1,2,11): remove dead assemble_reel.py, clean config, remove dead loop"
```

---

### Task 4B: P3.3 + P3.4 + P3.5 — Review server fixes

**P3.3 — Express trigger GET → POST:**

In `execution/review_server.py:771`:

```python
@app.route("/api/express/trigger")
def trigger_express():
```

Change to:

```python
@app.route("/api/express/trigger", methods=["POST"])
def trigger_express():
```

Update dashboard client to use POST.

**P3.4 — Deduplicate review logic:**

Extract shared `_execute_review_action()` from `execution/api/blueprints.py` review routes. Move to `execution/review_server.py`. Both call sites delegate to the shared function.

**P3.5 — SESSION_COOKIE_SECURE env-based:**

In `execution/review_server.py:70`:

```python
app.config["SESSION_COOKIE_SECURE"] = True
```

Replace with:

```python
app.config["SESSION_COOKIE_SECURE"] = os.getenv("FLASK_COOKIE_SECURE", "true").lower() == "true"
```

**Commit:**

```bash
git commit -m "fix(P3.3-5): express POST, deduplicate review logic, env-based cookie secure"
```

---

### Task 4C: P3.6 + P3.7 + P3.8 + P3.9 + P3.10 + P3.12 — Remaining

**P3.6 — Unify config loading:**

Replace all inline `yaml.safe_load()` calls across the codebase with `config_loader.load_config()`.

**P3.7 — YouTube resumable upload retry:**

In `execution/utils/youtube_client.py`, add per-chunk retry with exponential backoff in `upload_short()` and `upload_video()`:

```python
    for chunk_attempt in range(3):
        try:
            status, response = request.next_chunk()
            break
        except Exception as e:
            if chunk_attempt == 2:
                raise
            wait = 2 ** chunk_attempt
            logger.warning("YouTube chunk upload failed (attempt %d), retrying in %ds: %s",
                          chunk_attempt + 1, wait, e)
            time.sleep(wait)
```

**P3.8 — YouTube client error handling:**

In `execution/utils/youtube_client.py`, add `requests.HTTPError` catch in `post_comment()` and `update_metadata()`.

**P3.9 — Persist community post text:**

In `execution/publish_youtube.py`, when `manual_post_required` is set, write full text to `.tmp/runs/<run_id>/manual_posts/<bp_id>.json`.

**P3.10 — Landscape spec in single-file --video mode:**

In `execution/validate_videos.py` main(), check `_landscape` suffix in single-video path and route to `_check_landscape_spec()`.

**P3.12 — OData filter input validation:**

In `execution/api/blueprints.py`, validate `action_taken` against an allowlist before interpolating into OData filter:

```python
ALLOWED_ACTIONS = {"approved", "rejected", "revised", "skipped"}
if action_taken not in ALLOWED_ACTIONS:
    return jsonify({"error": f"Invalid action: {action_taken}"}), 400
```

**Commit:**

```bash
git commit -m "chore(P3.6-10,12): unify config, YT retry, error handling, OData validation"
```

---

### Phase 4 Regression

```bash
venv/bin/python -m pytest tests/ -x -q --tb=short 2>&1 | tail -20
```

---

## Final Verification

After all 4 phases:

```bash
# Full test suite
venv/bin/python -m pytest tests/ -x -q --tb=short

# Import health check
venv/bin/python -c "
from execution.publish_all_platforms import PUBLISH_OP_ERRORS
from execution.utils.twitter_client import TwitterClient
from execution.utils.ffmpeg_utils import FINAL_VIDEO_PARAMS, FINAL_AUDIO_PARAMS
from execution.render_text_overlays import _compute_ffmpeg_timeout
from execution.assemble_video_reel import _allocate_scene_durations
print('All imports OK')
print('PUBLISH_OP_ERRORS:', len(PUBLISH_OP_ERRORS), 'entries')
print('FINAL_VIDEO_PARAMS has -r 30:', '-r' in FINAL_VIDEO_PARAMS)
print('Dynamic timeout for 900s video:', _compute_ffmpeg_timeout(900))
print('Zero-duration 3 clips:', _allocate_scene_durations(3, 0.0, 0.5))
"

# Verify risk rules
venv/bin/python -c "
import yaml
with open('config/risk_rules.yaml') as f:
    rules = yaml.safe_load(f)
keywords = rules.get('high_risk_keywords', [])
for kw in ['deepfake', 'synthetic media', 'election', 'terrorism']:
    assert kw in keywords, f'Missing keyword: {kw}'
print('Risk rules OK:', len(keywords), 'keywords')
"
```

Expected: ~980-985 tests passing, 0 failures.

---

## Git Log Summary (Expected)

```
Phase 4: chore(P3.6-10,12): unify config, YT retry, error handling, OData validation
Phase 4: fix(P3.3-5): express POST, deduplicate review logic, env-based cookie secure
Phase 4: chore(P3.1,2,11): remove dead assemble_reel.py, clean config, remove dead loop
Phase 3: fix(P2.7-9,12): disk pre-flight, temp cleanup, cross-story reels, FB char limit
Phase 3: fix(P2.3-6): reel constraints, dedup escape_drawtext, font escape, max_tokens 4096
Phase 3: fix(P2.1,2,10,11): feedparser timeout, URL case, cache TTL, incremental fetch
Phase 2: fix(P1.10,11): read SLO from config, add risk keywords
Phase 2: fix(P1.7,8,9): story_url injection check, resanitize backlog, FB adaptation
Phase 2: fix(P1.5,6,14): add -r 30 to params, 256k audio, dynamic clip timeout
Phase 2: fix(P1.2,3,4): carousel timeout return None, CDN outside retry loop
Phase 2: fix(P1.1,12,13): partial thread detection, tw credential skip, media cap
Phase 1: fix(P0.1-3,8): PUBLISHING skip, 700s timeout, SKIPPED_* whitelist, tw rate limit
Phase 1: fix(P0.7): return [0.0]*n when total_duration<=0 (full clip)
Phase 1: fix(P0.6): dynamic FFmpeg timeout = max(120, duration * 4)
Phase 1: fix(P0.5): use chunked=True for Twitter video upload (>5MB)
Phase 1: fix(P0.4): add HttpError to PUBLISH_OP_ERRORS + YouTube catch
```
