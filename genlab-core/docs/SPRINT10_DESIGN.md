# Sprint 10 — Threads Publishing Design Notes

## Pre-flight findings

1. **instagram_client.py flow**: Three-step (create container → poll status → publish).
   Uses `graph.facebook.com`, EAA page token, `PublishResult` dataclass, exponential
   backoff polling.

2. **publish_all_platforms.py dispatch**: `publish_to_platform()` is an if/elif chain
   routing to platform-specific handlers. ThreadPoolExecutor for concurrent publish.
   Handlers receive `(blueprint_fields, config, story_fields, dry_run)`.
   Threads and TikTok are in `enabled_platforms` config but NOT dispatched.

3. **publish_gaming_content.py**: Fully different pattern — class-based with
   `_publish_{platform}` methods, Postiz integration, 3-layer dedup, CDN upload
   for Threads video URLs. **Already has Threads wired.**

4. **CW/SR/FD**: No publishing stubs at all.

5. **BB env vars**: `META_ACCESS_TOKEN` + `META_IG_USER_ID` for Instagram.
   `THREADS_ACCESS_TOKEN` present but `THREADS_USER_ID` **missing** from BB .env.

## ThreadsClient (genlab-core) — already exists

Location: `genlab_core/publishing/threads_client.py`
- Has: `publish_video()`, `get_post_insights()`, `refresh_token()`, `ThreadsTokenManager`
- Missing: `publish_text()` — needed for BB (text/image content)
- API: `graph.threads.net/v1.0` (correct)
- Caption: truncates at 500 chars

## CDN mechanisms

- BB: `execution/utils/local_cdn.py` — litterbox.catbox.moe (24h temp URLs)
- CR: `genlab_core/publishing/cdn_upload.py` — Cloudflare tunnel (LocalCDNUpload)
- Both produce public HTTPS URLs suitable for Threads PULL_FROM_URL

## What this sprint does

1. Add `publish_text()` to existing ThreadsClient
2. Write tests for ThreadsClient
3. Wire Threads into BB's `publish_to_platform()` dispatch
4. Create publishing stubs for CW/SR/FD
5. Update .env.example files
