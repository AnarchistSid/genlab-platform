# Express Lane E2E Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Prove the express lane pipeline works end-to-end, build a dark-terminal-aesthetic web review UI, and optimize breaking-news-to-review speed to <60 seconds.

**Architecture:** Three-phase approach — (1) E2E test harness with synthetic breaking news and real LLM calls to find bugs and measure baseline timing, (2) Flask + WebSocket web review dashboard to replace CLI streaming review, (3) speed profiling and optimization of the bottlenecks revealed by phase 1.

**Tech Stack:** Python 3.13, Flask + flask-socketio, OpenAI gpt-4o-mini, existing Microsoft Lists client, vanilla HTML/CSS/JS

---

### Task 1: Install Flask Dependencies

**Files:**
- Modify: `requirements.txt`

**Step 1: Add Flask and flask-socketio to requirements**

Add these two lines at the end of `requirements.txt` (before any trailing newline):

```
# Web review UI (Phase 8: Express Lane)
flask>=3.0.0
flask-socketio>=5.3.0
```

**Step 2: Install dependencies**

Run: `cd "/Users/anarchistsid/GenLab/Content Scraper" && ./venv/bin/pip install flask flask-socketio`
Expected: Both packages install successfully. Verify with:
Run: `./venv/bin/python -c "import flask; import flask_socketio; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add requirements.txt
git commit -m "feat: add flask + flask-socketio dependencies for web review UI"
```

---

### Task 2: Create Synthetic Breaking News Test Data

**Files:**
- Create: `.tmp/runs/test_express/parsed_items.json`

**Step 1: Create test run directory**

Run: `mkdir -p "/Users/anarchistsid/GenLab/Content Scraper/.tmp/runs/test_express"`

**Step 2: Write synthetic test data**

Create `.tmp/runs/test_express/parsed_items.json` with 5 stories designed to trigger specific urgency levels:

```json
[
  {
    "title": "OpenAI Releases GPT-5 With Revolutionary Multi-Modal Reasoning",
    "summary": "Breaking: OpenAI just announced GPT-5. The model shows 3x improvement on reasoning benchmarks with native vision, audio, and code execution. Sam Altman says this represents the biggest leap since GPT-4.",
    "url": "https://openai.com/blog/gpt-5-release",
    "link": "https://openai.com/blog/gpt-5-release",
    "published": "2026-02-20T10:00:00Z",
    "domain": "openai.com",
    "source": "OpenAI",
    "story_id": "test_critical_001",
    "_test_expected_urgency": "CRITICAL"
  },
  {
    "title": "Google Acquires Anthropic for $40B in Landmark AI Deal",
    "summary": "Google has acquired Anthropic in a $40 billion deal, the largest acquisition in AI history. Claude will be integrated into Google's product lineup. Anthropic CEO Dario Amodei will lead Google's newly formed AI Safety division.",
    "url": "https://blog.google/technology/ai/anthropic-acquisition",
    "link": "https://blog.google/technology/ai/anthropic-acquisition",
    "published": "2026-02-20T09:30:00Z",
    "domain": "blog.google",
    "source": "Google",
    "story_id": "test_critical_002",
    "_test_expected_urgency": "CRITICAL"
  },
  {
    "title": "BREAKING: Gemini 3 Just Launched With 10M Token Context Window",
    "summary": "Google DeepMind just released Gemini 3 with a 10 million token context window. The model outperforms GPT-5 on 8 of 12 standard benchmarks. Available today in Google AI Studio.",
    "url": "https://deepmind.google/gemini-3-launch",
    "link": "https://deepmind.google/gemini-3-launch",
    "published": "2026-02-20T11:00:00Z",
    "domain": "deepmind.google",
    "source": "Google DeepMind",
    "story_id": "test_high_001",
    "_test_expected_urgency": "HIGH"
  },
  {
    "title": "Just Announced: NVIDIA Unveils B300 GPU at GTC 2026 Keynote",
    "summary": "NVIDIA just announced the B300 Blackwell Ultra GPU at GTC 2026. The chip delivers 5x inference throughput of the H200. Jensen Huang calls it the most important product launch in NVIDIA history.",
    "url": "https://blogs.nvidia.com/blog/b300-gtc-2026",
    "link": "https://blogs.nvidia.com/blog/b300-gtc-2026",
    "published": "2026-02-20T14:00:00Z",
    "domain": "blogs.nvidia.com",
    "source": "NVIDIA",
    "story_id": "test_high_002",
    "_test_expected_urgency": "HIGH"
  },
  {
    "title": "New Study Shows AI Coding Assistants Improve Productivity by 15%",
    "summary": "Researchers at MIT found that AI coding assistants provide a modest 15 percent improvement in developer productivity for routine tasks. The study analyzed 500 developers over 6 months.",
    "url": "https://techcrunch.com/2026/02/20/ai-coding-productivity",
    "link": "https://techcrunch.com/2026/02/20/ai-coding-productivity",
    "published": "2026-02-20T08:00:00Z",
    "domain": "techcrunch.com",
    "source": "TechCrunch",
    "story_id": "test_low_001",
    "_test_expected_urgency": "LOW"
  }
]
```

**Step 3: Verify the data is valid JSON**

Run: `./venv/bin/python -c "import json; d=json.load(open('.tmp/runs/test_express/parsed_items.json')); print(f'{len(d)} stories loaded')"`
Expected: `5 stories loaded`

---

### Task 3: Create E2E Express Test Harness

**Files:**
- Create: `.tmp/runs/test_express/run_express_e2e.py`

**Step 1: Write the test harness**

Create `.tmp/runs/test_express/run_express_e2e.py` — a Python script that runs each express lane step as a subprocess, captures timing, validates outputs exist, and prints a report.

Key requirements:
- Uses `subprocess.run()` with `VENV_PYTHON` for each step
- Times each step with `time.time()` deltas
- After step 1 (classify_urgency): verify `express_candidates.json` exists and has >=3 stories with `express: true`
- After step 2 (compose_blueprints): verify `blueprint_pack.json` exists and has >=1 candidate
- After step 3 (generate_content): verify blueprint_pack.json candidates have `hook` field
- After step 4 (run_qc_gates): verify it ran without fatal error (non-fatal QC failures OK)
- After step 5 (write_post_content): verify at least one blueprint has `caption` field in output
- Step 6 (prepare_for_review --stream --dry-run): verify it prints review cards
- **Skip:** push_to_backlog (no Microsoft Lists writes during test)
- Writes `express_e2e_report.json` with all timing data + pass/fail per step
- Prints a formatted timing summary at the end

The script structure:

```python
#!/usr/bin/env python3
"""E2E test for express lane pipeline — breaking news → review-ready."""

import json, subprocess, sys, time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
VENV_PYTHON = str(PROJECT_ROOT / "venv/bin/python3")
RUN_ID = "test_express"
RUN_DIR = PROJECT_ROOT / ".tmp/runs" / RUN_ID

def run_step(label, cmd, check_fn=None):
    """Run a pipeline step, time it, validate output."""
    # ... timing, subprocess.run, check_fn validation

STEPS = [
    ("classify_urgency", [VENV_PYTHON, "execution/classify_urgency.py", "--run-id", RUN_ID], check_urgency),
    ("compose_blueprints", [VENV_PYTHON, "execution/compose_blueprints.py", "--trend-pack", str(RUN_DIR/"express_trend_pack.json"), "--run-id", RUN_ID], check_blueprints),
    ("generate_content", [VENV_PYTHON, "execution/generate_content.py", "--run-id", RUN_ID], check_content),
    ("run_qc_gates", [VENV_PYTHON, "execution/run_qc_gates.py", "--run-id", RUN_ID], None),
    ("write_post_content", [VENV_PYTHON, "execution/write_post_content.py", "--run-id", RUN_ID], check_post_content),
    ("prepare_for_review", [VENV_PYTHON, "execution/prepare_for_review.py", "--run-id", RUN_ID, "--stream", "--dry-run"], None),
]
```

**Step 2: Run the E2E test**

Run: `cd "/Users/anarchistsid/GenLab/Content Scraper" && ./venv/bin/python .tmp/runs/test_express/run_express_e2e.py`
Expected: Each step runs, timing is captured, express_e2e_report.json is written.

**Step 3: Fix any failures**

If any step fails: read the error, fix the underlying script, re-run just the failing step. Common issues to watch for:
- `classify_urgency.py` might not find `parsed_items.json` if it checks wrong directory
- `compose_blueprints.py` needs an `inspo_pack.json` or templates — may need to create a minimal one
- `generate_content.py` needs valid OPENAI_API_KEY in .env
- `write_post_content.py` reads from Microsoft Lists by default — needs `--run-id` to read from local files instead

**Step 4: Show timing results**

After successful run, read and display `express_e2e_report.json`. The output should look like:
```
EXPRESS LANE E2E TEST — RESULTS
═══════════════════════════════════════════
  classify_urgency:     0.Xs  ✓
  compose_blueprints:   X.Xs  ✓
  generate_content:     X.Xs  ✓  (LLM: OpenAI)
  run_qc_gates:         X.Xs  ✓
  write_post_content:   X.Xs  ✓  (LLM: OpenAI)
  prepare_for_review:   X.Xs  ✓
  ─────────────────────────────────
  TOTAL:               XX.Xs
  TARGET:              <60.0s
  STATUS:              PASS/FAIL
```

---

### Task 4: Build Web Review Server — Backend

**Files:**
- Create: `execution/review_server.py`

**Step 1: Write the Flask app backend**

Create `execution/review_server.py` with these routes:

```python
#!/usr/bin/env python3
"""Web-based review dashboard for the express lane pipeline.

Dark terminal aesthetic. Shows DRAFTED + VISUAL_READY blueprints
as review cards with approve/reject/skip actions.

Usage:
    python execution/review_server.py                    # Start on :5151
    python execution/review_server.py --port 8080        # Custom port
    python execution/review_server.py --dry-run          # No Microsoft Lists writes
"""
```

Routes to implement:
- `GET /` — Serve the single-page dashboard HTML (embedded in Python string)
- `GET /api/blueprints` — Query Microsoft Lists for DRAFTED + VISUAL_READY blueprints, return JSON
- `POST /api/review/<record_id>` — Accept `{action: "approved"|"rejected"|"skipped", notes: "..."}`, update Microsoft Lists
- `GET /api/express/status` — Return last express run timing from `.tmp/runs/*/express_e2e_report.json`
- `GET /api/express/trigger` — Trigger a new express lane run (subprocess, background)

WebSocket events (flask-socketio):
- `express_progress` — Emitted during express runs with step name + timing
- `blueprint_updated` — Emitted after a review action (so other tabs refresh)

Microsoft Lists integration:
- Import and instantiate `BacklogClient` from `execution.utils.backlog_client`
- `get_blueprints_by_status("DRAFTED")` + `get_blueprints_by_status("VISUAL_READY")`
- `client.blueprints.update(record_id, {action_taken: ...}, typecast=True)`
- Wrap in try/except — if Microsoft Lists is down, return cached data or error

**Step 2: Test the backend API**

Run: `cd "/Users/anarchistsid/GenLab/Content Scraper" && ./venv/bin/python execution/review_server.py &`
Then: `curl http://localhost:5151/api/blueprints | python3 -m json.tool`
Expected: JSON array of blueprints (or empty array if none in Microsoft Lists)

Then: `curl http://localhost:5151/api/express/status | python3 -m json.tool`
Expected: JSON with last run timing or `{"status": "no_data"}`

Kill server: `kill %1`

**Step 3: Commit**

```bash
git add execution/review_server.py
git commit -m "feat: add Flask web review server backend (API + WebSocket)"
```

---

### Task 5: Build Web Review Server — Frontend (Dark Terminal UI)

**Files:**
- Modify: `execution/review_server.py` (add HTML template)

**Step 1: Add the embedded HTML/CSS/JS template**

Add a `DASHBOARD_HTML` string constant to `review_server.py` containing the full single-page app. Design specs:

**CSS:**
- Background: `#0d1117` (GitHub dark)
- Card background: `#161b22`
- Border: `#30363d`
- Text: `#c9d1d9` (body), `#f0f6fc` (headings)
- Green accent: `#3fb950` (approve)
- Red accent: `#f85149` (reject)
- Amber accent: `#d29922` (urgency HIGH)
- Red badge: `#da3633` background for CRITICAL
- Font: `'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace`
- Card max-width: 600px, grid layout

**HTML structure:**
```html
<div id="app">
  <header> Express Lane Status | Timer | Trigger Button </header>
  <main id="cards"> <!-- Blueprint cards injected here --> </main>
</div>
```

**Each card:**
```html
<div class="card" data-id="rec123">
  <div class="card-header">
    <span class="urgency-badge critical">CRITICAL</span>
    <span class="topic">AI / GPT-5</span>
    <span class="format">reel</span>
  </div>
  <div class="hook">"OpenAI Just Dropped GPT-5"</div>
  <div class="slides">Slide 1: ... | Slide 2: ...</div>
  <div class="caption">...</div>
  <div class="actions">
    <button class="btn-approve" onclick="review('rec123','approved')">✓ Approve</button>
    <button class="btn-reject" onclick="review('rec123','rejected')">✗ Reject</button>
    <button class="btn-skip" onclick="review('rec123','skipped')">→ Skip</button>
  </div>
</div>
```

**JavaScript:**
- `fetchBlueprints()` — GET `/api/blueprints`, render cards
- `review(id, action)` — POST `/api/review/{id}`, remove card with fade animation
- `pollBlueprints()` — `setInterval(fetchBlueprints, 5000)`
- WebSocket connection to `/ws/stream` for express lane progress
- Express trigger button calls `GET /api/express/trigger`

**Step 2: Test the full UI**

Run: `./venv/bin/python execution/review_server.py`
Open: `http://localhost:5151` in browser
Expected: Dark dashboard loads, shows any DRAFTED blueprints (or empty state message)

**Step 3: Commit**

```bash
git add execution/review_server.py
git commit -m "feat: add dark terminal review UI (embedded HTML/CSS/JS)"
```

---

### Task 6: Wire Express Trigger to Web UI

**Files:**
- Modify: `execution/review_server.py` (add express trigger + WebSocket progress)

**Step 1: Implement express trigger endpoint**

Add `GET /api/express/trigger` that:
1. Spawns the express pipeline as a background thread
2. Runs each step sequentially (classify → compose → generate → QC → write_post → review_prep)
3. After each step completes, emits a `express_progress` WebSocket event with:
   ```json
   {"step": "classify_urgency", "status": "complete", "elapsed_s": 0.3, "total_elapsed_s": 0.3}
   ```
4. When all steps complete, emits `express_complete` with full timing summary
5. Refreshes the blueprint list (emits `blueprints_updated`)

**Step 2: Add progress bar to frontend**

Add a `<div id="express-status">` bar at the top of the dashboard that shows:
- Idle: `🟢 Express lane idle — last run: 23.4s`
- Running: `🔴 Express lane running... classify_urgency ✓ (0.3s) → compose_blueprints...`
- Complete: `✅ Express complete — 4 stories in 34.2s`

The bar updates in real-time via WebSocket.

**Step 3: Test the trigger**

Run: `./venv/bin/python execution/review_server.py`
Open: `http://localhost:5151`
Click: "Trigger Express Run" button (with test data in .tmp/runs/test_express/)
Expected: Progress bar animates through each step, cards appear when stories are ready

**Step 4: Commit**

```bash
git add execution/review_server.py
git commit -m "feat: wire express lane trigger + WebSocket progress to review UI"
```

---

### Task 7: Integrate with daily_intel.sh

**Files:**
- Modify: `runbooks/daily_intel.sh` (line ~131, step 2h)

**Step 1: Add --web flag option to step 2h**

Change the express lane step 2h from:
```bash
run_step "2h" "Express: streaming review"      false "$VENV_PYTHON" execution/prepare_for_review.py --run-id "$RUN_ID" --stream
```
to:
```bash
# Express review: --web launches browser UI, --stream stays CLI
if [ "${EXPRESS_REVIEW_MODE:-stream}" = "web" ]; then
    run_step "2h" "Express: launching review UI" false "$VENV_PYTHON" execution/review_server.py --run-id "$RUN_ID" --auto-open --timeout 300
else
    run_step "2h" "Express: streaming review"    false "$VENV_PYTHON" execution/prepare_for_review.py --run-id "$RUN_ID" --stream
fi
```

This lets users set `EXPRESS_REVIEW_MODE=web` in `.env` to use the web UI instead of CLI.

**Step 2: Add --auto-open and --timeout to review_server.py**

Add CLI flags:
- `--auto-open` — opens browser to `http://localhost:5151` on startup (via `webbrowser.open()`)
- `--timeout 300` — auto-shutdown server after N seconds of inactivity (for cron safety)
- `--run-id` — pre-loads express timing data from that run

**Step 3: Commit**

```bash
git add runbooks/daily_intel.sh execution/review_server.py
git commit -m "feat: integrate web review UI into daily_intel.sh express lane"
```

---

### Task 8: Speed Profiling and Optimization

**Files:**
- Modify: `execution/generate_content.py` (parallel LLM calls)
- Modify: `execution/write_post_content.py` (parallel LLM calls)

**Step 1: Re-run E2E test to get baseline timing**

Run: `./venv/bin/python .tmp/runs/test_express/run_express_e2e.py`
Record: baseline timing per step. Expected bottlenecks: generate_content (10-20s), write_post_content (10-20s).

**Step 2: Add parallel LLM calls for express stories in generate_content.py**

Check if `generate_content.py` already processes blueprints sequentially. If so, add a `--parallel` flag or detect `express: true` in blueprint_pack.json and use `ThreadPoolExecutor(max_workers=3)` to generate content for 3-4 stories concurrently.

Key constraint: OpenAI rate limits — gpt-4o-mini allows ~500 RPM, so 3-4 concurrent calls is safe.

**Step 3: Add parallel LLM calls for express in write_post_content.py**

Same approach: detect express blueprints, parallelize the LLM calls.

**Step 4: Re-run E2E test to measure improvement**

Run: `./venv/bin/python .tmp/runs/test_express/run_express_e2e.py`
Compare: before vs after timing. Target: <60s total.

**Step 5: Commit**

```bash
git add execution/generate_content.py execution/write_post_content.py
git commit -m "perf: parallelize LLM calls for express lane stories"
```

---

### Task 9: Final E2E Proof and Report

**Files:**
- Create: `.tmp/runs/test_express/final_report.json`

**Step 1: Clean test artifacts and re-run from scratch**

```bash
rm -rf .tmp/runs/test_express/express_*.json .tmp/runs/test_express/blueprint_pack.json .tmp/runs/test_express/urgency_report.json
```

Run: `./venv/bin/python .tmp/runs/test_express/run_express_e2e.py`

**Step 2: Show final timing report**

Print the formatted timing comparison:
```
EXPRESS LANE SPEED — FINAL RESULTS
═══════════════════════════════════════════
Step                  Baseline    Optimized
─────────────────────────────────────────
classify_urgency      X.Xs        X.Xs
compose_blueprints    X.Xs        X.Xs
generate_content      X.Xs        X.Xs  ← parallelized
run_qc_gates          X.Xs        X.Xs
write_post_content    X.Xs        X.Xs  ← parallelized
prepare_for_review    X.Xs        X.Xs
─────────────────────────────────────────
TOTAL                 XX.Xs       XX.Xs
TARGET                <60.0s      <60.0s
```

**Step 3: Verify web UI works end-to-end**

Start server: `./venv/bin/python execution/review_server.py`
Open browser: `http://localhost:5151`
Click "Trigger Express Run" with test data
Verify: cards appear, approve/reject works, timing bar shows progress

**Step 4: Final commit**

```bash
git add -A
git commit -m "feat: express lane E2E validated — breaking news to review in <60s"
```
