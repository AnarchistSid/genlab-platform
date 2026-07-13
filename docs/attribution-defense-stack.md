# Attribution Defense Stack

**Purpose**: single-page reference for the 5-layer defense that ensures every
Gen Lab reel published to real audiences carries a visible credit to the source
creator ("🎬 Original: @creator — url"). Consolidates the 21-PR arc across
2026-07-09 → 2026-07-13.

**Read this before**:

- Modifying any file listed in the Layer sections below
- Adding a new platform client
- Changing a metric that measures "attribution health"
- Loosening an env flag or threshold

Every one of these actions can silently regress the invariant. The stack
looks robust because every layer catches what the others miss — but that
also means loosening one layer only becomes visible when the whole stack
fails together.

---

## The invariant

**Every reel published to Facebook / Instagram / YouTube / Threads / X /
TikTok must include a visible credit line ("🎬 Original: @{handle} — {url}"
or "Footage: {url}") somewhere the audience can see it.**

Visible means:

- In the caption (any of `caption`, `facebook_content`, `instagram_content`,
  `threads_content`, `youtube_content`, `twitter_content`, `tiktok_content`)
- OR burned into the video frame (survives platform-side caption edits)

Ideally both. The frame watermark (Layer 6) is our belt against caption
edits by operators or platform algorithms.

---

## Why 5 layers?

Each layer catches a different class of failure. If any single layer were
"the fix," we'd rely on it — but real failures come from unexpected code
paths (fetcher drops a field, writer fallback bypasses the wire, operator
manually edits a caption to remove the marker). Defense-in-depth ensures
that even when one layer misfires, another catches it.

The 2026-07-13 audit exposed that **layers were all present but the wire
between them was broken for weeks**. Layer 5's tightening (PR #776) made
this visible for the first time — see the class-of-bug section below.

---

## Layer 1 — Fetcher gate

**File**: `genlab-core/src/genlab_core/media/trending_video_fetcher.py`
**Function**: `TrendingVideoFetcher._filter_missing_channel_metadata` (line 494)
**PR**: [#766](https://github.com/AnarchistSid/genlab-platform/pull/766)

**What it catches**: YouTube trending clips that arrive without `channel_id`
or `channel_name`. These would produce blueprints with no source-creator
data — nothing to credit.

**How it fails**:

```python
if not v.channel_id or not v.channel_name:
    # DEBUG: dropped candidate — flip GENLAB_ATTRIBUTION_LAYER1_ALLOW_MISSING=1
    # to accept anyway (for niches where YT metadata is unreliable)
    log.debug(...)
    continue
```

**Bypass**: `GENLAB_ATTRIBUTION_LAYER1_ALLOW_MISSING=1` (env var, off by
default). Only flip in emergency — bypassing means the whole downstream
stack has no data to build credit from.

**Wire pin**: `genlab-core/tests/media/test_trending_video_fetcher_channel_gate.py`

---

## Layer 2 — Persist gate

**File**: `genlab-core/src/genlab_core/pipeline/stages/push_to_backlog.py`
**Function**: `PushToBacklog._credit` helper + `_source_channel_id` persistence (line 1922)
**PR**: [#762](https://github.com/AnarchistSid/genlab-platform/pull/762) +
[#764](https://github.com/AnarchistSid/genlab-platform/pull/764)

**What it catches**: blueprints where the writer produced a caption but
didn't append `content["source_attribution"]`, OR the story didn't carry
`channel_id`. Fires at DB persist time — before the row hits the backlog.

**How it works**:

```python
_src_attr = content.get("source_attribution", "") or ""
def _credit(text: str) -> str:
    # Idempotent — same marker won't get appended twice
    if not _src_attr or _src_attr in text:
        return text
    return text.rstrip() + "\n\n" + _src_attr

fields = {
    "caption": _credit(ig.get("caption", "")),
    ...
    "source_channel_id": story.get("source_channel_id") or story.get("channel_id"),
    "source_channel_title": story.get("channel_name") or story.get("source_channel_title"),
}
```

**Bypass**: `GENLAB_ATTRIBUTION_LAYER2_ALLOW_MISSING=1` (off by default).
When flipped, blueprints without `source_channel_id` are still allowed
through — for legacy re-publish paths that lack YT metadata.

**Wire pin**: `genlab-core/tests/pipeline/stages/test_push_to_backlog_credit_wire.py`

---

## Layer 3 — Policy gate

**File**: `genlab-core/src/genlab_core/compliance/copyright_safety.py`
**Function**: `check_copyright_attribution(blueprint, platform, niche_id)` (line 201)
**PR**: [#767](https://github.com/AnarchistSid/genlab-platform/pull/767) +
[#770](https://github.com/AnarchistSid/genlab-platform/pull/770) (Twitch fallback)

**What it catches**: publish-time policy check — every publisher client
calls this before hitting the platform API. Warns or blocks based on
whether `format_source_attribution(blueprint)` can produce a URL from
`(video_id, source)` OR the Twitch fallback (`video_url` starts with
`https://twitch.tv/`).

**How it fails**:

```python
# WARN (default): logs compliance_events row
# BLOCK (LAYER3_ENFORCE=1): raises PolicyViolation, publish aborts
```

**Bypass**: `GENLAB_ATTRIBUTION_LAYER3_ENFORCE=0` (default — WARN only).
When `=1`, blocks publishes with no derivable source URL. Currently OFF in
prod; enable after Layer 4 is confirmed clean for 24-48h.

**Wire pin**: `genlab-core/tests/compliance/test_copyright_safety.py::TestPolicyGate`

---

## Layer 4 — Publisher validation

**Files**:

- `genlab-core/src/genlab_core/platforms/facebook.py:143`
- `genlab-core/src/genlab_core/platforms/instagram.py:200`
- `genlab-core/src/genlab_core/platforms/youtube.py:348`
- `genlab-core/src/genlab_core/platforms/threads.py:175`
- `genlab-core/src/genlab_core/platforms/x_twitter.py:378`
- `genlab-core/src/genlab_core/publishing/tiktok_client.py:106`

**Function**: `validate_caption_has_attribution(caption, source_url=None)` in `genlab_core.platforms.caption_validation`

**PRs**: [#768](https://github.com/AnarchistSid/genlab-platform/pull/768) (initial 4 clients) +
[#776](https://github.com/AnarchistSid/genlab-platform/pull/776) (tightening — source_url escape hatch removed) +
[#777](https://github.com/AnarchistSid/genlab-platform/pull/777) (X/Twitter + TikTok added, behavioral tests)

**What it catches**: last-line-of-defense at the API-POST boundary.
Each platform client calls the validator before sending the caption to
the platform. If the caption lacks a `🎬 Original:` or `Footage:` marker,
either warns (default) or blocks the publish outright.

**The critical function** (`caption_validation.py:71`):

```python
def validate_caption_has_attribution(
    caption: str,
    *,
    source_url: str | None = None,  # noqa: ARG001 — reserved for future
) -> tuple[bool, str | None]:
    """is_valid is True ONLY when the CAPTION contains a credit marker."""
    lowered = (caption or "").lower()
    if _MARKER_ORIGINAL in lowered or _MARKER_FOOTAGE in lowered:
        return (True, None)
    return (False, "missing_attribution_line")
```

**Post-2026-07-13 audit**: the `source_url` parameter is retained in the
signature but no longer satisfies validation. The Twitch directory URL
was satisfying the check while shipping empty-of-credit captions — that
whole escape hatch is gone. See PR #776 for the full rationale.

**Bypass**: `GENLAB_ATTRIBUTION_LAYER4_BLOCK=1` (off by default = WARN).
Enable after tomorrow's 12:00 IST fire proves Layer 5 is healthy — see
`/opt/genlab/scripts/verify_writer_wire_and_flip_l4.sh` for the guarded
flip runbook.

**Wire pin**: `genlab-core/tests/platforms/test_caption_validation.py`
(source pins × 6 clients + behavioral pins × 6 that exercise the actual
short-circuit).

---

## Layer 5 — Observability + alerting

**Files**:

- `dashboard/server/core/attribution_health.py` (metric SQL)
- `dashboard/server/api/attribution_health.py` (`/api/v1/attribution-health/stats`)
- `genlab-core/src/genlab_core/monitoring/attribution_health_monitor.py` (timer-driven alerter)
- `dashboard/frontend/src/views/mission-control/AttributionHealthCard.tsx` (UI)

**PRs**: [#769](https://github.com/AnarchistSid/genlab-platform/pull/769) (endpoint) +
[#771](https://github.com/AnarchistSid/genlab-platform/pull/771) (React card) +
[#775](https://github.com/AnarchistSid/genlab-platform/pull/775) (monitor timer) +
[#776](https://github.com/AnarchistSid/genlab-platform/pull/776) (tightening — removed source_channel_id proxy)

**What it does**: measures the audience-facing invariant directly. Queries
`blueprints WHERE status='PUBLISHED' AND updated_at > NOW() - INTERVAL 'N hours'`
and counts how many have `🎬 Original:` or `Footage:` in ANY of 6 caption
fields.

**Metric SQL** (`attribution_health.py`):

```sql
COUNT(*) FILTER (
    WHERE COALESCE(caption, '') LIKE '%🎬 Original:%'
       OR COALESCE(caption, '') LIKE '%Footage:%'
       OR COALESCE(extra->>'facebook_content', '') LIKE '%🎬 Original:%'
       OR COALESCE(extra->>'facebook_content', '') LIKE '%Footage:%'
       OR COALESCE(extra->>'threads_content', '') LIKE '%🎬 Original:%'
       OR COALESCE(extra->>'threads_content', '') LIKE '%Footage:%'
       OR COALESCE(extra->>'youtube_content', '') LIKE '%🎬 Original:%'
       OR COALESCE(extra->>'youtube_content', '') LIKE '%Footage:%'
       OR COALESCE(extra->>'twitter_content', '') LIKE '%🎬 Original:%'
       OR COALESCE(extra->>'twitter_content', '') LIKE '%Footage:%'
       OR COALESCE(extra->>'tiktok_content', '') LIKE '%🎬 Original:%'
       OR COALESCE(extra->>'tiktok_content', '') LIKE '%Footage:%'
) AS with_attribution
```

**Thresholds** (post-2026-07-13 tightening — see class-of-bug section):

- **Healthy** (`_HEALTHY_PCT = 100.0`): every publish credited
- **Caution** (`_CAUTION_PCT = 99.0`): any single miss

**Two consumers, tiered noise tolerance**:

| Consumer | Threshold | Cadence | Purpose |
|---|---|---|---|
| `attribution_health_monitor.timer` | 99% | 30 min | Pages on any single-miss |
| `post_deploy_verify.sh` check #8 | 80% | On deploy | Catches class-of-bug regressions |
| Mission Control React card | Descriptive | 60s polling | Operator dashboard |

**Wire pin**: `dashboard/tests/test_attribution_health_layer5.py`

---

## Layer 6 — Frame watermark (bonus layer)

**File**: `genlab-core/src/genlab_core/media/frame_compositor.py`
**Function**: `FrameCompositor._build_watermark_filter(source_credit, y, font, in_label, out_label)` (line 553)
**PR**: [#778](https://github.com/AnarchistSid/genlab-platform/pull/778)

**What it does**: FFmpeg drawtext filter burns `"Original: {creator}"` at
bottom-right of the video canvas area (NOT the pillarbox) during render.
Survives platform-side caption edits — the caption might be edited to
remove the credit line but the video frame cannot.

**Wire**: `base_visual_render._compose_frame` extracts
`story.source_channel_handle → channel_name → source_channel_title` and
passes it as `source_credit` kwarg to `compositor.compose()`. Empty string
skips the watermark entirely.

**Geometry constants** (`frame_compositor.py:117-124`):

```python
WATERMARK_FONT_SIZE = 18
WATERMARK_OPACITY = 0.55
L_WATERMARK_Y = L_VIDEO_Y + L_VIDEO_H - WATERMARK_BOTTOM_INSET  # landscape
S_WATERMARK_Y = S_VIDEO_Y + S_VIDEO_H - WATERMARK_BOTTOM_INSET  # square
P_WATERMARK_Y = CANVAS_H - WATERMARK_BOTTOM_INSET - WATERMARK_FONT_SIZE - 10  # portrait
```

**Wire pin**: `genlab-core/tests/media/test_frame_compositor_watermark.py`
(geometry pins ensure Y anchor stays inside the video area, not the pillarbox)

---

## Data flow: end-to-end

```
YouTube trending API
  ↓
TrendingVideoFetcher.fetch_trending() → TrendingVideo(channel_id, channel_name, ...)
  ↓ [LAYER 1 gate — drop candidates missing channel data]
  ↓
TrendingVideo.to_story() → { channel_id, channel_name, source_url, ... }
  ↓
context["stories"] (in-memory dict)
  ↓
base_writing._story_to_video_dict(story) → video dict for LLM
  ↓ Bug A fix (2026-07-13): channel_name is read HERE (not story.source)
  ↓
video_content_writer.write_video_content(video, ...)
  ↓ sets content["source_attribution"] = format_source_attribution({...})
  ↓
base_writing._write_story_llm() → story["content"]
  ↓ Bug C fix (2026-07-13): content["source_attribution"] MUST propagate here
  ↓
push_to_backlog._credit(caption) [LAYER 2 wire]
  ↓ appends "🎬 Original:" line to every caption field
  ↓
publisher.publish(payload)
  ↓ [LAYER 3 check_copyright_attribution — WARN or BLOCK]
  ↓ [LAYER 4 validate_caption_has_attribution — WARN or BLOCK]
  ↓
Meta / YouTube / X API (audience-facing publish)
  ↓
[LAYER 5 attribution_health metric — measures 30-min window]
[LAYER 6 watermark burned into video frame during render]
```

---

## Class-of-bug this stack codifies

**"Metrics that count proxy signals mask the failure they were designed to catch."**

See `memory/class-of-bug-metric-proxies-mask-audience-facing-failures.md` for
the full pattern. Applied to this stack:

**Before 2026-07-13**: Layer 5 SQL counted `source_channel_id IS NOT NULL` as
attribution — a signal populated via `push_to_backlog` reading
`story.get("source_channel_id")` directly, NOT through the caption wire.
So the metric showed healthy for weeks while audiences saw uncredited
posts. The wire (Bugs A + C in `base_writing`) was silently broken —
`channel_name` never reached the writer, `source_attribution` never
reached `story["content"]`, `_credit` helper no-op'd, captions shipped
without markers.

**After PR #776**: Layer 5 removed the `source_channel_id` signal. The
metric now measures ONLY the caption content. Within 24 hours the honest
metric caught the wire that had been silently broken for weeks.

**Applied within-session (PR #782)**: `check_threads` had the same
timestamp-only proxy pattern (env-var age used as "healthy" without a
live probe). Fixed by mirroring `check_meta_token`'s live-probe pattern.

**Detection heuristic**: any metric SQL that uses `IS NOT NULL` or
disjunct unions where each disjunct comes from a DIFFERENT populating
code path than the audience-facing invariant is a masking risk.

---

## Environment flags (kill switches)

All flags default OFF. Enable per operator judgment; never enable in a
PR without a concrete plan for the next 24h of validation.

| Flag | Layer | Default | When to enable |
|---|---|---|---|
| `GENLAB_ATTRIBUTION_LAYER1_ALLOW_MISSING` | 1 | 0 (enforce) | Emergency only — allows candidates without channel data |
| `GENLAB_ATTRIBUTION_LAYER2_ALLOW_MISSING` | 2 | 0 (enforce) | Same as Layer 1 |
| `GENLAB_ATTRIBUTION_LAYER3_ENFORCE` | 3 | 0 (warn only) | After Layer 4 clean for 24-48h |
| `GENLAB_ATTRIBUTION_LAYER4_BLOCK` | 4 | 0 (warn only) | After tomorrow's 12:00 IST fire proves Layer 5 healthy |

**Global kill**: `touch /opt/genlab/.runtime/attribution_kill_switch` disables
all attribution-related enforcement (Layers 1-4 fall through to WARN mode).
Use only in incidents.

---

## Verify + flip runbooks

**`scripts/verify_writer_wire_and_flip_l4.sh`**: one-command guarded flip
for Layer 4. Queries Layer 5 6h window; if ≥80% healthy, prompts (or
`--yes`) before flipping `LAYER4_BLOCK=1`; if <80%, fails LOUD with a
trace playbook baked into stdout. Idempotent, never reads `.env` content
into a shell variable.

**Usage**:

```bash
# on prod, ~30 min after publisher fire
/opt/genlab/scripts/verify_writer_wire_and_flip_l4.sh          # verify + prompt
/opt/genlab/scripts/verify_writer_wire_and_flip_l4.sh --yes    # verify + auto-flip
/opt/genlab/scripts/verify_writer_wire_and_flip_l4.sh --dry    # preview only
```

**`scripts/retro_credit_uncredited_posts.py`**: retroactive credit-line
editor for historical uncredited posts. State-file-backed
(`/opt/genlab/.runtime/retro_credit_state.json`) so partial runs continue
where they left off. Fires autonomously via `genlab-retro-credit.timer`
every 90 min until state file catches all targets.

---

## PR arc — the 21 shipped

Sorted chronologically by prod HEAD progression:

**2026-07-09 → 2026-07-12 (pre-audit)**:
[#761](https://github.com/AnarchistSid/genlab-platform/pull/761) writer wire ·
[#762](https://github.com/AnarchistSid/genlab-platform/pull/762) Layer 2 persist gate ·
[#763](https://github.com/AnarchistSid/genlab-platform/pull/763) Layer 1 fetcher gate ·
[#764](https://github.com/AnarchistSid/genlab-platform/pull/764) publisher backstop ·
[#765](https://github.com/AnarchistSid/genlab-platform/pull/765) fb_survival_check tightening ·
[#766](https://github.com/AnarchistSid/genlab-platform/pull/766) fetcher gate PR ·
[#767](https://github.com/AnarchistSid/genlab-platform/pull/767) Layer 3 policy gate ·
[#768](https://github.com/AnarchistSid/genlab-platform/pull/768) Layer 4 initial 4 clients ·
[#769](https://github.com/AnarchistSid/genlab-platform/pull/769) Layer 5 backend endpoint ·
[#770](https://github.com/AnarchistSid/genlab-platform/pull/770) Twitch source recognition ·
[#771](https://github.com/AnarchistSid/genlab-platform/pull/771) AttributionHealthCard React ·
[#772](https://github.com/AnarchistSid/genlab-platform/pull/772) Vite TS build fix ·
[#773](https://github.com/AnarchistSid/genlab-platform/pull/773) audio bitrate fix (2207082) ·
[#774](https://github.com/AnarchistSid/genlab-platform/pull/774) copyright Twitch fallback ·
[#775](https://github.com/AnarchistSid/genlab-platform/pull/775) attribution_health_monitor timer

**2026-07-13 (audit day)**:
[#776](https://github.com/AnarchistSid/genlab-platform/pull/776) tightening (100/99, source_url escape hatch removed) ·
[#777](https://github.com/AnarchistSid/genlab-platform/pull/777) 7 gaps closed (X/Twitter+TikTok L4, SQL union, refusal all-fields, fallback credit, dashboard revalidate, block-branch tests) ·
[#778](https://github.com/AnarchistSid/genlab-platform/pull/778) G9 frame watermark ·
[#779](https://github.com/AnarchistSid/genlab-platform/pull/779) W1 writer wire fix (Bugs A + C) ·
[#780](https://github.com/AnarchistSid/genlab-platform/pull/780) Bug B StoryStore field persist ·
[#781](https://github.com/AnarchistSid/genlab-platform/pull/781) post_deploy_verify check #8 ·
[#782](https://github.com/AnarchistSid/genlab-platform/pull/782) check_threads live-probe (class-of-bug applied) ·
[#783](https://github.com/AnarchistSid/genlab-platform/pull/783) verify-and-flip runbook ·
[#784](https://github.com/AnarchistSid/genlab-platform/pull/784) Improvement B pre-render quality gate ·
[#785](https://github.com/AnarchistSid/genlab-platform/pull/785) Improvement A skip thin-context ·
[#786](https://github.com/AnarchistSid/genlab-platform/pull/786) auto-approver stall fix (0.85→0.80) ·
[#787](https://github.com/AnarchistSid/genlab-platform/pull/787) retro_credit script + state tracking ·
[#788](https://github.com/AnarchistSid/genlab-platform/pull/788) genlab-retro-credit.timer

---

## What NOT to do

1. **Never re-add `source_channel_id IS NOT NULL` as a Layer 5 attribution
   signal.** It's a proxy for the wrong invariant — see the class-of-bug
   section.
2. **Never lower `_HEALTHY_PCT` below 100 or `_CAUTION_PCT` below 99.**
   These were tightened after empirical proof that even 1-in-20 uncredited
   is a real audience-facing failure. Loosen only with a memo explaining
   why + supporting metric evidence.
3. **Never wire a new platform client without adding a Layer 4 call.**
   The behavioral pin tests exist to catch this exact regression.
4. **Never flip `LAYER4_BLOCK` or `LAYER3_ENFORCE` without a 24h
   observability window afterward.** Enabling enforcement mid-day means
   in-flight blueprints could hard-fail.
5. **Never `.retro_credit_state.json` reset without operator sign-off.**
   Resetting means the retro script starts editing already-credited posts,
   which double-appends the marker.

---

## Contact

For questions on this stack, grep the codebase for `[layer4]`, `[layer3]`,
`[attribution]`, or `_credit(`. Every change to this doc should be paired
with a PR that also updates the file:line references above.

Last significant reorganization: 2026-07-13 (audit day). Next scheduled
review: after tomorrow's 12:00 IST fire validates the full stack
end-to-end.
