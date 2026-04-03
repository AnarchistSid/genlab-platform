# Video Review App Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Upgrade review_server.py from text-only express dashboard to a mobile-friendly video review app with video playback, batch operations, and optional auto-approve timer.

**Architecture:** Extend the existing `execution/review_server.py` (Flask + SocketIO embedded SPA). Add 3 new API routes (media serving, batch review, settings) and rewrite the embedded HTML/CSS/JS to include a video player, carousel thumbnails, batch selection panel, and swipe gestures. All changes in a single file.

**Tech Stack:** Flask, flask-socketio, HTML5 `<video>`, vanilla JS (DOM API — NO innerHTML), CSS flexbox (mobile-first)

---

## Context for Implementer

**Existing file:** `execution/review_server.py` (977 lines). It's a Flask + SocketIO app with an embedded HTML SPA (the `DASHBOARD_HTML` string constant starting at line 421). The frontend uses pure DOM API (`document.createElement`, `textContent`, `addEventListener`) — NO innerHTML with untrusted data. This is a security requirement enforced by a pre-commit hook.

**Key data structure — `normalize_blueprint()` output** (line 344):
```python
{
    "id": "record_id_or_candidate_id",
    "source": "lists" | "local",
    "title": "story title",
    "hook": "hook text",
    "caption": "caption text",
    "slide_content": "Slide 1: ... | Slide 2: ...",
    "format": "carousel" | "reel",
    "template_id": "template_name",
    "status": "DRAFTED" | "VISUAL_READY",
    "urgency": "CRITICAL" | "HIGH" | "MEDIUM" | "LOW",
    "urgency_score": 0.85,
    "category": "template_category",
    "hashtags": "#ai #tech",
    "slide_previews": [],    # Microsoft Lists attachments (URLs)
    "candidate_id": "sha256_hash",
}
```

**Missing from normalize_blueprint():** `visual_paths` — a JSON string containing an array of absolute file paths to rendered PNGs and MP4s. Format: `'["/abs/path/slide_1.png", "/abs/path/reel.mp4"]'`. This field exists on Microsoft Lists records and in local blueprint_pack.json files but is NOT currently passed through to the frontend.

**Where rendered media lives locally:**
- `.tmp/runs/<run_id>/rendered/*.mp4` — reel videos
- `.tmp/runs/<run_id>/rendered/*.png` — carousel slides
- `.tmp/visual_output/<candidate_id>/*.png` — Microsoft Lists visual output (when render_visuals.py runs)

**Security constraint:** All frontend code MUST use `document.createElement()` + `textContent` / `setAttribute()`. NEVER use `innerHTML` with any data that originated from blueprints, Microsoft Lists, or user input. The pre-commit hook (PreToolUse:Write) blocks innerHTML with untrusted content.

---

### Task 1: Add `visual_paths` to `normalize_blueprint()` + media API route

**Files:**
- Modify: `execution/review_server.py:344-382` (normalize_blueprint function)
- Modify: `execution/review_server.py:56-58` (add media route after existing routes section)

**Step 1: Add visual_paths to normalize_blueprint()**

In `execution/review_server.py`, find the `normalize_blueprint()` function (line 344). Add `visual_paths` to both the Microsoft Lists and local return dicts.

For the **lists** branch (line 348-364), add after line 363 (`"candidate_id"`):
```python
            "visual_paths": _parse_visual_paths(fields.get("visual_paths", "[]")),
```

For the **local** branch (line 366-382), add after line 381 (`"candidate_id"`):
```python
            "visual_paths": _parse_visual_paths(bp.get("visual_paths", "[]")),
```

**Step 2: Add the `_parse_visual_paths` helper**

Add this function right after `_format_slides()` (after line 398):

```python
def _parse_visual_paths(raw):
    """Parse visual_paths field — returns list of absolute path strings."""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(p) for p in raw if p]
    if isinstance(raw, str):
        try:
            paths = json.loads(raw)
            if isinstance(paths, list):
                return [str(p) for p in paths if p]
        except (json.JSONDecodeError, TypeError):
            pass
    return []
```

**Step 3: Add the `/api/media/<path>` route**

Add this route right after the `/api/local/blueprints` route (after line 175), before the Express Pipeline Runner section:

```python
import mimetypes
from flask import send_file, abort

@app.route("/api/media/<path:filepath>")
def serve_media(filepath):
    """Serve local media files (PNG/MP4) with path validation.

    Accepts absolute paths or paths relative to PROJECT_ROOT.
    Only serves files under PROJECT_ROOT or .tmp/ directories.
    Supports HTTP Range requests for video seeking.
    """
    # Reconstruct absolute path
    if filepath.startswith("/"):
        abs_path = Path(filepath)
    else:
        abs_path = PROJECT_ROOT / filepath

    abs_path = abs_path.resolve()

    # Security: only serve files under PROJECT_ROOT
    try:
        abs_path.relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        logger.warning("Blocked media request outside PROJECT_ROOT: %s", abs_path)
        abort(403)

    if not abs_path.is_file():
        abort(404)

    # Only serve media files
    suffix = abs_path.suffix.lower()
    allowed = {".png", ".jpg", ".jpeg", ".mp4", ".webm", ".gif"}
    if suffix not in allowed:
        abort(403)

    mime = mimetypes.guess_type(str(abs_path))[0] or "application/octet-stream"
    return send_file(str(abs_path), mimetype=mime, conditional=True)
```

Note: `conditional=True` enables Flask's built-in Range request support for video seeking.

**Step 4: Add mimetypes import at top of file**

Add `import mimetypes` to the imports section (after line 18, `import subprocess`). Also add `from flask import Flask, jsonify, request, Response, send_file, abort` — update the existing import on line 32.

**Step 5: Verify the route works**

Run:
```bash
cd /Users/anarchistsid/GenLab/Content\ Scraper
./venv/bin/python -c "
from execution.review_server import app
client = app.test_client()
# Test 404 for missing file
resp = client.get('/api/media/.tmp/nonexistent.png')
assert resp.status_code == 404, f'Expected 404, got {resp.status_code}'
print('404 test: PASS')

# Test 403 for path traversal
resp = client.get('/api/media//etc/passwd')
assert resp.status_code == 403, f'Expected 403, got {resp.status_code}'
print('403 test: PASS')

# Test normalize_blueprint includes visual_paths
from execution.review_server import normalize_blueprint
bp = {'candidate_id': 'test', 'visual_paths': '[\"/tmp/test.mp4\"]'}
result = normalize_blueprint(bp, source='local')
assert result['visual_paths'] == ['/tmp/test.mp4'], f'Got: {result[\"visual_paths\"]}'
print('visual_paths normalize test: PASS')

print('All media route tests passed!')
"
```
Expected: All 3 tests pass.

**Step 6: Commit**

```bash
git add execution/review_server.py
git commit -m "feat(review): add visual_paths to blueprint normalization + media serving route"
```

---

### Task 2: Add batch review API route

**Files:**
- Modify: `execution/review_server.py` (add route after `/api/review/<record_id>`)

**Step 1: Add the `/api/batch-review` route**

Add this route after the existing `/api/review/<record_id>` route (after line 139):

```python
@app.route("/api/batch-review", methods=["POST"])
def batch_review():
    """Batch approve/reject multiple blueprints."""
    data = request.json or {}
    record_ids = data.get("record_ids", [])
    action = data.get("action", "")
    notes = data.get("notes", "")
    dry_run = app.config.get("DRY_RUN", False)

    if action not in ("approved", "rejected"):
        return jsonify({"error": f"Invalid batch action: {action}"}), 400
    if not record_ids:
        return jsonify({"error": "No record_ids provided"}), 400

    results = []
    for record_id in record_ids:
        try:
            if dry_run:
                logger.info("[DRY RUN] Batch %s: %s", action, record_id)
                results.append({"id": record_id, "status": "ok", "dry_run": True})
                socketio.emit("blueprint_updated", {
                    "record_id": record_id,
                    "action": action,
                    "dry_run": True,
                })
            else:
                from execution.utils.backlog_client import BacklogClient
                client = BacklogClient()
                update_fields = {
                    "action_taken": action,
                    "review_notes": notes or f"Batch {action}",
                    "reviewed_at": datetime.now(timezone.utc).isoformat(),
                }
                if action == "approved":
                    update_fields["Status"] = "APPROVED"
                elif action == "rejected":
                    update_fields["feedback_issue"] = "rejected_in_review"
                    update_fields["feedback_notes"] = notes or f"Batch {action}"

                client.blueprints.update(record_id, update_fields, typecast=True)
                results.append({"id": record_id, "status": "ok"})
                socketio.emit("blueprint_updated", {
                    "record_id": record_id,
                    "action": action,
                })
        except Exception as e:
            logger.error("Batch review failed for %s: %s", record_id, e)
            results.append({"id": record_id, "status": "error", "error": str(e)})

    succeeded = sum(1 for r in results if r["status"] == "ok")
    failed = len(results) - succeeded
    logger.info("Batch %s: %d/%d succeeded", action, succeeded, len(results))
    return jsonify({"results": results, "succeeded": succeeded, "failed": failed})
```

**Step 2: Verify batch route works**

Run:
```bash
cd /Users/anarchistsid/GenLab/Content\ Scraper
./venv/bin/python -c "
from execution.review_server import app
app.config['DRY_RUN'] = True
app.config['LOCAL_MODE'] = True
client = app.test_client()

import json

# Test batch approve (dry run)
resp = client.post('/api/batch-review',
    data=json.dumps({'record_ids': ['id1', 'id2'], 'action': 'approved'}),
    content_type='application/json')
data = resp.get_json()
assert data['succeeded'] == 2, f'Expected 2 succeeded, got {data}'
print('Batch approve test: PASS')

# Test invalid action
resp = client.post('/api/batch-review',
    data=json.dumps({'record_ids': ['id1'], 'action': 'invalid'}),
    content_type='application/json')
assert resp.status_code == 400
print('Invalid action test: PASS')

# Test empty ids
resp = client.post('/api/batch-review',
    data=json.dumps({'record_ids': [], 'action': 'approved'}),
    content_type='application/json')
assert resp.status_code == 400
print('Empty ids test: PASS')

print('All batch review tests passed!')
"
```
Expected: All 3 tests pass.

**Step 3: Commit**

```bash
git add execution/review_server.py
git commit -m "feat(review): add batch review API route for multi-select approve/reject"
```

---

### Task 3: Add settings API route (auto-approve toggle)

**Files:**
- Modify: `execution/review_server.py` (add route + settings state)

**Step 1: Add settings state**

After the `express_state` dict (after line 51), add:

```python
review_settings = {
    "auto_approve": False,
    "auto_approve_seconds": 60,
}
```

**Step 2: Add settings routes**

Add after the batch-review route:

```python
@app.route("/api/settings")
def get_settings():
    """Get current review settings."""
    return jsonify(review_settings)


@app.route("/api/settings", methods=["POST"])
def update_settings():
    """Update review settings (auto-approve toggle + timer)."""
    data = request.json or {}
    if "auto_approve" in data:
        review_settings["auto_approve"] = bool(data["auto_approve"])
    if "auto_approve_seconds" in data:
        val = int(data["auto_approve_seconds"])
        review_settings["auto_approve_seconds"] = max(10, min(300, val))  # Clamp 10-300s
    logger.info("Settings updated: %s", review_settings)
    socketio.emit("settings_updated", review_settings)
    return jsonify(review_settings)
```

**Step 3: Verify settings routes work**

Run:
```bash
cd /Users/anarchistsid/GenLab/Content\ Scraper
./venv/bin/python -c "
from execution.review_server import app
import json
client = app.test_client()

# GET default settings
resp = client.get('/api/settings')
data = resp.get_json()
assert data['auto_approve'] == False
assert data['auto_approve_seconds'] == 60
print('Default settings test: PASS')

# POST update
resp = client.post('/api/settings',
    data=json.dumps({'auto_approve': True, 'auto_approve_seconds': 30}),
    content_type='application/json')
data = resp.get_json()
assert data['auto_approve'] == True
assert data['auto_approve_seconds'] == 30
print('Update settings test: PASS')

# Clamp test (min 10s)
resp = client.post('/api/settings',
    data=json.dumps({'auto_approve_seconds': 5}),
    content_type='application/json')
data = resp.get_json()
assert data['auto_approve_seconds'] == 10
print('Clamp min test: PASS')

print('All settings tests passed!')
"
```
Expected: All 3 tests pass.

**Step 4: Commit**

```bash
git add execution/review_server.py
git commit -m "feat(review): add settings API for auto-approve toggle and timer"
```

---

### Task 4: Rewrite embedded CSS for mobile-first video review layout

**Files:**
- Modify: `execution/review_server.py:427-590` (the `<style>` section inside `DASHBOARD_HTML`)

**Step 1: Replace the CSS**

Find the `<style>` block inside `DASHBOARD_HTML` (line 427-590). Replace the entire contents between `<style>` and `</style>` with the mobile-first CSS below. Keep the `@import` for JetBrains Mono.

This is a full replacement of the CSS block. Key changes:
- Cards are now full-width on mobile, max-width 600px centered on desktop
- Video player styles (16:9 aspect ratio, rounded corners)
- Thumbnail strip (horizontal scroll, 60px tall)
- Batch panel (fixed at bottom, slides up)
- Auto-approve countdown bar
- Swipe hint animations
- Checkbox styles for batch mode

```css
  @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&display=swap');

  * { margin: 0; padding: 0; box-sizing: border-box; }

  :root {
    --bg: #0d1117;
    --card-bg: #161b22;
    --border: #30363d;
    --text: #c9d1d9;
    --heading: #f0f6fc;
    --green: #3fb950;
    --red: #f85149;
    --amber: #d29922;
    --critical-bg: #da3633;
    --blue: #58a6ff;
    --dim: #8b949e;
    --font: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace;
  }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--font);
    font-size: 13px;
    line-height: 1.5;
    min-height: 100vh;
    padding-bottom: 120px; /* room for stats + batch panel */
    -webkit-overflow-scrolling: touch;
  }

  header {
    background: var(--card-bg);
    border-bottom: 1px solid var(--border);
    padding: 12px 16px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    position: sticky;
    top: 0;
    z-index: 100;
    flex-wrap: wrap;
    gap: 8px;
  }

  .logo { font-size: 14px; font-weight: 700; color: var(--heading); }
  .logo span { color: var(--green); }

  .header-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }

  .btn-trigger {
    background: var(--green); color: var(--bg); border: none;
    padding: 6px 12px; font-family: var(--font); font-size: 11px;
    font-weight: 600; border-radius: 6px; cursor: pointer;
    transition: opacity 0.2s;
  }
  .btn-trigger:hover { opacity: 0.85; }
  .btn-trigger:disabled { background: var(--border); color: var(--dim); cursor: not-allowed; }

  .header-toggle {
    background: transparent; color: var(--dim); border: 1px solid var(--border);
    padding: 6px 12px; font-family: var(--font); font-size: 11px;
    border-radius: 6px; cursor: pointer; transition: all 0.2s;
  }
  .header-toggle:hover { border-color: var(--dim); color: var(--text); }
  .header-toggle.active { border-color: var(--blue); color: var(--blue); background: rgba(88,166,255,0.1); }

  .filter-pills { display: flex; gap: 4px; }
  .filter-pill {
    font-size: 10px; padding: 3px 8px; border-radius: 10px;
    border: 1px solid var(--border); background: transparent;
    color: var(--dim); cursor: pointer; font-family: var(--font);
    transition: all 0.2s;
  }
  .filter-pill:hover { border-color: var(--dim); }
  .filter-pill.active { border-color: var(--blue); color: var(--blue); background: rgba(88,166,255,0.1); }

  .status-pill {
    font-size: 10px; padding: 3px 8px; border-radius: 12px;
    background: var(--border); color: var(--dim);
  }
  .status-pill.running {
    background: rgba(248,81,73,0.15); color: var(--red);
    animation: pulse 1.5s ease-in-out infinite;
  }
  .status-pill.complete { background: rgba(63,185,80,0.15); color: var(--green); }
  @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:0.5; } }

  #express-bar {
    background: var(--card-bg); border-bottom: 1px solid var(--border);
    padding: 8px 16px; display: none; font-size: 11px;
  }
  #express-bar.active { display: block; }

  .progress-track { display: flex; gap: 4px; margin-top: 6px; }
  .progress-step {
    flex: 1; height: 3px; background: var(--border);
    border-radius: 2px; transition: background 0.3s;
  }
  .progress-step.done { background: var(--green); }
  .progress-step.active { background: var(--amber); animation: pulse 1s ease-in-out infinite; }
  .progress-step.error { background: var(--red); }
  .progress-labels {
    display: flex; justify-content: space-between;
    margin-top: 4px; color: var(--dim); font-size: 9px;
  }

  /* ── Card Stack (mobile-first) ── */
  main { padding: 12px; max-width: 600px; margin: 0 auto; }

  .review-progress {
    text-align: center; padding: 8px 0 12px;
    color: var(--dim); font-size: 11px;
  }
  .review-progress .count { color: var(--heading); font-weight: 600; }

  .cards-stack { display: flex; flex-direction: column; gap: 16px; }

  .card {
    background: var(--card-bg); border: 1px solid var(--border);
    border-radius: 12px; overflow: hidden;
    transition: all 0.4s ease; position: relative;
  }
  .card:hover { border-color: var(--dim); }
  .card.removing { opacity: 0; transform: translateX(100px); transition: all 0.4s ease; }
  .card.removing-left { opacity: 0; transform: translateX(-100px); transition: all 0.4s ease; }

  /* Batch checkbox */
  .card-checkbox {
    position: absolute; top: 12px; right: 12px; z-index: 10;
    width: 24px; height: 24px; border-radius: 6px;
    border: 2px solid var(--border); background: rgba(13,17,23,0.8);
    cursor: pointer; display: none; /* shown when batch mode active */
    align-items: center; justify-content: center;
    transition: all 0.2s;
  }
  .card-checkbox.visible { display: flex; }
  .card-checkbox.checked { border-color: var(--blue); background: var(--blue); }
  .card-checkbox.checked::after {
    content: '\2713'; color: #fff; font-size: 14px; font-weight: 700;
  }

  /* Video player */
  .card-video-wrapper {
    position: relative; width: 100%;
    aspect-ratio: 9 / 16; /* vertical reel */
    background: #000; overflow: hidden;
  }
  .card-video-wrapper.landscape { aspect-ratio: 16 / 9; }
  .card-video {
    width: 100%; height: 100%; object-fit: contain;
    background: #000;
  }
  .card-video-placeholder {
    width: 100%; height: 100%;
    display: flex; align-items: center; justify-content: center;
    background: rgba(0,0,0,0.5); color: var(--dim); font-size: 14px;
  }

  /* Thumbnail strip */
  .card-thumbnails {
    display: flex; gap: 4px; padding: 8px 12px;
    overflow-x: auto; -webkit-overflow-scrolling: touch;
  }
  .card-thumbnails::-webkit-scrollbar { height: 3px; }
  .card-thumbnails::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }
  .card-thumb {
    width: 48px; height: 48px; border-radius: 4px;
    object-fit: cover; border: 1px solid var(--border);
    cursor: pointer; flex-shrink: 0; transition: border-color 0.2s;
  }
  .card-thumb:hover { border-color: var(--blue); }

  /* Metadata */
  .card-meta { padding: 10px 14px 6px; }
  .card-header { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; flex-wrap: wrap; }

  .urgency-badge {
    font-size: 9px; font-weight: 700; padding: 2px 6px; border-radius: 4px;
    text-transform: uppercase; letter-spacing: 0.5px;
  }
  .urgency-badge.critical { background: var(--critical-bg); color: #fff; }
  .urgency-badge.high { background: rgba(210,153,34,0.2); color: var(--amber); border: 1px solid var(--amber); }
  .urgency-badge.medium { background: rgba(88,166,255,0.15); color: var(--blue); }
  .urgency-badge.low { background: var(--border); color: var(--dim); }

  .format-tag {
    font-size: 9px; padding: 2px 6px; border: 1px solid var(--border);
    border-radius: 3px; color: var(--dim);
  }
  .status-tag { font-size: 9px; padding: 2px 6px; border-radius: 3px; margin-left: auto; }
  .status-tag.drafted { background: rgba(88,166,255,0.15); color: var(--blue); }
  .status-tag.visual-ready { background: rgba(63,185,80,0.15); color: var(--green); }

  .card-title {
    font-size: 11px; color: var(--dim); margin-bottom: 4px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .card-hook { font-size: 14px; font-weight: 700; color: var(--heading); margin-bottom: 6px; line-height: 1.3; }
  .card-caption {
    font-size: 11px; color: var(--text); max-height: 48px; overflow-y: auto;
    margin-bottom: 4px;
  }

  /* Auto-approve countdown bar */
  .auto-approve-bar {
    height: 3px; background: var(--green);
    transition: width linear;
    border-radius: 0 0 12px 12px;
  }

  /* Action buttons */
  .card-actions { display: flex; gap: 8px; padding: 8px 14px 14px; }
  .card-actions button {
    flex: 1; padding: 10px 12px; border: 1px solid var(--border);
    border-radius: 8px; font-family: var(--font); font-size: 12px;
    font-weight: 600; cursor: pointer; transition: all 0.2s; background: transparent;
    -webkit-tap-highlight-color: transparent;
  }
  .btn-approve { color: var(--green); border-color: var(--green); }
  .btn-approve:hover, .btn-approve:active { background: var(--green); color: var(--bg); }
  .btn-reject { color: var(--red); border-color: var(--red); }
  .btn-reject:hover, .btn-reject:active { background: var(--red); color: #fff; }
  .btn-skip { color: var(--dim); }
  .btn-skip:hover, .btn-skip:active { background: var(--border); color: var(--text); }

  .empty-state { text-align: center; padding: 60px 24px; color: var(--dim); }
  .empty-state .icon { font-size: 48px; margin-bottom: 12px; }
  .empty-state h2 { color: var(--heading); font-size: 16px; margin-bottom: 8px; }

  /* ── Bottom panels ── */
  .stats-bar {
    position: fixed; bottom: 0; left: 0; right: 0;
    background: var(--card-bg); border-top: 1px solid var(--border);
    padding: 6px 16px; display: flex; gap: 16px;
    font-size: 10px; color: var(--dim); z-index: 90;
    transition: bottom 0.3s;
  }
  .stats-bar .stat-value { color: var(--heading); font-weight: 600; }

  .batch-panel {
    position: fixed; bottom: -200px; left: 0; right: 0;
    background: var(--card-bg); border-top: 1px solid var(--border);
    padding: 12px 16px; z-index: 95;
    transition: bottom 0.3s ease;
    display: flex; flex-direction: column; gap: 8px;
  }
  .batch-panel.active { bottom: 0; }
  .batch-panel .batch-header {
    display: flex; justify-content: space-between; align-items: center;
  }
  .batch-panel .batch-count { font-size: 12px; color: var(--heading); font-weight: 600; }
  .batch-panel .batch-actions { display: flex; gap: 8px; }
  .batch-panel .batch-btn {
    padding: 8px 16px; border-radius: 6px; font-family: var(--font);
    font-size: 11px; font-weight: 600; cursor: pointer; border: none;
    transition: opacity 0.2s;
  }
  .batch-btn.approve-all { background: var(--green); color: var(--bg); }
  .batch-btn.reject-all { background: var(--red); color: #fff; }
  .batch-btn.select-all { background: transparent; color: var(--blue); border: 1px solid var(--blue); }
  .batch-btn:hover { opacity: 0.85; }
  .batch-btn:disabled { opacity: 0.4; cursor: not-allowed; }

  #toast-container {
    position: fixed; top: 60px; right: 12px; z-index: 200;
    display: flex; flex-direction: column; gap: 8px;
  }
  .toast { padding: 8px 12px; border-radius: 6px; font-size: 11px; animation: slideIn 0.3s ease; }
  .toast.success { background: rgba(63,185,80,0.15); border: 1px solid var(--green); color: var(--green); }
  .toast.error { background: rgba(248,81,73,0.15); border: 1px solid var(--red); color: var(--red); }
  @keyframes slideIn { from { opacity:0; transform:translateX(20px); } to { opacity:1; transform:translateX(0); } }

  /* Fullscreen slide viewer */
  .slide-overlay {
    position: fixed; inset: 0; z-index: 300;
    background: rgba(0,0,0,0.95); display: none;
    align-items: center; justify-content: center;
    cursor: pointer;
  }
  .slide-overlay.active { display: flex; }
  .slide-overlay img {
    max-width: 95vw; max-height: 90vh; object-fit: contain;
    border-radius: 8px;
  }

  /* Mobile adjustments */
  @media (max-width: 640px) {
    header { padding: 8px 12px; }
    .logo { font-size: 12px; }
    main { padding: 8px; }
    .card-actions button { padding: 12px 8px; font-size: 11px; }
    .btn-trigger { font-size: 10px; padding: 5px 10px; }
  }
```

**Step 2: Verify the CSS is syntactically valid**

Run:
```bash
cd /Users/anarchistsid/GenLab/Content\ Scraper
./venv/bin/python -c "
# Quick check: ensure DASHBOARD_HTML parses as valid HTML with style tag
from execution.review_server import DASHBOARD_HTML
assert '<style>' in DASHBOARD_HTML
assert '</style>' in DASHBOARD_HTML
assert 'cards-stack' in DASHBOARD_HTML  # new class name
assert 'card-video' in DASHBOARD_HTML   # new video styles
assert 'batch-panel' in DASHBOARD_HTML  # new batch panel
print('CSS integration check: PASS')
"
```

**Step 3: Commit**

```bash
git add execution/review_server.py
git commit -m "feat(review): mobile-first CSS with video player, batch panel, auto-approve styles"
```

---

### Task 5: Rewrite embedded HTML structure

**Files:**
- Modify: `execution/review_server.py` (the HTML body inside `DASHBOARD_HTML`, between `<body>` and the `<script>` tag)

**Step 1: Replace the HTML body**

Find the section between `<body>` and `<script src=...>` (approximately lines 592-622 in the original). Replace with:

```html
  <header>
    <div class="logo">&#x26A1; <span>Express Lane</span> Review</div>
    <div class="header-actions">
      <div class="filter-pills">
        <button class="filter-pill active" data-filter="all">All</button>
        <button class="filter-pill" data-filter="DRAFTED">Drafted</button>
        <button class="filter-pill" data-filter="VISUAL_READY">Visual</button>
      </div>
      <button id="btn-batch" class="header-toggle">&#x2610; Batch</button>
      <button id="btn-auto" class="header-toggle">&#x23F1; Auto</button>
      <span id="status-pill" class="status-pill">&#x25CF; Idle</span>
      <button id="btn-trigger" class="btn-trigger">&#x25B6; Run</button>
    </div>
  </header>

  <div id="express-bar">
    <div id="express-text"></div>
    <div class="progress-track" id="progress-track"></div>
    <div class="progress-labels" id="progress-labels"></div>
  </div>

  <main>
    <div class="review-progress" id="review-progress"></div>
    <div id="cards" class="cards-stack"></div>
  </main>

  <div class="stats-bar" id="stats-bar">
    <div>Total: <span class="stat-value" id="stat-total">0</span></div>
    <div>Drafted: <span class="stat-value" id="stat-drafted">0</span></div>
    <div>Visual: <span class="stat-value" id="stat-visual">0</span></div>
    <div>Express: <span class="stat-value" id="stat-last-run">&mdash;</span></div>
  </div>

  <div class="batch-panel" id="batch-panel">
    <div class="batch-header">
      <span class="batch-count" id="batch-count">0 selected</span>
      <div class="batch-actions">
        <button class="batch-btn select-all" id="btn-select-all">Select All</button>
        <button class="batch-btn approve-all" id="btn-batch-approve" disabled>&#x2713; Approve</button>
        <button class="batch-btn reject-all" id="btn-batch-reject" disabled>&#x2717; Reject</button>
      </div>
    </div>
  </div>

  <div class="slide-overlay" id="slide-overlay"></div>

  <div id="toast-container"></div>
```

**Step 2: Verify HTML structure**

Run:
```bash
cd /Users/anarchistsid/GenLab/Content\ Scraper
./venv/bin/python -c "
from execution.review_server import DASHBOARD_HTML
# Check key new elements exist
checks = ['btn-batch', 'btn-auto', 'filter-pill', 'batch-panel',
          'slide-overlay', 'review-progress', 'cards-stack',
          'btn-batch-approve', 'btn-batch-reject', 'btn-select-all']
for check in checks:
    assert check in DASHBOARD_HTML, f'Missing: {check}'
    print(f'{check}: FOUND')
print('All HTML structure checks passed!')
"
```

**Step 3: Commit**

```bash
git add execution/review_server.py
git commit -m "feat(review): rewrite HTML structure with video player, batch panel, filter pills"
```

---

### Task 6: Rewrite embedded JavaScript — card builder with video player

**Files:**
- Modify: `execution/review_server.py` (the `<script>` section inside `DASHBOARD_HTML`)

**Step 1: Replace the JavaScript**

Replace the entire `<script>` block (after the socket.io CDN include) with the new JavaScript. This is the biggest change — the new JS adds:

1. **`buildCard(bp)`** — creates card with video player + thumbnail strip + metadata + action buttons + batch checkbox
2. **`buildVideoWrapper(bp)`** — creates `<video>` for MP4 or placeholder, using `/api/media/` URLs
3. **`buildThumbnailStrip(bp)`** — creates horizontal thumbnail row for PNG slides
4. **Touch swipe gestures** for approve/reject
5. **`batchMode`** toggle + select/deselect + batch actions
6. **`autoApprove`** toggle with countdown timer per card
7. **Filter pills** (All / Drafted / Visual Ready)
8. **Fullscreen slide viewer** on thumbnail tap

The full JS code is approximately 350 lines. Key functions:

```javascript
'use strict';

let blueprints = [];
let filteredBlueprints = [];
let expressRunning = false;
let batchMode = false;
let selectedIds = new Set();
let autoApprove = false;
let autoApproveSeconds = 60;
let autoApproveTimers = {};
let currentFilter = 'all';
const socket = io();

const STEP_NAMES = ['classify_urgency','compose_blueprints','generate_content','run_qc_gates'];

function escText(s) { return String(s || ''); }

function mediaUrl(absPath) {
  // Convert absolute path to /api/media/ URL
  if (!absPath) return null;
  return '/api/media/' + encodeURIComponent(absPath);
}

function getReelPath(bp) {
  // Find first .mp4 in visual_paths
  var paths = bp.visual_paths || [];
  for (var i = 0; i < paths.length; i++) {
    if (paths[i] && paths[i].toLowerCase().endsWith('.mp4')) return paths[i];
  }
  return null;
}

function getPngPaths(bp) {
  // Find all .png in visual_paths
  var paths = bp.visual_paths || [];
  return paths.filter(function(p) { return p && p.toLowerCase().endsWith('.png'); });
}

function buildVideoWrapper(bp) {
  var wrapper = document.createElement('div');
  wrapper.className = 'card-video-wrapper';

  var reelPath = getReelPath(bp);
  if (reelPath) {
    var video = document.createElement('video');
    video.className = 'card-video';
    video.src = mediaUrl(reelPath);
    video.controls = true;
    video.muted = true;
    video.playsInline = true;
    video.preload = 'metadata';
    video.setAttribute('playsinline', '');
    video.setAttribute('webkit-playsinline', '');
    wrapper.appendChild(video);
  } else {
    wrapper.className += ' landscape'; // 16:9 for non-video
    var placeholder = document.createElement('div');
    placeholder.className = 'card-video-placeholder';
    placeholder.textContent = 'No video — carousel only';
    wrapper.appendChild(placeholder);
  }
  return wrapper;
}

function buildThumbnailStrip(bp) {
  var pngs = getPngPaths(bp);
  if (pngs.length === 0) return null;

  var strip = document.createElement('div');
  strip.className = 'card-thumbnails';
  pngs.forEach(function(pngPath) {
    var img = document.createElement('img');
    img.className = 'card-thumb';
    img.src = mediaUrl(pngPath);
    img.alt = 'Slide';
    img.loading = 'lazy';
    img.addEventListener('click', function() { openSlideViewer(pngPath); });
    strip.appendChild(img);
  });
  return strip;
}

function buildCard(bp) {
  var card = document.createElement('div');
  card.className = 'card';
  card.id = 'card-' + bp.id;
  card.dataset.id = bp.id;
  card.dataset.status = bp.status || '';

  // Batch checkbox
  var cb = document.createElement('div');
  cb.className = 'card-checkbox' + (batchMode ? ' visible' : '');
  cb.dataset.id = bp.id;
  cb.addEventListener('click', function(e) {
    e.stopPropagation();
    toggleSelect(bp.id, cb);
  });
  card.appendChild(cb);

  // Video player
  card.appendChild(buildVideoWrapper(bp));

  // Thumbnail strip
  var strip = buildThumbnailStrip(bp);
  if (strip) card.appendChild(strip);

  // Metadata section
  var meta = document.createElement('div');
  meta.className = 'card-meta';

  // Header row (urgency + format + status)
  var hdr = document.createElement('div');
  hdr.className = 'card-header';

  var u = String(bp.urgency || '').toUpperCase();
  if (u && u !== 'FALSE') {
    var badge = document.createElement('span');
    var urgClass = u === 'CRITICAL' || u === 'TRUE' ? 'critical' :
                   u === 'HIGH' ? 'high' : u === 'MEDIUM' ? 'medium' : 'low';
    badge.className = 'urgency-badge ' + urgClass;
    badge.textContent = u === 'TRUE' ? 'CRITICAL' : u;
    hdr.appendChild(badge);
  }

  var fmt = document.createElement('span');
  fmt.className = 'format-tag';
  fmt.textContent = bp.format || 'carousel';
  hdr.appendChild(fmt);

  if (bp.template_id) {
    var tmpl = document.createElement('span');
    tmpl.className = 'format-tag';
    tmpl.textContent = bp.template_id;
    hdr.appendChild(tmpl);
  }

  var st = document.createElement('span');
  st.className = 'status-tag ' + (bp.status || '').toLowerCase().replace('_', '-');
  st.textContent = bp.status || '';
  hdr.appendChild(st);
  meta.appendChild(hdr);

  // Title
  var title = document.createElement('div');
  title.className = 'card-title';
  title.textContent = escText(bp.title);
  meta.appendChild(title);

  // Hook
  var hook = document.createElement('div');
  hook.className = 'card-hook';
  hook.textContent = escText(bp.hook || '(no hook)');
  meta.appendChild(hook);

  // Caption (collapsed)
  if (bp.caption) {
    var caption = document.createElement('div');
    caption.className = 'card-caption';
    caption.textContent = escText(bp.caption);
    meta.appendChild(caption);
  }

  card.appendChild(meta);

  // Auto-approve bar
  var autoBar = document.createElement('div');
  autoBar.className = 'auto-approve-bar';
  autoBar.id = 'auto-bar-' + bp.id;
  autoBar.style.width = '0%';
  card.appendChild(autoBar);

  // Action buttons
  var actions = document.createElement('div');
  actions.className = 'card-actions';

  var btnApprove = document.createElement('button');
  btnApprove.className = 'btn-approve';
  btnApprove.textContent = '\u2713 Approve';
  btnApprove.addEventListener('click', function() { reviewAction(bp.id, 'approved'); });

  var btnReject = document.createElement('button');
  btnReject.className = 'btn-reject';
  btnReject.textContent = '\u2717 Reject';
  btnReject.addEventListener('click', function() { reviewAction(bp.id, 'rejected'); });

  var btnSkip = document.createElement('button');
  btnSkip.className = 'btn-skip';
  btnSkip.textContent = '\u2192 Skip';
  btnSkip.addEventListener('click', function() { reviewAction(bp.id, 'skipped'); });

  actions.appendChild(btnApprove);
  actions.appendChild(btnReject);
  actions.appendChild(btnSkip);
  card.appendChild(actions);

  // Touch swipe gestures
  setupSwipe(card, bp.id);

  // Auto-play video when visible
  setupAutoPlay(card);

  // Auto-approve timer
  if (autoApprove) startAutoApproveTimer(bp.id);

  return card;
}

// ── Swipe Gestures ──

function setupSwipe(card, id) {
  var startX = 0, startY = 0, dx = 0;
  card.addEventListener('touchstart', function(e) {
    startX = e.touches[0].clientX;
    startY = e.touches[0].clientY;
  }, { passive: true });
  card.addEventListener('touchmove', function(e) {
    dx = e.touches[0].clientX - startX;
    var dy = Math.abs(e.touches[0].clientY - startY);
    if (Math.abs(dx) > dy && Math.abs(dx) > 20) {
      card.style.transform = 'translateX(' + dx + 'px)';
      card.style.opacity = Math.max(0.3, 1 - Math.abs(dx) / 300);
    }
  }, { passive: true });
  card.addEventListener('touchend', function() {
    if (dx > 100) {
      reviewAction(id, 'approved');
    } else if (dx < -100) {
      reviewAction(id, 'rejected');
    } else {
      card.style.transform = '';
      card.style.opacity = '';
    }
    dx = 0;
  });
}

// ── Video Auto-Play (IntersectionObserver) ──

function setupAutoPlay(card) {
  if (!('IntersectionObserver' in window)) return;
  var video = card.querySelector('video');
  if (!video) return;
  var observer = new IntersectionObserver(function(entries) {
    entries.forEach(function(entry) {
      if (entry.isIntersecting) {
        video.play().catch(function() {});
      } else {
        video.pause();
      }
    });
  }, { threshold: 0.5 });
  observer.observe(card);
}

// ── Auto-Approve Timer ──

function startAutoApproveTimer(id) {
  clearAutoApproveTimer(id);
  var elapsed = 0;
  var interval = 100; // update every 100ms
  autoApproveTimers[id] = setInterval(function() {
    elapsed += interval;
    var pct = Math.min(100, (elapsed / (autoApproveSeconds * 1000)) * 100);
    var bar = document.getElementById('auto-bar-' + id);
    if (bar) bar.style.width = pct + '%';
    if (elapsed >= autoApproveSeconds * 1000) {
      clearAutoApproveTimer(id);
      reviewAction(id, 'approved');
    }
  }, interval);
}

function clearAutoApproveTimer(id) {
  if (autoApproveTimers[id]) {
    clearInterval(autoApproveTimers[id]);
    delete autoApproveTimers[id];
  }
  var bar = document.getElementById('auto-bar-' + id);
  if (bar) bar.style.width = '0%';
}

// ── Batch Mode ──

function toggleBatchMode() {
  batchMode = !batchMode;
  selectedIds.clear();
  var btn = document.getElementById('btn-batch');
  btn.classList.toggle('active', batchMode);
  var panel = document.getElementById('batch-panel');
  panel.classList.toggle('active', batchMode);
  var statsBar = document.getElementById('stats-bar');
  statsBar.style.bottom = batchMode ? '60px' : '0';
  // Toggle checkbox visibility
  document.querySelectorAll('.card-checkbox').forEach(function(cb) {
    cb.classList.toggle('visible', batchMode);
    cb.classList.remove('checked');
  });
  updateBatchCount();
}

function toggleSelect(id, cb) {
  if (selectedIds.has(id)) {
    selectedIds.delete(id);
    cb.classList.remove('checked');
  } else {
    selectedIds.add(id);
    cb.classList.add('checked');
  }
  updateBatchCount();
}

function selectAll() {
  var allSelected = selectedIds.size === filteredBlueprints.length;
  selectedIds.clear();
  document.querySelectorAll('.card-checkbox').forEach(function(cb) {
    if (allSelected) {
      cb.classList.remove('checked');
    } else {
      var id = cb.dataset.id;
      selectedIds.add(id);
      cb.classList.add('checked');
    }
  });
  updateBatchCount();
}

function updateBatchCount() {
  var count = selectedIds.size;
  document.getElementById('batch-count').textContent = count + ' selected';
  document.getElementById('btn-batch-approve').disabled = count === 0;
  document.getElementById('btn-batch-reject').disabled = count === 0;
  var selBtn = document.getElementById('btn-select-all');
  selBtn.textContent = selectedIds.size === filteredBlueprints.length ? 'Deselect All' : 'Select All';
}

async function batchAction(action) {
  if (selectedIds.size === 0) return;
  var ids = Array.from(selectedIds);
  try {
    var res = await fetch('/api/batch-review', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ record_ids: ids, action: action }),
    });
    var data = await res.json();
    showToast(action + ': ' + data.succeeded + '/' + ids.length + ' succeeded', 'success');
    selectedIds.clear();
    setTimeout(fetchBlueprints, 500);
  } catch (e) {
    showToast('Batch error: ' + e.message, 'error');
  }
}

// ── Filter ──

function setFilter(filter) {
  currentFilter = filter;
  document.querySelectorAll('.filter-pill').forEach(function(pill) {
    pill.classList.toggle('active', pill.dataset.filter === filter);
  });
  applyFilter();
  renderCards();
}

function applyFilter() {
  if (currentFilter === 'all') {
    filteredBlueprints = blueprints.slice();
  } else {
    filteredBlueprints = blueprints.filter(function(b) { return b.status === currentFilter; });
  }
}

// ── Slide Viewer ──

function openSlideViewer(path) {
  var overlay = document.getElementById('slide-overlay');
  while (overlay.firstChild) overlay.removeChild(overlay.firstChild);
  var img = document.createElement('img');
  img.src = mediaUrl(path);
  img.alt = 'Slide preview';
  overlay.appendChild(img);
  overlay.classList.add('active');
  overlay.addEventListener('click', function handler() {
    overlay.classList.remove('active');
    overlay.removeEventListener('click', handler);
  });
}

// ── Auto-Approve Toggle ──

function toggleAutoApprove() {
  autoApprove = !autoApprove;
  var btn = document.getElementById('btn-auto');
  btn.classList.toggle('active', autoApprove);
  // Post to server
  fetch('/api/settings', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ auto_approve: autoApprove }),
  });
  if (!autoApprove) {
    // Clear all timers
    Object.keys(autoApproveTimers).forEach(clearAutoApproveTimer);
  } else {
    // Start timers for all visible cards
    filteredBlueprints.forEach(function(bp) { startAutoApproveTimer(bp.id); });
  }
  showToast(autoApprove ? 'Auto-approve ON (' + autoApproveSeconds + 's)' : 'Auto-approve OFF', 'success');
}

// ── Core Functions ──

async function fetchBlueprints() {
  try {
    var res = await fetch('/api/blueprints');
    blueprints = await res.json();
    applyFilter();
    renderCards();
    updateStats();
  } catch (e) {
    console.error('Failed to fetch blueprints:', e);
  }
}

function renderCards() {
  var container = document.getElementById('cards');
  while (container.firstChild) container.removeChild(container.firstChild);

  // Progress indicator
  var progress = document.getElementById('review-progress');
  var reviewed = blueprints.length - filteredBlueprints.length;
  progress.textContent = '';
  if (filteredBlueprints.length > 0) {
    var progSpan = document.createElement('span');
    progSpan.className = 'count';
    progSpan.textContent = filteredBlueprints.length;
    progress.appendChild(progSpan);
    progress.appendChild(document.createTextNode(' blueprints to review'));
  }

  if (filteredBlueprints.length === 0) {
    var empty = document.createElement('div');
    empty.className = 'empty-state';
    var icon = document.createElement('div');
    icon.className = 'icon';
    icon.textContent = '\uD83D\uDCED';
    var h2 = document.createElement('h2');
    h2.textContent = 'No blueprints awaiting review';
    var p = document.createElement('p');
    p.textContent = 'Click "Run" to trigger the express pipeline, or wait for the daily run.';
    empty.appendChild(icon);
    empty.appendChild(h2);
    empty.appendChild(p);
    container.appendChild(empty);
    return;
  }

  filteredBlueprints.forEach(function(bp) {
    container.appendChild(buildCard(bp));
  });
}

function updateStats() {
  document.getElementById('stat-total').textContent = blueprints.length;
  document.getElementById('stat-drafted').textContent =
    blueprints.filter(function(b) { return b.status === 'DRAFTED'; }).length;
  document.getElementById('stat-visual').textContent =
    blueprints.filter(function(b) { return b.status === 'VISUAL_READY'; }).length;
}

async function reviewAction(id, action) {
  clearAutoApproveTimer(id);
  var card = document.getElementById('card-' + id);
  if (card) {
    card.classList.add(action === 'rejected' ? 'removing-left' : 'removing');
  }
  try {
    var res = await fetch('/api/review/' + id, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: action }),
    });
    var data = await res.json();
    if (data.status === 'ok') {
      showToast(
        action === 'approved' ? '\u2713 Approved' :
        action === 'rejected' ? '\u2717 Rejected' : '\u2192 Skipped',
        'success'
      );
      setTimeout(function() {
        blueprints = blueprints.filter(function(b) { return b.id !== id; });
        selectedIds.delete(id);
        applyFilter();
        renderCards();
        updateStats();
        updateBatchCount();
      }, 400);
    } else {
      showToast('Error: ' + (data.error || 'Unknown'), 'error');
      if (card) { card.classList.remove('removing'); card.classList.remove('removing-left'); }
    }
  } catch (e) {
    showToast('Network error: ' + e.message, 'error');
    if (card) { card.classList.remove('removing'); card.classList.remove('removing-left'); }
  }
}

async function triggerExpress() {
  if (expressRunning) return;
  try {
    var res = await fetch('/api/express/trigger');
    var data = await res.json();
    if (data.error) showToast(data.error, 'error');
  } catch (e) {
    showToast('Failed to trigger: ' + e.message, 'error');
  }
}

// ── WebSocket events (unchanged logic, same as before) ──

socket.on('express_progress', function(data) {
  var bar = document.getElementById('express-bar');
  var text = document.getElementById('express-text');
  var pill = document.getElementById('status-pill');
  var btn = document.getElementById('btn-trigger');

  if (data.type === 'started') {
    expressRunning = true;
    bar.classList.add('active');
    pill.className = 'status-pill running';
    pill.textContent = '\u25CF Running...';
    btn.disabled = true;
    renderProgressTrack(data.total_steps);
    text.textContent = 'Express: ' + data.total_steps + ' steps';
  }
  else if (data.type === 'step_start') {
    text.textContent = data.step + '...';
    setStepState(data.step_index, 'active');
  }
  else if (data.type === 'step_complete') {
    var s = data.success ? '\u2713' : '\u2717';
    text.textContent = data.step + ' ' + s + ' (' + data.elapsed_s + 's)';
    setStepState(data.step_index, data.success ? 'done' : 'error');
  }
  else if (data.type === 'complete') {
    expressRunning = false;
    text.textContent = (data.all_passed ? '\u2705' : '\u26A0\uFE0F') + ' Done \u2014 ' + data.total_seconds + 's';
    pill.className = 'status-pill complete';
    pill.textContent = '\u25CF ' + data.total_seconds + 's';
    btn.disabled = false;
    document.getElementById('stat-last-run').textContent = data.total_seconds + 's';
    setTimeout(fetchBlueprints, 1000);
    setTimeout(function() {
      bar.classList.remove('active');
      pill.className = 'status-pill';
      pill.textContent = '\u25CF Idle';
    }, 10000);
  }
});

socket.on('blueprint_updated', function() { fetchBlueprints(); });
socket.on('blueprints_updated', function() { fetchBlueprints(); });
socket.on('settings_updated', function(data) {
  autoApprove = data.auto_approve;
  autoApproveSeconds = data.auto_approve_seconds;
});

function renderProgressTrack(totalSteps) {
  var track = document.getElementById('progress-track');
  var labels = document.getElementById('progress-labels');
  while (track.firstChild) track.removeChild(track.firstChild);
  while (labels.firstChild) labels.removeChild(labels.firstChild);
  for (var i = 0; i < totalSteps && i < STEP_NAMES.length; i++) {
    var step = document.createElement('div');
    step.className = 'progress-step';
    step.id = 'pstep-' + i;
    track.appendChild(step);
    var label = document.createElement('span');
    label.textContent = STEP_NAMES[i].replace('_', ' ');
    labels.appendChild(label);
  }
}

function setStepState(index, state) {
  var el = document.getElementById('pstep-' + index);
  if (el) el.className = 'progress-step ' + state;
}

function showToast(msg, type) {
  var container = document.getElementById('toast-container');
  var toast = document.createElement('div');
  toast.className = 'toast ' + type;
  toast.textContent = msg;
  container.appendChild(toast);
  setTimeout(function() { toast.remove(); }, 3000);
}

async function loadExpressStatus() {
  try {
    var res = await fetch('/api/express/status');
    var data = await res.json();
    if (data.last_run) {
      var t = data.last_run.pipeline_time_seconds || data.last_run.total_seconds || '?';
      document.getElementById('stat-last-run').textContent = t + 's';
    }
  } catch (e) {}
}

async function loadSettings() {
  try {
    var res = await fetch('/api/settings');
    var data = await res.json();
    autoApprove = data.auto_approve;
    autoApproveSeconds = data.auto_approve_seconds;
    document.getElementById('btn-auto').classList.toggle('active', autoApprove);
  } catch (e) {}
}

// ── Event Listeners ──

document.getElementById('btn-trigger').addEventListener('click', triggerExpress);
document.getElementById('btn-batch').addEventListener('click', toggleBatchMode);
document.getElementById('btn-auto').addEventListener('click', toggleAutoApprove);
document.getElementById('btn-select-all').addEventListener('click', selectAll);
document.getElementById('btn-batch-approve').addEventListener('click', function() { batchAction('approved'); });
document.getElementById('btn-batch-reject').addEventListener('click', function() { batchAction('rejected'); });

// Filter pills
document.querySelectorAll('.filter-pill').forEach(function(pill) {
  pill.addEventListener('click', function() { setFilter(pill.dataset.filter); });
});

// ── Init ──

fetchBlueprints();
loadExpressStatus();
loadSettings();
setInterval(fetchBlueprints, 15000);
```

**Step 2: Verify the JS integration**

Run:
```bash
cd /Users/anarchistsid/GenLab/Content\ Scraper
./venv/bin/python -c "
from execution.review_server import DASHBOARD_HTML
# Check critical JS functions exist
checks = ['buildCard', 'buildVideoWrapper', 'buildThumbnailStrip',
          'setupSwipe', 'setupAutoPlay', 'toggleBatchMode',
          'batchAction', 'setFilter', 'openSlideViewer',
          'startAutoApproveTimer', 'mediaUrl', 'getReelPath']
for fn in checks:
    assert fn in DASHBOARD_HTML, f'Missing JS function: {fn}'
    print(f'{fn}: FOUND')

# Verify no innerHTML usage
assert 'innerHTML' not in DASHBOARD_HTML, 'SECURITY: innerHTML found! Must use DOM API.'
print('innerHTML check: CLEAN')

print('All JS integration checks passed!')
"
```

**Step 3: Commit**

```bash
git add execution/review_server.py
git commit -m "feat(review): complete JS rewrite with video player, swipe gestures, batch mode, auto-approve"
```

---

### Task 7: Create test media files + integration test

**Files:**
- Create: `tests/test_review_server.py`

**Step 1: Create the test file**

```python
#!/usr/bin/env python3
"""Integration tests for the video review server.

Tests media serving, batch review, settings, and blueprint normalization.
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from execution.review_server import app, normalize_blueprint, _parse_visual_paths


@pytest.fixture
def client():
    """Flask test client with local mode + dry run."""
    app.config["TESTING"] = True
    app.config["DRY_RUN"] = True
    app.config["LOCAL_MODE"] = True
    with app.test_client() as c:
        yield c


class TestParseVisualPaths:
    """Test _parse_visual_paths helper."""

    def test_empty_string(self):
        assert _parse_visual_paths("") == []

    def test_none(self):
        assert _parse_visual_paths(None) == []

    def test_valid_json_string(self):
        result = _parse_visual_paths('["/tmp/a.png", "/tmp/b.mp4"]')
        assert result == ["/tmp/a.png", "/tmp/b.mp4"]

    def test_already_list(self):
        result = _parse_visual_paths(["/tmp/a.png"])
        assert result == ["/tmp/a.png"]

    def test_invalid_json(self):
        result = _parse_visual_paths("not json")
        assert result == []

    def test_empty_list_json(self):
        result = _parse_visual_paths("[]")
        assert result == []


class TestNormalizeBlueprint:
    """Test visual_paths in normalize_blueprint."""

    def test_local_with_visual_paths(self):
        bp = {
            "candidate_id": "test123",
            "visual_paths": '["/tmp/slide.png", "/tmp/reel.mp4"]',
        }
        result = normalize_blueprint(bp, source="local")
        assert result["visual_paths"] == ["/tmp/slide.png", "/tmp/reel.mp4"]

    def test_local_without_visual_paths(self):
        bp = {"candidate_id": "test123"}
        result = normalize_blueprint(bp, source="local")
        assert result["visual_paths"] == []

    def test_lists_with_visual_paths(self):
        bp = {
            "id": "rec123",
            "fields": {
                "visual_paths": '["/tmp/slide.png"]',
                "title": "Test",
            },
        }
        result = normalize_blueprint(bp, source="lists")
        assert result["visual_paths"] == ["/tmp/slide.png"]


class TestMediaRoute:
    """Test /api/media/ file serving."""

    def test_404_missing_file(self, client):
        resp = client.get("/api/media/.tmp/nonexistent.png")
        assert resp.status_code == 404

    def test_403_path_traversal(self, client):
        resp = client.get("/api/media//etc/passwd")
        assert resp.status_code == 403

    def test_403_non_media_file(self, client):
        # Create a temp .py file inside project root
        test_file = PROJECT_ROOT / ".tmp" / "test_security.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("print('hello')")
        try:
            resp = client.get(f"/api/media/{test_file}")
            assert resp.status_code == 403
        finally:
            test_file.unlink(missing_ok=True)

    def test_200_serves_png(self, client):
        # Create a tiny test PNG
        test_png = PROJECT_ROOT / ".tmp" / "test_media_serve.png"
        test_png.parent.mkdir(parents=True, exist_ok=True)
        # Minimal 1x1 PNG
        import base64
        png_data = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )
        test_png.write_bytes(png_data)
        try:
            resp = client.get(f"/api/media/{test_png}")
            assert resp.status_code == 200
            assert resp.content_type == "image/png"
        finally:
            test_png.unlink(missing_ok=True)


class TestBatchReview:
    """Test /api/batch-review endpoint."""

    def test_batch_approve_dry_run(self, client):
        resp = client.post(
            "/api/batch-review",
            data=json.dumps({"record_ids": ["id1", "id2"], "action": "approved"}),
            content_type="application/json",
        )
        data = resp.get_json()
        assert data["succeeded"] == 2
        assert data["failed"] == 0

    def test_batch_invalid_action(self, client):
        resp = client.post(
            "/api/batch-review",
            data=json.dumps({"record_ids": ["id1"], "action": "invalid"}),
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_batch_empty_ids(self, client):
        resp = client.post(
            "/api/batch-review",
            data=json.dumps({"record_ids": [], "action": "approved"}),
            content_type="application/json",
        )
        assert resp.status_code == 400


class TestSettings:
    """Test /api/settings endpoints."""

    def test_get_defaults(self, client):
        resp = client.get("/api/settings")
        data = resp.get_json()
        assert "auto_approve" in data
        assert "auto_approve_seconds" in data

    def test_update_settings(self, client):
        resp = client.post(
            "/api/settings",
            data=json.dumps({"auto_approve": True, "auto_approve_seconds": 45}),
            content_type="application/json",
        )
        data = resp.get_json()
        assert data["auto_approve"] is True
        assert data["auto_approve_seconds"] == 45

    def test_clamp_min(self, client):
        resp = client.post(
            "/api/settings",
            data=json.dumps({"auto_approve_seconds": 3}),
            content_type="application/json",
        )
        data = resp.get_json()
        assert data["auto_approve_seconds"] == 10

    def test_clamp_max(self, client):
        resp = client.post(
            "/api/settings",
            data=json.dumps({"auto_approve_seconds": 999}),
            content_type="application/json",
        )
        data = resp.get_json()
        assert data["auto_approve_seconds"] == 300


class TestDashboardHTML:
    """Test the embedded SPA has required elements."""

    def test_no_innerhtml(self):
        from execution.review_server import DASHBOARD_HTML
        assert "innerHTML" not in DASHBOARD_HTML, "Security: innerHTML not allowed"

    def test_has_video_elements(self):
        from execution.review_server import DASHBOARD_HTML
        assert "card-video" in DASHBOARD_HTML
        assert "buildVideoWrapper" in DASHBOARD_HTML

    def test_has_batch_elements(self):
        from execution.review_server import DASHBOARD_HTML
        assert "batch-panel" in DASHBOARD_HTML
        assert "batchAction" in DASHBOARD_HTML

    def test_has_auto_approve(self):
        from execution.review_server import DASHBOARD_HTML
        assert "auto-approve-bar" in DASHBOARD_HTML
        assert "startAutoApproveTimer" in DASHBOARD_HTML

    def test_serves_dashboard(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert b"Express Lane" in resp.data
```

**Step 2: Run the tests**

Run:
```bash
cd /Users/anarchistsid/GenLab/Content\ Scraper
./venv/bin/python -m pytest tests/test_review_server.py -v
```
Expected: All tests pass.

**Step 3: Commit**

```bash
git add tests/test_review_server.py
git commit -m "test: add review server integration tests (media, batch, settings, security)"
```

---

### Task 8: Manual mobile browser test

**Files:**
- No code changes — manual testing

**Step 1: Start the server with local test data**

```bash
cd /Users/anarchistsid/GenLab/Content\ Scraper
./venv/bin/python execution/review_server.py --local --dry-run --port 5151
```

**Step 2: Get your Mac's local IP**

```bash
ipconfig getifaddr en0
```

**Step 3: Test on desktop browser**

Open `http://localhost:5151` and verify:
- Dashboard loads with dark theme
- Blueprint cards show with video player area (placeholder since test data has no visual_paths)
- Filter pills work (All / Drafted / Visual)
- Batch mode toggles (checkboxes appear on cards)
- Auto-approve toggle shows toast
- Approve/Reject/Skip buttons work (dry-run)
- Express trigger works

**Step 4: Test on mobile browser**

Open `http://<mac-ip>:5151` on your phone and verify:
- Cards fill viewport width
- Video player area is tap-friendly
- Action buttons are large enough to tap
- Swipe right to approve, swipe left to reject
- Batch mode checkbox works with touch
- Page scrolls smoothly

**Step 5: Commit any fixes**

If any fixes are needed, make them and commit:
```bash
git add execution/review_server.py
git commit -m "fix(review): mobile layout adjustments from manual testing"
```

---

### Task 9: End-to-end test with real rendered media

**Files:**
- No new files — uses existing test data

**Step 1: Verify media serving works with real files**

The `.tmp/runs/brand_test_001/rendered/` directory has real MP4 files. Test serving them:

```bash
cd /Users/anarchistsid/GenLab/Content\ Scraper
./venv/bin/python -c "
from execution.review_server import app
import os

app.config['LOCAL_MODE'] = True
app.config['DRY_RUN'] = True
client = app.test_client()

# Find a real MP4 from the test renders
import glob
mp4s = glob.glob('.tmp/runs/*/rendered/*.mp4')
if mp4s:
    # Get absolute path
    from pathlib import Path
    abs_path = str(Path(mp4s[0]).resolve())
    resp = client.get(f'/api/media/{abs_path}')
    print(f'MP4 serve test ({mp4s[0]}): status={resp.status_code}, type={resp.content_type}')
    assert resp.status_code == 200, f'Expected 200, got {resp.status_code}'
    assert 'video/mp4' in resp.content_type
    print('PASS: Real MP4 serving works')
else:
    print('SKIP: No MP4 files found in test data')

# Find a real PNG
pngs = glob.glob('.tmp/runs/*/comparison/*.png')
if pngs:
    from pathlib import Path
    abs_path = str(Path(pngs[0]).resolve())
    resp = client.get(f'/api/media/{abs_path}')
    print(f'PNG serve test ({pngs[0]}): status={resp.status_code}, type={resp.content_type}')
    assert resp.status_code == 200
    print('PASS: Real PNG serving works')
else:
    print('SKIP: No PNG files found in test data')

print('Media serving E2E: COMPLETE')
"
```

**Step 2: Test the full server with media-enriched blueprints**

This is a visual check — start the server and manually verify video plays in the browser:
```bash
cd /Users/anarchistsid/GenLab/Content\ Scraper
./venv/bin/python execution/review_server.py --local --dry-run --port 5151
```

Open `http://localhost:5151` and check that:
- If any blueprints have visual_paths, the video player loads and plays
- Thumbnails appear and are clickable for fullscreen view

**Step 3: Final commit if needed**

```bash
git add execution/review_server.py tests/test_review_server.py
git commit -m "feat(review): video review app complete — mobile-first, batch ops, auto-approve"
```
