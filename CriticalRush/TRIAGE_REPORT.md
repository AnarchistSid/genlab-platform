# CriticalRush Triage Report

**Run analysed:** `gaming_20260307_201054`
**Date:** 2026-03-08
**Type:** Read-only audit — no code changes made

---

## 1. DUPLICATE ROOT CAUSE

**Severity: HIGH**

The publisher has **no cross-run duplicate detection**. The only dedup mechanism is an in-memory `already_published_paths` set that tracks `(platform, rendered_path)` tuples within a single run. It is cleared when the process exits.

There is no check against SharePoint, the PendingFeedback list, or any persistent store before publishing. If the same RSS story appears in consecutive runs (common for multi-day stories like the Marathon microtransaction backlash — present as 2 near-identical articles in this run), it will be fetched, scored, rendered, and published again.

**Dedup that does exist (but doesn't prevent this):**

| Layer | What it catches | What it misses |
|---|---|---|
| RSS dedup (FetchGamingStories) | Same URL within a run | Same story from different outlets |
| Title dedup (FilterGamingStories) | Jaccard/TF-IDF similarity within a run | Cross-run repeats |
| Video hash dedup (RenderGamingVideo) | Same clip file reused | Same story re-rendered with different clip |
| In-run publisher set | Same file published twice in one run | Same content across runs |

**Evidence from this run:** Two Marathon stories published ("Marathon Is Already Patching…" and "Bungie Responds Quickly to Marathon Microtransactions…") — same event, different outlets. The within-run title dedup (Jaccard 0.80 / TF-IDF 0.70 thresholds) was not enough to catch them.

**Fix needed:** Before publishing, query SharePoint PendingFeedback for existing posts on the same platform with matching content_id or title similarity. Skip if found.

---

## 2. FACEBOOK / X ROOT CAUSE

### Facebook: 400 Bad Request

**Severity: HIGH**

All 5 stories failed on Facebook with `400 Bad Request` during video upload. The legacy publisher uploads via `POST /{page_id}/videos` with multipart form data. The error is almost certainly a token/permissions issue:

- The Facebook Page Access Token may have expired or lack `publish_video` permission.
- The Graph API version (v21.0 used for Instagram) may not be explicitly set for the Facebook endpoint.
- No detailed error body is logged — the publisher catches `Exception` and stores `str(e)`, losing Facebook's error JSON which would contain the specific sub-error code.

**Fix needed:** Log the full Facebook error response body. Verify the Page Access Token has `pages_manage_posts` and `publish_video` permissions. Confirm the page ID is correct.

### X/Twitter: Not enabled

**Severity: LOW (intentional)**

X/Twitter is not in `niche.yaml` → `platforms_enabled: [youtube, instagram, facebook]`. The publisher skips it entirely. This is by design — `publishing.yaml` lists twitter in `platforms.enabled` but `niche.yaml` overrides.

No action needed unless X/Twitter publishing is desired.

---

## 3. VIDEO FRAMING ROOT CAUSE

**Severity: LOW**

All clips are normalised to 9:16 (1080×1920) universally via FFmpeg `scale` + `crop`. There is no per-platform aspect ratio branching. This is acceptable because all target platforms (YouTube Shorts, Instagram Reels, Facebook Reels) use 9:16 portrait format — confirmed by `platform_specs.yaml`.

**Actual framing issue:** The `scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920` filter chain crops non-9:16 source clips (e.g., 16:9 gameplay footage from YouTube/Pexels) to fit. This means significant content loss on the left/right edges. The blurred-pillarbox fallback mentioned in comments is actually still a crop — both the `[bg]` and `[fg]` streams use `crop=1080:1920`, not `pad`.

**Hook text overlay:** Wrapped at 32 chars/line, max 80 chars total. Positioned at y=120 (near top). Semi-transparent black box background. This works for short hooks but may clip on edge devices with notch cutouts (safe zone typically starts at y=150+).

**Fix priority:** Low. The crop approach works for Pexels B-roll. For actual gameplay clips from YouTube, a pad-based approach (pillarbox with blurred edges) would preserve more content. Hook text y-position could be bumped to 150.

---

## 4. HOOK QUALITY ROOT CAUSE

**Severity: MEDIUM**

The hook generation pipeline has three quality gates:

1. **LLM generation** (`write_gaming_content.py`): Claude Haiku generates hooks via a structured prompt that forbids "BREAKING:", "JUST IN:", "announces", "reveals". Max 8 words per line enforced by `content_prompts.yaml`.

2. **HookValidator** (`genlab_core.intelligence.hook_validator`): Validates hook quality (length, formatting, engagement signals). If the primary hook fails, tries `tweet` and `youtube.title` as fallback candidates.

3. **Platform rules** (`enforce_platform_rules`): Applied twice — once in WriteGamingContent, once in AdaptGamingContent. Enforces YouTube question format, Instagram CTA, etc.

**What's missing:**

- **No markdown stripping in hooks:** While the content writer strips markdown fences from the overall LLM response, there's no explicit check for `**`, `##`, `>`, or other markdown artifacts *within* the hook text itself. If the LLM outputs `**This changes everything**` as a hook, the asterisks render as literal text in the video overlay.
- **No Reddit-formatting check:** Stories sourced from Reddit RSS may carry formatting artifacts in titles that propagate to hooks.
- **Validation failure logged but not blocking:** The run report shows `"validation_failures": 1` in content_writing stats, but all 5 stories still have `"has_hook": true`. A validation failure triggers a retry or fallback template — it doesn't drop the story.
- **content_type always "unknown":** The publisher sets `content_type = story.get("content_type", "unknown")` for feedback registration, but the gaming pipeline never populates `content_type` on the story dict. This means the bandit can't learn which content types perform better.

**Fix needed:** Add a markdown-stripping pass on hook text before rendering. Set `content_type` on stories (e.g., "rss_news", "twitch_trending", "steam_trending").

---

## 5. FOCUS REVIEW ROOT CAUSE

**Severity: MEDIUM**

The Focus Review system is fully functional and gaming-aware. Gaming blueprints pushed by `push_to_backlog.py` have `niche_id: "gaming"` and the dashboard supports gaming as a registered niche.

**How it works:**

```
GET /api/v1/blueprints/review-queue?niche_id=gaming
```

The review queue fetches all `VISUAL_READY` blueprints with no `action_taken`, then filters client-side by `niche_id`. The dashboard's `useFocusReviewQueue()` hook reads the selected niche from `useNicheStore()`.

**The problem:** The `niche_id` parameter defaults to `"ai_creators"`:

```python
niche_id = request.args.get("niche_id", "ai_creators")
```

And the client-side filter also defaults missing niche_id fields to "ai_creators":

```python
if (r.get("fields", {}).get("niche_id") or "ai_creators") == niche_id
```

This means:
1. If a user opens Focus Review without selecting the "gaming" niche, they see zero gaming blueprints.
2. When "All" niches is selected, the UI maps it to `"ai_creators"` — gaming items are invisible.
3. There is no "all niches" passthrough — the niche filter is always applied.

**Additional issue:** The `push_to_backlog.py` stage sets `status: "VISUAL_READY"` on blueprints that have a rendered video. But the publisher runs *after* push-to-backlog and publishes immediately — by the time a human could review in Focus Review, the content is already live. There is no review gate before publishing.

**Fix needed:**
1. Make "All" niches actually show all niches (skip the niche_id filter).
2. Add an optional review gate: if `require_review: true` in niche config, hold blueprints in VISUAL_READY and wait for Focus Review approval before publishing.

---

## 6. PRIORITY FIX ORDER

| # | Issue | Severity | Effort | Impact |
|---|---|---|---|---|
| 1 | **Cross-run duplicate detection** | HIGH | Medium | Prevents publishing the same story repeatedly. Query PendingFeedback by title similarity or content_id before publishing. |
| 2 | **Facebook error diagnosis** | HIGH | Low | Log the full error response body, re-verify Page Access Token permissions. Could restore 1 of 3 platforms. |
| 3 | **Set content_type on stories** | MEDIUM | Low | One-line fix per source. Enables bandit learning by content type. Currently always "unknown". |
| 4 | **Markdown stripping on hook text** | MEDIUM | Low | `re.sub(r'[*#>_~`]', '', hook)` before render. Prevents formatting artifacts in video overlays. |
| 5 | **Focus Review "All" niche filter** | MEDIUM | Low | Change `"all" → "ai_creators"` mapping to skip niche_id filter entirely. Gaming items become visible. |
| 6 | **Review gate before publish** | MEDIUM | Medium | Optional `require_review` flag in niche.yaml to hold content for human review before publishing. |
| 7 | **Daily post cap** | MEDIUM | Low | Add `max_posts_per_day` to niche.yaml. Check count in PendingFeedback before publishing. Prevents audience fatigue. |
| 8 | **Video hash near-zero cleanup** | LOW | Low | Filter out hashes with hamming weight < 4 from `video_hashes.json`. Prevents false-positive dedup. |
| 9 | **Hook text y-position** | LOW | Low | Bump drawtext y from 120 to 150 for notch-safe rendering. |
| 10 | **YouTube quota** | LOW | N/A | `uploadLimitExceeded` is external. Resets daily. No code fix — just retry next run. |

### Recommended first sprint (fixes 1-4):

These four fixes address the most impactful issues with the least risk:
- **#1** prevents embarrassing duplicate posts
- **#2** could restore Facebook as a distribution channel
- **#3** is a one-liner that unblocks bandit learning
- **#4** is a simple regex that prevents visual artifacts

All are read-path or publish-path changes with no impact on content generation.
