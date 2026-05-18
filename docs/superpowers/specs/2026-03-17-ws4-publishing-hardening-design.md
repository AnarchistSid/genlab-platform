# WS4: Publishing Hardening

**Goal**: G1 Publishing 55% → 75%
**Effort**: ~4h
**Dependencies**: None

## Problem

1. Per-niche FB tokens return HTTP 400 (4/5 niches)
2. Gaming FFmpeg renders timeout (180s) on long source clips (169-188s)
3. 5 LaunchAgent plists have misconfigurations (missing HOME, WorkingDirectory)
4. Twitter ai-news poller has ENGAGEMENT_DISPATCH=false
5. hook_validator not wired into CW, SR, FD pipelines (B17/B18)

## Changes

### 1. Fix FB token resolution — `niche_credentials.py`

Add diagnostic logging to `resolve_meta_credentials()`:

```python
def resolve_meta_credentials(niche_id: str) -> Dict[str, str]:
    prefix = _NICHE_PREFIX_MAP.get(niche_id, niche_id.upper())
    token_key = f"{prefix}_META_ACCESS_TOKEN"
    fb_token_key = f"{prefix}_FB_PAGE_ACCESS_TOKEN"
    # Try META first, then FB_PAGE
    token = os.environ.get(token_key) or os.environ.get(fb_token_key, "")
    logger.debug("[niche_creds] %s: trying %s=%s, %s=%s",
                 niche_id, token_key, "SET" if token else "MISSING",
                 fb_token_key, "SET" if os.environ.get(fb_token_key) else "MISSING")
```

Verify `_NICHE_PREFIX_MAP` maps correctly:
- `ai_creators` → `BLACKBOXBRIEF`
- `gaming` → `CRITICALRUSH`
- `sports` → `CLUTCHWIRE`
- `movies` → `SPLICEREEL`
- `anime` → `FRAMEDRIFT`

If the map is wrong or missing, fix it. The .env uses these exact prefixes.

### 2. Fix gaming FFmpeg timeout — `render_gaming_video.py`

Current: hardcoded `timeout=180` for all clips.

Fix:
- Calculate timeout from source duration: `timeout = max(300, int(duration_seconds * 3))`
- Truncate source clips >60s to 60s before rendering (per spec: "15-60 seconds long")
- Add `-preset ultrafast` as final fallback (already partially implemented but timeout too low)

```python
# Before render
clip_duration = get_duration(clip_path)
if clip_duration > 60:
    logger.info("[Render] Truncating %s from %.0fs to 60s", clip_path, clip_duration)
    clip_path = _truncate_clip(clip_path, max_seconds=60)
    clip_duration = 60

render_timeout = max(300, int(clip_duration * 3))
```

### 3. Fix 5 plist misconfigurations

| Plist | Fix |
|---|---|
| `com.genlab.cleanup-runs.plist` | Add `HOME` to EnvironmentVariables |
| `com.genlab.criticalrush-cleanup.plist` | Add `HOME` + `WorkingDirectory` |
| `com.genlab.review-server.plist` | Add `HOME` to EnvironmentVariables |
| `com.genlab.review-tunnel.plist` | Add `HOME` + `WorkingDirectory` |
| `com.genlab.engagement.poller.twitter.ai-news.plist` | Set `ENGAGEMENT_DISPATCH=true` |

### 4. Wire hook_validator into CW, SR, FD

Each niche has a `HookStrategy` class. Add hook validation call after hook generation:

```python
# In cw_strategies/hooks.py, sr_strategies/hooks.py, fd_strategies/hooks.py
from genlab_core.intelligence.hook_validator import validate_hook

class SportHookStrategy(HookStrategy):
    def execute(self, context):
        # ... existing hook generation ...
        for blueprint in blueprints:
            hook = blueprint.get("hook", "")
            result = validate_hook(hook, niche_id=context["niche_id"])
            if not result.valid:
                logger.warning("[HookValidator] Rejected: %s — %s", hook, result.reason)
                blueprint["hook"] = result.suggested or hook  # Use suggestion or keep
```

## Files Modified

| File | Change |
|---|---|
| `genlab-core/src/genlab_core/publishing/niche_credentials.py` | Add diagnostic logging, verify prefix map |
| `CriticalRush/niches/gaming/stages/render_gaming_video.py` | Dynamic timeout + clip truncation |
| `~/Library/LaunchAgents/com.genlab.cleanup-runs.plist` | Add HOME |
| `~/Library/LaunchAgents/com.genlab.criticalrush-cleanup.plist` | Add HOME + WorkingDirectory |
| `~/Library/LaunchAgents/com.genlab.review-server.plist` | Add HOME |
| `~/Library/LaunchAgents/com.genlab.review-tunnel.plist` | Add HOME + WorkingDirectory |
| `~/Library/LaunchAgents/com.genlab.engagement.poller.twitter.ai-news.plist` | ENGAGEMENT_DISPATCH=true |
| `ClutchWire/cw_strategies/hooks.py` | Wire hook_validator |
| `SpliceReel/sr_strategies/hooks.py` | Wire hook_validator |
| `FrameDrift/fd_strategies/hooks.py` | Wire hook_validator |

## Validation

- FB token test: `python -c "from genlab_core.publishing.niche_credentials import resolve_meta_credentials; print(resolve_meta_credentials('gaming'))"` returns valid token
- Gaming pipeline renders at least 1 video (was 0/2)
- `launchctl list | grep genlab` shows 0 non-zero exits
- Hook validator test: generate hook >60 chars → rejected and replaced
- All existing tests pass

## Risks

- Clip truncation may cut important content — mitigated by choosing the "best 60s" segment (highest action density) rather than simple head truncation
- Plist changes require `launchctl unload + load` to take effect
