# CLAUDE.md — Gen Lab Operations Dashboard

Shared operations dashboard serving all Gen Lab niches (Blackbox Brief, CriticalRush).

## Architecture

```
dashboard/
├── frontend/               # React 19 + TypeScript + Vite
│   ├── src/
│   │   ├── views/          # Mission Control, Analytics, Focus Review, Publishing Queue, etc.
│   │   ├── api/            # API client + React Query hooks
│   │   ├── hooks/          # Shared React hooks
│   │   └── niches/         # Niche registry for multi-niche support
│   ├── package.json
│   └── vite.config.ts
├── server/                 # Flask + Flask-SocketIO
│   ├── review_server.py    # Main app, auth, WebSocket, static serving
│   ├── api/                # REST API v1 route modules
│   │   ├── analytics.py    # /api/v1/analytics/* — engagement data + data_status
│   │   ├── blueprints.py   # /api/v1/blueprints/* — CRUD + review actions
│   │   ├── pipeline.py     # /api/v1/pipeline/* — run history + express lane
│   │   ├── publishing_queue.py  # /api/v1/queue/* — approve/hold/release
│   │   ├── token_health.py # /api/v1/token-health — platform token checks
│   │   ├── overview.py     # /api/v1/overview — mission control data
│   │   └── (schedule, stories, niches, config_routes, platform_posts)
│   └── core/
│       └── publishing_queue.py  # Queue status derivation + gate invariant
├── runbooks/
│   └── review_server_wrapper.sh  # Gunicorn launcher for launchd
├── tests/                  # Dashboard-specific tests
├── pyproject.toml          # uv workspace member (depends on genlab-core)
└── CLAUDE.md               # This file
```

## Key Design Decisions

- **BlackboxBrief on sys.path**: `review_server.py` adds BlackboxBrief root to `sys.path` via `GENLAB_PROJECT_ROOT` env var. This allows `execution.*` imports for token health and other checks that are tightly coupled to BlackboxBrief platform clients.
- **BACKLOG_CONFIG_PATH**: Set by the wrapper script to point to BlackboxBrief's `config/lists_config.yaml`.
- **Static files**: Frontend dist served from `frontend/dist/`. Built by wrapper script if stale.
- **Media access**: `.tmp/` media files are served from BlackboxBrief's directory via PROJECT_ROOT.

## Running Locally

```bash
# Build frontend
cd dashboard/frontend && npm run build

# Start server (uses wrapper script)
./dashboard/runbooks/review_server_wrapper.sh

# Or via launchd (production)
launchctl load ~/Library/LaunchAgents/com.genlab.review-server.plist
```

## API Conventions

- All endpoints under `/api/v1/`
- Basic auth: admin:<password from .env>
- Analytics includes `data_status` per platform: `"available"`, `"no_metrics"`, `"no_data"`
- `is_estimated` flag when data falls back to hardcoded reach estimates

## Launchd

- Plist: `~/Library/LaunchAgents/com.genlab.review-server.plist`
- WorkingDirectory: `GenLab/dashboard/`
- KeepAlive + RunAtLoad
- Logs: `BlackboxBrief/.tmp/logs/review_server_*.log`
