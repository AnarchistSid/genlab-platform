# GenLab End-to-End Credential Flow Trace

Generated: 2026-03-17
Covers every .env file, token, refresh path, and launchd plist.

---

## 1. Env File Inventory

### 1.1 File Locations and Key Names

#### `/Users/anarchistsid/GenLab/.env` (Root shared env)

| Env Var | Category |
|---------|----------|
| `ANTHROPIC_API_KEY` | LLM |
| `OPENAI_API_KEY` | LLM |
| `AZURE_TENANT_ID` | SharePoint |
| `AZURE_CLIENT_ID` | SharePoint |
| `AZURE_CLIENT_SECRET` | SharePoint |
| `SHAREPOINT_SITE_ID` | SharePoint |
| `YOUTUBE_API_KEY` | YouTube |
| `YOUTUBE_CLIENT_ID` | YouTube |
| `YOUTUBE_CLIENT_SECRET` | YouTube |
| `TMDB_API_KEY` | Stock media |
| `FB_APP_ID` | Meta |
| `FB_APP_SECRET` | Meta |
| `META_APP_SECRET` | Meta |
| `META_WEBHOOK_VERIFY_TOKEN` | Meta |
| `PEXELS_API_KEY` | Stock media |
| `PIXABAY_API_KEY` | Stock media |
| `UNSPLASH_ACCESS_KEY` | Stock media |
| `CLUTCHWIRE_META_ACCESS_TOKEN` | Per-niche (sports) |
| `CLUTCHWIRE_FB_PAGE_ACCESS_TOKEN` | Per-niche (sports) |
| `CLUTCHWIRE_FB_PAGE_ID` | Per-niche (sports) |
| `CLUTCHWIRE_IG_USER_ID` | Per-niche (sports) |
| `CLUTCHWIRE_YT_CHANNEL_ID` | Per-niche (sports) |
| `CLUTCHWIRE_YOUTUBE_REFRESH_TOKEN` | Per-niche (sports) |
| `CLUTCHWIRE_X_API_KEY` | Per-niche (sports) -- EMPTY |
| `CLUTCHWIRE_X_API_SECRET` | Per-niche (sports) -- EMPTY |
| `CLUTCHWIRE_X_ACCESS_TOKEN` | Per-niche (sports) -- EMPTY |
| `CLUTCHWIRE_X_ACCESS_SECRET` | Per-niche (sports) -- EMPTY |
| `CLUTCHWIRE_THREADS_ACCESS_TOKEN` | Per-niche (sports) -- EMPTY |
| `CLUTCHWIRE_THREADS_USER_ID` | Per-niche (sports) -- EMPTY |
| `SPLICEREEL_META_ACCESS_TOKEN` | Per-niche (movies) |
| `SPLICEREEL_FB_PAGE_ACCESS_TOKEN` | Per-niche (movies) |
| `SPLICEREEL_FB_PAGE_ID` | Per-niche (movies) |
| `SPLICEREEL_IG_USER_ID` | Per-niche (movies) |
| `SPLICEREEL_YT_CHANNEL_ID` | Per-niche (movies) |
| `SPLICEREEL_YOUTUBE_REFRESH_TOKEN` | Per-niche (movies) |
| `SPLICEREEL_X_API_KEY` | Per-niche (movies) -- EMPTY |
| `SPLICEREEL_X_API_SECRET` | Per-niche (movies) -- EMPTY |
| `SPLICEREEL_X_ACCESS_TOKEN` | Per-niche (movies) -- EMPTY |
| `SPLICEREEL_X_ACCESS_SECRET` | Per-niche (movies) -- EMPTY |
| `SPLICEREEL_THREADS_ACCESS_TOKEN` | Per-niche (movies) -- EMPTY |
| `SPLICEREEL_THREADS_USER_ID` | Per-niche (movies) -- EMPTY |
| `FRAMEDRIFT_META_ACCESS_TOKEN` | Per-niche (anime) |
| `FRAMEDRIFT_FB_PAGE_ACCESS_TOKEN` | Per-niche (anime) |
| `FRAMEDRIFT_IG_USER_ID` | Per-niche (anime) |
| `FRAMEDRIFT_FB_PAGE_ID` | Per-niche (anime) |
| `FRAMEDRIFT_YT_CHANNEL_ID` | Per-niche (anime) |
| `FRAMEDRIFT_YOUTUBE_REFRESH_TOKEN` | Per-niche (anime) |
| `FRAMEDRIFT_X_API_KEY` | Per-niche (anime) -- EMPTY |
| `FRAMEDRIFT_X_API_SECRET` | Per-niche (anime) -- EMPTY |
| `FRAMEDRIFT_X_ACCESS_TOKEN` | Per-niche (anime) -- EMPTY |
| `FRAMEDRIFT_X_ACCESS_SECRET` | Per-niche (anime) -- EMPTY |
| `FRAMEDRIFT_THREADS_ACCESS_TOKEN` | Per-niche (anime) -- EMPTY |
| `FRAMEDRIFT_THREADS_USER_ID` | Per-niche (anime) -- EMPTY |
| `CRITICALRUSH_META_ACCESS_TOKEN` | Per-niche (gaming) |
| `CRITICALRUSH_FB_PAGE_ACCESS_TOKEN` | Per-niche (gaming) |
| `CRITICALRUSH_FB_PAGE_ID` | Per-niche (gaming) |
| `CRITICALRUSH_IG_USER_ID` | Per-niche (gaming) |
| `CRITICALRUSH_YT_CHANNEL_ID` | Per-niche (gaming) |
| `CRITICALRUSH_YOUTUBE_REFRESH_TOKEN` | Per-niche (gaming) |
| `CRITICALRUSH_X_API_KEY` | Per-niche (gaming) -- EMPTY |
| `CRITICALRUSH_X_API_SECRET` | Per-niche (gaming) -- EMPTY |
| `CRITICALRUSH_X_ACCESS_TOKEN` | Per-niche (gaming) -- EMPTY |
| `CRITICALRUSH_X_ACCESS_SECRET` | Per-niche (gaming) -- EMPTY |
| `CRITICALRUSH_THREADS_ACCESS_TOKEN` | Per-niche (gaming) -- EMPTY |
| `CRITICALRUSH_THREADS_USER_ID` | Per-niche (gaming) -- EMPTY |
| `BLACKBOXBRIEF_META_ACCESS_TOKEN` | Per-niche (ai_creators) |
| `BLACKBOXBRIEF_IG_USER_ID` | Per-niche (ai_creators) |
| `BLACKBOXBRIEF_FB_PAGE_ID` | Per-niche (ai_creators) |
| `BLACKBOXBRIEF_FB_PAGE_ACCESS_TOKEN` | Per-niche (ai_creators) |
| `BLACKBOXBRIEF_YOUTUBE_REFRESH_TOKEN` | Per-niche (ai_creators) |
| `BLACKBOXBRIEF_YT_CHANNEL_ID` | Per-niche (ai_creators) |
| `ENGAGEMENT_DISPATCH` | Runtime |
| `TWITCH_CLIENT_ID` | Twitch/IGDB |
| `TWITCH_CLIENT_SECRET` | Twitch/IGDB |
| `POSTGRES_PASSWORD` | PostgreSQL |

#### `/Users/anarchistsid/GenLab/Content Scraper/.env` (BB channel env)

| Env Var | Category |
|---------|----------|
| `UNSPLASH_ACCESS_KEY` | Stock media |
| `ANTHROPIC_API_KEY` | LLM |
| `OPENAI_API_KEY` | LLM |
| `META_ACCESS_TOKEN` | Meta/IG (BB global) |
| `META_IG_USER_ID` | Meta/IG |
| `META_IG_APP_ID` | Meta/IG |
| `META_IG_APP_SECRET` | Meta/IG |
| `X_API_KEY` | Twitter (BB) |
| `X_API_SECRET` | Twitter (BB) |
| `X_ACCESS_TOKEN` | Twitter (BB) |
| `X_ACCESS_SECRET` | Twitter (BB) |
| `X_BEARER_TOKEN` | Twitter (BB) |
| `TWITTER_USER_ID_AI_NEWS` | Twitter (BB) |
| `YOUTUBE_CLIENT_ID` | YouTube |
| `YOUTUBE_CLIENT_SECRET` | YouTube |
| `YOUTUBE_REFRESH_TOKEN` | YouTube (BB global) |
| `YOUTUBE_API_KEY` | YouTube |
| `PEXELS_API_KEY` | Stock media |
| `PIXABAY_API_KEY` | Stock media |
| `META_FB_PAGE_ID` | Facebook (BB) |
| `FB_PAGE_ACCESS_TOKEN` | Facebook (BB -- "The People's Democracy" page, STALE) |
| `FB_APP_ID` | Meta |
| `FB_APP_SECRET` | Meta |
| `REVIEW_AUTH_USER` | Dashboard auth |
| `REVIEW_AUTH_PASS` | Dashboard auth |
| `AZURE_TENANT_ID` | SharePoint |
| `AZURE_CLIENT_ID` | SharePoint |
| `AZURE_CLIENT_SECRET` | SharePoint |
| `SHAREPOINT_SITE_ID` | SharePoint |
| `FLASK_SECRET_KEY` | Dashboard |
| `THREADS_ACCESS_TOKEN` | Threads (BB) |
| `THREADS_TOKEN_ISSUED_AT` | Threads (BB) |
| `THREADS_APP_ID` | Threads |
| `THREADS_APP_SECRET` | Threads |
| `THREADS_USER_ID` | Threads (BB) |
| `META_WEBHOOK_VERIFY_TOKEN` | Meta |
| `META_APP_SECRET` | Meta |
| `YT_CHANNEL_ID` | YouTube (BB) |
| `ENGAGEMENT_DISPATCH` | Runtime |
| `TMDB_API_KEY` | Stock media |

#### `/Users/anarchistsid/GenLab/CriticalRush/.env` (Gaming channel env)

| Env Var | Category |
|---------|----------|
| `AGENT_ROOT` | Runtime config |
| `ANTHROPIC_API_KEY` | LLM |
| `AZURE_TENANT_ID` | SharePoint |
| `AZURE_CLIENT_ID` | SharePoint |
| `AZURE_CLIENT_SECRET` | SharePoint |
| `SHAREPOINT_SITE_ID` | SharePoint |
| `TWITCH_CLIENT_ID` | Twitch/IGDB |
| `TWITCH_CLIENT_SECRET` | Twitch/IGDB |
| `META_ACCESS_TOKEN` | Meta (CR-specific page token) |
| `META_IG_USER_ID` | Meta/IG (CR) |
| `META_IG_APP_ID` | Meta |
| `META_IG_APP_SECRET` | Meta |
| `FB_PAGE_ACCESS_TOKEN` | Facebook (CR, STALE -- different from root) |
| `META_FB_PAGE_ID` | Facebook (CR) |
| `YOUTUBE_CLIENT_ID` | YouTube |
| `YOUTUBE_CLIENT_SECRET` | YouTube |
| `YOUTUBE_REFRESH_TOKEN` | YouTube (CR-specific) |
| `X_API_KEY` | Twitter (CR app) |
| `X_API_SECRET` | Twitter (CR app) |
| `X_BEARER_TOKEN` | Twitter (CR app-only bearer) |
| `X_ACCESS_TOKEN` | Twitter -- EMPTY |
| `X_ACCESS_SECRET` | Twitter -- EMPTY |
| `TWITTER_USER_ID_GAMING` | Twitter (CR) |
| `STEAM_API_KEY` | Steam API |
| `PEXELS_API_KEY` | Stock media |
| `THREADS_ACCESS_TOKEN` | Threads -- EMPTY |
| `THREADS_USER_ID` | Threads -- EMPTY |
| `THREADS_TOKEN_ISSUED_AT` | Threads -- EMPTY |
| `TIKTOK_CLIENT_KEY` | TikTok |
| `TIKTOK_CLIENT_SECRET` | TikTok |
| `TIKTOK_ACCESS_TOKEN` | TikTok -- EMPTY |
| `TIKTOK_REFRESH_TOKEN` | TikTok -- EMPTY |
| `TIKTOK_TOKEN_ISSUED_AT` | TikTok -- EMPTY |
| `TIKTOK_AUDIT_APPROVED` | TikTok (false) |
| `CLOUDFLARE_TUNNEL_URL` | CDN -- EMPTY |
| `SHORT_VIDEO_MAKER_URL` | Render service |
| `PREFECT_API_URL` | Prefect |
| `PREFECT_WORK_POOL` | Prefect |
| `CRITICALRUSH_FB_PAGE_ACCESS_TOKEN` | Per-niche FB (STALE -- differs from root .env) |

#### `/Users/anarchistsid/GenLab/ClutchWire/.env` (Sports channel env -- minimal)

| Env Var | Category |
|---------|----------|
| `AZURE_TENANT_ID` | SharePoint |
| `AZURE_CLIENT_ID` | SharePoint |
| `AZURE_CLIENT_SECRET` | SharePoint |
| `SHAREPOINT_SITE_ID` | SharePoint |
| `CLUTCHWIRE_FB_PAGE_ACCESS_TOKEN` | Facebook (CW -- DIFFERENT VALUE from root .env) |
| `CLUTCHWIRE_FB_PAGE_ID` | Facebook (CW) |

#### `/Users/anarchistsid/GenLab/SpliceReel/.env` (Movies channel env -- minimal)

| Env Var | Category |
|---------|----------|
| `AZURE_TENANT_ID` | SharePoint |
| `AZURE_CLIENT_ID` | SharePoint |
| `AZURE_CLIENT_SECRET` | SharePoint |
| `SHAREPOINT_SITE_ID` | SharePoint |
| `SPLICEREEL_FB_PAGE_ACCESS_TOKEN` | Facebook (SR -- DIFFERENT VALUE from root .env) |
| `SPLICEREEL_FB_PAGE_ID` | Facebook (SR) |

#### `/Users/anarchistsid/GenLab/FrameDrift/.env` (Anime channel env -- minimal)

| Env Var | Category |
|---------|----------|
| `AZURE_TENANT_ID` | SharePoint |
| `AZURE_CLIENT_ID` | SharePoint |
| `AZURE_CLIENT_SECRET` | SharePoint |
| `SHAREPOINT_SITE_ID` | SharePoint |
| `FRAMEDRIFT_FB_PAGE_ACCESS_TOKEN` | Facebook (FD -- DIFFERENT VALUE from root .env) |
| `FRAMEDRIFT_FB_PAGE_ID` | Facebook (FD) |

### 1.2 Key Duplication Matrix

The following keys appear in multiple .env files. The "Winner" column depends on load order.

| Env Var | Files | Values Match? | Notes |
|---------|-------|---------------|-------|
| `ANTHROPIC_API_KEY` | Root, CS, CR | YES | Same key across all |
| `OPENAI_API_KEY` | Root, CS | YES | Same key |
| `AZURE_TENANT_ID` | Root, CS, CR, CW, SR, FD | YES | Same tenant |
| `AZURE_CLIENT_ID` | Root, CS, CR, CW, SR, FD | YES | Same app reg |
| `AZURE_CLIENT_SECRET` | Root, CS, CR, CW, SR, FD | YES | Same secret |
| `SHAREPOINT_SITE_ID` | Root, CS, CR, CW, SR, FD | YES | Same site |
| `YOUTUBE_API_KEY` | Root, CS | YES | Same key |
| `YOUTUBE_CLIENT_ID` | Root, CS, CR | YES | Same OAuth app |
| `YOUTUBE_CLIENT_SECRET` | Root, CS, CR | YES | Same OAuth app |
| `YOUTUBE_REFRESH_TOKEN` | CS, CR | **NO** | CS=BB token, CR=gaming token |
| `META_ACCESS_TOKEN` | CS, CR | **NO** | CS=BB page, CR=CriticalRush page |
| `META_IG_USER_ID` | CS, CR | **NO** | CS=BB IG, CR=CriticalRush IG |
| `META_IG_APP_ID` | CS, CR | YES | Same app |
| `META_IG_APP_SECRET` | CS, CR | YES | Same app |
| `FB_PAGE_ACCESS_TOKEN` | CS, CR | **NO** | CS=BB page, CR=CR page |
| `META_FB_PAGE_ID` | CS, CR | **NO** | CS=422278584555262, CR=1025113540681145 |
| `FB_APP_ID` | Root, CS | YES | Same app |
| `FB_APP_SECRET` | Root, CS | YES | Same app |
| `META_APP_SECRET` | Root, CS | YES | Same |
| `META_WEBHOOK_VERIFY_TOKEN` | Root, CS | YES | Same |
| `PEXELS_API_KEY` | Root, CS, CR | YES | Same key |
| `PIXABAY_API_KEY` | Root, CS | YES | Same key |
| `UNSPLASH_ACCESS_KEY` | Root, CS | YES | Same key |
| `TWITCH_CLIENT_ID` | Root, CR | YES | Same |
| `TWITCH_CLIENT_SECRET` | Root, CR | YES | Same |
| `TMDB_API_KEY` | Root, CS | YES | Same |
| `ENGAGEMENT_DISPATCH` | Root, CS | YES | Both true |
| `X_API_KEY` | CS, CR | **NO** | CS=BB X app, CR=CriticalRush X app |
| `X_API_SECRET` | CS, CR | **NO** | Different apps |
| `X_BEARER_TOKEN` | CS, CR | **NO** | Different apps |
| `CLUTCHWIRE_FB_PAGE_ACCESS_TOKEN` | Root, CW | **NO** | DIFFERENT VALUES |
| `SPLICEREEL_FB_PAGE_ACCESS_TOKEN` | Root, SR | **NO** | DIFFERENT VALUES |
| `FRAMEDRIFT_FB_PAGE_ACCESS_TOKEN` | Root, FD | **NO** | DIFFERENT VALUES |
| `CRITICALRUSH_FB_PAGE_ACCESS_TOKEN` | Root, CR | **NO** | Root uses newer EAA token, CR has stale value |

### 1.3 Which File Wins?

The winner depends on the execution path:

| Execution Path | Load Order | Winner for Conflicts |
|----------------|------------|---------------------|
| **BB daily pipeline** (daily_intel.sh) | CS/.env (shell), then Python load_dotenv(CS/.env, override=True) | CS wins |
| **BB publisher** (orchestrator.sh publish) | CS/.env (shell load_dotenv), then Python load_dotenv(override=True) | CS wins |
| **genlab-core publisher** (gaming/sports/etc plist) | settings.py: load_dotenv(root .env, override=False) | Root wins (but only root .env is loaded) |
| **Dashboard** (review_server_wrapper.sh) | CS/.env (shell), then Python imports settings.py | CS wins (shell exports first, settings.py override=False) |
| **Engagement pollers** (launch_wrapper.sh) | CS/.env then CR/.env (shell source), then Python settings.py | CR wins for conflicts (loaded second with set -a source) |
| **Token health** (com.genlab.token-refresh) | scripts/token_health.py: load_dotenv(CS/.env) | CS wins |
| **Fetch insights** (launch_wrapper.sh) | CS/.env then CR/.env (shell source), then Python settings.py | CR wins for conflicts |

---

## 2. Token Types and Lifetimes

### 2.1 Meta / Instagram

| Niche | Env Var | Token Type | Lifetime | Refresh Mechanism |
|-------|---------|------------|----------|-------------------|
| BB (ai_creators) | `META_ACCESS_TOKEN` (CS/.env), `BLACKBOXBRIEF_META_ACCESS_TOKEN` (root) | EAA Page Token | **Permanent** (expires_at=0) | Never expires. No refresh needed. `refresh_meta_token()` is a no-op. |
| Gaming | `CRITICALRUSH_META_ACCESS_TOKEN` (root) | EAA Page Token | **Permanent** | Never expires |
| Sports | `CLUTCHWIRE_META_ACCESS_TOKEN` (root) | EAA Page Token | **Permanent** | Never expires |
| Movies | `SPLICEREEL_META_ACCESS_TOKEN` (root) | EAA Page Token | **Permanent** | Never expires |
| Anime | `FRAMEDRIFT_META_ACCESS_TOKEN` (root) | EAA Page Token | **Permanent** | Never expires |

All Meta API calls use `graph.facebook.com` (never `graph.instagram.com`).
App: Aspire Publisher (App ID: `2203397347132949`).
`ig_refresh_token` grant type MUST NOT be used on EAA tokens.

### 2.2 Facebook

| Niche | Env Var | Token Type | Lifetime | Refresh Mechanism |
|-------|---------|------------|----------|-------------------|
| BB | `BLACKBOXBRIEF_FB_PAGE_ACCESS_TOKEN` (root) / `FB_PAGE_ACCESS_TOKEN` (CS) | EAA Page Token | **Permanent** | Never expires |
| Gaming | `CRITICALRUSH_FB_PAGE_ACCESS_TOKEN` (root) | EAA Page Token | **Permanent** | Never expires |
| Sports | `CLUTCHWIRE_FB_PAGE_ACCESS_TOKEN` (root) | EAA Page Token | **Permanent** | Never expires |
| Movies | `SPLICEREEL_FB_PAGE_ACCESS_TOKEN` (root) | EAA Page Token | **Permanent** | Never expires |
| Anime | `FRAMEDRIFT_FB_PAGE_ACCESS_TOKEN` (root) | EAA Page Token | **Permanent** | Never expires |

**WARNING**: Per-niche FB tokens in per-channel .env files (CW/.env, SR/.env, FD/.env) have DIFFERENT values from root .env. The per-channel .env values appear to be from an earlier provisioning. Root .env tokens are the authoritative ones.

### 2.3 YouTube

| Niche | Env Var | Token Type | Lifetime | Refresh Mechanism |
|-------|---------|------------|----------|-------------------|
| ALL | `YOUTUBE_API_KEY` (root, CS) | Data API v3 Key | **Permanent** | Never expires. 10,000 units/day quota. |
| ALL | `YOUTUBE_CLIENT_ID` + `YOUTUBE_CLIENT_SECRET` (root, CS, CR) | OAuth 2.0 Client | **Permanent** | App registration. Single OAuth app for all niches. |
| BB | `YOUTUBE_REFRESH_TOKEN` (CS) / `BLACKBOXBRIEF_YOUTUBE_REFRESH_TOKEN` (root) | OAuth 2.0 Refresh Token | **Permanent** (unless revoked) | Access tokens auto-refreshed via `google-auth` library. Refresh token exchanged for 1h access token on each use. |
| Gaming | `CRITICALRUSH_YOUTUBE_REFRESH_TOKEN` (root) | OAuth 2.0 Refresh Token | **Permanent** | Same auto-refresh mechanism |
| Sports | `CLUTCHWIRE_YOUTUBE_REFRESH_TOKEN` (root) | OAuth 2.0 Refresh Token | **Permanent** | Same |
| Movies | `SPLICEREEL_YOUTUBE_REFRESH_TOKEN` (root) | OAuth 2.0 Refresh Token | **Permanent** | Same |
| Anime | `FRAMEDRIFT_YOUTUBE_REFRESH_TOKEN` (root) | OAuth 2.0 Refresh Token | **Permanent** | Same |

Each niche has its own refresh token (per-channel OAuth consent), but all share the same OAuth Client ID/Secret (single Google Cloud project).

### 2.4 X / Twitter

| Niche | Env Var | Token Type | Lifetime | Refresh Mechanism |
|-------|---------|------------|----------|-------------------|
| BB | `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET` (CS) | OAuth 1.0a | **Permanent** (unless revoked) | No auto-refresh. Regenerate at developer.twitter.com if revoked. |
| BB | `X_BEARER_TOKEN` (CS) | App-only Bearer | **Permanent** | No refresh needed. 403 on /users/me is expected (paid plan). |
| Gaming | `CRITICALRUSH_X_API_KEY` etc. (root) | - | **EMPTY** | Not yet provisioned |
| Sports | `CLUTCHWIRE_X_API_KEY` etc. (root) | - | **EMPTY** | Not yet provisioned |
| Movies | `SPLICEREEL_X_API_KEY` etc. (root) | - | **EMPTY** | Not yet provisioned |
| Anime | `FRAMEDRIFT_X_API_KEY` etc. (root) | - | **EMPTY** | Not yet provisioned |

CriticalRush/.env has separate `X_API_KEY`/`X_API_SECRET`/`X_BEARER_TOKEN` for a separate CR X app, but access tokens are EMPTY (publishing disabled). These are loaded only when launch_wrapper.sh sources CR/.env.

### 2.5 Threads

| Niche | Env Var | Token Type | Lifetime | Refresh Mechanism |
|-------|---------|------------|----------|-------------------|
| BB | `THREADS_ACCESS_TOKEN`, `THREADS_TOKEN_ISSUED_AT` (CS) | Long-lived Token | **60 days** | Auto-refresh via `graph.threads.net/refresh_access_token` when >50 days old. `scripts/token_health.py` handles this and writes new token to CS/.env. |
| Gaming | `CRITICALRUSH_THREADS_ACCESS_TOKEN` (root) | - | **EMPTY** | Not provisioned |
| Sports | `CLUTCHWIRE_THREADS_ACCESS_TOKEN` (root) | - | **EMPTY** | Not provisioned |
| Movies | `SPLICEREEL_THREADS_ACCESS_TOKEN` (root) | - | **EMPTY** | Not provisioned |
| Anime | `FRAMEDRIFT_THREADS_ACCESS_TOKEN` (root) | - | **EMPTY** | Not provisioned |

Threads App: Blackbox Brief Threads (App ID: `918419570569169`).
Issued at: epoch 1773047145 (~2026-03-07).

### 2.6 TikTok

| Niche | Env Var | Token Type | Lifetime | Refresh Mechanism |
|-------|---------|------------|----------|-------------------|
| Gaming (only) | `TIKTOK_ACCESS_TOKEN` (CR) | Content Posting API v2 | **24 hours** | Requires `TIKTOK_REFRESH_TOKEN` (1 year). Currently EMPTY -- not provisioned. |
| Gaming (only) | `TIKTOK_REFRESH_TOKEN` (CR) | OAuth Refresh | **1 year** | Currently EMPTY. |
| Gaming (only) | `TIKTOK_AUDIT_APPROVED` (CR) | Flag | n/a | Set to `false`. Publishing gated behind audit. |

No other niche has TikTok credentials.

### 2.7 Anthropic / Claude

| Env Var | Token Type | Lifetime | Refresh |
|---------|------------|----------|---------|
| `ANTHROPIC_API_KEY` (root, CS, CR) | API Key | **Permanent** (until rotated) | No refresh. Billing-based. |

### 2.8 OpenAI

| Env Var | Token Type | Lifetime | Refresh |
|---------|------------|----------|---------|
| `OPENAI_API_KEY` (root, CS) | API Key (project-scoped) | **Permanent** (until rotated) | No refresh. Billing-based. |

### 2.9 Azure / SharePoint

| Env Var | Token Type | Lifetime | Refresh |
|---------|------------|----------|---------|
| `AZURE_TENANT_ID` | Tenant ID | **Permanent** | n/a |
| `AZURE_CLIENT_ID` | App Registration Client ID | **Permanent** | n/a |
| `AZURE_CLIENT_SECRET` | Client Secret | **Configurable** (default 2 years) | Manual rotation in Azure Portal. `BacklogClient` uses MSAL client_credential flow to get access tokens automatically. |
| `SHAREPOINT_SITE_ID` | Site ID | **Permanent** | n/a |

MSAL client_credential flow: `BacklogClient` automatically requests new access tokens (1h lifetime) using the client secret. No manual refresh needed as long as the secret hasn't expired.

### 2.10 Twitch / IGDB

| Env Var | Token Type | Lifetime | Refresh |
|---------|------------|----------|---------|
| `TWITCH_CLIENT_ID` (root, CR) | Client ID | **Permanent** | n/a |
| `TWITCH_CLIENT_SECRET` (root, CR) | Client Secret | **Permanent** | OAuth client_credential flow auto-refreshes access tokens. |

### 2.11 Other API Keys

| Env Var | Service | Lifetime |
|---------|---------|----------|
| `TMDB_API_KEY` | The Movie Database | Permanent |
| `PEXELS_API_KEY` | Pexels stock media | Permanent |
| `PIXABAY_API_KEY` | Pixabay stock media | Permanent |
| `UNSPLASH_ACCESS_KEY` | Unsplash stock images | Permanent |
| `STEAM_API_KEY` (CR only) | Steam Web API | Permanent |

---

## 3. Credential Loading Chains

### Path A: BB Daily Pipeline (`daily_intel.sh`)

```
Trigger: launchd com.genlab.daily-intel plist
         -> /bin/bash -lc cron_wrapper.sh
         -> orchestrator.sh daily
         -> daily_intel.sh

SHELL LAYER:
  1. daily_intel.sh sources Content Scraper/.env line-by-line
     (custom while-read loop, strips quotes, exports each var)
  2. WorkingDirectory = Content Scraper/
  3. Plist sets: GENLAB_PROJECT_DIR, HOME, PATH, LANG

PYTHON LAYER (each script):
  4. settings.py: _PROJECT_ROOT resolves from AGENT_ROOT env or
     4 parents up from settings.py -> GenLab root
  5. settings.py: load_dotenv(GenLab/.env, override=False)
     -- won't overwrite vars already set by shell
  6. publish_all_platforms.py (BB version): load_dotenv(override=True)
     -- reads closest .env (Content Scraper/.env since CWD is there)
  7. niche_credentials.py: resolves BLACKBOXBRIEF_* prefixed vars
     from os.environ (populated by steps 1+5)
```

**Winner for `META_ACCESS_TOKEN`**: Content Scraper/.env (shell exports first, settings.py override=False respects it).

**Winner for `YOUTUBE_REFRESH_TOKEN`**: Content Scraper/.env (BB's token). Root .env has `BLACKBOXBRIEF_YOUTUBE_REFRESH_TOKEN` but not unprefixed `YOUTUBE_REFRESH_TOKEN`.

### Path B: Per-Niche Publishers (genlab-core publisher plists)

```
Trigger: launchd com.genlab.{niche}-publisher plist
         e.g., com.genlab.gaming-publisher

PLIST CONFIG:
  - ProgramArguments: .venv/bin/python3 -m genlab_core.publishing.publish_all_platforms --niche gaming
  - WorkingDirectory: /Users/anarchistsid/GenLab
  - EnvironmentVariables: PATH, HOME, BACKLOG_CONFIG_PATH
  - NO launch_wrapper.sh -- no shell .env loading

PYTHON LAYER:
  1. genlab-core settings.py: _PROJECT_ROOT = GenLab root
     (no AGENT_ROOT set, so it resolves from __file__ parents)
  2. settings.py: load_dotenv(GenLab/.env, override=False)
     -- populates ALL per-niche prefixed vars from root .env
  3. genlab-core publish_all_platforms.py main(): load_dotenv(override=True)
     -- reads GenLab/.env again (CWD is GenLab)
  4. niche_credentials.py: resolves {PREFIX}_* vars
     e.g., CRITICALRUSH_META_ACCESS_TOKEN for --niche gaming
```

**Key**: These plists do NOT use launch_wrapper.sh and do NOT load Content Scraper/.env or CriticalRush/.env. They rely entirely on GenLab/.env (root). This is the correct design for per-niche isolation.

### Path C: BB Publisher (legacy plist via orchestrator.sh)

```
Trigger: launchd com.genlab.instagram-publisher plist
         -> launch_wrapper.sh (sources CS/.env + CR/.env)
         -> publisher_wrapper.sh
         -> orchestrator.sh publish
         -> publish_all_platforms.py --niche ai_creators

SHELL LAYER:
  1. launch_wrapper.sh: source CS/.env (with set -a)
  2. launch_wrapper.sh: source CR/.env (with set -a) -- CR WINS for conflicts
  3. orchestrator.sh: load_dotenv() sources CS/.env again (custom loop)
     -- but vars already exported by launch_wrapper.sh take precedence
  4. Plist sets: NICHE_ID=ai_creators, MAX_BLUEPRINTS_PER_RUN=1

PYTHON LAYER:
  5. BB publish_all_platforms.py: load_dotenv(override=True)
     -- re-reads CS/.env from CWD (Content Scraper)
  6. niche_credentials.py: resolves BLACKBOXBRIEF_* from os.environ
```

**DANGER ZONE**: launch_wrapper.sh sources CR/.env AFTER CS/.env, so `META_ACCESS_TOKEN` would be CriticalRush's token, not BB's. However, the niche_credentials guard should resolve `BLACKBOXBRIEF_META_ACCESS_TOKEN` from root .env instead. This works because publish_all_platforms calls `resolve_niche_env("ai_creators", ...)` which looks for `BLACKBOXBRIEF_META_ACCESS_TOKEN`, not the unprefixed global.

### Path D: Dashboard Server

```
Trigger: launchd com.genlab.review-server plist
         -> review_server_wrapper.sh

SHELL LAYER:
  1. review_server_wrapper.sh: sources Content Scraper/.env (custom loop)
  2. Sets GENLAB_PROJECT_ROOT, BACKLOG_CONFIG_PATH
  3. Runs gunicorn via uv

PYTHON LAYER:
  4. review_server.py: imports from genlab_core
  5. settings.py: load_dotenv(root .env, override=False)
     -- CS/.env vars already in env take precedence
  6. Dashboard API: uses check_token_health functions which read
     global vars (META_ACCESS_TOKEN, etc.) -- gets BB's values from CS/.env
```

**Winner**: Content Scraper/.env (shell export happens first).

### Path E: Engagement Pollers

```
Trigger: launchd com.genlab.engagement.poller.{platform}.{niche} plist
         -> launch_wrapper.sh
         -> uv run --package genlab-core python scripts/run_engagement_poller.py

SHELL LAYER:
  1. launch_wrapper.sh: source CS/.env (set -a)
  2. launch_wrapper.sh: source CR/.env (set -a) -- CR WINS for conflicts
  3. Plist sets: ENGAGEMENT_DISPATCH=true, REDIS_HOST, REDIS_PORT

PYTHON LAYER:
  4. settings.py: load_dotenv(root .env, override=False)
     -- shell vars already present take precedence
  5. Poller uses niche_credentials to resolve per-niche vars
```

**Winner for `META_ACCESS_TOKEN`**: CR/.env (loaded second by launch_wrapper.sh).
**Winner for `YOUTUBE_REFRESH_TOKEN`**: CR/.env (gaming token, not BB).
This is safe because pollers use niche_credentials with the specific niche, not global vars.

### Path F: Fetch Insights Collectors

```
Trigger: launchd com.genlab.fetch-insights-{niche}-{window}h plist
         -> launch_wrapper.sh
         -> uv run --package genlab-core python -m genlab_core.scripts.run_fetch_insights

SHELL LAYER:
  1. launch_wrapper.sh: source CS/.env then CR/.env
  2. Plist sets: BACKLOG_CONFIG_PATH

PYTHON LAYER:
  3. settings.py: load_dotenv(root .env, override=False)
  4. Fetch insights reads global platform tokens for API calls
     (YouTube Analytics, Meta Insights, etc.)
```

### Path G: Token Health Checker

```
Trigger: launchd com.genlab.token-refresh plist (daily at 20:30 UTC)
         -> uv run python scripts/token_health.py

PYTHON LAYER:
  1. scripts/token_health.py: load_dotenv(CS/.env)
     -- loads BB's global credentials
  2. Imports NICHE_CREDENTIAL_PREFIXES from genlab_core
  3. Checks BB globals: check_meta_token(), check_youtube_token(),
     check_twitter_token(), check_threads_token()
  4. Checks all niches: _check_niche_meta(), _check_niche_youtube(),
     _check_niche_threads() for each prefix
  5. Auto-refreshes Threads if >50 days old (writes to CS/.env)
  6. Auto-refreshes YouTube access token (writes to token.json)
  7. Writes report to ~/.genlab/token_health.json
```

**Note**: This script does NOT load root .env directly. It loads CS/.env for globals, then reads per-niche prefixed vars from env. Since niche prefixed vars (CRITICALRUSH_*, etc.) are in root .env, they are NOT available here unless settings.py or another mechanism loads root .env.

**BUG**: `scripts/token_health.py` calls `load_dotenv(CS/.env)` but the per-niche prefixed vars (`CRITICALRUSH_META_ACCESS_TOKEN`, etc.) are defined in root .env, not CS/.env. The niche checks (`check_all_niches()`) will see these vars as missing unless `genlab_core.publishing.niche_credentials` import triggers `settings.py` which does `load_dotenv(root .env, override=False)`. This works because the import of `genlab_core.publishing.niche_credentials` triggers `genlab_core/__init__.py` and eventually `settings.py` which loads root .env.

---

## 4. niche_credentials.py Resolution

**Canonical file**: `/Users/anarchistsid/GenLab/genlab-core/src/genlab_core/publishing/niche_credentials.py`
**Shim**: `/Users/anarchistsid/GenLab/Content Scraper/execution/utils/niche_credentials.py` (re-exports from genlab_core)

### 4.1 Prefix Map

```python
NICHE_CREDENTIAL_PREFIXES = {
    "sports":      "CLUTCHWIRE",
    "movies":      "SPLICEREEL",
    "anime":       "FRAMEDRIFT",
    "gaming":      "CRITICALRUSH",
    "ai_creators": "BLACKBOXBRIEF",
    "ai_tech":     "BLACKBOXBRIEF",  # alias
}
```

### 4.2 Resolution Logic

`resolve_niche_env(niche_id, global_var, niche_suffix)`:
1. Look up prefix from `NICHE_CREDENTIAL_PREFIXES`
2. If prefix found: check `{PREFIX}_{niche_suffix}` in os.environ
   - If non-empty: return it
   - If empty: **refuse fallback** to global var, return ""
3. If no prefix (unknown niche): fall back to `global_var` from os.environ

### 4.3 Per-Platform Resolution

| Function | Vars Checked (for niche with prefix) |
|----------|--------------------------------------|
| `resolve_meta_credentials()` | `{PREFIX}_META_ACCESS_TOKEN`, `{PREFIX}_IG_USER_ID`, `{PREFIX}_FB_PAGE_ACCESS_TOKEN`, `{PREFIX}_FB_PAGE_ID` |
| `resolve_fb_credentials()` | `{PREFIX}_FB_PAGE_ACCESS_TOKEN`, `{PREFIX}_FB_PAGE_ID` |
| `resolve_threads_credentials()` | `{PREFIX}_THREADS_ACCESS_TOKEN`, `{PREFIX}_THREADS_USER_ID` |
| `resolve_youtube_credentials()` | `{PREFIX}_YOUTUBE_CLIENT_ID`, `{PREFIX}_YOUTUBE_CLIENT_SECRET`, `{PREFIX}_YOUTUBE_REFRESH_TOKEN` |
| `resolve_twitter_credentials()` | `{PREFIX}_X_API_KEY`, `{PREFIX}_X_API_SECRET`, `{PREFIX}_X_ACCESS_TOKEN`, `{PREFIX}_X_ACCESS_SECRET` |

### 4.4 Cross-Channel Guard

`validate_niche_match(blueprint_niche, credential_niche)`:
- Raises `CrossChannelPublishError` if blueprint niche != credential niche
- Raises if credential_niche is empty

### 4.5 What Happens When a Per-Niche Var is Missing

**Returns ""** (empty string). Does NOT fall back to the global/BB credential. This prevents accidentally publishing sports content to BB's Instagram page.

Example: If `CLUTCHWIRE_X_API_KEY` is empty (which it is), `resolve_twitter_credentials("sports")` returns `{"api_key": "", ...}`. The publisher will log a SKIPPED record for Twitter rather than using BB's X account.

---

## 5. Token Health Checking

### 5.1 Three Token Health Systems

There are three separate health-checking mechanisms:

| System | File | Purpose | Schedule |
|--------|------|---------|----------|
| **CLI checker** | `genlab_core/monitoring/token_health.py` | Library + CLI. Checks AI + social via HealthCheckable protocol. | On-demand |
| **Comprehensive checker** | `scripts/token_health.py` | Checks all 5 niches + auto-refreshes Threads. | Daily at 20:30 UTC via `com.genlab.token-refresh` plist |
| **Dashboard API** | `dashboard/server/api/token_health.py` | Flask endpoint for dashboard. 5-minute cache. | On request via `/api/token-health` |

### 5.2 Platform Checks in `genlab_core/monitoring/token_health.py`

| Check | What it Does | Auto-Refresh? |
|-------|-------------|---------------|
| `check_anthropic()` | Makes a minimal Claude Haiku request | No |
| `check_openai()` | Makes a minimal gpt-4o-mini request | No |
| `check_meta_token()` | GET /{page_id}?fields=... + debug_token | No. `refresh_meta_token()` is a **no-op** by design. |
| `check_threads()` | Reads `THREADS_TOKEN_ISSUED_AT`, calculates days remaining | No (in this module) |
| `check_tiktok()` | Reads `TIKTOK_TOKEN_ISSUED_AT`, calculates hours remaining | No |
| `check_backlog()` | `BacklogClient.health_check()` | Auto (MSAL) |

### 5.3 Platform Checks in `genlab_core/monitoring/check_token_health.py`

| Check | What it Does | Auto-Refresh? |
|-------|-------------|---------------|
| `check_youtube()` | OAuth token exchange via google-auth or raw POST | Implicitly refreshes access token during check |
| `check_facebook()` | GET /{page_id} + debug_token for scopes | No |
| `check_twitter()` | tweepy verify_credentials or bearer /users/me | No |

### 5.4 Comprehensive Checker: `scripts/token_health.py`

| Check | What it Does | Auto-Refresh? |
|-------|-------------|---------------|
| `check_meta_token()` | GET /v21.0/me + debug_token | No |
| `check_youtube_token()` | Reads token.json, exchanges refresh_token | **Yes** -- writes new access token to token.json |
| `check_twitter_token()` | Bearer /2/users/me | No |
| `check_threads_token()` | Reads issued_at, refreshes if >50 days | **Yes** -- calls `graph.threads.net/refresh_access_token`, writes new token to CS/.env |
| `_check_niche_meta()` | GET /v21.0/me with per-niche token | No |
| `_check_niche_youtube()` | Token exchange with per-niche refresh token | No (validation only, does not persist) |
| `_check_niche_threads()` | Same as Threads check but with prefixed vars | **Yes** -- auto-refresh if >50 days old |

### 5.5 Token Refresh Plist

```
Plist: com.genlab.token-refresh
File:  CriticalRush/runbooks/com.genlab.token-refresh.plist
CMD:   uv run --project GenLab python scripts/token_health.py
Schedule: Daily at 20:30 UTC (02:00 IST)
Logs:  ~/Library/Logs/genlab-token-refresh.log
```

### 5.6 Meta Token Refresh -- Is it Truly a No-Op?

**Yes**, in `genlab_core/monitoring/token_health.py`:
```python
def refresh_meta_token(current_token: str) -> dict:
    """No-op: EAA page tokens are permanent and don't need refresh."""
    return {"success": False, "error": "EAA page tokens are permanent -- refresh not needed"}
```

The comprehensive checker (`scripts/token_health.py`) also does NOT attempt to refresh Meta tokens. It only validates them and warns if they become invalid (which would require manual OAuth flow to fix).

---

## 6. LaunchAgent Plist Inventory

### 6.1 Pipeline Runners

| Plist | Schedule | Env Loading | Command |
|-------|----------|-------------|---------|
| `com.genlab.daily-intel` | 08:00 IST | Shell: CS/.env via cron_wrapper -> orchestrator -> daily_intel | BB 23-step pipeline |
| `com.genlab.criticalrush` (ai.aspirehub.criticalrush.pipeline) | Per setup/ | N/A | CriticalRush pipeline |

### 6.2 Daily Pipelines (non-BB)

| Plist | Schedule | Notes |
|-------|----------|-------|
| `com.genlab.clutchwire-daily` | - | ClutchWire daily pipeline |
| `com.genlab.splicereel-daily` | - | SpliceReel daily pipeline |
| `com.genlab.framedrift-daily` | - | FrameDrift daily pipeline |

### 6.3 Publishers

| Plist | Niche | Schedule | Env Loading | Command |
|-------|-------|----------|-------------|---------|
| `com.genlab.instagram-publisher` | ai_creators | 12:00 IST | launch_wrapper.sh (CS+CR .env) -> publisher_wrapper.sh | Legacy BB publisher |
| `com.genlab.ai-creators-publisher` | ai_creators | 06:30 UTC | **No shell wrapper** -- Python loads root .env via settings.py | genlab-core publisher |
| `com.genlab.gaming-publisher` | gaming | 06:30 UTC | **No shell wrapper** | genlab-core publisher |
| `com.genlab.sports-publisher` | sports | 06:30 UTC | **No shell wrapper** | genlab-core publisher |
| `com.genlab.movies-publisher` | movies | 06:30 UTC | **No shell wrapper** | genlab-core publisher |
| `com.genlab.anime-publisher` | anime | 06:30 UTC | **No shell wrapper** | genlab-core publisher |
| `com.genlab.criticalrush-publisher` | gaming | - | CriticalRush runbooks | Legacy CR publisher |
| `com.genlab.clutchwire-publisher` | sports | - | ClutchWire runbooks | Legacy CW publisher |
| `com.genlab.splicereel-publisher` | movies | - | SpliceReel runbooks | Legacy SR publisher |
| `com.genlab.framedrift-publisher` | anime | - | FrameDrift runbooks | Legacy FD publisher |

**NOTE**: There are TWO publisher plists per niche -- a legacy one in each channel's runbooks/ and a new one in genlab-core/runbooks/. The genlab-core ones are the canonical Sprint 62 publishers.

### 6.4 Engagement Pollers

| Plist | Platform/Niche | Schedule | Env Loading |
|-------|----------------|----------|-------------|
| `com.genlab.engagement-poller` | YouTube/gaming | KeepAlive | **No launch_wrapper** -- Python settings.py loads root .env |
| `com.genlab.engagement.poller.youtube.sports` | YouTube/sports | KeepAlive | launch_wrapper.sh (CS+CR .env) |
| `com.genlab.engagement.poller.youtube.movies` | YouTube/movies | KeepAlive | launch_wrapper.sh |
| `com.genlab.engagement.poller.youtube.anime` | YouTube/anime | KeepAlive | launch_wrapper.sh |
| `com.genlab.engagement.poller.twitter.ai-news` | Twitter/ai_creators | KeepAlive | launch_wrapper.sh |
| `com.genlab.engagement.poller.twitter.sports` | Twitter/sports | KeepAlive | launch_wrapper.sh |
| `com.genlab.engagement.poller.twitter.movies` | Twitter/movies | KeepAlive | launch_wrapper.sh |
| `com.genlab.engagement.poller.twitter.anime` | Twitter/anime | KeepAlive | launch_wrapper.sh |

### 6.5 Fetch Insights (Metric Collection)

| Plist | Window | Niche | Schedule | Env Loading |
|-------|--------|-------|----------|-------------|
| `com.genlab.fetch-insights-ai-creators-48h` | 48h | ai_creators | Staggered | launch_wrapper.sh |
| `com.genlab.fetch-insights-gaming-48h` | 48h | gaming | 14:18 UTC | launch_wrapper.sh |
| `com.genlab.fetch-insights-sports-48h` | 48h | sports | Staggered | launch_wrapper.sh |
| `com.genlab.fetch-insights-movies-48h` | 48h | movies | Staggered | launch_wrapper.sh |
| `com.genlab.fetch-insights-anime-48h` | 48h | anime | Staggered | launch_wrapper.sh |
| `com.genlab.fetch-insights-ai-creators-168h` | 168h | ai_creators | Staggered | launch_wrapper.sh |
| `com.genlab.fetch-insights-gaming-168h` | 168h | gaming | Staggered | launch_wrapper.sh |
| `com.genlab.fetch-insights-sports-168h` | 168h | sports | Staggered | launch_wrapper.sh |
| `com.genlab.fetch-insights-movies-168h` | 168h | movies | Staggered | launch_wrapper.sh |
| `com.genlab.fetch-insights-anime-168h` | 168h | anime | Staggered | launch_wrapper.sh |

### 6.6 Infrastructure Services

| Plist | Schedule | Env Loading | Purpose |
|-------|----------|-------------|---------|
| `com.genlab.review-server` | KeepAlive + RunAtLoad | review_server_wrapper.sh loads CS/.env | Dashboard (Flask + Gunicorn) |
| `com.genlab.token-refresh` | Daily 20:30 UTC | Python: load_dotenv(CS/.env) | Token health + Threads auto-refresh |
| `com.genlab.metric-collector` | - | - | Metric collection orchestrator |
| `com.genlab.quota-monitor` | - | - | YouTube quota monitoring |
| `com.genlab.feedback-collector` | - | - | Engagement feedback collection |
| `com.genlab.spike-detector` | - | - | Viral spike detection |
| `com.genlab.prefect-server` | KeepAlive | - | Prefect orchestration server |
| `com.genlab.prefect-worker` | KeepAlive | - | Prefect worker process |
| `com.genlab.cleanup-runs` | - | - | BB artifact cleanup |
| `com.genlab.criticalrush-cleanup` | - | - | CR artifact cleanup |
| `com.genlab.review-tunnel` | - | - | Cloudflare tunnel for dashboard |

---

## 7. Known Credential Issues

### 7.1 Tokens Requiring Manual Provisioning

| Token | Status | Action Required |
|-------|--------|-----------------|
| `CLUTCHWIRE_X_*` | **EMPTY** | Create X developer app for @ClutchWire, generate OAuth 1.0a keys |
| `SPLICEREEL_X_*` | **EMPTY** | Create X developer app for @SpliceReel |
| `FRAMEDRIFT_X_*` | **EMPTY** | Create X developer app for @FrameDrift |
| `CRITICALRUSH_X_ACCESS_TOKEN/SECRET` | **EMPTY** | Generate Read+Write access tokens in CR X app settings |
| `CLUTCHWIRE_THREADS_*` | **EMPTY** | Set up Threads API for ClutchWire account |
| `SPLICEREEL_THREADS_*` | **EMPTY** | Set up Threads API for SpliceReel account |
| `FRAMEDRIFT_THREADS_*` | **EMPTY** | Set up Threads API for FrameDrift account |
| `CRITICALRUSH_THREADS_*` | **EMPTY** | Set up Threads API for CriticalRush account |
| `TIKTOK_ACCESS_TOKEN` | **EMPTY** | Complete TikTok OAuth flow, set TIKTOK_AUDIT_APPROVED=true |
| `AZURE_CLIENT_SECRET` | Active | Will expire (~2 years from creation). Monitor in Azure Portal. |

### 7.2 Tokens That Auto-Refresh

| Token | Mechanism | Frequency |
|-------|-----------|-----------|
| BB `THREADS_ACCESS_TOKEN` | `scripts/token_health.py` calls `graph.threads.net/refresh_access_token` | Daily check. Refreshes when >50 days old. Writes to CS/.env. |
| YouTube access tokens | Google OAuth2 library exchanges refresh_token automatically | Every API call (1h token lifetime). Transparent to application. |
| YouTube token.json | `scripts/token_health.py` `check_youtube_token()` refreshes and persists | Daily check. Writes to token.json. |
| Azure/SharePoint access tokens | MSAL `client_credential` flow | Automatic on every `BacklogClient` call (1h token lifetime). |
| Twitch access tokens | OAuth `client_credentials` flow | Automatic when needed by IGDB/Twitch API calls. |

### 7.3 What Happens When a Token Expires During Publish

| Platform | Behavior |
|----------|----------|
| Instagram/Facebook | Publish fails with HTTP 400/401. `platform_publish_status` set to ERROR. Blueprint stays at current status. Retry on next run. |
| YouTube | `google-auth` auto-refreshes access token from refresh token. If refresh token is revoked, upload fails with 401. |
| Twitter | OAuth 1.0a tokens don't expire. If revoked, publish fails with 401. SKIPPED record written. |
| Threads | If token expired (>60 days), publish fails. Auto-refresh should have caught it within 10 days of expiry. |
| TikTok | Access token (24h). If expired, publish fails. Currently disabled anyway. |

### 7.4 Stale / Conflicting Tokens

| Issue | Description | Risk |
|-------|-------------|------|
| **CS/.env `FB_PAGE_ACCESS_TOKEN`** | Points to "The People's Democracy" page (422278584555262) -- this is BB's legacy token. Root .env has `BLACKBOXBRIEF_FB_PAGE_ACCESS_TOKEN` which is the correct BB Facebook token. | If legacy BB code uses unprefixed `FB_PAGE_ACCESS_TOKEN`, it gets the STALE token, not the current one from root .env. The new genlab-core publisher uses niche_credentials which resolves `BLACKBOXBRIEF_FB_PAGE_ACCESS_TOKEN`. |
| **CR/.env `META_ACCESS_TOKEN`** | A different, possibly older CR page token vs `CRITICALRUSH_META_ACCESS_TOKEN` in root .env. | launch_wrapper.sh sources CR/.env, overwriting any global `META_ACCESS_TOKEN`. Safe because niche_credentials uses prefixed vars. |
| **CR/.env `CRITICALRUSH_FB_PAGE_ACCESS_TOKEN`** | Different value from root .env's `CRITICALRUSH_FB_PAGE_ACCESS_TOKEN`. | If launch_wrapper.sh sources CR/.env, the CR/.env value overwrites. May be a stale token. |
| **CW/.env `CLUTCHWIRE_FB_PAGE_ACCESS_TOKEN`** | Different value from root .env. | Same risk. Per-channel .env tokens may be from earlier provisioning round. |
| **SR/.env `SPLICEREEL_FB_PAGE_ACCESS_TOKEN`** | Different value from root .env. | Same risk. |
| **FD/.env `FRAMEDRIFT_FB_PAGE_ACCESS_TOKEN`** | Different value from root .env. | Same risk. |
| **CR/.env `YOUTUBE_REFRESH_TOKEN`** | CR's gaming channel token, different from CS/.env's BB token. | Safe when niche_credentials resolves `CRITICALRUSH_YOUTUBE_REFRESH_TOKEN`. Dangerous if any code reads unprefixed `YOUTUBE_REFRESH_TOKEN` from a process that sourced CR/.env. |

### 7.5 Credential Loading Hazard: launch_wrapper.sh

`launch_wrapper.sh` sources both CS/.env and CR/.env with `set -a` (export all). For any var that appears in both files with DIFFERENT values, CR/.env wins. Affected vars:

- `META_ACCESS_TOKEN`: CR's page token overwrites BB's
- `META_IG_USER_ID`: CR's IG account overwrites BB's
- `FB_PAGE_ACCESS_TOKEN`: CR's page token overwrites BB's
- `META_FB_PAGE_ID`: CR's page ID overwrites BB's
- `YOUTUBE_REFRESH_TOKEN`: CR's refresh token overwrites BB's
- `X_API_KEY`/`X_API_SECRET`/`X_BEARER_TOKEN`: CR's X app overwrites BB's

**Mitigated by**: niche_credentials always using prefixed vars (`CRITICALRUSH_*`, `BLACKBOXBRIEF_*`). Any code that reads unprefixed vars is vulnerable.

### 7.6 Recommended Actions

1. **Remove per-channel .env FB tokens that conflict with root**: CW/.env, SR/.env, FD/.env each have stale FB tokens. Either remove them or update to match root .env.
2. **Provision per-niche X/Twitter credentials**: All non-BB niches have empty X credentials (blocker H5).
3. **Provision per-niche Threads credentials**: All non-BB niches have empty Threads credentials (blocker H5).
4. **CriticalRush X access tokens**: App exists but access tokens are empty -- generate in X developer console.
5. **Monitor Azure client secret expiry**: Check in Azure Portal for expiration date.
6. **Consider removing launch_wrapper.sh multi-source pattern**: The dual-source of CS+CR .env is a footgun. Per-niche publisher plists (genlab-core/runbooks/) correctly avoid this by loading only root .env via Python.
