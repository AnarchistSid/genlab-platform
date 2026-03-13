# GenLab — Project Overview

GenLab is a multi-niche automated content pipeline that fetches trending content from various sources, scores/deduplicates it, writes platform-native copy, renders video reels, and publishes to Instagram, YouTube, X/Twitter, Facebook, and Threads.

## Niche Channels

| Niche | Brand | niche_id | Folder | Status |
|-------|-------|----------|--------|--------|
| AI/Tech | Blackbox Brief | `ai_news` | `Content Scraper/` | Production |
| Gaming | CriticalRush | `gaming` | `CriticalRush/` | Production |
| Sports | ClutchWire | `sports` | `ClutchWire/` | Config stub |
| Movies | SpliceReel | `movies` | `SpliceReel/` | MVP |
| Anime | FrameDrift | `anime` | `FrameDrift/` | MVP |

## Monorepo Structure

- `genlab-core/` — Shared library (src-layout: `src/genlab_core/`). Cache, HTTP, media, learning, TTS, engagement, render modules. ~1000 tests.
- `Content Scraper/` — Blackbox Brief pipeline + execution scripts. ~1340 tests.
- `CriticalRush/` — Gaming pipeline runner + self-learning system. ~490 tests.
- `ClutchWire/` — Sports channel strategies + configs. ~94 tests.
- `SpliceReel/` — Movies channel strategies + configs. ~101 tests.
- `FrameDrift/` — Anime channel strategies + configs. ~105 tests.
- `dashboard/` — Shared React+Flask operations dashboard. ~120 tests.
- `scripts/` — Shared intelligence scripts (analytics, briefing, trends).

## Tech Stack

- **Python 3.14** — all backend
- **uv** workspace — single lockfile at `/GenLab/uv.lock`, binary at `~/.local/bin/uv`
- **React 19 + TypeScript + Vite** — dashboard frontend
- **Flask + Flask-SocketIO** — dashboard server
- **FFmpeg** — video rendering (sandwich layout, 9:16 reels)
- **OpenSandbox** — containerized FFmpeg execution (optional)
- **SharePoint / Microsoft Lists** — backlog storage (via MS Graph API)
- **Meta Graph API, YouTube Data API v3, X API v2** — publishing
- **Prefect** — workflow orchestration (metric collection flows)
- **launchd** — macOS daemon scheduling (plists in ~/Library/LaunchAgents)
