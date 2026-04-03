# Phase 6: Content Writing, Visual Generation, Instagram Publishing — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Turn blueprint skeletons into 4 daily Instagram posts with LLM-written copy, dark/techy carousel visuals, and automated Meta Graph API publishing.

**Architecture:** Three new pipeline steps run after the existing 15-step daily intel: (16) Claude Haiku writes real slide copy + captions for the top 4 blueprints, (17) Playwright renders HTML templates into 1080x1350 carousel PNGs, and a separate launchd job publishes approved posts to Instagram via the Meta Graph API.

**Tech Stack:** Anthropic Python SDK (Haiku), Playwright + Jinja2 (HTML→PNG), Meta Graph API (publishing), Microsoft Lists (review gate)

**Design doc:** `docs/plans/2026-02-18-phase6-content-to-publish-design.md`

---

## Task 1: Dependencies and Config

**Files:**
- Modify: `requirements.txt`
- Create: `config/publishing.yaml`
- Create: `config/content_prompts.yaml`
- Create: `schemas/post_content.schema.json`

**Step 1: Add new dependencies to requirements.txt**

Add these lines after line 39 (`python-dateutil>=2.8.2`):

```
# LLM content generation (Phase 6)
anthropic>=0.40.0

# HTML templating for visual generation
jinja2>=3.1.0
```

Note: `playwright>=1.40.0` and `Pillow>=10.0.0` are already present.

**Step 2: Install dependencies**

Run: `cd "/Users/anarchistsid/GenLab/Content Scraper" && ./venv/bin/pip install anthropic jinja2`
Expected: Successfully installed anthropic, jinja2, and their dependencies.

Then: `./venv/bin/python -m playwright install chromium`
Expected: Chromium browser downloaded (if not already present).

**Step 3: Create `config/publishing.yaml`**

```yaml
# Publishing configuration — controls post selection, scheduling, and visual rendering
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
    max_caption_length: 2200
    include_source_credit: true

content_generation:
  model: "claude-haiku-4-5-20251001"
  max_retries: 1
  delay_between_calls_seconds: 0.5

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
  hero_overlay_opacity: 0.7

publisher:
  check_interval_minutes: 30
  max_retries: 1
  meta_api_version: "v21.0"
```

**Step 4: Create `config/content_prompts.yaml`**

```yaml
# Prompt templates for Claude Haiku content generation
# Variables: {format}, {template_name}, {hook}, {cta}, {story_title}, {story_summary},
#            {story_url}, {source}, {structure_json}, {constraints_json}, {max_slides}

system_prompt: |
  You are an Instagram content writer for an AI news account. You write
  engaging, jargon-free carousel slides and captions that a 16-year-old
  would understand. Your tone is conversational — like explaining something
  cool to a friend. You use emoji sparingly but effectively.

  Rules:
  - Every claim must be traceable to the source article
  - No clickbait — hooks must be accurate
  - Use "you" language — make the reader feel involved
  - Keep slide text SHORT — people scroll fast
  - Each slide must add new information (no filler)

carousel_user_prompt: |
  Write Instagram carousel content for this story:

  **Story:** {story_title}
  **Source:** {source} — {story_url}
  **Summary:** {story_summary}

  **Template:** {template_name}
  **Hook (already chosen):** {hook}
  **CTA (already chosen):** {cta}
  **Slide structure:** {structure_json}
  **Constraints:** max {max_slides} slides, max {max_title_words} words per slide title, max {max_body_words} words per slide body

  Return JSON with this exact structure:
  {{
    "slides": [
      {{
        "slide_number": 1,
        "slide_title": "the hook text",
        "slide_body": ""
      }},
      {{
        "slide_number": 2,
        "slide_title": "short punchy title",
        "slide_body": "2-3 sentences explaining this point clearly"
      }}
    ],
    "caption": "Full Instagram caption with hook, 3-4 sentences, line breaks, and CTA at the end",
    "hashtags": ["AI", "Tech", "ArtificialIntelligence"],
    "alt_text_slides": ["Description of slide 1 for accessibility", "Description of slide 2"]
  }}

  IMPORTANT:
  - Slide 1 title MUST be exactly: {hook}
  - Last slide title MUST be exactly: {cta}
  - Respect all word count constraints
  - Caption must NOT repeat slide text verbatim
  - Include 10-15 relevant hashtags

reel_user_prompt: |
  Write Instagram reel script for this story:

  **Story:** {story_title}
  **Source:** {source} — {story_url}
  **Summary:** {story_summary}

  **Template:** {template_name}
  **Hook (already chosen):** {hook}
  **CTA (already chosen):** {cta}
  **Beat structure:** {structure_json}
  **Constraint:** max 30 seconds total

  Return JSON:
  {{
    "beats": [
      {{
        "beat_number": 1,
        "timestamp": "0-3s",
        "voiceover": "actual spoken words",
        "visual_notes": "what appears on screen"
      }}
    ],
    "caption": "Full Instagram caption",
    "hashtags": ["AI", "Tech"],
    "cover_title": "Bold 3-5 word title for reel cover image"
  }}
```

**Step 5: Create `schemas/post_content.schema.json`**

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "Post Content",
  "description": "Schema for LLM-generated Instagram post content",
  "oneOf": [
    {
      "type": "object",
      "required": ["slides", "caption", "hashtags"],
      "properties": {
        "slides": {
          "type": "array",
          "minItems": 3,
          "maxItems": 10,
          "items": {
            "type": "object",
            "required": ["slide_number", "slide_title"],
            "properties": {
              "slide_number": {"type": "integer", "minimum": 1},
              "slide_title": {"type": "string", "minLength": 1},
              "slide_body": {"type": "string"}
            }
          }
        },
        "caption": {"type": "string", "minLength": 10, "maxLength": 2200},
        "hashtags": {"type": "array", "items": {"type": "string"}, "maxItems": 30},
        "alt_text_slides": {"type": "array", "items": {"type": "string"}}
      }
    },
    {
      "type": "object",
      "required": ["beats", "caption", "hashtags", "cover_title"],
      "properties": {
        "beats": {
          "type": "array",
          "minItems": 3,
          "items": {
            "type": "object",
            "required": ["beat_number", "voiceover"],
            "properties": {
              "beat_number": {"type": "integer"},
              "timestamp": {"type": "string"},
              "voiceover": {"type": "string"},
              "visual_notes": {"type": "string"}
            }
          }
        },
        "caption": {"type": "string", "minLength": 10, "maxLength": 2200},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "cover_title": {"type": "string", "minLength": 1}
      }
    }
  ]
}
```

**Step 6: Commit**

```bash
git add requirements.txt config/publishing.yaml config/content_prompts.yaml schemas/post_content.schema.json
git commit -m "feat: add Phase 6 config, prompts, and schema"
```

---

## Task 2: Content Writer (`execution/write_post_content.py`)

**Files:**
- Create: `execution/write_post_content.py`
- Create: `tests/test_write_post_content.py`
- Modify: `execution/review_content.py:93-96` (add new statuses to PROTECTED_STATUSES)

**Step 1: Write the tests first**

Create `tests/test_write_post_content.py`:

```python
#!/usr/bin/env python3
"""Tests for write_post_content.py — LLM content writer."""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Selection logic tests ──

class TestSelectTopBlueprints:
    """Test the top-4 selection with format diversity."""

    def _make_bp(self, candidate_id, fmt, score, status='INTEL_READY'):
        return {
            'id': f'rec{candidate_id}',
            'fields': {
                'candidate_id': candidate_id,
                'format': fmt,
                'priority_score': score,
                'status': status,
                'hook': 'Test hook',
                'cta': 'Test cta',
                'structure': 'Slide 1: Hook\nSlide 2: Detail',
                'story': ['recSTORY1'],
            }
        }

    def test_selects_top_4_by_score(self):
        from execution.write_post_content import select_top_blueprints
        bps = [
            self._make_bp('A', 'carousel', 1.0),
            self._make_bp('B', 'carousel', 0.9),
            self._make_bp('C', 'carousel', 0.8),
            self._make_bp('D', 'carousel', 0.7),
            self._make_bp('E', 'carousel', 0.6),
        ]
        selected = select_top_blueprints(bps, posts_per_day=4, format_mix={'carousel': 3, 'reel': 1})
        assert len(selected) == 4
        ids = [s['fields']['candidate_id'] for s in selected]
        assert 'A' in ids
        assert 'E' not in ids

    def test_enforces_format_diversity(self):
        from execution.write_post_content import select_top_blueprints
        bps = [
            self._make_bp('A', 'carousel', 1.0),
            self._make_bp('B', 'carousel', 0.9),
            self._make_bp('C', 'carousel', 0.8),
            self._make_bp('D', 'carousel', 0.7),
            self._make_bp('E', 'reel', 0.5),  # lower score but needed for diversity
        ]
        selected = select_top_blueprints(bps, posts_per_day=4, format_mix={'carousel': 3, 'reel': 1})
        formats = [s['fields']['format'] for s in selected]
        assert formats.count('reel') >= 1
        assert formats.count('carousel') <= 3

    def test_returns_fewer_if_not_enough(self):
        from execution.write_post_content import select_top_blueprints
        bps = [
            self._make_bp('A', 'carousel', 1.0),
            self._make_bp('B', 'reel', 0.9),
        ]
        selected = select_top_blueprints(bps, posts_per_day=4, format_mix={'carousel': 3, 'reel': 1})
        assert len(selected) == 2

    def test_skips_non_intel_ready(self):
        from execution.write_post_content import select_top_blueprints
        bps = [
            self._make_bp('A', 'carousel', 1.0, status='DRAFTED'),
            self._make_bp('B', 'carousel', 0.9),
        ]
        selected = select_top_blueprints(bps, posts_per_day=4, format_mix={'carousel': 3, 'reel': 1})
        assert len(selected) == 1
        assert selected[0]['fields']['candidate_id'] == 'B'


# ── Schedule assignment tests ──

class TestAssignScheduleSlots:
    def test_assigns_tomorrow_slots(self):
        from execution.write_post_content import assign_schedule_slots
        slots = ["08:00", "12:30", "17:30", "21:00"]
        blueprints = [{'fields': {}} for _ in range(4)]
        result = assign_schedule_slots(blueprints, slots, "America/Los_Angeles")
        assert len(result) == 4
        # All should be for tomorrow
        for bp in result:
            scheduled = bp['fields']['scheduled_for']
            assert 'T08:00' in scheduled or 'T12:30' in scheduled or 'T17:30' in scheduled or 'T21:00' in scheduled


# ── Prompt building tests ──

class TestBuildPrompt:
    def test_carousel_prompt_includes_story_data(self):
        from execution.write_post_content import build_prompt
        blueprint = {
            'fields': {
                'format': 'carousel',
                'hook': 'This is huge 👇',
                'cta': 'Follow for more',
                'structure': 'Slide 1: Hook\nSlide 2: Detail\nSlide 3: CTA',
            }
        }
        story = {
            'fields': {
                'title': 'Claude Sonnet 4.6 Released',
                'summary': 'Anthropic released a new model.',
                'url': 'https://anthropic.com/news',
                'source': 'Anthropic',
            }
        }
        prompt = build_prompt(blueprint, story)
        assert 'Claude Sonnet 4.6' in prompt
        assert 'This is huge' in prompt
        assert 'carousel' in prompt.lower() or 'slide' in prompt.lower()

    def test_reel_prompt_includes_beats(self):
        from execution.write_post_content import build_prompt
        blueprint = {
            'fields': {
                'format': 'reel',
                'hook': 'Watch this ⚡',
                'cta': 'Follow',
                'structure': 'Beat 1: Hook\nBeat 2: Detail',
            }
        }
        story = {
            'fields': {
                'title': 'Test',
                'summary': 'Test summary',
                'url': 'https://example.com',
                'source': 'Test',
            }
        }
        prompt = build_prompt(blueprint, story)
        assert 'reel' in prompt.lower() or 'beat' in prompt.lower()


# ── Response parsing tests ──

class TestParseResponse:
    def test_parses_valid_carousel_json(self):
        from execution.write_post_content import parse_llm_response
        raw = json.dumps({
            "slides": [
                {"slide_number": 1, "slide_title": "Hook", "slide_body": ""},
                {"slide_number": 2, "slide_title": "Detail", "slide_body": "Some text"},
                {"slide_number": 3, "slide_title": "CTA", "slide_body": ""},
            ],
            "caption": "This is a test caption with enough words to pass validation.",
            "hashtags": ["AI", "Tech"],
            "alt_text_slides": ["Slide 1 alt", "Slide 2 alt", "Slide 3 alt"],
        })
        result = parse_llm_response(raw, "carousel")
        assert result is not None
        assert len(result['slides']) == 3

    def test_extracts_json_from_markdown_block(self):
        from execution.write_post_content import parse_llm_response
        raw = '```json\n{"slides": [{"slide_number": 1, "slide_title": "H", "slide_body": ""},{"slide_number": 2, "slide_title": "D", "slide_body": "x"},{"slide_number": 3, "slide_title": "C", "slide_body": ""}], "caption": "Test caption with enough text.", "hashtags": ["AI"]}\n```'
        result = parse_llm_response(raw, "carousel")
        assert result is not None

    def test_returns_none_for_invalid_json(self):
        from execution.write_post_content import parse_llm_response
        result = parse_llm_response("not json at all", "carousel")
        assert result is None

    def test_returns_none_for_missing_fields(self):
        from execution.write_post_content import parse_llm_response
        raw = json.dumps({"slides": []})  # missing caption
        result = parse_llm_response(raw, "carousel")
        assert result is None
```

**Step 2: Run tests to verify they fail**

Run: `cd "/Users/anarchistsid/GenLab/Content Scraper" && ./venv/bin/python -m pytest tests/test_write_post_content.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'execution.write_post_content'`

**Step 3: Write `execution/write_post_content.py`**

Create the full implementation (~300 lines). Key functions:

- `select_top_blueprints(blueprints, posts_per_day, format_mix)` — Filters `INTEL_READY`, sorts by score, enforces format diversity, returns top N
- `assign_schedule_slots(blueprints, slots, timezone_str)` — Assigns tomorrow's time slots
- `build_prompt(blueprint, story)` — Loads templates from `config/content_prompts.yaml`, fills variables
- `call_claude(prompt, system_prompt, model)` — Calls Anthropic SDK, returns raw text
- `parse_llm_response(raw, format)` — Extracts JSON from response, validates against schema
- `write_content_for_blueprint(client, blueprint, story, config)` — Orchestrates: build prompt → call Claude → parse → update Microsoft Lists
- `main()` — CLI entrypoint with `--run-id`, `--dry-run`, `--limit`

The Microsoft Lists update per blueprint writes: `caption`, `slide_content` (JSON string), `hashtags`, `alt_text_slides`, `scheduled_for`, `content_generated_at`, and advances `status` to `DRAFTED`.

**Step 4: Run tests to verify they pass**

Run: `cd "/Users/anarchistsid/GenLab/Content Scraper" && ./venv/bin/python -m pytest tests/test_write_post_content.py -v`
Expected: All 11 tests PASS

**Step 5: Update PROTECTED_STATUSES in review_content.py**

In `execution/review_content.py` line 93-96, add `VISUAL_READY` and `APPROVED` to the protected set:

```python
PROTECTED_STATUSES = {
    "RESEARCHED", "DRAFTED", "QC_PASSED", "SCHEDULED",
    "PUBLISHED", "ANALYZED", "ARCHIVED",
    "VISUAL_READY", "APPROVED",
}
```

**Step 6: Commit**

```bash
git add execution/write_post_content.py tests/test_write_post_content.py execution/review_content.py
git commit -m "feat: add LLM content writer (Phase 6a)

Selects top 4 INTEL_READY blueprints by priority with format
diversity, calls Claude Haiku to write slide copy + captions,
assigns tomorrow's schedule slots, advances to DRAFTED."
```

---

## Task 3: HTML Slide Templates (Dark/Techy Theme)

**Files:**
- Create: `templates/slides/base.css`
- Create: `templates/slides/hook_slide.html`
- Create: `templates/slides/content_slide.html`
- Create: `templates/slides/cta_slide.html`
- Create: `templates/slides/reel_cover.html`

**Step 1: Create shared CSS (`templates/slides/base.css`)**

Dark/techy theme: `#0a0a0f` → `#1a1a2e` gradient, Inter font, white text, 1080×1350 canvas.

Key classes:
- `.slide` — Full 1080×1350 container with gradient background
- `.slide-number` — Top-right indicator (e.g., "3/8")
- `.title` — Bold, large, max 80% width
- `.body` — Regular weight, slightly smaller, max 75% width
- `.accent-bar` — Left-side colored bar (color passed as CSS variable)
- `.brand` — Bottom watermark/handle
- `.hero-bg` — Background image with dark overlay

**Step 2: Create `templates/slides/hook_slide.html`**

Jinja2 template. Variables: `hook_text`, `accent_color`, `hero_image_url`, `slide_total`, `brand_handle`, `source_name`.

Layout: Hero image background (if available) with 70% dark overlay, large bold hook text centered, source badge top-left, slide indicator "1/N" top-right.

**Step 3: Create `templates/slides/content_slide.html`**

Variables: `slide_number`, `slide_total`, `slide_title`, `slide_body`, `accent_color`, `brand_handle`.

Layout: Accent bar left side, title bold at top, body text below, slide indicator top-right.

**Step 4: Create `templates/slides/cta_slide.html`**

Variables: `cta_text`, `accent_color`, `brand_handle`.

Layout: Gradient background (accent color fade), large CTA text centered, brand handle prominent.

**Step 5: Create `templates/slides/reel_cover.html`**

Variables: `cover_title`, `accent_color`, `brand_handle`.

Layout: Dark background, bold 3-5 word title, play icon overlay, accent color accents.

**Step 6: Commit**

```bash
git add templates/slides/
git commit -m "feat: add dark/techy HTML slide templates for carousel visuals"
```

---

## Task 4: Visual Renderer (`execution/render_visuals.py`)

**Files:**
- Create: `execution/render_visuals.py`
- Create: `tests/test_render_visuals.py`

**Step 1: Write the tests**

Create `tests/test_render_visuals.py`:

```python
#!/usr/bin/env python3
"""Tests for render_visuals.py — HTML→PNG carousel renderer."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestBuildSlideHtml:
    """Test HTML generation from slide data."""

    def test_hook_slide_uses_hook_template(self):
        from execution.render_visuals import build_slide_html
        slide = {"slide_number": 1, "slide_title": "This is huge 👇", "slide_body": ""}
        html = build_slide_html(slide, total_slides=8, accent_color="#00d4ff",
                                brand_handle="@testaccount", hero_image_url=None, source_name="TechCrunch")
        assert "This is huge" in html
        assert "1/8" in html or "1 / 8" in html

    def test_content_slide_includes_body(self):
        from execution.render_visuals import build_slide_html
        slide = {"slide_number": 3, "slide_title": "Key Detail", "slide_body": "Here is the explanation."}
        html = build_slide_html(slide, total_slides=8, accent_color="#7c3aed",
                                brand_handle="@test", hero_image_url=None, source_name=None)
        assert "Key Detail" in html
        assert "Here is the explanation" in html

    def test_cta_slide_uses_cta_template(self):
        from execution.render_visuals import build_slide_html
        slide = {"slide_number": 8, "slide_title": "Save this 💾", "slide_body": ""}
        html = build_slide_html(slide, total_slides=8, accent_color="#00d4ff",
                                brand_handle="@test", hero_image_url=None, source_name=None,
                                is_cta=True)
        assert "Save this" in html


class TestSelectAccentColor:
    def test_rotates_colors_by_candidate_id(self):
        from execution.render_visuals import select_accent_color
        colors = ["#00d4ff", "#7c3aed", "#10b981", "#f59e0b"]
        c1 = select_accent_color("abc123", colors)
        c2 = select_accent_color("def456", colors)
        assert c1 in colors
        assert c2 in colors


class TestGetHeroImageUrl:
    def test_returns_url_from_assets(self):
        from execution.render_visuals import get_hero_image_url
        assets = [
            {'fields': {'story': ['recSTORY1'], 'url': 'https://img.com/hero.jpg',
                         'quality_tier': 'hero', 'status': 'READY'}},
        ]
        url = get_hero_image_url('recSTORY1', assets)
        assert url == 'https://img.com/hero.jpg'

    def test_returns_none_if_no_match(self):
        from execution.render_visuals import get_hero_image_url
        url = get_hero_image_url('recNONE', [])
        assert url is None
```

**Step 2: Run tests to verify they fail**

Run: `cd "/Users/anarchistsid/GenLab/Content Scraper" && ./venv/bin/python -m pytest tests/test_render_visuals.py -v`
Expected: FAIL

**Step 3: Write `execution/render_visuals.py`**

Key functions:

- `load_visual_config()` — Reads `config/publishing.yaml` visuals section
- `select_accent_color(candidate_id, colors)` — Deterministic color from hash
- `get_hero_image_url(story_record_id, assets)` — Find best hero image for story
- `build_slide_html(slide, total_slides, accent_color, brand_handle, hero_image_url, source_name, is_cta)` — Renders Jinja2 template → HTML string
- `render_slide_png(html, output_path, width, height)` — Playwright screenshot of HTML
- `render_blueprint_visuals(client, blueprint, config)` — Orchestrates: load slides → render each → upload to Microsoft Lists → advance to VISUAL_READY
- `main()` — CLI with `--run-id`, `--dry-run`, `--brand-handle`

Playwright usage: launch Chromium headless, `page.set_content(html)`, `page.screenshot(path=..., type='png')`. One browser instance reused across all slides.

Microsoft Lists upload: Uses `visual_files` attachment field. Microsoft Lists accepts file uploads via the `[{"url": "file://..."}]` format — but since we need public URLs, we first save PNGs to `.tmp/visuals/<candidate_id>/`, then the publisher reads from there. Alternative: upload as base64 attachment. We'll use the simpler approach of saving locally and letting the publisher handle hosting.

**Updated approach for image hosting:** Save PNGs to `.tmp/visuals/<candidate_id>/slide_1.png` etc. The publisher will upload these to a publicly accessible location (or use Microsoft Lists attachments which auto-generate public URLs).

**Step 4: Run tests**

Run: `cd "/Users/anarchistsid/GenLab/Content Scraper" && ./venv/bin/python -m pytest tests/test_render_visuals.py -v`
Expected: All 5 tests PASS

**Step 5: Commit**

```bash
git add execution/render_visuals.py tests/test_render_visuals.py
git commit -m "feat: add carousel visual renderer (Phase 6b)

Renders HTML slide templates to 1080x1350 PNGs using Playwright.
Dark/techy theme with accent color rotation. Uploads to Microsoft Lists
and advances status to VISUAL_READY."
```

---

## Task 5: Instagram Publisher (`execution/publish_to_instagram.py`)

**Files:**
- Create: `execution/publish_to_instagram.py`
- Create: `tests/test_publish_to_instagram.py`

**Step 1: Write the tests**

Create `tests/test_publish_to_instagram.py`:

```python
#!/usr/bin/env python3
"""Tests for publish_to_instagram.py — Meta Graph API publisher."""

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestShouldPublishNow:
    def test_publish_when_scheduled_in_past(self):
        from execution.publish_to_instagram import should_publish_now
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        assert should_publish_now(past) is True

    def test_skip_when_scheduled_in_future(self):
        from execution.publish_to_instagram import should_publish_now
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        assert should_publish_now(future) is False

    def test_skip_when_no_schedule(self):
        from execution.publish_to_instagram import should_publish_now
        assert should_publish_now('') is False
        assert should_publish_now(None) is False


class TestBuildCarouselCaption:
    def test_includes_hashtags(self):
        from execution.publish_to_instagram import build_final_caption
        caption = "Test caption"
        hashtags = ["AI", "Tech", "News"]
        result = build_final_caption(caption, hashtags, source="TechCrunch",
                                     include_source_credit=True, max_length=2200)
        assert "#AI" in result
        assert "#Tech" in result
        assert "TechCrunch" in result

    def test_truncates_to_max_length(self):
        from execution.publish_to_instagram import build_final_caption
        long_caption = "x" * 2500
        result = build_final_caption(long_caption, ["AI"], source="Test",
                                     include_source_credit=False, max_length=2200)
        assert len(result) <= 2200

    def test_handles_empty_hashtags(self):
        from execution.publish_to_instagram import build_final_caption
        result = build_final_caption("Caption", [], source=None,
                                     include_source_credit=False, max_length=2200)
        assert result == "Caption"


class TestMetaApiCalls:
    """Test Meta API call construction (mocked HTTP)."""

    @patch('execution.publish_to_instagram.requests')
    def test_upload_image_returns_creation_id(self, mock_requests):
        from execution.publish_to_instagram import upload_carousel_image
        mock_requests.post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"id": "17889615691000001"}
        )
        result = upload_carousel_image(
            ig_user_id="123456",
            image_url="https://example.com/slide.png",
            access_token="test_token",
            api_version="v21.0"
        )
        assert result == "17889615691000001"

    @patch('execution.publish_to_instagram.requests')
    def test_upload_image_returns_none_on_failure(self, mock_requests):
        from execution.publish_to_instagram import upload_carousel_image
        mock_requests.post.return_value = MagicMock(
            status_code=400,
            json=lambda: {"error": {"message": "Bad request"}},
            text="Bad request"
        )
        result = upload_carousel_image(
            ig_user_id="123456",
            image_url="https://example.com/slide.png",
            access_token="test_token",
            api_version="v21.0"
        )
        assert result is None

    @patch('execution.publish_to_instagram.requests')
    def test_create_carousel_returns_container_id(self, mock_requests):
        from execution.publish_to_instagram import create_carousel_container
        mock_requests.post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"id": "17889615691000099"}
        )
        result = create_carousel_container(
            ig_user_id="123456",
            children_ids=["id1", "id2", "id3"],
            caption="Test caption #AI",
            access_token="test_token",
            api_version="v21.0"
        )
        assert result == "17889615691000099"

    @patch('execution.publish_to_instagram.requests')
    def test_publish_container_returns_post_id(self, mock_requests):
        from execution.publish_to_instagram import publish_container
        mock_requests.post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"id": "17889615691000200"}
        )
        result = publish_container(
            ig_user_id="123456",
            container_id="17889615691000099",
            access_token="test_token",
            api_version="v21.0"
        )
        assert result == "17889615691000200"
```

**Step 2: Run tests to verify they fail**

Run: `cd "/Users/anarchistsid/GenLab/Content Scraper" && ./venv/bin/python -m pytest tests/test_publish_to_instagram.py -v`
Expected: FAIL

**Step 3: Write `execution/publish_to_instagram.py`**

Key functions:

- `should_publish_now(scheduled_for)` — Check if scheduled time has passed
- `build_final_caption(caption, hashtags, source, include_source_credit, max_length)` — Assemble final caption with hashtags + source credit
- `upload_carousel_image(ig_user_id, image_url, access_token, api_version)` — `POST /{id}/media` with `is_carousel_item=true`
- `create_carousel_container(ig_user_id, children_ids, caption, access_token, api_version)` — `POST /{id}/media` with `media_type=CAROUSEL`
- `publish_container(ig_user_id, container_id, access_token, api_version)` — `POST /{id}/media_publish`
- `get_image_urls_from_lists(blueprint)` — Read `visual_files` attachment URLs
- `publish_blueprint(client, blueprint, config)` — Full orchestration: check time → get images → upload → create container → publish → update Microsoft Lists status to PUBLISHED
- `main()` — CLI with `--dry-run`. Queries `APPROVED` blueprints, publishes those due.

**Step 4: Run tests**

Run: `cd "/Users/anarchistsid/GenLab/Content Scraper" && ./venv/bin/python -m pytest tests/test_publish_to_instagram.py -v`
Expected: All 8 tests PASS

**Step 5: Commit**

```bash
git add execution/publish_to_instagram.py tests/test_publish_to_instagram.py
git commit -m "feat: add Instagram publisher via Meta Graph API (Phase 6c)

Publishes APPROVED carousel posts when their scheduled_for time
arrives. Uploads slide images, creates carousel container, and
publishes via Meta Graph API. Handles errors gracefully."
```

---

## Task 6: Pipeline Integration

**Files:**
- Modify: `runbooks/daily_intel.sh` (add steps 16-17)
- Create: `~/Library/LaunchAgents/com.genlab.instagram-publisher.plist`
- Modify: `execution/utils/backlog_client.py` (add helper for visual upload)

**Step 1: Add steps 16-17 to daily_intel.sh**

After step 15 (process_feedback) and before the closing banner, add:

```bash
echo "[16/17] Writing post content (top 4 blueprints)..."
"$VENV_PYTHON" execution/write_post_content.py --run-id "$RUN_ID" || echo "  ⚠️  Content writing skipped (non-fatal — ANTHROPIC_API_KEY may not be set)"
echo ""

echo "[17/17] Rendering carousel visuals..."
"$VENV_PYTHON" execution/render_visuals.py --run-id "$RUN_ID" || echo "  ⚠️  Visual rendering skipped (non-fatal — playwright may not be installed)"
echo ""
```

Update the step counts in all echo statements from `[N/15]` to `[N/17]`.

**Step 2: Create publisher launchd plist**

Create `~/Library/LaunchAgents/com.genlab.instagram-publisher.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.genlab.instagram-publisher</string>

    <key>ProgramArguments</key>
    <array>
        <string>/Users/anarchistsid/GenLab/Content Scraper/runbooks/publisher_wrapper.sh</string>
    </array>

    <!-- Run every 30 minutes -->
    <key>StartInterval</key>
    <integer>1800</integer>

    <key>StandardOutPath</key>
    <string>/Users/anarchistsid/GenLab/Content Scraper/.tmp/logs/publisher_stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/anarchistsid/GenLab/Content Scraper/.tmp/logs/publisher_stderr.log</string>

    <key>WorkingDirectory</key>
    <string>/Users/anarchistsid/GenLab/Content Scraper</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>

    <key>KeepAlive</key>
    <false/>

    <key>TimeOut</key>
    <integer>300</integer>
</dict>
</plist>
```

**Step 3: Create publisher wrapper script**

Create `runbooks/publisher_wrapper.sh`:

```bash
#!/bin/bash
# Publisher wrapper — loads .env then runs the Instagram publisher.
set -euo pipefail

PROJECT_DIR="/Users/anarchistsid/GenLab/Content Scraper"

if [ -f "$PROJECT_DIR/.env" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        if [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
            export "$line"
        fi
    done < "$PROJECT_DIR/.env"
fi

exec "$PROJECT_DIR/venv/bin/python3" "$PROJECT_DIR/execution/publish_to_instagram.py"
```

Make executable: `chmod +x runbooks/publisher_wrapper.sh`

**Step 4: Commit**

```bash
git add runbooks/daily_intel.sh runbooks/publisher_wrapper.sh
git commit -m "feat: integrate Phase 6 into pipeline and add publisher launchd job

Daily pipeline now runs 17 steps (added content writing + visual
rendering). Publisher runs every 30 minutes via separate launchd."
```

---

## Task 7: End-to-End Test

**Step 1: Add ANTHROPIC_API_KEY to .env**

User must add their key: `echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env`

**Step 2: Run the full pipeline**

```bash
cd "/Users/anarchistsid/GenLab/Content Scraper" && ./runbooks/daily_intel.sh
```

Expected: All 17 steps complete. Steps 16-17 should:
- Select 4 blueprints
- Generate content via Haiku
- Render slide PNGs to `.tmp/visuals/`
- Upload to Microsoft Lists
- 4 blueprints now at `VISUAL_READY`

**Step 3: Verify in Microsoft Lists**

Check that 4 blueprints have:
- `status` = `VISUAL_READY`
- `caption` field populated (real text, no placeholders)
- `slide_content` field populated (JSON with real copy)
- `visual_files` attachment with PNG images
- `scheduled_for` set to tomorrow's slots

**Step 4: Visual spot check**

Open a rendered PNG from `.tmp/visuals/` — verify:
- 1080×1350 dimensions
- Dark gradient background
- White text, readable
- Accent color bar/elements
- Slide number indicator

**Step 5: Run all tests**

```bash
cd "/Users/anarchistsid/GenLab/Content Scraper" && ./venv/bin/python -m pytest tests/ -v
```

Expected: All tests pass (255 existing + ~24 new = ~279 total).

**Step 6: Load publisher launchd (do NOT start until Meta account is ready)**

```bash
# Don't load until you have META_ACCESS_TOKEN and INSTAGRAM_BUSINESS_ID in .env
# launchctl load ~/Library/LaunchAgents/com.genlab.instagram-publisher.plist
```

**Step 7: Final commit**

```bash
git add -A
git commit -m "feat: Phase 6 complete — content writing, visuals, publisher ready

Pipeline now generates Instagram-ready content with LLM copy,
dark/techy carousel visuals, and automated Meta publishing.
Publisher launchd job ready to activate once Meta Business
account is configured."
```

---

## Summary

| Task | What | Files | Tests |
|------|------|-------|-------|
| 1 | Dependencies + config | requirements.txt, config/publishing.yaml, config/content_prompts.yaml, schemas/post_content.schema.json | — |
| 2 | Content writer | execution/write_post_content.py | 11 tests |
| 3 | HTML templates | templates/slides/*.html + base.css | — |
| 4 | Visual renderer | execution/render_visuals.py | 5 tests |
| 5 | Instagram publisher | execution/publish_to_instagram.py | 8 tests |
| 6 | Pipeline integration | daily_intel.sh, publisher plist + wrapper | — |
| 7 | End-to-end verification | Full pipeline run + Microsoft Lists check | All ~279 |
