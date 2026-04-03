# Review Server Redesign — Design Document

**Date:** 2026-02-26
**Status:** Approved
**Goal:** Make the review dashboard publicly accessible, always-on, and visually polished.

---

## Problem Statement

The current review server (`execution/review_server.py`) is a localhost-only Flask app that must be manually started. It works but has three key issues:

1. **Not accessible remotely** — only works at `localhost:5151`
2. **Not always running** — requires manual start, crashes silently
3. **Rough UI** — functional but not polished; hard to navigate with many posts

---

## Solution Overview

| Component | Current | Target |
|-----------|---------|--------|
| Hosting | localhost:5151 | `review.aspirehub.ai` via Cloudflare named tunnel |
| Process | Manual Flask dev server | launchd daemon + Gunicorn, auto-restart on crash |
| Auth | Optional HTTP Basic Auth | Cloudflare Access (Google SSO, free tier) |
| UI | Embedded dark terminal SPA | Full redesign: card grid, split view, video player |
| Frontend code | ~1,200 lines embedded in Python | Extracted to `templates/review/` (HTML + CSS + JS) |
| Stability | No crash recovery | KeepAlive daemon, graceful degradation |

---

## 1. Infrastructure

### Cloudflare Named Tunnel

- Create tunnel `genlab-review` (separate from existing `trading-bot`)
- Route: `review.aspirehub.ai` -> `http://localhost:5151`
- Requires aspirehub.ai nameservers pointing to Cloudflare
- Cloudflare handles HTTPS termination, DDoS protection

**Config (`~/.cloudflared/config.yml`):**
```yaml
tunnel: trading-bot
credentials-file: ~/.cloudflared/8f86aa87-...json

ingress:
  - hostname: dash.astuteos.com       # Existing
    service: http://localhost:8501
  - hostname: review.aspirehub.ai     # NEW
    service: http://localhost:5151
  - service: http_status:404
```

Or a separate config for a new tunnel if preferred.

### launchd Daemon

- Plist: `runbooks/com.genlab.review-server.plist`
- `KeepAlive: true` — auto-restart on crash
- `ThrottleInterval: 10` — prevent restart storms
- Logs: `.tmp/logs/review_server_{stdout,stderr}.log`
- Starts on boot, runs indefinitely

### Authentication

- Cloudflare Access (Zero Trust, free tier: 50 users)
- Allows Google/GitHub SSO before traffic reaches the server
- No server-side auth code changes needed
- Access policy: allow specific email addresses

---

## 2. Server Stability

### Gunicorn (production WSGI)

Replace Flask dev server with Gunicorn:
- 2 worker processes (review server is lightweight)
- `--timeout 120` for large media requests
- `--access-logfile` for request logging
- eventlet or gevent worker class for SocketIO support

### Graceful Degradation

- If Microsoft Lists is unreachable: show cached blueprints with "stale data" banner
- Existing 8-second cache TTL prevents hammering Microsoft Lists
- SocketIO auto-reconnects on connection drop (already implemented)

### Health Monitoring

- `/api/health` already returns uptime, blueprint counts, cache status
- Cloudflare can be configured to monitor this endpoint
- Log rotation: keep last 7 days of logs

### Media Serving

- Proper `Content-Type` headers (video/mp4, image/png)
- HTTP Range request support for video seeking
- Cache headers for static assets (CSS/JS/fonts)
- Existing path traversal protection maintained

---

## 3. UI/UX Redesign

### Architecture

Extract frontend code from `review_server.py` into separate files:

```
templates/review/
  index.html          # Main dashboard page
  css/
    dashboard.css     # Styles
  js/
    dashboard.js      # Application logic
    components.js     # Reusable UI components
```

Flask serves these via `render_template()` and static file routes.

### Layout: Card Grid + Detail Panel

**Dashboard (main view):**
- Top status bar: pending count, published today, schedule coverage bar
- Filter pills: All | Pending | Approved | Rejected
- Responsive card grid (3 columns desktop, 2 tablet, 1 mobile)
- Each card shows: thumbnail/video preview, hook text, schedule time, status badge

**Detail panel (click to expand):**
- Large video player with controls (auto-loop, mute toggle)
- Full caption text
- Platform preview tabs (Instagram / YouTube / Twitter)
- Per-platform publish status badges
- Review action buttons (Approve / Reject / Revise)
- Feedback form (issue category + notes) on reject/revise

### Video/Media

- **Card thumbnails:** First frame poster image, auto-play on hover (muted)
- **Detail view:** Full video player with seek, loop, volume
- **Carousel viewer:** Left/right arrows through multi-slide carousels
- **Lazy loading:** Only load video data when card scrolls into viewport

### Review Actions

- **Quick actions on cards:** Approve/reject without opening detail view
- **Keyboard shortcuts:** `a` approve, `r` reject, `v` revise, `s` skip, `j/k` or arrow navigate
- **Undo (5s window):** After approve/reject, 5-second undo toast before committing
- **Batch mode:** Checkbox select multiple cards, batch approve/reject all

### Information Display

- **Schedule timeline:** Horizontal bar showing today's 4 schedule slots with post assignments
- **Platform status:** Per-post badges showing IG/YT/TW/FB publish state
- **Post metadata:** Story source, template type, urgency level, file size
- **Auto-approve timer:** Visual countdown bar with cancel button

### Design Language

- Dark theme (consistent with brand)
- Inter font family (already available in repo)
- Accent colors from `config/publishing.yaml` (indigo #6366f1, purple #8b5cf6, cyan #06b6d4)
- Smooth transitions, subtle hover effects
- Mobile-first responsive design

---

## 4. Security

All existing security measures maintained:
- CSRF token protection on POST routes
- Path traversal prevention on media routes
- CORS locked to the tunnel domain
- Cloudflare Access as the primary auth layer
- No credentials exposed in URLs or logs

---

## 5. Non-Goals (Explicitly Out of Scope)

- No React/Vue/Next.js framework — vanilla JS keeps it simple and dependency-free
- No separate database — Microsoft Lists remains the source of truth
- No user accounts within the app — Cloudflare Access handles identity
- No analytics dashboard — this is purely for content review
- No real-time collaboration — one reviewer at a time is fine

---

## 6. Verification Criteria

- [ ] Server starts automatically on boot via launchd
- [ ] Server recovers from crashes (KeepAlive)
- [ ] `review.aspirehub.ai` resolves and loads the dashboard
- [ ] Cloudflare Access prompts for Google login
- [ ] Dashboard shows VISUAL_READY posts from Microsoft Lists
- [ ] Video playback works (auto-loop, seek, controls)
- [ ] Approve/reject/revise actions update Microsoft Lists
- [ ] Keyboard shortcuts work (a/r/v/s/j/k)
- [ ] Undo window prevents accidental actions
- [ ] Batch mode works for multiple selections
- [ ] Mobile layout is usable (responsive grid, touch actions)
- [ ] Graceful degradation when Microsoft Lists is unreachable
- [ ] All existing tests pass
