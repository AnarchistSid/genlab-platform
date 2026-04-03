# Video Review App — Design Document

**Date:** 2026-02-20
**Status:** Approved
**Goal:** Upgrade review_server.py into a mobile-friendly video review dashboard with video playback, batch operations, and optional auto-approve timer.

---

## Context

The existing `execution/review_server.py` is a text-only express lane dashboard. It shows blueprint cards with title, hook, and status — but has zero visual capability. The video-first pipeline now produces MP4 reels and PNG carousel slides stored in `visual_paths`, but the web review UI never displays them. This upgrade bridges that gap.

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Architecture | Upgrade existing review_server.py | Already has Microsoft Lists integration, WebSocket, express trigger |
| Frontend | Embedded SPA (Python string constants) | Zero build tooling, single file, proven pattern |
| Primary content | Video reels (MP4) | Video-first pipeline — reels are the main output |
| Carousel display | Thumbnail strip | PNGs shown as horizontal row, tap to expand |
| Auto-approve | Off by default, toggle per-session | Require explicit action; timer is opt-in |
| Batch mode | Checkbox select + action | Most flexible: select specific items, then approve/reject |

## Architecture

### New API Routes

| Route | Method | Purpose |
|-------|--------|---------|
| `/api/media/<path>` | GET | Serve local PNG/MP4 files with path validation |
| `/api/batch-review` | POST | Batch approve/reject selected blueprints |
| `/api/settings` | GET/POST | Auto-approve toggle + timer configuration |

### Existing Routes (Unchanged)

- `GET /` — Dashboard HTML
- `GET /api/blueprints` — DRAFTED + VISUAL_READY from Microsoft Lists or local
- `POST /api/review/<id>` — Single approve/reject/skip
- `GET /api/express/status` — Express pipeline status
- `GET /api/express/trigger` — Trigger express run
- `GET /api/local/blueprints` — Always-local blueprints

### Media Serving

The `visual_paths` field contains absolute file paths (e.g., `/Users/.../visual_output/abc123/slide_1.png`). The `/api/media/` route:

- Validates paths are under PROJECT_ROOT or `.tmp/` (prevents directory traversal)
- Detects MIME type (`.mp4` -> `video/mp4`, `.png` -> `image/png`)
- Supports HTTP Range requests for video seeking
- Returns 404 for missing files, 403 for paths outside allowed directories

## Frontend Components (Mobile-First)

### Review Card

Each blueprint renders as a vertical card filling most of the mobile viewport:

1. **Video player** — `<video>` tag for MP4 reels. Controls: play/pause, mute, scrubber. Auto-plays muted when card is visible (IntersectionObserver). Falls back to poster image if no MP4.
2. **Carousel thumbnail strip** — Horizontal scrolling row of PNG slides below the video. Tap any thumbnail for full-screen slide viewer.
3. **Metadata bar** — Story title, status badge (DRAFTED/VISUAL_READY), urgency tag, template name.
4. **Action buttons** — Approve (green), Reject (red), Skip (gray). Touch gesture support: swipe right = approve, swipe left = reject.
5. **Batch checkbox** — Top-right corner, visible when batch mode is active.

### Top Bar

- Blueprint count + progress (e.g., "3/12 reviewed")
- Batch mode toggle button
- Auto-approve toggle (off by default) with configurable timer
- Filter: DRAFTED / VISUAL_READY / All

### Batch Panel

Slides up from bottom when batch mode is active:

- "Select All" / "Deselect All" buttons
- "Approve Selected (N)" / "Reject Selected (N)" action buttons
- Selected item count display

### Responsive Layout

- Flexbox-based, mobile-first
- Cards max-width 600px centered on desktop
- Cards fill viewport width on mobile
- Dark terminal aesthetic inherited from existing UI (#0d1117 bg, JetBrains Mono, green/amber/red accents)

## Data Flow

```
Blueprint (Microsoft Lists/local)
  -> GET /api/blueprints
  -> Frontend renders card
  -> Parse visual_paths JSON
  -> GET /api/media/<path> for each PNG/MP4
  -> <video> for reels, <img> for carousel thumbnails

User action:
  Single: tap Approve/Reject -> POST /api/review/<id> -> Microsoft Lists update -> WebSocket notify
  Batch:  checkbox select -> POST /api/batch-review -> Loop update each -> WebSocket per item

Auto-approve:
  Client-side countdown timer (per-session, not persisted)
  -> Timer expires without user action
  -> Client sends POST /api/review/<id> action=approved
  -> Timer resets on any user interaction with the card
```

## Testing Plan

1. **Local mode** — `--local` flag, verify test_express blueprints display with media
2. **Media serving** — Create test PNG/MP4, verify `/api/media/` serves with correct MIME types and Range support
3. **Batch review** — Select 3 blueprints, batch approve in `--dry-run`, verify WebSocket updates
4. **Mobile browser** — Open via local network (`http://<mac-ip>:5151`), verify responsive layout, video playback, swipe gestures
5. **Auto-approve** — Enable 10s timer, verify countdown display + auto-submission

## Files Modified

- `execution/review_server.py` — All changes (backend routes + embedded frontend HTML/CSS/JS)

## Files NOT Created

- No new directories (no `web_review_app/`)
- No npm/node_modules/build tooling
- No separate template files
