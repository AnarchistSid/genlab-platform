# Review Server Redesign — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Transform the review server from a localhost-only Flask dev server into a publicly accessible, always-on, polished review dashboard at `review.aspirehub.ai`.

**Architecture:** Flask + Gunicorn behind a Cloudflare named tunnel. Frontend extracted from embedded HTML to separate template files (`templates/review/`). Cloudflare Access handles SSO authentication. launchd daemon ensures always-on availability with crash recovery.

**Tech Stack:** Python/Flask/Gunicorn, Cloudflare Tunnel + Access, vanilla JS/HTML/CSS (Inter font, dark theme), launchd, Flask-SocketIO with eventlet.

**Design doc:** `docs/plans/2026-02-26-review-server-redesign.md`

---

## Task 1: Add Gunicorn + Eventlet Dependencies

**Files:**
- Modify: `requirements.txt`
- Run: `pip install` in venv

**Step 1: Add gunicorn and eventlet to requirements.txt**

In `requirements.txt`, update the web review section at the bottom:

```python
# Web review UI (Phase 8: Express Lane)
flask>=3.0.0
flask-socketio>=5.3.0
gunicorn>=22.0.0
eventlet>=0.36.0
```

**Step 2: Install the new dependencies**

Run: `venv/bin/pip install gunicorn eventlet`
Expected: Both install successfully.

**Step 3: Verify imports work**

Run: `venv/bin/python -c "import gunicorn; import eventlet; print('OK')"`
Expected: Prints `OK`.

**Step 4: Commit**

```bash
git add requirements.txt
git commit -m "deps: add gunicorn + eventlet for production review server"
```

---

## Task 2: Create WSGI Entry Point + Update Server for Remote Access

**Files:**
- Create: `execution/wsgi_review.py`
- Modify: `execution/review_server.py` (lines 56-66, 2123-2130)

**Step 1: Create the Gunicorn WSGI entry point**

Create `execution/wsgi_review.py`:

```python
#!/usr/bin/env python3
"""WSGI entry point for the review server (Gunicorn).

Usage:
    cd "/Users/anarchistsid/GenLab/Content Scraper"
    gunicorn execution.wsgi_review:app \
        --worker-class eventlet \
        --workers 2 \
        --timeout 120 \
        --bind 127.0.0.1:5151 \
        --access-logfile .tmp/logs/review_access.log \
        --error-logfile .tmp/logs/review_error.log

The Cloudflare tunnel connects to 127.0.0.1:5151, so we only
bind to localhost. Cloudflare handles HTTPS + auth externally.
"""

import sys
from pathlib import Path

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env", override=True)

from execution.review_server import app, socketio

# Gunicorn picks up `app` as the WSGI callable.
# For SocketIO, we need to use socketio.run() or monkey-patch.
# eventlet worker class handles WebSocket upgrade automatically.
```

**Step 2: Update CORS in review_server.py to allow tunnel domain**

In `execution/review_server.py`, find the SocketIO initialization (lines 57-66) and update:

```python
# ── Security: restrict CORS to known origins ──
_DEFAULT_PORT = 5151
_TUNNEL_DOMAIN = os.getenv("REVIEW_TUNNEL_DOMAIN", "review.aspirehub.ai")

_cors_origins = [
    f"http://localhost:{_DEFAULT_PORT}",
    f"http://127.0.0.1:{_DEFAULT_PORT}",
]
# Add tunnel domain if configured
if _TUNNEL_DOMAIN:
    _cors_origins.append(f"https://{_TUNNEL_DOMAIN}")

socketio = SocketIO(
    app,
    cors_allowed_origins=_cors_origins,
    async_mode="threading",
)
```

**Step 3: Update main() to bind to 0.0.0.0 by default for tunnel access**

In `execution/review_server.py`, update the `main()` function (around line 2123):

```python
    # Bind to 0.0.0.0 for tunnel access (Cloudflare connects to localhost:5151)
    # Use --localhost flag to restrict to 127.0.0.1 for local-only development
    host = "127.0.0.1" if getattr(args, 'localhost', False) else "0.0.0.0"
```

Also add the `--localhost` flag to the argument parser:

```python
    parser.add_argument("--localhost", action="store_true",
                        help="Bind to 127.0.0.1 only (default: 0.0.0.0 for tunnel access)")
```

**Step 4: Run existing tests to verify nothing breaks**

Run: `venv/bin/python -m pytest tests/test_review_server.py -v`
Expected: All existing tests pass.

**Step 5: Commit**

```bash
git add execution/wsgi_review.py execution/review_server.py
git commit -m "feat: add Gunicorn WSGI entry point + update CORS for tunnel domain"
```

---

## Task 3: Create launchd Daemon + Cloudflare Tunnel Config

**Files:**
- Create: `runbooks/com.genlab.review-server.plist`
- Modify: `~/.cloudflared/config.yml` (user config, not repo)

**Step 1: Create the launchd plist**

Create `runbooks/com.genlab.review-server.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.genlab.review-server</string>

    <key>ProgramArguments</key>
    <array>
        <string>/Users/anarchistsid/GenLab/Content Scraper/venv/bin/gunicorn</string>
        <string>execution.wsgi_review:app</string>
        <string>--worker-class</string>
        <string>eventlet</string>
        <string>--workers</string>
        <string>2</string>
        <string>--timeout</string>
        <string>120</string>
        <string>--bind</string>
        <string>127.0.0.1:5151</string>
        <string>--access-logfile</string>
        <string>/Users/anarchistsid/GenLab/Content Scraper/.tmp/logs/review_access.log</string>
        <string>--error-logfile</string>
        <string>/Users/anarchistsid/GenLab/Content Scraper/.tmp/logs/review_error.log</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/anarchistsid/GenLab/Content Scraper</string>

    <key>StandardOutPath</key>
    <string>/Users/anarchistsid/GenLab/Content Scraper/.tmp/logs/review_server_stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/anarchistsid/GenLab/Content Scraper/.tmp/logs/review_server_stderr.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/Users/anarchistsid/GenLab/Content Scraper/venv/bin:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
        <key>HOME</key>
        <string>/Users/anarchistsid</string>
        <key>LANG</key>
        <string>en_US.UTF-8</string>
        <key>PYTHONPATH</key>
        <string>/Users/anarchistsid/GenLab/Content Scraper</string>
    </dict>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>ThrottleInterval</key>
    <integer>10</integer>
</dict>
</plist>
```

**Step 2: Update cloudflared config to add review.aspirehub.ai**

Update `~/.cloudflared/config.yml` to add the review ingress:

```yaml
tunnel: trading-bot
credentials-file: /Users/anarchistsid/.cloudflared/8f86aa87-1eb5-4fd9-b07e-f1e0dd1b8aee.json

ingress:
  - hostname: dash.astuteos.com
    service: http://localhost:8501
  - hostname: review.aspirehub.ai
    service: http://localhost:5151
  - service: http_status:404
```

**Step 3: Add DNS CNAME for review.aspirehub.ai**

This requires DNS configuration. Two options:

**Option A (aspirehub.ai already on Cloudflare):**
```bash
# Add CNAME via Cloudflare dashboard or CLI
cloudflared tunnel route dns trading-bot review.aspirehub.ai
```

**Option B (aspirehub.ai on external DNS):**
Add a CNAME record:
- Name: `review`
- Target: `8f86aa87-1eb5-4fd9-b07e-f1e0dd1b8aee.cfargotunnel.com`
- Proxy: OFF (if external DNS) or ON (if Cloudflare)

**Step 4: Restart cloudflared to pick up new config**

```bash
# If cloudflared runs as a launchd service:
launchctl stop com.cloudflare.cloudflared 2>/dev/null
launchctl start com.cloudflare.cloudflared 2>/dev/null

# Or if running manually:
cloudflared tunnel run trading-bot
```

**Step 5: Verify tunnel routes include review.aspirehub.ai**

```bash
cloudflared tunnel info trading-bot
```

Expected: Should list both `dash.astuteos.com` and `review.aspirehub.ai`.

**Step 6: Commit the plist (cloudflared config is not in repo)**

```bash
git add runbooks/com.genlab.review-server.plist
git commit -m "infra: add launchd daemon plist for always-on review server"
```

---

## Task 4: Extract Frontend — HTML Template

**Files:**
- Create: `templates/review/index.html`
- Create: `templates/review/css/dashboard.css`
- Create: `templates/review/js/dashboard.js`
- Create: `templates/review/js/components.js`
- Modify: `execution/review_server.py` — replace embedded HTML with `render_template()`

This is the largest task. The current `DASHBOARD_HTML` variable (lines 893-2085 of review_server.py) contains ~1,200 lines of embedded HTML + CSS + JS. We extract it into separate files.

**Step 1: Create template directory structure**

```bash
mkdir -p templates/review/css templates/review/js
```

**Step 2: Create `templates/review/index.html`**

This is the main HTML shell. CSS and JS are loaded as separate files via Flask static routes.

The HTML should contain:
- `<head>` with meta tags, link to `dashboard.css`
- `<body>` with the dashboard layout structure
- `<script>` tags loading `dashboard.js` and `components.js`
- A `<meta name="csrf-token">` tag injected by Flask (Jinja2 template variable)

Key HTML structure (extracted from current embedded HTML):
```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="csrf-token" content="{{ csrf_token }}">
    <title>Review Dashboard — Blackbox Brief</title>
    <link rel="stylesheet" href="/static/review/css/dashboard.css">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
</head>
<body>
    <!-- Header / nav bar -->
    <header>...</header>

    <!-- Status bar -->
    <div class="status-bar">...</div>

    <!-- Filter pills -->
    <div class="filter-bar">...</div>

    <!-- Main content: card grid -->
    <main class="card-grid" id="card-grid"></main>

    <!-- Detail panel (slide-in overlay) -->
    <div class="detail-overlay" id="detail-overlay"></div>
    <div class="detail-panel" id="detail-panel"></div>

    <!-- Slide viewer modal -->
    <div class="slide-viewer" id="slide-viewer">...</div>

    <!-- Undo toast -->
    <div class="undo-toast" id="undo-toast"></div>

    <!-- Batch action bar -->
    <div class="batch-bar" id="batch-bar">...</div>

    <!-- Keyboard shortcut help -->
    <div class="shortcut-overlay" id="shortcut-help" style="display:none">...</div>

    <script src="/static/review/js/components.js"></script>
    <script src="/static/review/js/dashboard.js"></script>
</body>
</html>
```

Extract the full HTML structure from the current `DASHBOARD_HTML` in `review_server.py` (lines 893-2085). Replace the font (JetBrains Mono to Inter), update CSS variable names, and restructure the layout from a single-column list to a responsive card grid.

**Step 3: Create `templates/review/css/dashboard.css`**

Extract and modernize all CSS from the embedded `<style>` block. Key changes from current:

- Font: `'JetBrains Mono'` to `'Inter', system-ui, sans-serif`
- Layout: Single column to CSS Grid responsive card grid
- Colors: Keep dark theme, update accent colors to brand palette (indigo #6366f1, purple #8b5cf6, cyan #06b6d4)
- Add detail panel styles (slide-in from right, 480px width)
- Add undo toast styles (fixed bottom-center, 5s animation)
- Add schedule timeline styles (horizontal bar with slot indicators)
- Mobile-first responsive breakpoints

CSS custom properties (root variables):
```css
:root {
    --bg: #0a0a0f;
    --surface: #141420;
    --surface-hover: #1a1a2e;
    --border: #2a2a3e;
    --text: #e2e8f0;
    --text-muted: #94a3b8;
    --heading: #f8fafc;
    --primary: #6366f1;
    --primary-hover: #818cf8;
    --secondary: #8b5cf6;
    --accent: #06b6d4;
    --success: #22c55e;
    --danger: #ef4444;
    --warning: #f59e0b;
    --font: 'Inter', system-ui, -apple-system, sans-serif;
    --radius: 12px;
    --radius-sm: 8px;
    --shadow: 0 4px 6px -1px rgba(0,0,0,0.3);
    --transition: 150ms ease;
}
```

**Step 4: Create `templates/review/js/components.js`**

Extract reusable UI component builders. These functions create DOM elements:

- `buildCard(bp)` — Creates a review card with thumbnail, metadata, action buttons
- `buildVideoWrapper(bp)` — Creates video player with auto-loop, mute, controls
- `buildThumbnailStrip(bp)` — Creates carousel slide thumbnail row
- `buildDetailPanel(bp)` — Creates the expanded detail view
- `buildUndoToast(action, recordId, onUndo)` — Creates undo notification
- `buildScheduleTimeline(blueprints)` — Creates the horizontal schedule bar
- `buildFeedbackForm(onSubmit)` — Creates reject/revise feedback form
- `buildCarouselViewer(bp)` — Creates navigable carousel slide viewer
- `buildShortcutHelp()` — Creates keyboard shortcut reference overlay

Each component should be a pure function that returns a DOM element, following the pattern already used in the current codebase. All dynamic content must use `textContent` or safe DOM methods (createElement/appendChild) — never raw HTML string insertion for user data.

**Step 5: Create `templates/review/js/dashboard.js`**

Extract application logic:

- State management (current filter, batch mode, selected cards, auto-approve settings)
- API calls (`fetchBlueprints()`, `reviewAction()`, `batchAction()`, `loadSettings()`)
- CSRF token handling (read from meta tag)
- WebSocket connection + event handlers
- Keyboard shortcut handler (`a`=approve, `r`=reject, `v`=revise, `s`=skip, `j/k`=navigate, `Escape`=close detail)
- Filter logic
- Undo timer management (5-second window)
- Auto-approve timer + progress bar
- `IntersectionObserver` for lazy video loading
- Touch swipe gesture handlers
- Detail panel open/close logic

**Step 6: Update Flask server to serve templates + static files**

In `execution/review_server.py`, make these changes:

1. Add template and static folder config:
```python
app = Flask(
    __name__,
    template_folder=str(PROJECT_ROOT / "templates"),
    static_folder=str(PROJECT_ROOT / "templates"),
    static_url_path="/static",
)
```

2. Update the index route:
```python
from flask import render_template

@app.route("/")
def index():
    """Serve the review dashboard."""
    return render_template("review/index.html", csrf_token=_generate_csrf_token())
```

3. Remove the `DASHBOARD_HTML` variable entirely (lines 893-2085).

4. Add a route for serving review static assets with path traversal protection:
```python
@app.route("/static/review/<path:filepath>")
def serve_review_static(filepath):
    """Serve CSS/JS files for the review dashboard."""
    static_dir = PROJECT_ROOT / "templates" / "review"
    file_path = (static_dir / filepath).resolve()
    # Security: ensure path is under static_dir
    try:
        file_path.relative_to(static_dir.resolve())
    except ValueError:
        abort(403)
    if not file_path.is_file():
        abort(404)
    mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    return send_file(str(file_path), mimetype=mime)
```

**Step 7: Run tests**

Run: `venv/bin/python -m pytest tests/test_review_server.py -v`
Expected: Tests pass. Some tests may need updates for the new template-based response (checking that HTML is returned, not the exact string).

**Step 8: Manual smoke test**

Run: `venv/bin/python execution/review_server.py --local --port 5151`
Open: `http://localhost:5151`
Expected: Dashboard loads with the new design. Cards render from local blueprints.

**Step 9: Commit**

```bash
git add templates/review/ execution/review_server.py
git commit -m "feat: extract frontend to template files + new card grid layout"
```

---

## Task 5: UI Redesign — Card Grid Layout + Design System

**Files:**
- Modify: `templates/review/index.html`
- Modify: `templates/review/css/dashboard.css`
- Modify: `templates/review/js/components.js`

This task implements the visual redesign. The card grid replaces the current single-column list layout.

**Step 1: Implement responsive card grid CSS**

Key layout rules in `dashboard.css`:

```css
.card-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
    gap: 20px;
    padding: 20px;
    max-width: 1600px;
    margin: 0 auto;
}

@media (max-width: 1024px) {
    .card-grid { grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px; padding: 16px; }
}

@media (max-width: 640px) {
    .card-grid { grid-template-columns: 1fr; gap: 12px; padding: 12px; }
}
```

Card styling:
```css
.card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    overflow: hidden;
    transition: transform var(--transition), box-shadow var(--transition);
    position: relative;
    cursor: pointer;
}

.card:hover {
    transform: translateY(-2px);
    box-shadow: var(--shadow);
    border-color: var(--primary);
}
```

**Step 2: Implement status bar**

HTML structure for the top status bar showing pending count, approved today, and schedule coverage.

**Step 3: Implement filter pills**

Update the filter bar with pill-style buttons: All | Pending | Approved | Rejected

**Step 4: Update card component with new design**

Update `buildCard()` in `components.js` to match new card structure:

- **Video/media area** (top of card): Poster image or first carousel slide, auto-play on hover
- **Content area** (middle): Hook text (truncated to 2 lines), schedule time badge, status badge
- **Action bar** (bottom): Approve (green), Reject (red), Revise (amber) buttons

Card visual design details:
- Status badge: colored pill in top-right corner of media area
- Format tag: small grey pill (carousel/reel)
- Quick-approve: green checkmark on hover (right side)
- Quick-reject: red X on hover (left side)

**Step 5: Verify responsive layout**

Run: `venv/bin/python execution/review_server.py --local`
Test at widths: 1440px (3 cols), 1024px (2 cols), 375px (1 col)
Expected: Grid reflows cleanly, cards maintain aspect ratios.

**Step 6: Commit**

```bash
git add templates/review/
git commit -m "feat: implement card grid layout + new design system"
```

---

## Task 6: UI Redesign — Detail Panel + Video Player

**Files:**
- Modify: `templates/review/css/dashboard.css`
- Modify: `templates/review/js/components.js`
- Modify: `templates/review/js/dashboard.js`

**Step 1: Implement detail panel (slide-in overlay)**

The detail panel slides in from the right when a card is clicked. 480px wide on desktop, full-width on mobile.

CSS for panel + overlay backdrop with smooth transitions.

**Step 2: Build detail panel content**

The `buildDetailPanel(bp)` function creates:

1. **Close button** (top-right X)
2. **Large video player** with controls (loop toggle, mute, seek, fullscreen)
3. **Carousel viewer** with left/right arrows for multi-slide posts
4. **Full caption text** (scrollable)
5. **Platform preview tabs** (Instagram / YouTube / Twitter)
6. **Per-platform publish status badges**
7. **Review action buttons** (large, prominent: Approve / Reject / Revise)
8. **Feedback form** (visible on reject/revise): dropdown for issue category + textarea for notes

JS to handle opening/closing via `openDetail(bp)` and `closeDetail()`.

**Step 3: Enhance video player**

Update `buildVideoWrapper()` with improved controls:
- Custom play/pause, mute/unmute, loop toggle buttons
- `preload="metadata"` for lazy loading
- `playsInline` attribute for mobile

**Step 4: Add carousel slide viewer**

For carousel posts, navigable carousel with prev/next buttons and slide counter.
Each slide image loaded lazily. Arrow keys navigate when detail panel is open.

**Step 5: Verify detail panel + video**

Run: `venv/bin/python execution/review_server.py --local`
- Click a card: detail panel slides in from right
- Click overlay or press Escape: panel closes
- Video plays with loop + controls work
- Carousel arrows navigate slides

**Step 6: Commit**

```bash
git add templates/review/
git commit -m "feat: add detail panel with video player + carousel viewer"
```

---

## Task 7: UI Redesign — Keyboard Shortcuts, Undo, Batch Mode

**Files:**
- Modify: `templates/review/js/dashboard.js`
- Modify: `templates/review/css/dashboard.css`
- Modify: `templates/review/js/components.js`

**Step 1: Implement keyboard navigation**

Add a keyboard handler in `dashboard.js`:

Shortcuts:
- `a` = Approve focused card
- `r` = Reject focused card
- `v` = Revise focused card
- `s` = Skip focused card
- `j` / `ArrowDown` = Next card
- `k` / `ArrowUp` = Previous card
- `Enter` = Open detail for focused card
- `Escape` = Close detail panel
- `b` = Toggle batch mode
- `Space` = Toggle select in batch mode
- `?` = Show shortcut help

Ignore keystrokes when focus is in an input/textarea. Track `focusedCardIndex` for navigation. Show visual `.focused` indicator on the active card.

**Step 2: Implement undo toast (5-second window)**

When a review action is taken, show an undo toast for 5 seconds before committing to Microsoft Lists:

- `reviewActionWithUndo(recordId, action)` — shows toast, starts 5s timer, optimistically updates UI
- `undoLastAction()` — cancels timer, restores card UI
- `commitReview(recordId, action)` — actual API call to `/api/review/<id>`

Toast CSS: fixed bottom-center, slides up on show, includes countdown progress bar (CSS animation from 100% to 0% width over 5 seconds).

**Step 3: Enhance batch mode**

Floating batch action bar at the bottom when batch mode is active:
- Count of selected items
- Batch Approve / Batch Reject buttons
- Select All / Cancel buttons

Checkboxes visible on each card only in batch mode. Space to toggle selection on focused card.

**Step 4: Add keyboard shortcut help overlay**

Show available shortcuts when user presses `?`. Modal overlay with a grid of `<kbd>` elements + descriptions. Closes on Escape or clicking outside.

**Step 5: Verify keyboard navigation + undo + batch**

Run: `venv/bin/python execution/review_server.py --local`
Test:
- Press `j`/`k`: focus moves between cards with visual indicator
- Press `a` with card focused: undo toast appears, card greys out
- Click "Undo" within 5 seconds: card restored
- Wait 5 seconds: action commits to API
- Press `b`: batch mode activates, checkboxes appear
- Space to select cards, batch approve: all selected cards approved
- Press `?`: shortcut help overlay appears

**Step 6: Commit**

```bash
git add templates/review/
git commit -m "feat: add keyboard shortcuts, undo toast, enhanced batch mode"
```

---

## Task 8: Update Tests for New Structure

**Files:**
- Modify: `tests/test_review_server.py`

**Step 1: Update test imports and fixtures**

The existing tests import `DASHBOARD_HTML` which no longer exists. Update imports to remove `DASHBOARD_HTML` reference. Keep:

```python
from execution.review_server import (
    app,
    normalize_blueprint,
    _parse_visual_paths,
    _generate_csrf_token,
)
```

**Step 2: Update the index page test**

Replace any test checking for `DASHBOARD_HTML` content with tests for the template:

```python
class TestIndexPage:
    def test_index_returns_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.content_type.startswith("text/html")

    def test_index_contains_csrf_meta(self, client):
        resp = client.get("/")
        assert b'csrf-token' in resp.data

    def test_css_loads(self, client):
        resp = client.get("/static/review/css/dashboard.css")
        assert resp.status_code == 200
        assert "text/css" in resp.content_type

    def test_js_loads(self, client):
        resp = client.get("/static/review/js/dashboard.js")
        assert resp.status_code == 200
        assert "javascript" in resp.content_type
```

**Step 3: Add test for static file path traversal protection**

```python
class TestStaticFileSecurity:
    def test_blocks_path_traversal(self, client):
        resp = client.get("/static/review/../../../.env")
        assert resp.status_code in (403, 404)

    def test_blocks_absolute_path(self, client):
        resp = client.get("/static/review//etc/passwd")
        assert resp.status_code in (403, 404)
```

**Step 4: Verify all existing tests still pass**

Run: `venv/bin/python -m pytest tests/test_review_server.py -v`
Expected: All tests pass.

**Step 5: Run full test suite to check for regressions**

Run: `venv/bin/python -m pytest tests/ -x --timeout=300 -q`
Expected: All tests pass (the existing timezone test failure is pre-existing and unrelated).

**Step 6: Commit**

```bash
git add tests/test_review_server.py
git commit -m "test: update review server tests for template-based frontend"
```

---

## Task 9: End-to-End Verification + Documentation

**Files:**
- Modify: `CLAUDE.md` (add review server to execution tools table)

**Step 1: Start the review server with Gunicorn**

```bash
cd "/Users/anarchistsid/GenLab/Content Scraper"
venv/bin/gunicorn execution.wsgi_review:app \
    --worker-class eventlet \
    --workers 2 \
    --timeout 120 \
    --bind 127.0.0.1:5151
```

Expected: Server starts, logs show 2 workers.

**Step 2: Verify local access**

```bash
curl -s http://localhost:5151/ | head -5
curl -s http://localhost:5151/api/health | python3 -m json.tool
```

Expected: HTML response + health JSON with `"status": "ok"`.

**Step 3: Install launchd daemon**

```bash
cp runbooks/com.genlab.review-server.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.genlab.review-server.plist
```

Verify it starts:
```bash
launchctl list | grep review-server
curl -s http://localhost:5151/api/health | python3 -m json.tool
```

**Step 4: Verify tunnel access (after DNS propagation)**

```bash
# This will only work after aspirehub.ai DNS is configured
curl -s https://review.aspirehub.ai/api/health | python3 -m json.tool
```

Expected: Same health response (may take time for DNS propagation).

**Step 5: Verify Cloudflare Access (manual)**

1. Open `https://review.aspirehub.ai` in an incognito browser
2. Should see Cloudflare Access login page
3. Sign in with Google SSO
4. Should see the review dashboard

Note: Cloudflare Access must be configured manually via the Cloudflare Zero Trust dashboard:
- Create an Access Application for `review.aspirehub.ai`
- Policy: Allow specific email addresses
- Identity provider: Google (or GitHub)

**Step 6: Run verification checklist**

From the design doc, verify each item:

- [ ] Server starts automatically on boot via launchd
- [ ] Server recovers from crashes (KeepAlive)
- [ ] Dashboard shows VISUAL_READY posts from Microsoft Lists
- [ ] Video playback works (auto-loop, seek, controls)
- [ ] Approve/reject/revise actions update Microsoft Lists
- [ ] Keyboard shortcuts work (a/r/v/s/j/k)
- [ ] Undo window prevents accidental actions
- [ ] Batch mode works for multiple selections
- [ ] Mobile layout is usable (responsive grid, touch actions)
- [ ] All existing tests pass

**Step 7: Update CLAUDE.md execution tools table**

Add entry for `wsgi_review.py`:

```
| `wsgi_review.py` | Gunicorn WSGI entry point for review server |
```

**Step 8: Final commit**

```bash
git add CLAUDE.md
git commit -m "docs: add wsgi_review.py to execution tools table"
```

---

## Execution Notes

### Dependencies Between Tasks

```
Task 1 (Gunicorn deps)
  |
Task 2 (WSGI entry + CORS)
  |
Task 3 (launchd + cloudflare) <-- can run in parallel with Task 4
  |
Task 4 (extract frontend)
  |
Task 5 (card grid + design)
  |
Task 6 (detail panel + video)
  |
Task 7 (keyboard + undo + batch)
  |
Task 8 (tests)
  |
Task 9 (E2E verification)
```

Tasks 3 and 4 can be done in parallel since they touch different files. All other tasks are sequential.

### Infrastructure Pre-requisites (manual, before Task 3)

1. **aspirehub.ai DNS**: Must be pointed to Cloudflare nameservers, OR have a CNAME record `review` pointing to `8f86aa87-1eb5-4fd9-b07e-f1e0dd1b8aee.cfargotunnel.com`
2. **Cloudflare Access**: Must be configured in the Cloudflare Zero Trust dashboard (free tier, up to 50 users)
3. **Log directory**: `mkdir -p .tmp/logs` (should already exist)

### Risk Areas

- **Cloudflare tunnel config**: Adding `review.aspirehub.ai` to the existing `trading-bot` tunnel config. If the tunnel restarts, both `dash.astuteos.com` and `review.aspirehub.ai` are affected briefly.
- **SocketIO + eventlet**: Gunicorn with eventlet worker may behave differently from Flask dev server for WebSocket connections. Test SocketIO thoroughly.
- **Frontend extraction**: The embedded HTML is 1,200 lines. Extracting to separate files requires careful handling of the CSRF token injection (template variable) and API URL references.
