# WS4: Publishing Hardening — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix per-niche FB token resolution, gaming FFmpeg timeouts, plist misconfigs, and wire hook_validator into CW/SR/FD.

**Architecture:** Targeted fixes across niche_credentials, render_gaming_video, 5 plists, and 3 hook strategy files. No new modules.

**Tech Stack:** Python, FFmpeg, launchd, SharePoint

**Spec:** `docs/superpowers/specs/2026-03-17-ws4-publishing-hardening-design.md`

---

## Chunk 1: FB Token Fix + Gaming FFmpeg

### Task 1: Fix niche_credentials prefix map

**Files:**
- Modify: `genlab-core/src/genlab_core/publishing/niche_credentials.py:21-28`
- Test: `genlab-core/tests/publishing/test_niche_credentials.py`

- [ ] **Step 1: Write failing test**

```python
# genlab-core/tests/publishing/test_niche_credentials.py (add to existing)
import os
from unittest.mock import patch
from genlab_core.publishing.niche_credentials import resolve_meta_credentials, NICHE_CREDENTIAL_PREFIXES


def test_all_niches_have_prefix():
    """Every supported niche must have a credential prefix."""
    required = {"sports", "movies", "anime", "gaming", "ai_creators"}
    assert required.issubset(set(NICHE_CREDENTIAL_PREFIXES.keys()))


def test_ai_creators_uses_blackboxbrief():
    assert NICHE_CREDENTIAL_PREFIXES["ai_creators"] == "BLACKBOXBRIEF"


def test_resolve_meta_for_gaming():
    with patch.dict(os.environ, {
        "CRITICALRUSH_META_ACCESS_TOKEN": "test_token",
        "CRITICALRUSH_IG_USER_ID": "12345",
        "CRITICALRUSH_FB_PAGE_ACCESS_TOKEN": "fb_token",
        "CRITICALRUSH_FB_PAGE_ID": "67890",
    }):
        creds = resolve_meta_credentials("gaming")
        assert creds["ig_access_token"] == "test_token"
        assert creds["fb_page_id"] == "67890"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/anarchistsid/GenLab && uv run --package genlab-core pytest genlab-core/tests/publishing/test_niche_credentials.py -v --tb=short -k "all_niches_have_prefix or ai_creators_uses"
```

Expected: FAIL (ai_creators not in NICHE_CREDENTIAL_PREFIXES)

- [ ] **Step 3: Add ai_creators to prefix map**

In `niche_credentials.py` line 21-28, add:

```python
NICHE_CREDENTIAL_PREFIXES: Dict[str, str] = {
    "sports": "CLUTCHWIRE",
    "movies": "SPLICEREEL",
    "anime": "FRAMEDRIFT",
    "gaming": "CRITICALRUSH",
    "ai_creators": "BLACKBOXBRIEF",
    "ai_tech": "BLACKBOXBRIEF",  # alias
}
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/anarchistsid/GenLab && uv run --package genlab-core pytest genlab-core/tests/publishing/test_niche_credentials.py -v --tb=short
```

- [ ] **Step 5: Commit**

```bash
git add genlab-core/src/genlab_core/publishing/niche_credentials.py genlab-core/tests/publishing/test_niche_credentials.py
git commit -m "fix(creds): add ai_creators + ai_tech to NICHE_CREDENTIAL_PREFIXES

Maps to BLACKBOXBRIEF_ env var prefix. Was missing, causing BB credential
resolution to fall through to empty string."
```

### Task 2: Fix gaming FFmpeg timeout + clip truncation

**Files:**
- Modify: `CriticalRush/niches/gaming/stages/render_gaming_video.py:826-840`

- [ ] **Step 1: Fix timeout calculation and add clip truncation**

At line 826, replace the hardcoded timeout loop:

```python
# Before the render loop, truncate clips >60s
clip_duration = get_duration(clip_path) if clip_path else 0
if clip_duration and clip_duration > 60:
    logger.info("[RENDER] Truncating clip from %.0fs to 60s: %s",
                clip_duration, clip_path)
    truncated = clip_path.replace(".mp4", "_trunc.mp4")
    trunc_cmd = [
        "ffmpeg", "-y", "-i", clip_path,
        "-t", "60", "-c", "copy", truncated,
    ]
    subprocess.run(trunc_cmd, capture_output=True, timeout=30)
    if os.path.exists(truncated) and os.path.getsize(truncated) > 0:
        clip_path = truncated
        clip_duration = 60

# Dynamic timeout: 3x duration, minimum 300s
render_timeout = max(300, int((clip_duration or 60) * 3))

for preset, timeout_mult in [("medium", 1.0), ("ultrafast", 1.5)]:
    actual_timeout = int(render_timeout * timeout_mult)
    full_cmd = cmd + ["-preset", preset] + encode_args
    try:
        result = subprocess.run(
            full_cmd, capture_output=True, text=True, timeout=actual_timeout,
        )
```

- [ ] **Step 2: Run existing gaming tests**

```bash
cd /Users/anarchistsid/GenLab && uv run --package genlab-core pytest CriticalRush/tests/ -x -q --tb=short 2>&1 | tail -10
```

- [ ] **Step 3: Commit**

```bash
git add CriticalRush/niches/gaming/stages/render_gaming_video.py
git commit -m "fix(gaming): dynamic FFmpeg timeout + truncate clips >60s

Timeout = max(300, duration * 3) instead of hardcoded 180.
Clips >60s truncated before render per spec (15-60s target)."
```

---

## Chunk 2: Plist Fixes + Hook Validator Wiring

### Task 3: Fix 5 LaunchAgent plists

- [ ] **Step 1: Fix each plist**

For each plist that needs HOME:
```xml
<key>EnvironmentVariables</key>
<dict>
    <key>HOME</key>
    <string>/Users/anarchistsid</string>
    <!-- ... existing vars ... -->
</dict>
```

For those needing WorkingDirectory:
```xml
<key>WorkingDirectory</key>
<string>/Users/anarchistsid/GenLab</string>
```

Fix ENGAGEMENT_DISPATCH in twitter ai-news poller:
```xml
<key>ENGAGEMENT_DISPATCH</key>
<string>true</string>
```

- [ ] **Step 2: Reload all modified plists**

```bash
for plist in com.genlab.cleanup-runs com.genlab.criticalrush-cleanup com.genlab.review-server com.genlab.review-tunnel com.genlab.engagement.poller.twitter.ai-news; do
  launchctl unload ~/Library/LaunchAgents/${plist}.plist 2>/dev/null
  launchctl load ~/Library/LaunchAgents/${plist}.plist 2>/dev/null
done
launchctl list | grep -E "cleanup|review|twitter.ai" | awk '{printf "%-6s %-4s %s\n", $1, $2, $3}'
```

- [ ] **Step 3: Commit**

```bash
git add ~/Library/LaunchAgents/com.genlab.cleanup-runs.plist \
  ~/Library/LaunchAgents/com.genlab.criticalrush-cleanup.plist \
  ~/Library/LaunchAgents/com.genlab.review-server.plist \
  ~/Library/LaunchAgents/com.genlab.review-tunnel.plist \
  ~/Library/LaunchAgents/com.genlab.engagement.poller.twitter.ai-news.plist
git commit -m "fix(ops): add HOME/WorkingDirectory to 5 plists + enable ENGAGEMENT_DISPATCH"
```

### Task 4: Wire hook_validator into CW, SR, FD

**Files:**
- Modify: `ClutchWire/cw_strategies/hooks.py:164` (SportHookStrategy.execute)
- Modify: `SpliceReel/sr_strategies/hooks.py:183` (MovieHookStrategy.execute)
- Modify: `FrameDrift/fd_strategies/hooks.py:170` (AnimeHookStrategy.execute)

- [ ] **Step 1: Write failing test**

```python
# ClutchWire/tests/test_hook_validation.py
def test_hook_over_60_chars_gets_rejected():
    """Hooks >60 characters should be flagged by validator."""
    from genlab_core.intelligence.hook_validator import HookValidator
    validator = HookValidator()
    result = validator.validate("A" * 65, platform="instagram")
    assert not result.valid or len(result.suggested) <= 60
```

- [ ] **Step 2: Add hook validation to each niche's execute() method**

In each hook strategy's `execute()`, after hooks are generated, add validation:

```python
from genlab_core.intelligence.hook_validator import HookValidator

# Inside execute(), after hook generation loop:
validator = HookValidator()
for bp in context.get("blueprints", []):
    hook = bp.get("hook", "")
    if not hook:
        continue
    result = validator.validate(hook, platform="instagram")
    if not result.valid:
        logger.warning("[HookValidator] Rejected: '%s' — %s", hook[:50], result.reason)
        if result.suggested:
            bp["hook"] = result.suggested
```

- [ ] **Step 3: Run niche tests**

```bash
cd /Users/anarchistsid/GenLab && uv run --package genlab-core pytest ClutchWire/tests/ SpliceReel/tests/ FrameDrift/tests/ -x -q --tb=short 2>&1 | tail -10
```

- [ ] **Step 4: Commit**

```bash
git add ClutchWire/cw_strategies/hooks.py SpliceReel/sr_strategies/hooks.py FrameDrift/fd_strategies/hooks.py
git commit -m "feat(hooks): wire HookValidator into CW, SR, FD hook strategies

All 5 niches now validate hooks (length, banned patterns, fan voice).
Rejected hooks get replaced with validator suggestions."
```
