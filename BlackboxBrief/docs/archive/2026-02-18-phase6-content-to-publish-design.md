# Phase 6 Design: Content Writing, Visual Generation, Instagram Publishing

**Date:** 2026-02-18
**Status:** Approved
**Goal:** Turn blueprint skeletons into 4 Instagram posts/day, scheduled for the next day, with human review in Microsoft Lists before publishing.

---

## Context

The pipeline (steps 1-15) produces ranked AI news stories and blueprint candidates with hooks, CTAs, and structural outlines. But slide bodies are `[placeholders]`, not real copy. No visuals are generated and no publishing mechanism exists.

This design adds 3 layers to close the gap:
- **6a** LLM content writer (Anthropic SDK / Haiku)
- **6b** Visual renderer (HTML + Playwright -> PNG)
- **6c** Instagram publisher (Meta Graph API)

---

## Daily Timeline

| Time | Event | Trigger |
|------|-------|---------|
| 8:00 AM | Daily intel pipeline (steps 1-15) | `com.genlab.daily-intel` launchd |
| ~8:10 AM | Step 16: Write post content for top 4 blueprints | Part of pipeline |
| ~8:12 AM | Step 17: Render carousel slide PNGs | Part of pipeline |
| ~8:15 AM | 4 posts are `VISUAL_READY` in Microsoft Lists with preview images | -- |
| Anytime | Human reviews posts in Microsoft Lists, advances to `APPROVED` | Manual |
| Every 30 min | Publisher checks for `APPROVED` posts, publishes on schedule | `com.genlab.instagram-publisher` launchd |

---

## Status Flow (Extended)

```
INTEL_READY -> DRAFTED -> VISUAL_READY -> [human review] -> APPROVED -> PUBLISHED
```

Only `APPROVED` posts get published. Everything before `APPROVED` is automated.

---

## 6a: Content Writer (`execution/write_post_content.py`)

### Selection Logic (4 posts/day)

1. Query Microsoft Lists for all `INTEL_READY` blueprints
2. Sort by `priority_score` descending
3. Apply format diversity: at least 2 carousels + 1 reel. 4th slot = highest remaining score.
4. For each of the 4 winners, call Claude Haiku to generate content
5. Assign `scheduled_for` times (tomorrow's slots from config)
6. Advance status to `DRAFTED`

### What Gets Generated Per Blueprint

| Field | Description |
|-------|-------------|
| `slide_content` | Full JSON with actual copy per slide (no placeholders) |
| `caption` | Instagram caption: hook + 3-4 sentences + hashtags + CTA |
| `hashtags` | Top 15 relevant hashtags (mix high-volume + niche) |
| `alt_text_slides` | Per-slide accessibility descriptions |
| `scheduled_for` | Tomorrow's time slot (e.g., `2026-02-19T08:00:00-08:00`) |
| `content_generated_at` | Timestamp of generation |

### Claude Haiku Call

- SDK: `anthropic.Anthropic()`
- Model: `claude-haiku-4-5-20251001`
- One call per blueprint
- System prompt includes: template structure, constraints, story data, tone guidelines
- Response: structured JSON (`slides[]` + `caption` + `hashtags[]`)
- Prompts stored in `config/content_prompts.yaml` (not hardcoded)
- Budget enforcement: post-generation word count check as safety net

### Error Handling

- Malformed JSON from Claude: retry once, then `NEEDS_REVIEW`
- Missing `ANTHROPIC_API_KEY`: skip with warning, pipeline continues
- Rate limiting: 0.5s delay between calls (4 calls = 2s total)

### Cost

~4 Haiku calls/day x ~800 input + ~400 output tokens = ~$0.01/day

---

## 6b: Visual Renderer (`execution/render_visuals.py`)

### Tech Stack

- **Playwright** (headless Chromium) for HTML -> PNG
- **Jinja2** for HTML templating
- No external services, $0 cost

### How It Works

1. Query Microsoft Lists for `DRAFTED` blueprints
2. For each blueprint, read `slide_content` JSON
3. For each slide, render HTML template -> 1080x1350 PNG (4:5 Instagram ratio)
4. Upload PNGs to Microsoft Lists `visual_files` attachment field
5. Advance status to `VISUAL_READY`

### HTML Templates (`templates/slides/`)

| Template | Used For | Description |
|----------|----------|-------------|
| `hook_slide.html` | Slide 1 | Large bold hook, hero image background with dark overlay |
| `content_slide.html` | Slides 2-7 | Title + body, slide number indicator, accent bar |
| `cta_slide.html` | Last slide | CTA text, follow handle, gradient background |
| `reel_cover.html` | Reel thumbnail | Bold title, play icon overlay |
| `base.css` | All slides | Shared dark theme styles |

### Dark/Techy Theme

- Background: `#0a0a0f` to `#1a1a2e` gradient
- Accent colors: `#00d4ff` (electric blue), `#7c3aed` (purple) -- rotated per post
- Font: Inter (Google Fonts) -- bold titles, regular body
- Text: white `#f0f0f0`, max 80% width
- Dimensions: 1080x1350px (4:5 ratio, optimal for Instagram feed)

### Hero Image Handling

- Hook slide uses the story's hero image from Assets table as background
- Dark overlay (70% opacity) ensures text readability
- If hero image fetch fails: solid gradient background (still looks good)

### Error Handling

- Playwright not installed: skip with warning, log which blueprints need visuals
- Single slide render failure: retry once, then render without problematic element
- All renders are idempotent (re-running overwrites previous PNGs)

---

## 6c: Instagram Publisher (`execution/publish_to_instagram.py`)

### Meta Graph API Flow (Carousels)

1. Upload each slide image: `POST /{ig-user-id}/media` with `image_url` + `is_carousel_item=true`
2. Create carousel container: `POST /{ig-user-id}/media` with `media_type=CAROUSEL`, `children=[...]`, `caption=...`
3. Publish: `POST /{ig-user-id}/media_publish` with container ID
4. Store `instagram_post_id` and `instagram_permalink` on the blueprint record
5. Advance status to `PUBLISHED`

### Image Hosting

Microsoft Lists attachment URLs are publicly accessible. The publisher reads attachment URLs from the `visual_files` field and passes them directly to Meta's API. No extra hosting needed.

### Scheduling

- Separate launchd job: `com.genlab.instagram-publisher.plist`
- Runs every 30 minutes via `StartInterval: 1800`
- Query: `status = APPROVED AND scheduled_for <= now()`
- If matches: publish. If not: exit silently.
- Posts go out within 30 minutes of scheduled time.

### Reels (MVP)

- Reels get drafted with cover image + voiceover script in Microsoft Lists
- User records/produces the reel manually using the script
- Publisher CAN post a reel if a video URL is provided, but carousel is the default

### Error Handling

- Missing Meta credentials: skip with clear warning, log what would have been published
- Image upload failure: retry once, then `NEEDS_REVIEW`
- Meta rate limit (50 calls/hour): back off, try next 30-min cycle
- Failed post after container creation: log container ID for manual recovery

---

## New Microsoft Lists Fields (Blueprints Table)

| Field | Type | Set By |
|-------|------|--------|
| `caption` | Long text | write_post_content.py |
| `slide_content` | Long text (JSON) | write_post_content.py |
| `hashtags` | Long text | write_post_content.py |
| `alt_text_slides` | Long text (JSON) | write_post_content.py |
| `content_generated_at` | DateTime | write_post_content.py |
| `scheduled_for` | DateTime | write_post_content.py |
| `visual_files` | Attachment | render_visuals.py |
| `cover_image` | Attachment | render_visuals.py |
| `instagram_post_id` | Text | publish_to_instagram.py |
| `instagram_permalink` | URL | publish_to_instagram.py |
| `published_at` | DateTime | publish_to_instagram.py |

---

## New Files

| File | Purpose |
|------|---------|
| `execution/write_post_content.py` | LLM content writer |
| `execution/render_visuals.py` | HTML -> PNG visual renderer |
| `execution/publish_to_instagram.py` | Meta Graph API publisher |
| `templates/slides/hook_slide.html` | Hook slide HTML |
| `templates/slides/content_slide.html` | Content slide HTML |
| `templates/slides/cta_slide.html` | CTA slide HTML |
| `templates/slides/reel_cover.html` | Reel thumbnail HTML |
| `templates/slides/base.css` | Shared dark theme CSS |
| `config/publishing.yaml` | Schedule, format mix, caption config |
| `config/content_prompts.yaml` | Claude system/user prompt templates |
| `schemas/post_content.schema.json` | Schema for Claude JSON output |
| `~/Library/LaunchAgents/com.genlab.instagram-publisher.plist` | Publisher launchd job |
| `tests/test_write_post_content.py` | Content writer tests |
| `tests/test_render_visuals.py` | Visual renderer tests |
| `tests/test_publish_to_instagram.py` | Publisher tests |

---

## Config: `config/publishing.yaml`

```yaml
instagram:
  posts_per_day: 4
  schedule_slots:
    - "08:00"
    - "12:30"
    - "17:30"
    - "21:00"
  timezone: "America/Los_Angeles"
  format_mix:
    carousel: 3
    reel: 1
  caption:
    max_hashtags: 15
    include_source_credit: true
    max_caption_length: 2200

content_generation:
  model: "claude-haiku-4-5-20251001"
  max_retries: 1
  delay_between_calls_seconds: 0.5
  tone: "conversational, like explaining to a friend, jargon-free, emoji-appropriate"

visuals:
  dimensions:
    width: 1080
    height: 1350
  theme: "dark_techy"
  accent_colors:
    - "#00d4ff"
    - "#7c3aed"
    - "#10b981"
    - "#f59e0b"
  font_family: "Inter"
```

---

## New Environment Variables

```
ANTHROPIC_API_KEY=...           # For Claude Haiku calls
META_ACCESS_TOKEN=...           # Long-lived Meta page access token
INSTAGRAM_BUSINESS_ID=...      # IG business account ID
```

---

## Dependencies to Add

```
anthropic>=0.40.0
playwright>=1.45.0
jinja2>=3.1.0
```

Post-install: `playwright install chromium`

---

## Cost Summary

| Component | Daily Cost |
|-----------|-----------|
| Anthropic (4 Haiku calls) | ~$0.01 |
| Visual rendering (local) | $0.00 |
| Meta API (organic posting) | $0.00 |
| **Total added** | **~$0.01/day** |

---

## Verification Criteria

1. `write_post_content.py` produces 4 blueprints with real slide copy (no `[placeholders]`)
2. `render_visuals.py` produces 1080x1350 PNGs that look good in dark/techy theme
3. `publish_to_instagram.py` can create a carousel post (tested with Meta sandbox first)
4. Full pipeline runs in under 15 minutes (within launchd timeout)
5. All new tests pass
6. Microsoft Lists shows clean status flow: INTEL_READY -> DRAFTED -> VISUAL_READY -> APPROVED -> PUBLISHED
