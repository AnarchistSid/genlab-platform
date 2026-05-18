# .env Audit Report

**Date:** 2026-03-17
**Status:** Research only -- no files modified
**Constraint:** Per user feedback (2026-03-16), never consolidate .env files after provisioning tokens. Stale values can overwrite fresh ones.

---

## 1. Inventory of .env Files

| # | File | Lines | Purpose |
|---|------|-------|---------|
| 1 | `GenLab/.env` (root) | 117 | Shared infrastructure + all per-niche prefixed credentials |
| 2 | `BlackboxBrief/.env` | 129 | BB-specific + legacy "master" env (all channels source this) |
| 3 | `CriticalRush/.env` | 74 | Gaming-specific (Twitch, IGDB, Steam, Prefect) + unprefixed platform creds |
| 4 | `ClutchWire/.env` | 9 | Azure/SharePoint + FB page token (minimal) |
| 5 | `SpliceReel/.env` | 9 | Azure/SharePoint + FB page token (minimal) |
| 6 | `FrameDrift/.env` | 9 | Azure/SharePoint + FB page token (minimal) |

---

## 2. Variable-by-Variable Cross-File Matrix

### 2.1 Global Infrastructure (identical across all copies)

| Variable | Root | BB | CR | CW | SR | FD | Values Match? |
|----------|:----:|:--:|:--:|:--:|:--:|:--:|:---:|
| AZURE_TENANT_ID | Y | Y | Y | Y | Y | Y | IDENTICAL |
| AZURE_CLIENT_ID | Y | Y | Y | Y | Y | Y | IDENTICAL |
| AZURE_CLIENT_SECRET | Y | Y | Y | Y | Y | Y | IDENTICAL |
| SHAREPOINT_SITE_ID | Y | Y | Y | Y | Y | Y | IDENTICAL |
| ANTHROPIC_API_KEY | Y | Y | Y | - | - | - | IDENTICAL |
| OPENAI_API_KEY | Y | Y | - | - | - | - | IDENTICAL |
| YOUTUBE_API_KEY | Y | Y | - | - | - | - | IDENTICAL |
| YOUTUBE_CLIENT_ID | Y | Y | Y | - | - | - | IDENTICAL |
| YOUTUBE_CLIENT_SECRET | Y | Y | Y | - | - | - | IDENTICAL |
| PEXELS_API_KEY | Y | Y | Y | - | - | - | IDENTICAL |
| FB_APP_ID | Y | Y | - | - | - | - | IDENTICAL |
| FB_APP_SECRET | Y | Y | - | - | - | - | IDENTICAL |
| META_APP_SECRET | Y | Y | - | - | - | - | IDENTICAL |
| META_WEBHOOK_VERIFY_TOKEN | Y | Y | - | - | - | - | IDENTICAL |
| TMDB_API_KEY | Y | Y | - | - | - | - | IDENTICAL |
| TWITCH_CLIENT_ID | Y | - | Y | - | - | - | IDENTICAL |
| TWITCH_CLIENT_SECRET | Y | - | Y | - | - | - | IDENTICAL |
| ENGAGEMENT_DISPATCH | Y | Y | - | - | - | - | IDENTICAL (true) |

### 2.2 CONFLICTS -- Same Key, Different Values

| Variable | File A | Value (truncated) | File B | Value (truncated) | Severity |
|----------|--------|-------------------|--------|-------------------|----------|
| **META_ACCESS_TOKEN** | BB | `EAAfTZB...Efx` (BB page token) | CR | `EAAfTZB...BiJ` (OLD/stale CR token) | **HIGH** |
| **META_IG_USER_ID** | BB | `17841448019867838` (BB IG acct) | CR | `17841442899013893` (CR IG acct) | **HIGH** |
| **FB_PAGE_ACCESS_TOKEN** | BB | `EAAfTZB...XHk` (BB FB page -- old/deprecated) | CR | `EAAfTZB...Mra` (CR FB page -- old/deprecated) | **HIGH** |
| **META_FB_PAGE_ID** | BB | `422278584555262` (BB page) | CR | `1025113540681145` (CR page) | **HIGH** |
| **YOUTUBE_REFRESH_TOKEN** | BB | `1//0g9z0bzq...` (BB channel) | CR | `1//0gHwHhpi...` (CR channel) | **HIGH** |
| **X_API_KEY** | BB | `FgXP2OE...` (BB app) | CR | `g0hYFLB...` (CR app) | **HIGH** |
| **X_API_SECRET** | BB | `sm4UOwq...` (BB app) | CR | `f1Uvubg...` (CR app) | **HIGH** |
| **X_ACCESS_TOKEN** | BB | `632788275-RBH...` (populated) | CR | (empty) | MEDIUM |
| **X_ACCESS_SECRET** | BB | `H4XZRnt...` (populated) | CR | (empty) | MEDIUM |
| **X_BEARER_TOKEN** | BB | `AAAA...dld` (BB app) | CR | `AAAA...f9j` (CR app) | **HIGH** |
| **TWITTER_USER_ID_*** | BB | `TWITTER_USER_ID_AI_NEWS=632788275` | CR | `TWITTER_USER_ID_GAMING=2029218265801846784` | OK (different key names) |
| **CLUTCHWIRE_FB_PAGE_ACCESS_TOKEN** | Root | `EAAfTZB...MuL` | CW | `EAAfTZB...pM` | **CRITICAL** |
| **CRITICALRUSH_FB_PAGE_ACCESS_TOKEN** | Root | `EAAfTZB...gEo` | CR | `EAAfTZB...Mra` | **CRITICAL** |

### 2.3 Variables Unique to Each File

**Root `.env` only:**
- All `CLUTCHWIRE_*` prefixed vars (META, FB, IG, YT, X, Threads)
- All `SPLICEREEL_*` prefixed vars (META, FB, IG, YT, X, Threads)
- All `FRAMEDRIFT_*` prefixed vars (META, FB, IG, YT, X, Threads)
- All `CRITICALRUSH_*` prefixed vars (META, FB, IG, YT, X, Threads)
- All `BLACKBOXBRIEF_*` prefixed vars (META, FB, IG, YT)
- `POSTGRES_PASSWORD`, `DATABASE_URL`, `GENLAB_USE_POSTGRES`
- `PIXABAY_API_KEY`, `UNSPLASH_ACCESS_KEY` (also in BB)

**BB `.env` only:**
- `META_IG_APP_ID`, `META_IG_APP_SECRET` (Meta app credentials for IG)
- `REVIEW_AUTH_USER`, `REVIEW_AUTH_PASS` (dashboard auth)
- `FLASK_SECRET_KEY`
- `THREADS_ACCESS_TOKEN`, `THREADS_USER_ID`, `THREADS_TOKEN_ISSUED_AT`, `THREADS_APP_ID`, `THREADS_APP_SECRET`
- `YT_CHANNEL_ID` (BB legacy, unprefixed)
- `YOUTUBE_REFRESH_TOKEN` (BB channel, unprefixed)

**CR `.env` only:**
- `AGENT_ROOT` (CriticalRush project path)
- `STEAM_API_KEY` (game metadata)
- `TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_*` vars
- `CLOUDFLARE_TUNNEL_URL`
- `SHORT_VIDEO_MAKER_URL`
- `PREFECT_API_URL`, `PREFECT_WORK_POOL`
- Unprefixed `META_ACCESS_TOKEN`, `FB_PAGE_ACCESS_TOKEN` (stale CR tokens)
- Unprefixed `X_*` vars (CR-specific Twitter app)

**CW/SR/FD `.env` files:**
- Only Azure/SharePoint + one `{NICHE}_FB_PAGE_ACCESS_TOKEN` + `{NICHE}_FB_PAGE_ID`

---

## 3. .env Loading Chain -- Who Loads What

### 3.1 Shell Wrapper Loading

| Service / Script | Loads (in order) | Notes |
|-----------------|------------------|-------|
| **BB daily_intel.sh** | `BlackboxBrief/.env` | BB orchestrator loads its own .env |
| **BB orchestrator.sh** (publish mode) | `BlackboxBrief/.env` | Same -- publish runs through BB |
| **CR daily_intel.sh** | `BlackboxBrief/.env` then `CriticalRush/.env` | BB first (shared creds), CR second (overrides) |
| **CW daily_intel.sh** | `BlackboxBrief/.env` only | Does NOT load `ClutchWire/.env` |
| **SR daily_intel.sh** | `BlackboxBrief/.env` only | Does NOT load `SpliceReel/.env` |
| **FD daily_intel.sh** | `BlackboxBrief/.env` only | Does NOT load `FrameDrift/.env` |
| **dashboard review_server_wrapper.sh** | `GenLab/.env` then `BlackboxBrief/.env` | Root first, BB second (BB overrides root on conflicts) |
| **scripts/launch_wrapper.sh** | `GenLab/.env` | Only root .env (but code also checks BB/.env exists) |
| **scripts/publish.sh** | Delegates to BB `orchestrator.sh publish` | Which loads `BlackboxBrief/.env` |

### 3.2 Python dotenv Loading

| Module | Loads | override= | Notes |
|--------|-------|-----------|-------|
| **genlab-core settings.py** | `_PROJECT_ROOT/.env` | False | AGENT_ROOT determines which root. CriticalRush sets AGENT_ROOT to CR dir |
| **BB script_bootstrap.py** | Walks up to find `.env` near `CLAUDE.md` | True | BB scripts find `BlackboxBrief/.env` |
| **BB publish_all_platforms.py** | `load_dotenv(override=True)` (cwd) | True | Loads whichever .env is in cwd (BB when run from BB) |
| **genlab-core publish_all_platforms.py** | `load_dotenv(override=True)` (in `__main__`) | True | Only at CLI entry |
| **CR test scripts** | `CriticalRush/.env` (explicit path) | default | Twitch/Steam/IGDB tests |
| **scripts/intelligence_hub.py** | `GenLab/.env` (parent of scripts/) | default | |
| **scripts/morning_briefing.py** | `GenLab/.env` | default | |
| **genlab-core run_monetisation_tracker.py** | `GenLab/.env` (via _GENLAB_ROOT) | default | |

### 3.3 Effective Loading -- What Each Service Actually Sees

| Service | Azure/SP | LLM Keys | Meta (BB) | Meta (per-niche) | YouTube | X/Twitter | Threads | Twitch |
|---------|:--------:|:--------:|:---------:|:-----------------:|:-------:|:---------:|:-------:|:------:|
| BB daily pipeline | BB | BB | BB (unprefixed) | Via root (prefixed) | BB | BB | BB | - |
| CR daily pipeline | BB then CR | BB | CR (unprefixed, stale!) | Via root (prefixed) | CR (unprefixed) | CR (unprefixed) | CR (empty) | CR |
| CW daily pipeline | BB | BB | BB (unprefixed -- WRONG for CW!) | NOT loaded (CW/.env not sourced!) | BB | BB | BB | - |
| SR daily pipeline | BB | BB | BB (unprefixed -- WRONG for SR!) | NOT loaded (SR/.env not sourced!) | BB | BB | BB | - |
| FD daily pipeline | BB | BB | BB (unprefixed -- WRONG for FD!) | NOT loaded (FD/.env not sourced!) | BB | BB | BB | - |
| Dashboard | Root then BB | Both | BB (overrides root) | Root (prefixed) | Both | BB | BB | Root |
| genlab-core Settings | AGENT_ROOT/.env | AGENT_ROOT | AGENT_ROOT | os.environ (if shell-loaded) | AGENT_ROOT | AGENT_ROOT | AGENT_ROOT | AGENT_ROOT |

**Key insight:** The niche_credentials.py module reads `os.environ` for prefixed vars (e.g., `CRITICALRUSH_META_ACCESS_TOKEN`). These are only present if the root `.env` was loaded by the shell wrapper or by settings.py. CW/SR/FD daily_intel.sh scripts only load BB/.env, which does NOT contain the prefixed vars. The prefixed vars live in root `.env`.

---

## 4. Identified Issues

### CRITICAL

**C-1: CLUTCHWIRE_FB_PAGE_ACCESS_TOKEN conflict (root vs CW/.env)**
- Root `.env` has one token value
- `ClutchWire/.env` has a DIFFERENT token value
- CW daily_intel.sh does NOT load `ClutchWire/.env`, so the CW-local value is never used by the pipeline
- But if any script explicitly loads `ClutchWire/.env`, it would get the wrong (stale) token
- **Risk:** Token mismatch could cause FB publish failures or publishing to wrong page

**C-2: CRITICALRUSH_FB_PAGE_ACCESS_TOKEN conflict (root vs CR/.env)**
- Root `.env` line 84: one token
- `CriticalRush/.env` line 73: a DIFFERENT token (matches unprefixed `FB_PAGE_ACCESS_TOKEN` in CR)
- CR daily_intel.sh loads BB first, then CR (CR overrides), but the var names differ (prefixed vs unprefixed)
- **Risk:** The unprefixed `FB_PAGE_ACCESS_TOKEN` in CR/.env shadows BB's value when CR scripts run, but niche_credentials.py reads the prefixed `CRITICALRUSH_FB_PAGE_ACCESS_TOKEN` from root/.env -- these are different tokens

**C-3: CW/SR/FD daily_intel.sh do not load per-niche .env or root .env**
- They only load `BlackboxBrief/.env`
- The per-niche prefixed credentials (CLUTCHWIRE_*, SPLICEREEL_*, FRAMEDRIFT_*) live in root `.env`
- If `genlab-core/settings.py` is imported, it loads root `.env` (via AGENT_ROOT defaulting to genlab-core's parent.parent.parent.parent = GenLab root). But the AGENT_ROOT heuristic may not resolve to GenLab root for CW/SR/FD packages
- **Risk:** Per-niche credential resolution via `niche_credentials.py` may fail silently, returning empty strings

### HIGH

**H-1: Unprefixed META_ACCESS_TOKEN in CR/.env is stale**
- CR/.env has `META_ACCESS_TOKEN` pointing to what appears to be an old CriticalRush-specific token
- Root `.env` has the canonical `CRITICALRUSH_META_ACCESS_TOKEN` (the fresh one)
- CR daily_intel.sh loads BB then CR, so CR's `META_ACCESS_TOKEN` shadows BB's
- Any code using the unprefixed `META_ACCESS_TOKEN` when running as CR would get the stale CR token instead of BB's token
- **Risk:** Cross-channel publishing confusion

**H-2: Unprefixed FB_PAGE_ACCESS_TOKEN in BB/.env is stale/deprecated**
- BB/.env line 83: `FB_PAGE_ACCESS_TOKEN` -- comment says "The People's Democracy" page (old name)
- Root `.env` line 100: `BLACKBOXBRIEF_FB_PAGE_ACCESS_TOKEN` (prefixed, fresh)
- Both tokens appear to be different values
- **Risk:** BB publish using unprefixed var gets wrong token. However, niche_credentials.py maps ai_creators to BLACKBOXBRIEF prefix, so the prefixed var is used for publishing

**H-3: BB/.env contains META_IG_APP_ID and META_IG_APP_SECRET for a DIFFERENT Meta app**
- BB/.env: `META_IG_APP_ID=1416127452837173` (different app)
- Root/.env: `FB_APP_ID=2203397347132949` (GenLab Publisher app)
- These are two different Meta apps. If any code mixes them up, API calls could fail

### MEDIUM

**M-1: SPLICEREEL/FRAMEDRIFT/CLUTCHWIRE .env files have FB tokens that differ from root**
- Each channel .env has its own `{NICHE}_FB_PAGE_ACCESS_TOKEN`
- Root .env also has `{NICHE}_FB_PAGE_ACCESS_TOKEN`
- The values are DIFFERENT -- one set is probably stale
- Since daily_intel.sh for CW/SR/FD does not load these channel .env files, the channel-local tokens are effectively dead code

**M-2: Dashboard loads root then BB -- BB values override root on conflict**
- `review_server_wrapper.sh` loads root `.env` first, then BB `.env`
- On key collision, BB wins (later source overrides)
- This means the dashboard sees BB's unprefixed `FB_PAGE_ACCESS_TOKEN` (stale) instead of root's prefixed vars
- Mitigated: dashboard likely uses niche_credentials.py which reads prefixed vars

**M-3: YOUTUBE_REFRESH_TOKEN exists unprefixed in both BB and CR with different values**
- BB: `1//0g9z0bzq...` (BB channel)
- CR: `1//0gHwHhpi...` (CR channel)
- Root has prefixed versions for each niche
- If any code reads the unprefixed var, it gets whichever .env was loaded last

---

## 5. Recommendations

### Safe to Deduplicate (LOW RISK)

| Action | Rationale | Risk |
|--------|-----------|------|
| Remove Azure/SharePoint vars from CW/.env, SR/.env, FD/.env | These are identical to root and BB. CW/SR/FD daily_intel.sh loads BB/.env which already has them. | **Minimal** -- these files are not loaded by daily pipelines anyway |
| Remove ANTHROPIC_API_KEY, OPENAI_API_KEY from BB/.env | Identical to root. All scripts that load BB also eventually get root vars via settings.py | **Low** -- verify BB orchestrator.sh doesn't depend on these being in BB/.env specifically |
| Remove YOUTUBE_API_KEY, YOUTUBE_CLIENT_ID, YOUTUBE_CLIENT_SECRET from BB/.env | Identical to root. | **Low** |
| Remove PEXELS_API_KEY from CR/.env | Identical to root and BB. | **Minimal** |
| Remove FB_APP_ID, FB_APP_SECRET, META_APP_SECRET, META_WEBHOOK_VERIFY_TOKEN from BB/.env | Identical to root. | **Low** |
| Remove TMDB_API_KEY from BB/.env | Identical to root. | **Low** |

### Must Stay Per-Channel (DO NOT CONSOLIDATE)

| Variable Pattern | Reason |
|-----------------|--------|
| All `CRITICALRUSH_*` prefixed vars in root | Per-niche credential isolation -- used by niche_credentials.py |
| All `CLUTCHWIRE_*` prefixed vars in root | Same |
| All `SPLICEREEL_*` prefixed vars in root | Same |
| All `FRAMEDRIFT_*` prefixed vars in root | Same |
| All `BLACKBOXBRIEF_*` prefixed vars in root | Same |
| `AGENT_ROOT` in CR/.env | CriticalRush-specific path |
| `STEAM_API_KEY` in CR/.env | Gaming-only |
| `TIKTOK_*` in CR/.env | Gaming TikTok config |
| `PREFECT_*` in CR/.env | CR orchestration |
| `THREADS_*` in BB/.env | BB-specific Threads account (unprefixed) |
| `X_*` in BB/.env | BB-specific Twitter app (unprefixed) |
| `X_*` in CR/.env | CR-specific Twitter app (unprefixed) |
| `REVIEW_AUTH_*`, `FLASK_SECRET_KEY` in BB/.env | Dashboard auth -- only needed by BB/dashboard |

### Requires Investigation Before Action (HIGH RISK)

| Action | Issue | Investigation Needed |
|--------|-------|---------------------|
| Resolve CLUTCHWIRE_FB_PAGE_ACCESS_TOKEN conflict | Root and CW/.env have different values | Determine which token is current/valid by testing against Meta Graph API |
| Resolve CRITICALRUSH_FB_PAGE_ACCESS_TOKEN conflict | Root and CR/.env have different values | Same -- test tokens |
| Resolve SPLICEREEL_FB_PAGE_ACCESS_TOKEN conflict | Root and SR/.env have different values | Same |
| Resolve FRAMEDRIFT_FB_PAGE_ACCESS_TOKEN conflict | Root and FD/.env have different values | Same |
| Remove stale unprefixed META_ACCESS_TOKEN from CR/.env | Points to old CR token, not BB | Verify no CR code reads unprefixed META_ACCESS_TOKEN |
| Remove stale unprefixed FB_PAGE_ACCESS_TOKEN from BB/.env | Old "People's Democracy" token | Verify no BB code reads unprefixed FB_PAGE_ACCESS_TOKEN directly |
| Fix CW/SR/FD daily_intel.sh to also load root .env | Prefixed credentials not available to niche_credentials.py | Test pipeline with root .env loading added before BB/.env |

### Recommended Architecture Fix

The CW/SR/FD daily_intel.sh scripts should load the root `.env` in addition to (or instead of) BB/.env:

```
Current:  source BlackboxBrief/.env
Proposed: source GenLab/.env && source BlackboxBrief/.env
```

This ensures:
1. Per-niche prefixed vars (from root) are available to `niche_credentials.py`
2. BB's unprefixed vars (ANTHROPIC_API_KEY, etc.) are still available
3. Order: root first, BB second -- BB-specific values override where needed

---

## 6. Summary Statistics

| Metric | Count |
|--------|-------|
| Total .env files | 6 |
| Total unique variable keys | ~95 |
| Variables duplicated (same value) across 2+ files | 18 |
| Variables with CONFLICTING values across files | 12 |
| Channel .env files that are effectively unused by pipelines | 3 (CW, SR, FD) |
| Critical conflicts needing immediate resolution | 4 (FB page token mismatches) |

---

## 7. Risk Assessment Summary

| Risk Level | Count | Description |
|------------|-------|-------------|
| CRITICAL | 2 | FB page token conflicts between root and channel .env files; CW/SR/FD not loading root .env |
| HIGH | 3 | Stale unprefixed META_ACCESS_TOKEN in CR; stale FB_PAGE_ACCESS_TOKEN in BB; different Meta app IDs |
| MEDIUM | 3 | Channel .env tokens diverged from root; dashboard load order; YOUTUBE_REFRESH_TOKEN conflicts |
| LOW | 6 | Identical vars safe to deduplicate from BB/.env |

**Bottom line:** The root `.env` with prefixed credentials is the canonical source of truth for per-niche publishing. The unprefixed vars in BB/.env and CR/.env are legacy remnants that can cause confusion but are mostly bypassed by `niche_credentials.py`. The immediate risk is that CW/SR/FD shell wrappers do not load root `.env`, potentially leaving prefixed credentials unavailable. The FB page token conflicts across files should be resolved by determining which tokens are still valid.
