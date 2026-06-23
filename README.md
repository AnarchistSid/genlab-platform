# GenLab

[![CI](https://github.com/AnarchistSid/genlab-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/AnarchistSid/genlab-platform/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

Video-first viral content automation platform for short-form video reels.

## Features

- **Video-first pipeline** — Finds trending clips on YouTube, writes platform-native captions via LLM, renders branded reels with FFmpeg
- **5 platform publishing** — Instagram Reels, YouTube Shorts, Facebook Reels, Threads, X/Twitter
  (TikTok ships a stub gated behind `TIKTOK_AUDIT_APPROVED=true`; not counted as live until the
  audit lands. R-06: was previously advertised as "6 platforms" — corrected 2026-06-11.)
- **Learning loop** — LinUCB contextual bandit optimizes content selection based on engagement feedback
- **Engagement engine** — AI-powered comment replies with toxicity filtering, rate limiting, and bot disclosure
- **Affiliate monetization** — Multi-network product matching, CTA injection, A/B testing, revenue attribution
- **Operations dashboard** — React + Flask dashboard for content approval, scheduling, analytics, and monitoring
- **Multi-channel** — Config-driven architecture supports unlimited niches with ~200 lines of niche-specific code each

## Architecture

```
genlab-core/         Shared infrastructure (pipeline, platform clients, learning loop)
BlackboxBrief/       AI/Tech news channel
CriticalRush/        Gaming channel
ClutchWire/          Sports channel
SpliceReel/          Movies channel
FrameDrift/          Anime channel
dashboard/           Operations dashboard (React + Flask)
deploy/              Cloud deployment configs (systemd, Docker, Caddy)
scripts/             Shared automation scripts
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for detailed architecture documentation.

### Pipeline stages

```
1. Fetch trending videos    ← YouTube API: find what's viral right now
2. Score and filter          ← View velocity + Google Trends multiplier
3. Compose blueprints       ← Build blueprint for top-N clips
4. Write content             ← LLM: write hook + captions around the video
5. Render visuals            ← FFmpeg: render video with logo overlay
6. Human review              ← Approve via dashboard
7. Publish                   ← All platforms in parallel
8. Collect metrics           ← Engagement after 6h/24h/48h/168h
9. Learn                     ← Update bandit arms for next run
```

## Quick start

```bash
# Prerequisites: Python 3.12+, PostgreSQL, FFmpeg, uv

# 1. Clone and install
git clone https://github.com/AnarchistSid/genlab-platform.git
cd genlab-platform
uv sync

# 2. Set up environment
cp .env.example .env
# Fill in API keys: ANTHROPIC_API_KEY, YOUTUBE_API_KEY, META_ACCESS_TOKEN, etc.

# 3. Set up database
createdb genlab
uv run alembic -c genlab-core/alembic.ini upgrade head

# 4. Run a pipeline
uv run --package genlab-core python -m genlab_core.pipeline --niche <niche_id>

# 5. Run tests
uv run --package genlab-core pytest genlab-core/tests/ -x

# 6. (Contributors) arm the local pre-commit hooks
pre-commit install
pre-commit run -a       # one-time check against the whole repo
```

## Operator + contributor playbook

After cloning, read **[`docs/OPERATOR-PLAYBOOK.md`](docs/OPERATOR-PLAYBOOK.md)** — single source of truth for:

- Post-deploy activation (which YAMLs to flip per niche, when to flip env flags)
- Daily dashboard workflow (Copy/Kit/Portfolio buttons for sponsor outreach)
- Architecture primitives (shared helpers + which PRs compose on them)
- Test patterns for lazy-imported dependencies (avoids common mock-target gotchas)
- Workflow patterns (pre-format-then-push, refactor-on-2nd-implementation, etc.)

The playbook is the durable handoff document — survives across clones and sessions.

## Configuration

Each channel has its own `config/` directory with YAML files for sources, scoring, visuals, scheduling, and publishing. Shared configs live in `genlab-core/config/`.

All credentials go in `.env` files (never committed). See `.env.example` for the full list.

## Tech stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.12+ |
| Package manager | [uv](https://github.com/astral-sh/uv) (workspace) |
| Database | PostgreSQL 16 (psycopg3) |
| LLM | Anthropic Claude Haiku |
| Video | FFmpeg, yt-dlp |
| Dashboard | React + Vite (frontend), Flask + Gunicorn (backend) |
| Queue | Dramatiq + Redis |
| Scheduling | systemd timers (cloud) |
| Deployment | Hetzner Cloud, Docker, Caddy, Cloudflare |
| CI | GitHub Actions (lint, test, secret scan) |

## Disclaimer

This software is provided for educational and research purposes. Users are solely responsible for ensuring their use complies with all applicable laws, platform terms of service, and content policies. The authors are not responsible for any misuse of this software.

By using this software, you agree to:
- Comply with YouTube, Instagram, Facebook, X/Twitter, Threads, and TikTok terms of service
- Obtain proper rights or licenses for any content you publish
- Include appropriate disclosures for AI-generated content and affiliate links
- Respect copyright and intellectual property rights

## License

[MIT](LICENSE)
