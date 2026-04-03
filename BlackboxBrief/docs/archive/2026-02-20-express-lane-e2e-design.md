# Express Lane E2E: Test, Web UI, and Speed Optimization

**Date:** 2026-02-20
**Status:** Approved
**Scope:** End-to-end express lane validation, web review UI, speed profiling

---

## Problem

The express lane pipeline (classify_urgency → compose → generate → QC → push → review) exists in code but has never been tested end-to-end. The streaming review is CLI-only (no web UI). We don't know the actual breaking-news-to-published timing.

## Audit Results

| Component | Status |
|-----------|--------|
| fetch_ai_creators.py parallelization | Working (5 threads, rate limiting, caching) |
| classify_urgency.py | Working (regex signals, express_trend_pack output) |
| daily_intel.sh express lane | Working (steps 2b-2h, all flags correct) |
| prepare_for_review.py --stream | Working (CLI cards, countdown, Microsoft Lists integration) |
| End-to-end test | **Never tested** |
| Web review UI | **Missing** |
| Test coverage | **No express-specific tests** |

## Design

### 1. E2E Test Harness

**Goal:** Prove the express lane works end-to-end with real LLM calls and measure step-by-step timing.

**Test data:** 5 synthetic breaking news stories injected as `parsed_items.json`:
- 2x CRITICAL (major product launch + company acquisition)
- 2x HIGH (breaking news + viral moment)
- 1x LOW (control — should NOT enter express lane)

**Pipeline steps tested:**
1. classify_urgency.py → expect 4 express candidates, 1 standard
2. compose_blueprints.py --trend-pack express_trend_pack.json → blueprint_pack.json
3. generate_content.py → real OpenAI gpt-4o-mini calls
4. run_qc_gates.py → claims + constraints + risk
5. write_post_content.py → final copy (real LLM)
6. prepare_for_review.py --stream --dry-run → verify streaming review format

**Timing capture:** Each step timed with wall-clock delta. Report format:
```
classify_urgency:     X.Xs
compose_blueprints:   X.Xs
generate_content:     X.Xs  (LLM)
run_qc_gates:         X.Xs
write_post_content:   X.Xs  (LLM)
prepare_for_review:   X.Xs
TOTAL EXPRESS:        X.Xs
```

**Skip during test:** Microsoft Lists push (no --type flag), actual review writes.

**File:** `.tmp/runs/test_express/` (all artifacts), `express_e2e_report.json` (timing + pass/fail)

### 2. Web Review UI

**Goal:** Replace CLI `--stream` mode with a browser-based review dashboard.

**Architecture:**
```
Flask app (localhost:5151)
GET  /                  → Dashboard (review-pending blueprints)
GET  /api/blueprints    → JSON list from Microsoft Lists
POST /api/review/:id    → Approve/reject/skip (writes to Microsoft Lists)
GET  /api/express       → Express lane status + timing
WS   /ws/stream         → WebSocket for live express lane progress
```

**Visual style:** Dark terminal aesthetic — dark background, monospace fonts, green/amber/red accents. Matches the CLI streaming review feel.

**UI layout:**
- Top bar: express lane status (idle/running/timing), run trigger button
- Card grid: each blueprint as a card with hook, slides preview, caption, urgency badge
- Action buttons per card: Approve / Reject / Skip → Microsoft Lists API
- Auto-refresh: poll every 5s or WebSocket push during express runs
- Timer bar: live step-by-step progress during express pipeline

**Tech stack:**
- Flask + flask-socketio (WebSocket)
- Vanilla HTML/CSS/JS (no framework, single embedded template)
- Reuses existing utils/backlog_client.py

**File:** `execution/review_server.py` (~400 lines, single file)

**Integration:** `daily_intel.sh` step 2h can optionally use `--web` flag. Server also standalone.

### 3. Speed Profiling & Optimization

**Known bottlenecks:**
1. LLM calls (generate_content + write_post_content) — likely 70%+ of time
2. Microsoft Lists round-trips (push_to_backlog) — network latency
3. QC gates — CPU-bound claim validation

**Optimization levers:**
- Parallel LLM calls for express stories (ThreadPoolExecutor)
- Skip QC for CRITICAL urgency + Tier 1 source
- Batch Microsoft Lists writes (verify 10-record batches)
- Potentially merge generate_content + write_post_content for express stories

**Target:** Breaking news → review-ready in <60 seconds.

## Implementation Order

1. Create E2E test harness + synthetic data
2. Run test, fix whatever breaks, capture baseline timing
3. Build web review UI (review_server.py)
4. Wire web UI to express lane (WebSocket progress)
5. Profile bottlenecks, apply optimizations
6. Re-run test, show before/after timing improvement

## Files Created/Modified

| File | Action |
|------|--------|
| `.tmp/runs/test_express/parsed_items.json` | Create (synthetic test data) |
| `.tmp/runs/test_express/express_e2e_test.py` | Create (test harness) |
| `execution/review_server.py` | Create (Flask web UI) |
| `runbooks/daily_intel.sh` | Minor: add --web option to step 2h |
| `requirements.txt` | Add: flask, flask-socketio |

## Cost

- LLM calls for 4 express stories: ~$0.05 (gpt-4o-mini)
- No additional API costs (Microsoft Lists skipped during test)
