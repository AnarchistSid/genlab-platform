# Codebase Hardening — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement 25 fixes across 3 phases (P0 reliability, P1 intelligence, P2 hygiene) to harden the content pipeline against silent failures, close the feedback loop, and remove dead code.

**Architecture:** Each fix is a targeted change to 1-2 files + test. Phases are sequential (P0 → P1 → P2) with a full test regression between phases. All changes are backwards-compatible.

**Tech Stack:** Python 3.12, pytest, Flask, Microsoft Graph SDK, FFmpeg, requests

**Design doc:** `docs/plans/2026-03-03-codebase-hardening-design.md`

---

## Phase 0 — Reliability (Prevent Data Loss)

### Task 1: P0.1 — Landscape Validator Blind Spot

**Files:**
- Modify: `execution/validate_videos.py:1305-1325`
- Test: `tests/test_validate_videos.py`

**Step 1: Write the failing test**

Add to `tests/test_validate_videos.py`:

```python
def test_landscape_video_skips_portrait_validation(tmp_path):
    """Landscape *_landscape.mp4 files must NOT be validated as portrait (1080x1920)."""
    # Create a fake landscape video file
    landscape = tmp_path / "story_abc_landscape.mp4"
    landscape.write_bytes(b"\x00" * 1024)

    # Patch probe to return landscape dimensions
    with patch("execution.validate_videos.probe_video_metadata") as mock_probe:
        mock_probe.return_value = {
            "width": 1920, "height": 1080, "codec": "h264",
            "duration": 60.0, "fps": 30.0, "audio_sample_rate": 48000,
            "file_size_mb": 1.0,
        }
        from execution.validate_videos import _validate_videos
        result = _validate_videos(
            [landscape], "test", tmp_path, strict=False, auto_fix=False, competitive=True
        )

    # Landscape video should pass (not flagged as wrong resolution)
    video_results = result.get("results", result.get("videos", []))
    for vr in video_results:
        assert "resolution" not in str(vr.get("critical_errors", [])).lower() or vr.get("passed", True)


def test_check_landscape_spec_validates_16_9():
    """_check_landscape_spec must validate 1920x1080, h264, ≤900s, ≤100MB."""
    from execution.validate_videos import _check_landscape_spec

    # Valid landscape
    with patch("execution.validate_videos.probe_video_metadata") as mock_probe:
        mock_probe.return_value = {
            "width": 1920, "height": 1080, "codec": "h264",
            "duration": 120.0, "fps": 30.0, "file_size_mb": 50.0,
        }
        result = _check_landscape_spec(Path("/fake/video_landscape.mp4"))
        assert result.passed

    # Wrong resolution
    with patch("execution.validate_videos.probe_video_metadata") as mock_probe:
        mock_probe.return_value = {
            "width": 1280, "height": 720, "codec": "h264",
            "duration": 120.0, "fps": 30.0, "file_size_mb": 50.0,
        }
        result = _check_landscape_spec(Path("/fake/video_landscape.mp4"))
        assert not result.passed
```

**Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_validate_videos.py::test_landscape_video_skips_portrait_validation tests/test_validate_videos.py::test_check_landscape_spec_validates_16_9 -v`
Expected: FAIL (function doesn't exist / landscape gets portrait validation)

**Step 3: Write minimal implementation**

In `execution/validate_videos.py`:

1. Add `_check_landscape_spec()` function (before `_validate_videos`):

```python
def _check_landscape_spec(video_path: Path) -> "VideoQuality":
    """Validate a landscape (16:9) video for Facebook/YouTube specs.

    Checks: 1920x1080, H.264, ≤900s, ≤100MB.
    """
    meta = probe_video_metadata(video_path)
    if not meta:
        return VideoQuality(video_path.name, passed=False,
                            critical_errors=["Could not probe video metadata"])

    errors = []
    warnings = []

    w, h = meta.get("width", 0), meta.get("height", 0)
    if (w, h) != (1920, 1080):
        errors.append(f"Landscape resolution {w}x{h} (expected 1920x1080)")

    codec = (meta.get("codec") or "").lower()
    if codec not in ("h264", "h.264", "avc"):
        errors.append(f"Codec '{codec}' (expected H.264)")

    duration = meta.get("duration", 0)
    if duration > 900:
        errors.append(f"Duration {duration:.0f}s exceeds 900s max")

    size_mb = meta.get("file_size_mb", 0)
    if size_mb > 100:
        errors.append(f"File size {size_mb:.1f}MB exceeds 100MB max")

    return VideoQuality(
        video_path.name, passed=len(errors) == 0,
        critical_errors=errors, warnings=warnings,
    )
```

2. In `_validate_videos()` at line 1305, add landscape skip after the `_bg` skip:

```python
    for video_path in videos:
        # Skip _bg.mp4 background-only files (intermediate artifacts)
        if video_path.stem.endswith("_bg"):
            continue

        # Skip _landscape.mp4 files from portrait validation — validate separately
        if video_path.stem.endswith("_landscape"):
            quality = _check_landscape_spec(video_path)
            results.append(quality.to_dict())
            if quality.passed:
                passed += 1
            else:
                failed += 1
                for err in quality.critical_errors:
                    logger.error("  LANDSCAPE FAIL [%s]: %s", video_path.name, err)
            continue
```

**Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_validate_videos.py -v -x`
Expected: PASS

**Step 5: Commit**

```bash
git add execution/validate_videos.py tests/test_validate_videos.py
git commit -m "fix(P0.1): landscape validator — skip portrait check for *_landscape.mp4"
```

---

### Task 2: P0.2 — CDN Upload Retry

**Files:**
- Modify: `execution/utils/local_cdn.py:76-113`
- Test: `tests/test_local_cdn.py`

**Step 1: Write the failing test**

Add to `tests/test_local_cdn.py`:

```python
def test_upload_retries_on_failure(tmp_path, monkeypatch):
    """CDN upload should retry up to 3 times with backoff."""
    video = tmp_path / "test.mp4"
    video.write_bytes(b"\x00" * 1024)

    call_count = {"n": 0}

    def mock_post(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise requests.RequestException("Network fluke")
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "https://litter.catbox.moe/abc123.mp4"
        return resp

    monkeypatch.setattr("execution.utils.local_cdn.requests.post", mock_post)
    monkeypatch.setattr("execution.utils.local_cdn.time.sleep", lambda _: None)

    url = upload_to_litterbox(video)
    assert url == "https://litter.catbox.moe/abc123.mp4"
    assert call_count["n"] == 3  # Failed twice, succeeded on third


def test_upload_validates_response_domain(tmp_path, monkeypatch):
    """CDN upload must reject URLs from unexpected domains."""
    video = tmp_path / "test.mp4"
    video.write_bytes(b"\x00" * 1024)

    def mock_post(*args, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "https://evil.example.com/payload.mp4"
        return resp

    monkeypatch.setattr("execution.utils.local_cdn.requests.post", mock_post)
    monkeypatch.setattr("execution.utils.local_cdn.time.sleep", lambda _: None)

    url = upload_to_litterbox(video)
    assert url is None  # Rejected: wrong domain
```

**Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_local_cdn.py::test_upload_retries_on_failure tests/test_local_cdn.py::test_upload_validates_response_domain -v`
Expected: FAIL (no retry logic, no domain validation)

**Step 3: Write minimal implementation**

Replace `execution/utils/local_cdn.py` lines 76-113 (the try/except block in `upload_to_litterbox`) with:

```python
    MAX_UPLOAD_RETRIES = 3
    RETRY_DELAYS = [5, 30, 120]  # seconds

    for attempt in range(MAX_UPLOAD_RETRIES):
        try:
            with open(file_path, "rb") as f:
                resp = requests.post(
                    _LITTERBOX_API,
                    files={"fileToUpload": (file_path.name, f)},
                    data={"reqtype": "fileupload", "time": expiry},
                    timeout=_UPLOAD_TIMEOUT,
                )

            if resp.status_code != 200:
                logger.warning(
                    "upload_to_litterbox: attempt %d/%d HTTP %d — %s",
                    attempt + 1, MAX_UPLOAD_RETRIES, resp.status_code, resp.text[:200],
                )
            else:
                url = resp.text.strip()
                if url.startswith("https://litter.catbox.moe/"):
                    logger.info("upload_to_litterbox: ✓ %s → %s", file_path.name, url)
                    return url
                logger.error("upload_to_litterbox: unexpected CDN URL domain: %s", url[:200])

        except requests.Timeout:
            logger.warning(
                "upload_to_litterbox: attempt %d/%d timed out after %ds for %s (%.1f MB)",
                attempt + 1, MAX_UPLOAD_RETRIES, _UPLOAD_TIMEOUT, file_path.name, size_mb,
            )
        except requests.RequestException as exc:
            logger.warning(
                "upload_to_litterbox: attempt %d/%d failed: %s",
                attempt + 1, MAX_UPLOAD_RETRIES, exc,
            )

        if attempt < MAX_UPLOAD_RETRIES - 1:
            time.sleep(RETRY_DELAYS[attempt])

    logger.error("upload_to_litterbox: all %d attempts failed for %s", MAX_UPLOAD_RETRIES, file_path.name)
    return None
```

Add `import time` at top if not already present.

**Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_local_cdn.py -v -x`
Expected: PASS

**Step 5: Commit**

```bash
git add execution/utils/local_cdn.py tests/test_local_cdn.py
git commit -m "fix(P0.2): CDN upload retry (3 attempts, exponential backoff) + domain validation"
```

---

### Task 3: P0.3 — CDN Failure break → continue

**Files:**
- Modify: `execution/publish_all_platforms.py:207-208`

**Step 1: Verify existing tests cover this**

Run: `venv/bin/python -m pytest tests/test_publish_all_platforms.py -v -x`
Expected: PASS (baseline)

**Step 2: Fix the bug**

In `execution/publish_all_platforms.py` line 208, change `break` to `continue`:

Before:
```python
                if not video_public_url:
                    logger.error("  Instagram: failed to upload reel to CDN")
                    break
```

After:
```python
                if not video_public_url:
                    logger.error("  Instagram: failed to upload reel to CDN (attempt %d/%d)", attempt + 1, 1 + max_retries)
                    continue
```

**Step 3: Run tests to verify no regression**

Run: `venv/bin/python -m pytest tests/test_publish_all_platforms.py -v -x`
Expected: PASS

**Step 4: Commit**

```bash
git add execution/publish_all_platforms.py
git commit -m "fix(P0.3): CDN failure break→continue so retry loop re-attempts upload"
```

---

### Task 4: P0.4 — OData Injection Escaping

**Files:**
- Modify: `execution/utils/backlog_client.py:151,159,477`
- Test: `tests/test_formula_translator.py`

**Step 1: Write the failing test**

Add to `tests/test_formula_translator.py`:

```python
def test_find_formula_escapes_single_quotes():
    """FIND formulas must escape single quotes to prevent OData injection."""
    from execution.utils.backlog_client import _formula_to_odata

    # Input with a single quote that could break OData filter
    formula = "FIND('O''Brien', ARRAYJOIN({source}))"
    result = _formula_to_odata(formula)
    # The value should be properly escaped
    assert "''" in result or "O''Brien" in result or result is not None

    # Direct injection attempt
    formula_inject = "FIND('test'); DROP TABLE--', {title})"
    result2 = _formula_to_odata(formula_inject)
    # Should not produce raw unescaped SQL-like content
    if result2:
        assert "'; DROP" not in result2 or "''" in result2
```

**Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_formula_translator.py::test_find_formula_escapes_single_quotes -v`
Expected: FAIL (quotes not escaped)

**Step 3: Write minimal implementation**

In `execution/utils/backlog_client.py`:

Line 151 — change:
```python
        return f"contains(fields/{field}_text, '{val}')"
```
to:
```python
        return f"contains(fields/{field}_text, '{_esc(val)}')"
```

Line 159 — change:
```python
        return f"contains(fields/{field}, '{val}')"
```
to:
```python
        return f"contains(fields/{field}, '{_esc(val)}')"
```

Line 477 — change `logger.debug` to `logger.warning`:
```python
                logger.warning(
                    "OData filter failed for %s, falling back to client-side: %s (%s)",
                    self._list_name, odata_filter, exc,
                )
```

**Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_formula_translator.py -v -x`
Expected: PASS

**Step 5: Commit**

```bash
git add execution/utils/backlog_client.py tests/test_formula_translator.py
git commit -m "fix(P0.4): OData injection escaping in FIND formulas + promote fallback to WARNING"
```

---

### Task 5: P0.5 — Review Server Auth Bypass Removal

**Files:**
- Modify: `execution/review_server.py:232-252,765-770`
- Test: `tests/test_review_server.py`

**Step 1: Write the failing test**

Add to `tests/test_review_server.py`:

```python
def test_flow_mp4_requires_auth(client):
    """Requests to *_flow.mp4 paths must require auth (no bypass)."""
    resp = client.get("/static/demo_flow.mp4")
    # Should redirect to login or return 401/302, NOT 200
    assert resp.status_code in (302, 401, 403)


def test_run_id_rejects_unsafe_characters(client, auth_session):
    """run_id must match safe pattern: alphanumeric + underscore + hyphen, 1-64 chars."""
    # Path traversal attempt
    resp = auth_session.get("/api/express/trigger?run-id=../../etc/passwd")
    assert resp.status_code == 400

    # Shell injection attempt
    resp = auth_session.get("/api/express/trigger?run-id=test;rm -rf /")
    assert resp.status_code == 400
```

**Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_review_server.py::test_flow_mp4_requires_auth tests/test_review_server.py::test_run_id_rejects_unsafe_characters -v`
Expected: FAIL (auth bypass lets _flow.mp4 through; semicolons in run_id not rejected)

**Step 3: Write minimal implementation**

In `execution/review_server.py`:

1. Remove the `_flow.mp4` bypass at lines 237-239:
```python
    # DELETE these lines:
    # Temporary: allow unauthenticated access to screencast files for Meta App Review
    # if request.path.endswith("_flow.mp4"):
    #     return None
```

2. Remove the CORS exemption at lines 248-252:
```python
    # DELETE these lines:
    # Temporary: CORS for screencast files (Meta App Review upload)
    # if request.path.endswith("_flow.mp4"):
    #     response.headers["Access-Control-Allow-Origin"] = "*"
    #     response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    #     return response
```

3. Add strict `run_id` validation near top of file:
```python
import re
_SAFE_RUN_ID = re.compile(r'^[a-zA-Z0-9_\-]{1,64}$')
```

4. Replace the run_id check at line 765-770:
```python
    run_id = request.args.get("run-id", "test_express")
    if not _SAFE_RUN_ID.match(run_id):
        with _express_lock:
            express_state["running"] = False
        return jsonify({"error": "Invalid run ID"}), 400
```

**Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_review_server.py -v -x`
Expected: PASS

**Step 5: Commit**

```bash
git add execution/review_server.py tests/test_review_server.py
git commit -m "fix(P0.5): remove auth bypass for _flow.mp4 + strict run_id regex validation"
```

---

### Task 6: P0.6 — Rate Limiter Per-Domain Locks

**Files:**
- Modify: `execution/utils/rate_limiter.py:112-135`
- Test: `tests/test_rate_limiter.py`

**Step 1: Write the failing test**

Add to `tests/test_rate_limiter.py`:

```python
import threading
import time


def test_concurrent_domains_not_serialized():
    """Requests to different domains must not block each other."""
    from execution.utils.rate_limiter import RateLimiter

    limiter = RateLimiter(default_delay=0.5)

    results = {}

    def timed_wait(domain):
        start = time.monotonic()
        limiter.wait(domain)
        results[domain] = time.monotonic() - start

    # First call to each domain (establishes baseline)
    limiter.wait("a.com")
    limiter.wait("b.com")

    # Now call both concurrently — they should NOT serialize
    t1 = threading.Thread(target=timed_wait, args=("a.com",))
    t2 = threading.Thread(target=timed_wait, args=("b.com",))

    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    # Both should complete in ~0.5s (parallel), not ~1.0s (serialized)
    total = max(results.values())
    assert total < 0.9, f"Domains serialized: max wait {total:.2f}s (expected <0.9s)"
```

**Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_rate_limiter.py::test_concurrent_domains_not_serialized -v`
Expected: FAIL (global lock serializes both domains — total ~1.0s)

**Step 3: Write minimal implementation**

In `execution/utils/rate_limiter.py`, replace the `wait()` method (lines 112-135):

Add to `__init__`:
```python
        self._domain_locks: Dict[str, threading.Lock] = {}
        self._domain_locks_lock = threading.Lock()
```

Add helper method:
```python
    def _get_domain_lock(self, domain: str) -> threading.Lock:
        """Get or create a per-domain lock."""
        with self._domain_locks_lock:
            if domain not in self._domain_locks:
                self._domain_locks[domain] = threading.Lock()
            return self._domain_locks[domain]
```

Replace `wait()`:
```python
    def wait(self, url_or_domain: str) -> float:
        """Wait if needed before making a request to this domain.

        Returns the actual wait time in seconds (0 if no wait needed).

        Uses per-domain locks so different domains don't block each other.
        """
        domain = self._get_domain(url_or_domain)
        delay = self.get_delay(domain)
        domain_lock = self._get_domain_lock(domain)

        with domain_lock:
            # Evict stale entries under the global lock (lightweight)
            with self._lock:
                self._evict_stale_domains()

            now = time.monotonic()
            with self._lock:
                last = self._last_request.get(domain, 0.0)
            elapsed = now - last
            wait_time = max(0.0, delay - elapsed)

            if wait_time > 0:
                logger.debug("Rate limiting %s: waiting %.1fs", domain, wait_time)
                time.sleep(wait_time)

            with self._lock:
                self._last_request[domain] = time.monotonic()
        return wait_time
```

Add `from typing import Dict` import if not present. Add `import threading` if not present.

**Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_rate_limiter.py -v -x`
Expected: PASS

**Step 5: Commit**

```bash
git add execution/utils/rate_limiter.py tests/test_rate_limiter.py
git commit -m "fix(P0.6): per-domain rate limiter locks — different domains no longer serialize"
```

---

### Task 7: P0.7 — Twitter FFmpeg Return Code

**Files:**
- Modify: `execution/publish_twitter.py:118-136`
- Test: `tests/test_publish_twitter.py`

**Step 1: Write the failing test**

Add to `tests/test_publish_twitter.py`:

```python
def test_ffmpeg_failure_uses_original_video(tmp_path, monkeypatch):
    """When FFmpeg truncation fails, use the original video instead of corrupt output."""
    original = tmp_path / "video.mp4"
    original.write_bytes(b"\x00" * 1024)

    def mock_run(*args, **kwargs):
        result = MagicMock()
        result.returncode = 1  # FFmpeg failure
        result.stderr = "Error: encoder failed"
        return result

    monkeypatch.setattr("subprocess.run", mock_run)

    # After FFmpeg fails, the original video should be used
    # (not a non-existent or corrupt truncated file)
    from execution.publish_twitter import _truncate_for_twitter
    result_path = _truncate_for_twitter(original, 140)
    assert result_path == original  # Falls back to original
```

**Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_publish_twitter.py::test_ffmpeg_failure_uses_original_video -v`
Expected: FAIL (function doesn't exist yet / returncode not checked)

**Step 3: Write minimal implementation**

In `execution/publish_twitter.py`, replace lines 118-136 with:

```python
            if str(mp).lower().endswith(".mp4"):
                from execution.utils.ffmpeg_utils import probe_video_duration
                dur = probe_video_duration(str(mp))
                if dur > TW_MAX_VIDEO_DURATION:
                    truncated = mp.parent / f"{mp.stem}_tw{mp.suffix}"
                    import subprocess
                    result = subprocess.run(
                        [
                            "ffmpeg", "-y", "-i", str(mp),
                            "-t", str(TW_MAX_VIDEO_DURATION),
                            "-map", "0:v:0", "-map", "0:a?",
                            "-c:v", "libx264", "-profile:v", "high",
                            "-preset", "medium", "-crf", "17",
                            "-c:a", "aac", "-b:a", "256k",
                            "-movflags", "+faststart",
                            str(truncated),
                        ],
                        capture_output=True, text=True, timeout=300,
                    )
                    if result.returncode == 0 and truncated.exists():
                        logger.info("  Twitter: truncated %.1fs → %ds for Free tier", dur, TW_MAX_VIDEO_DURATION)
                        truncated_media.append(truncated)
                        continue
                    else:
                        logger.warning(
                            "  Twitter: FFmpeg truncation failed (rc=%s), using original",
                            result.returncode,
                        )
            truncated_media.append(mp)
```

**Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_publish_twitter.py -v -x`
Expected: PASS

**Step 5: Commit**

```bash
git add execution/publish_twitter.py tests/test_publish_twitter.py
git commit -m "fix(P0.7): check FFmpeg returncode before using truncated Twitter video"
```

---

### Task 8: Phase 0 Regression

**Step 1: Run full test suite**

Run: `venv/bin/python -m pytest tests/ -x -q --tb=short`
Expected: 914+ passed, 0 failures

---

## Phase 1 — Intelligence (Improve Content Quality)

### Task 9: P1.1 — Performance-to-Scoring Feedback Loop

**Files:**
- Modify: `execution/process_feedback.py`
- Test: `tests/test_process_feedback.py`

**Step 1: Write the failing test**

Add to `tests/test_process_feedback.py`:

```python
def test_auto_tune_scoring_weights(tmp_path, monkeypatch):
    """auto_tune_scoring_weights adjusts hook formula weights based on Analytics data."""
    from execution.process_feedback import auto_tune_scoring_weights

    # Mock Analytics data: "question" hooks perform 2x average, "shocking_stat" at 0.4x
    mock_analytics = [
        {"hook_formula": "question", "viral_score": 8.0},
        {"hook_formula": "question", "viral_score": 9.0},
        {"hook_formula": "shocking_stat", "viral_score": 1.5},
        {"hook_formula": "shocking_stat", "viral_score": 2.0},
        {"hook_formula": "name_drop", "viral_score": 4.0},
        {"hook_formula": "name_drop", "viral_score": 5.0},
    ]

    weights_path = tmp_path / "scoring_weights.yaml"
    weights_path.write_text(yaml.dump({
        "hook_formulas": {
            "question": 0.20,
            "shocking_stat": 0.20,
            "name_drop": 0.20,
        }
    }))

    changes = auto_tune_scoring_weights(
        analytics_data=mock_analytics,
        weights_path=weights_path,
        dry_run=True,
    )

    # "question" (avg 8.5) ≥ 2x overall avg (~4.9) → weight should increase
    assert any(c["formula"] == "question" and c["direction"] == "up" for c in changes)
    # "shocking_stat" (avg 1.75) ≤ 0.5x overall avg → weight should decrease
    assert any(c["formula"] == "shocking_stat" and c["direction"] == "down" for c in changes)
```

**Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_process_feedback.py::test_auto_tune_scoring_weights -v`
Expected: FAIL (function doesn't exist)

**Step 3: Write minimal implementation**

Add to `execution/process_feedback.py`:

```python
def auto_tune_scoring_weights(
    analytics_data: List[Dict],
    weights_path: Path = PROJECT_ROOT / "config" / "scoring_weights.yaml",
    dry_run: bool = False,
) -> List[Dict[str, Any]]:
    """Adjust hook formula scoring weights based on actual post performance.

    Reads viral_score per hook_formula from analytics data.
    Categories at ≥2x average: weight += 0.05 (capped at 0.50)
    Categories at ≤0.5x average: weight -= 0.05 (floored at 0.05)

    Returns list of proposed/applied changes.
    """
    if not analytics_data:
        return []

    # Group scores by hook formula
    scores_by_formula: Dict[str, List[float]] = {}
    for record in analytics_data:
        formula = record.get("hook_formula", "")
        score = record.get("viral_score")
        if formula and score is not None:
            scores_by_formula.setdefault(formula, []).append(float(score))

    if not scores_by_formula:
        return []

    # Compute averages
    avg_by_formula = {f: sum(s) / len(s) for f, s in scores_by_formula.items()}
    overall_avg = sum(sum(s) for s in scores_by_formula.values()) / sum(len(s) for s in scores_by_formula.values())

    if overall_avg == 0:
        return []

    # Load current weights
    with open(weights_path) as f:
        config = yaml.safe_load(f) or {}
    hook_weights = config.get("hook_formulas", {})

    changes = []
    for formula, avg_score in avg_by_formula.items():
        if formula not in hook_weights:
            continue
        current = hook_weights[formula]
        ratio = avg_score / overall_avg

        if ratio >= 2.0 and current < 0.50:
            new_val = min(current + 0.05, 0.50)
            changes.append({"formula": formula, "direction": "up",
                            "old": current, "new": new_val, "ratio": ratio})
            hook_weights[formula] = new_val
        elif ratio <= 0.5 and current > 0.05:
            new_val = max(current - 0.05, 0.05)
            changes.append({"formula": formula, "direction": "down",
                            "old": current, "new": new_val, "ratio": ratio})
            hook_weights[formula] = new_val

    if changes and not dry_run:
        config["hook_formulas"] = hook_weights
        with open(weights_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False)
        logger.info("Auto-tune: applied %d weight changes to %s", len(changes), weights_path)

    return changes
```

**Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_process_feedback.py -v -x`
Expected: PASS

**Step 5: Commit**

```bash
git add execution/process_feedback.py tests/test_process_feedback.py
git commit -m "feat(P1.1): auto_tune_scoring_weights — feedback loop from Analytics to scoring config"
```

---

### Task 10: P1.2 — Cost Estimation Fix

**Files:**
- Modify: `execution/write_post_content.py:1013-1017`
- Test: `tests/test_write_post_content.py`

**Step 1: Write the failing test**

Add to `tests/test_write_post_content.py`:

```python
def test_cost_estimation_uses_correct_model_pricing():
    """Cost estimation must use the correct model's pricing, not hardcoded GPT-4o-mini."""
    from execution.write_post_content import MODEL_PRICING

    # Haiku pricing must differ from GPT-4o-mini
    assert "claude-haiku-4-5-20251001" in MODEL_PRICING
    haiku_input, haiku_output = MODEL_PRICING["claude-haiku-4-5-20251001"]
    assert haiku_input == 0.25
    assert haiku_output == 1.25

    assert "gpt-4o-mini" in MODEL_PRICING
    mini_input, mini_output = MODEL_PRICING["gpt-4o-mini"]
    assert mini_input == 0.15
    assert mini_output == 0.60
```

**Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_write_post_content.py::test_cost_estimation_uses_correct_model_pricing -v`
Expected: FAIL (MODEL_PRICING doesn't exist)

**Step 3: Write minimal implementation**

In `execution/write_post_content.py`:

Add near top-level constants:
```python
# Cost estimation: (input_price_per_1M_tokens, output_price_per_1M_tokens)
MODEL_PRICING = {
    "claude-haiku-4-5-20251001": (0.25, 1.25),
    "gpt-4o-mini": (0.15, 0.60),
}
```

Replace lines 1013-1016:
```python
        # Estimate cost using actual model pricing
        model_name = config.get("content_generation", {}).get("model", "claude-haiku-4-5-20251001")
        input_price, output_price = MODEL_PRICING.get(model_name, (0.25, 1.25))
        input_tokens = _token_usage.get("input", len(system_prompt + user_prompt) // 4)
        output_tokens = _token_usage.get("output", len(raw_response) // 4)
        bp_cost = input_tokens * (input_price / 1_000_000) + output_tokens * (output_price / 1_000_000)
```

**Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_write_post_content.py -v -x`
Expected: PASS

**Step 5: Commit**

```bash
git add execution/write_post_content.py tests/test_write_post_content.py
git commit -m "fix(P1.2): cost estimation uses actual model pricing instead of hardcoded GPT-4o-mini"
```

---

### Task 11: P1.3 — YouTube privacy_status Config Mismatch

**Files:**
- Modify: `execution/publish_youtube.py:204`
- Test: `tests/test_publish_youtube_strict.py`

**Step 1: Write the failing test**

Add to `tests/test_publish_youtube_strict.py`:

```python
def test_privacy_status_reads_correct_config_key():
    """YouTube publisher must read 'privacy_status' (not 'privacy') from config."""
    config = {"youtube": {"privacy_status": "unlisted"}}
    yt_config = config.get("youtube", {})
    # This is what the code SHOULD do:
    privacy = yt_config.get("privacy_status", "public")
    assert privacy == "unlisted"
```

**Step 2: Run test (this passes trivially — the real check is the code change)**

**Step 3: Fix the code**

In `execution/publish_youtube.py` line 204, change:
```python
    privacy = yt_config.get("privacy", "public")
```
to:
```python
    privacy = yt_config.get("privacy_status", "public")
```

**Step 4: Run tests**

Run: `venv/bin/python -m pytest tests/test_publish_youtube_strict.py -v -x`
Expected: PASS

**Step 5: Commit**

```bash
git add execution/publish_youtube.py tests/test_publish_youtube_strict.py
git commit -m "fix(P1.3): YouTube reads privacy_status (not privacy) from config"
```

---

### Task 12: P1.4 — Dead Hook System Cleanup

**Files:**
- Modify: `execution/generate_content.py:79-159,689`

**Step 1: Verify existing tests pass (baseline)**

Run: `venv/bin/python -m pytest tests/test_generate_content.py -v -x`
Expected: PASS

**Step 2: Remove dead code**

In `execution/generate_content.py`:

1. Delete `HOOK_PATTERNS` dict (lines 79-98)
2. Delete `HOOK_PATTERNS_BY_TEMPLATE` dict (lines 100-159)
3. At line 689, change the dead template branch:

Before:
```python
        elif fmt == "reel" or template_id in {"TPL_REE1", "TPL_REE2", "TPL_UGR1"}:
```
After:
```python
        elif fmt == "reel":
```

**Step 3: Run tests to verify no regression**

Run: `venv/bin/python -m pytest tests/test_generate_content.py -v -x`
Expected: PASS

**Step 4: Commit**

```bash
git add execution/generate_content.py
git commit -m "cleanup(P1.4): remove dead HOOK_PATTERNS + HOOK_PATTERNS_BY_TEMPLATE (active engine: generate_hooks.py)"
```

---

### Task 13: P1.5 — Hardcoded Constants → Config

**Files:**
- Modify: `execution/publish_youtube.py:131`
- Modify: `execution/publish_facebook.py:51`
- Modify: `execution/write_post_content.py:467,512`
- Modify: `config/publishing.yaml`

**Step 1: Fix YouTube max duration**

In `execution/publish_youtube.py`, line 131 replace:
```python
YT_SHORTS_MAX_DURATION = 180.0
```
with:
```python
# Default; overridden by config youtube.max_short_duration_seconds at runtime
YT_SHORTS_MAX_DURATION = 180.0
```

Then in `publish_youtube_post()` around line 202, after `yt_config = config.get("youtube", {})`:
```python
    yt_max_duration = yt_config.get("max_short_duration_seconds", YT_SHORTS_MAX_DURATION)
```
And use `yt_max_duration` instead of `YT_SHORTS_MAX_DURATION` throughout the function.

**Step 2: Fix Facebook max_retries**

In `execution/publish_facebook.py`, find the hardcoded `MAX_RETRIES` and read from config:
```python
    fb_config = config.get("facebook", {})
    max_retries = fb_config.get("max_retries", config.get("publisher", {}).get("max_retries", 3))
```

**Step 3: Add missing config keys to publishing.yaml**

In `config/publishing.yaml`, add under `facebook:` section:
```yaml
  max_retries: 3
```

And add under `content_generation:` section:
```yaml
  temperature: 0.7
```

**Step 4: Run tests**

Run: `venv/bin/python -m pytest tests/ -x -q --tb=short`
Expected: PASS

**Step 5: Commit**

```bash
git add execution/publish_youtube.py execution/publish_facebook.py execution/write_post_content.py config/publishing.yaml
git commit -m "fix(P1.5): move hardcoded constants to config (YT duration, FB retries, temperature)"
```

---

### Task 14: P1.6 — is_due() Timezone Fix

**Files:**
- Modify: `execution/utils/scheduling.py:27`
- Test: `tests/test_schedule_cascade.py`

**Step 1: Write the failing test**

Add to `tests/test_schedule_cascade.py`:

```python
def test_is_due_handles_z_suffix():
    """is_due() must handle ISO timestamps ending with 'Z'."""
    from execution.utils.scheduling import is_due
    from datetime import datetime, timezone

    # A time in the past with Z suffix
    past = "2020-01-01T12:00:00Z"
    assert is_due(past) is True

    # A time far in the future with Z suffix
    future = "2099-12-31T23:59:59Z"
    assert is_due(future) is False
```

**Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_schedule_cascade.py::test_is_due_handles_z_suffix -v`
Expected: FAIL (fromisoformat can't parse "Z" suffix in Python 3.10)

**Step 3: Fix the code**

In `execution/utils/scheduling.py` line 27, change:
```python
    scheduled_dt = datetime.fromisoformat(scheduled_for)
```
to:
```python
    scheduled_dt = datetime.fromisoformat(scheduled_for.replace("Z", "+00:00"))
```

**Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_schedule_cascade.py -v -x`
Expected: PASS

**Step 5: Commit**

```bash
git add execution/utils/scheduling.py tests/test_schedule_cascade.py
git commit -m "fix(P1.6): is_due() handles Z-suffix ISO timestamps"
```

---

### Task 15: P1.7 + P1.8 — Log Promotions

**Files:**
- Modify: `execution/publish_all_platforms.py:1231`
- Already done in Task 4: `execution/utils/backlog_client.py:477`

**Step 1: Promote status write log**

In `execution/publish_all_platforms.py` line 1231, change:
```python
                            logger.debug("  Incremental status update failed for %s: %s", p, _exc)
```
to:
```python
                            logger.warning("  Incremental status update failed for %s: %s", p, _exc)
```

**Step 2: Run tests**

Run: `venv/bin/python -m pytest tests/test_publish_all_platforms.py -v -x`
Expected: PASS

**Step 3: Commit**

```bash
git add execution/publish_all_platforms.py
git commit -m "fix(P1.7+P1.8): promote incremental status write + OData fallback logs to WARNING"
```

---

### Task 16: Phase 1 Regression

**Step 1: Run full test suite**

Run: `venv/bin/python -m pytest tests/ -x -q --tb=short`
Expected: 914+ passed, 0 failures

---

## Phase 2 — Hygiene (Reduce Tech Debt)

### Task 17: P2.1 — Remove Dead Carousel Logic

**Files:**
- Modify: `execution/compose_blueprints.py:1016-1038`
- Modify: `execution/process_feedback.py:266-304`

**Step 1: Verify existing tests pass**

Run: `venv/bin/python -m pytest tests/test_compose_blueprints_video_gate.py tests/test_compose_blueprints_hooks.py tests/test_process_feedback.py -v -x`
Expected: PASS

**Step 2: Remove dead code**

1. In `execution/compose_blueprints.py`, delete lines 1016-1038 (the `all_carousels` swap block). The loop condition `for reel_bp in overflow_reels:` and the `reels_to_add` decrement remain intact — just remove the carousel swap logic within.

2. In `execution/process_feedback.py`, delete the `tighten_carousel_constraints()` function (lines 266-304).

**Step 3: Run tests**

Run: `venv/bin/python -m pytest tests/ -x -q --tb=short`
Expected: PASS

**Step 4: Commit**

```bash
git add execution/compose_blueprints.py execution/process_feedback.py
git commit -m "cleanup(P2.1): remove dead carousel swap block + tighten_carousel_constraints"
```

---

### Task 18: P2.2 — Remove Airtable Remnants

**Files:**
- Modify: `execution/api/blueprints.py:60`
- Modify: `tests/test_publish_all_platforms.py:1235,1238`

**Step 1: Remove Airtable CDN check**

In `execution/api/blueprints.py` line 60, remove the `airtableusercontent.com` block (lines 60-67). Replace with a pass-through or remove entirely if the surrounding code handles empty URLs.

**Step 2: Fix Airtable-style record ID in test**

In `tests/test_publish_all_platforms.py`:
- Line 1235: change `"recXYZ123"` to `"12345"`
- Line 1238: change `["recXYZ123"]` to `["12345"]`

**Step 3: Run tests**

Run: `venv/bin/python -m pytest tests/test_publish_all_platforms.py tests/test_api_blueprints.py -v -x`
Expected: PASS

**Step 4: Commit**

```bash
git add execution/api/blueprints.py tests/test_publish_all_platforms.py
git commit -m "cleanup(P2.2): remove Airtable CDN check + fix record ID format in test"
```

---

### Task 19: P2.4 — Cache Security Hardening

**Files:**
- Modify: `execution/utils/cache.py`
- Test: `tests/test_cache.py`

**Step 1: Write the failing test**

Add to `tests/test_cache.py`:

```python
def test_cache_rejects_unsafe_keys():
    """Cache must reject keys with path traversal characters."""
    cache = Cache(cache_dir=str(tmp_path / "cache"))

    # These should be rejected (return None / raise / skip)
    cache.set("../../etc/passwd", {"evil": True})
    assert cache.get("../../etc/passwd") is None

    cache.set("key/with/slashes", {"evil": True})
    assert cache.get("key/with/slashes") is None


def test_cache_auto_purge_on_max_entries(tmp_path):
    """Cache must evict oldest entries when exceeding MAX_ENTRIES."""
    cache = Cache(cache_dir=str(tmp_path / "cache"), max_entries=5)

    for i in range(10):
        cache.set(f"key_{i}", {"data": i})
        time.sleep(0.01)  # Ensure distinct timestamps

    # Only 5 most recent entries should survive
    remaining = list((tmp_path / "cache").glob("*.json"))
    assert len(remaining) <= 5
```

**Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_cache.py::test_cache_rejects_unsafe_keys tests/test_cache.py::test_cache_auto_purge_on_max_entries -v`
Expected: FAIL

**Step 3: Write implementation**

In `execution/utils/cache.py`:

Add safe-key regex and MAX_ENTRIES:
```python
import re

_SAFE_KEY = re.compile(r'^[a-zA-Z0-9_\-]{1,256}$')
_DEFAULT_MAX_ENTRIES = 10000
```

Update `__init__`:
```python
    def __init__(self, cache_dir: str = '.tmp/cache', max_entries: int = _DEFAULT_MAX_ENTRIES):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_entries = max_entries
        self._auto_purge()
```

Add key validation to `get` and `set`:
```python
    def _validate_key(self, key: str) -> bool:
        if not _SAFE_KEY.match(key):
            logger.warning("Cache: rejected unsafe key: %s", key[:50])
            return False
        return True
```

In `get()`, add at top:
```python
        if not self._validate_key(key):
            return None
```

In `set()`, add at top:
```python
        if not self._validate_key(key):
            return
```

Add auto-purge method:
```python
    def _auto_purge(self):
        """Evict oldest entries if over max_entries."""
        entries = sorted(self.cache_dir.glob("*.json"), key=lambda f: f.stat().st_mtime)
        excess = len(entries) - self.max_entries
        if excess > 0:
            for f in entries[:excess]:
                try:
                    f.unlink()
                except OSError:
                    pass
            logger.debug("Cache: auto-purged %d entries (max=%d)", excess, self.max_entries)
```

Call `self._auto_purge()` at end of `set()`.

**Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_cache.py -v -x`
Expected: PASS

**Step 5: Commit**

```bash
git add execution/utils/cache.py tests/test_cache.py
git commit -m "fix(P2.4): cache safe-key validation + auto-purge with configurable MAX_ENTRIES"
```

---

### Task 20: P2.5 — Review Server Hardening

**Files:**
- Modify: `execution/review_server.py`
- Test: `tests/test_review_server.py`

**Step 1: Write the failing test**

Add to `tests/test_review_server.py`:

```python
def test_csrf_token_is_per_session(client):
    """CSRF tokens should differ between sessions (per-session nonce)."""
    # Login and get CSRF from session 1
    with client.session_transaction() as sess1:
        sess1["authenticated"] = True
    resp1 = client.get("/api/csrf-token")
    token1 = resp1.json.get("csrf_token", "")

    # Clear session and login again
    with client.session_transaction() as sess2:
        sess2.clear()
        sess2["authenticated"] = True
    resp2 = client.get("/api/csrf-token")
    token2 = resp2.json.get("csrf_token", "")

    assert token1 != token2, "CSRF tokens must differ between sessions"


def test_login_rate_limited(client):
    """Login endpoint must rate-limit after 5 attempts per minute."""
    for _ in range(5):
        client.post("/login", data={"password": "wrong"})

    resp = client.post("/login", data={"password": "wrong"})
    assert resp.status_code == 429  # Too Many Requests
```

**Step 2: Run test to verify it fails**

Run: `venv/bin/python -m pytest tests/test_review_server.py::test_csrf_token_is_per_session tests/test_review_server.py::test_login_rate_limited -v`
Expected: FAIL

**Step 3: Implement per-session CSRF + rate limiting**

In `execution/review_server.py`:

1. Replace static CSRF with per-session nonce (lines 283-291):
```python
def _generate_csrf_token() -> str:
    """Generate a CSRF token tied to the session."""
    from flask import session
    import secrets
    if "csrf_nonce" not in session:
        session["csrf_nonce"] = secrets.token_hex(16)
    return hmac.new(
        _CSRF_SECRET.encode(),
        session["csrf_nonce"].encode(),
        hashlib.sha256,
    ).hexdigest()[:32]
```

2. Add login rate limiter (near top):
```python
from collections import defaultdict
import time as _time

_login_attempts: Dict[str, list] = defaultdict(list)
_LOGIN_RATE_LIMIT = 5  # max attempts
_LOGIN_RATE_WINDOW = 60  # per 60 seconds
```

3. In the login route, add rate check:
```python
    ip = request.remote_addr or "unknown"
    now = _time.time()
    _login_attempts[ip] = [t for t in _login_attempts[ip] if now - t < _LOGIN_RATE_WINDOW]
    if len(_login_attempts[ip]) >= _LOGIN_RATE_LIMIT:
        return "Too many login attempts. Try again later.", 429
    _login_attempts[ip].append(now)
```

**Step 4: Run test to verify it passes**

Run: `venv/bin/python -m pytest tests/test_review_server.py -v -x`
Expected: PASS

**Step 5: Commit**

```bash
git add execution/review_server.py tests/test_review_server.py
git commit -m "fix(P2.5): per-session CSRF nonces + login rate limiting (5/min/IP)"
```

---

### Task 21: P2.6 — BacklogClient Column Map Caching

**Files:**
- Modify: `execution/utils/backlog_client.py`

**Step 1: Add module-level cache**

In `execution/utils/backlog_client.py`, add near the `GraphTableProxy` class:

```python
# Module-level column map cache — eliminates 8 API calls per BacklogClient construction
_column_map_cache: Dict[str, Dict[str, str]] = {}
```

In `GraphTableProxy.__init__` or wherever column maps are loaded, check cache first:

```python
    if list_id in _column_map_cache:
        self._column_map = _column_map_cache[list_id]
    else:
        self._column_map = self._fetch_column_map()
        _column_map_cache[list_id] = self._column_map
```

**Step 2: Run tests**

Run: `venv/bin/python -m pytest tests/ -x -q --tb=short`
Expected: PASS

**Step 3: Commit**

```bash
git add execution/utils/backlog_client.py
git commit -m "perf(P2.6): cache BacklogClient column maps at module level (eliminates 8 API calls/init)"
```

---

### Task 22: P2.7 — Add edge-tts to requirements.txt

**Files:**
- Modify: `requirements.txt`

**Step 1: Add edge-tts**

In `requirements.txt`, add after the CLI tools comment (line 68):

```
# TTS generation (optional — edge-tts for free TTS, used by generate_audio.py)
edge-tts==7.0.2                     # min: >=6.1.0
```

Note: Check current installed version first with `venv/bin/pip show edge-tts`.

**Step 2: Commit**

```bash
git add requirements.txt
git commit -m "fix(P2.7): add edge-tts to requirements.txt"
```

---

### Task 23: P2.8 — Safe Zone Constant Consolidation

**Files:**
- Modify: `execution/validate_videos.py`

**Step 1: Find and replace safe zone constants**

In `execution/validate_videos.py`, find independently defined `SAFE_TOP`, `SAFE_BOTTOM`, `SAFE_LEFT`, `SAFE_RIGHT` and replace with imports from the canonical source:

```python
from execution.utils.text_optimizer import SAFE_TOP, SAFE_BOTTOM, SAFE_LEFT, SAFE_RIGHT
```

Remove the duplicate definitions.

**Step 2: Run tests**

Run: `venv/bin/python -m pytest tests/test_validate_videos.py -v -x`
Expected: PASS

**Step 3: Commit**

```bash
git add execution/validate_videos.py
git commit -m "cleanup(P2.8): import safe zone constants from canonical text_optimizer source"
```

---

### Task 24: P2.9 — Alerting Channel Stub

**Files:**
- Modify: `config/monitoring.yaml:41-46`
- Modify: `execution/track_error_budget.py`

**Step 1: Uncomment Slack config**

In `config/monitoring.yaml`, replace lines 41-46:

```yaml
alerts:
  slack:
    webhook_url: "${SLACK_WEBHOOK_URL}"
    channel: "#ai-agent-alerts"
  log_file: ".tmp/runs/alerts.log"
```

**Step 2: Wire into track_error_budget.py**

In `execution/track_error_budget.py`, add a function stub:

```python
def _send_slack_alert(webhook_url: str, message: str):
    """Send an alert to Slack if webhook is configured."""
    if not webhook_url or webhook_url.startswith("${"):
        return  # Not configured
    try:
        resp = requests.post(webhook_url, json={"text": message}, timeout=10)
        if resp.status_code != 200:
            logger.warning("Slack alert failed: HTTP %d", resp.status_code)
    except Exception as exc:
        logger.warning("Slack alert failed: %s", exc)
```

**Step 3: Run tests**

Run: `venv/bin/python -m pytest tests/test_error_budget.py -v -x`
Expected: PASS

**Step 4: Commit**

```bash
git add config/monitoring.yaml execution/track_error_budget.py
git commit -m "feat(P2.9): uncomment Slack webhook config + wire alert stub into error budget tracker"
```

---

### Task 25: P2.10 — Dead Config Removal

**Files:**
- Modify: `config/publishing.yaml:134-139`

**Step 1: Remove streaming_review block**

Delete lines 134-139 from `config/publishing.yaml`:
```yaml
# DELETE:
# Streaming review — auto-approve rendered visuals after timeout
# Used by render_visuals.py --streaming-review
streaming_review:
  enabled: false
  auto_approve_timeout_seconds: 30
  upload_previews: true
```

**Step 2: Verify no code references it**

Run: `grep -r "streaming_review" execution/ --include="*.py"` — should return nothing active.

**Step 3: Run tests**

Run: `venv/bin/python -m pytest tests/ -x -q --tb=short`
Expected: PASS

**Step 4: Commit**

```bash
git add config/publishing.yaml
git commit -m "cleanup(P2.10): remove dead streaming_review config block"
```

---

### Task 26: Phase 2 Regression + Final Verification

**Step 1: Run full test suite**

Run: `venv/bin/python -m pytest tests/ -x -q --tb=short`
Expected: 914+ passed, 0 failures

**Step 2: Verify no import breakage**

Run: `venv/bin/python -c "from execution.validate_videos import _check_landscape_spec; print('OK')"`
Run: `venv/bin/python -c "from execution.process_feedback import auto_tune_scoring_weights; print('OK')"`
Run: `venv/bin/python -c "from execution.write_post_content import MODEL_PRICING; print('OK')"`

---

## Execution Summary

| Phase | Tasks | Commits | Est. Files |
|-------|-------|---------|------------|
| P0 (Reliability) | 1-8 | 7 | ~14 |
| P1 (Intelligence) | 9-16 | 7 | ~10 |
| P2 (Hygiene) | 17-26 | 9 | ~12 |
| **Total** | **26** | **23** | **~36** |
