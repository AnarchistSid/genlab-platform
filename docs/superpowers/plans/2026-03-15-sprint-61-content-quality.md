# Sprint 61: Content Quality, Video Gate, Sources

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace template-based writing with LLM-powered content generation, gate LLM calls behind video availability, and improve source quality across all 4 MVP channels.

**Architecture:** Three parallel tracks that share a foundation in genlab-core. Track A builds `AnthropicLLMClient` + extends `write_video_content()` + wires it into CW/SR/FD writing strategies. Track B adds a `VideoGate` pipeline stage between `DownloadTopVideos` and `Writing` to skip LLM calls for stories without clips. Track C updates sources.yaml and adds source-level filters. A small Track D adds `DedupEngine` to CW/SR and fixes FrameDrift brand safety.

**Tech Stack:** Python 3.12, Anthropic SDK (`anthropic`), genlab-core pipeline stages, YAML config, unittest.mock for tests.

---

## Critical Context

**Key data structures (read before implementing):**

- `context["stories"]` is a `list[dict]`, NOT `dict[str, dict]`. Each story has a `story_id` key.
- `context["clip_index"]` is `{"run_id": str, "videos_total": int, "videos_downloaded": int, "videos_failed": int, "clips": {story_id: {"story_id": str, "success": bool, "clip_path": str, "source_url": str, "backend": str, "duration_seconds": float, "error": str}}}`.
- To check if a story has a clip: `clip_index.get("clips", {}).get(story.get("story_id", ""), {}).get("success", False)`.
- `write_video_content(video, niche_id, llm_client, existing_hooks)` expects `video` dict with: `title`, `channel_name`, `view_count`, `view_velocity`, `description_snippet`, `tags`. Returns: `{hook, instagram_caption, twitter_content, youtube_content, facebook_content}`.
- `llm_client` must implement `.complete(system: str, user: str, max_tokens: int, temperature: float) -> str`.
- Model routing in `genlab-core/configs/model_routing.yaml`: hooks use `claude-sonnet-4-6`, content writing uses `claude-haiku-4-5-20251001`. We use Haiku for the combined write_video_content() call (cost trade-off: one Haiku call vs separate Sonnet+Haiku).
- Stage ordering in niche.yaml is ALREADY correct: `DownloadTopVideos` comes before `Writing`. We are ADDING `VideoGate` between them, not reordering.
- `DedupEngine` is ALREADY in `genlab_core.intelligence.dedup_engine`. CW/SR just need to import and use it.
- **CriticalRush does NOT use `DownloadTopVideos`** — it uses `ExtractGamingMedia` which does NOT populate `clip_index`. CR is excluded from VideoGate registration in this sprint.
- `anthropic` SDK is NOT in genlab-core pyproject.toml — must be added as optional dependency.

---

## Chunk 1: genlab-core Foundation

### Task 1: AnthropicLLMClient adapter

**Files:**
- Create: `genlab-core/src/genlab_core/writing/llm_client.py`
- Test: `genlab-core/tests/test_llm_client.py`

- [ ] **Step 1: Write the failing test**

```python
# genlab-core/tests/test_llm_client.py
"""Tests for AnthropicLLMClient adapter."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


class TestAnthropicLLMClient(unittest.TestCase):
    """Test the thin adapter bridging write_video_content to Anthropic SDK."""

    @patch("genlab_core.writing.llm_client.anthropic")
    def test_complete_calls_messages_create(self, mock_anthropic):
        from genlab_core.writing.llm_client import AnthropicLLMClient

        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"hook": "test"}')]
        mock_client.messages.create.return_value = mock_response

        client = AnthropicLLMClient(api_key="test-key", model="claude-haiku-4-5-20251001")
        result = client.complete(
            system="You are a writer.",
            user="Write a hook.",
            max_tokens=600,
            temperature=0.8,
        )

        mock_client.messages.create.assert_called_once_with(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            temperature=0.8,
            system="You are a writer.",
            messages=[{"role": "user", "content": "Write a hook."}],
        )
        self.assertEqual(result, '{"hook": "test"}')

    @patch("genlab_core.writing.llm_client.anthropic")
    def test_complete_returns_text_from_first_content_block(self, mock_anthropic):
        from genlab_core.writing.llm_client import AnthropicLLMClient

        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        block1 = MagicMock(text="first block")
        block2 = MagicMock(text="second block")
        mock_client.messages.create.return_value = MagicMock(content=[block1, block2])

        client = AnthropicLLMClient(api_key="k", model="m")
        result = client.complete(system="s", user="u")
        self.assertEqual(result, "first block")

    @patch("genlab_core.writing.llm_client.anthropic")
    def test_default_model_from_init(self, mock_anthropic):
        from genlab_core.writing.llm_client import AnthropicLLMClient

        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="ok")]
        )

        client = AnthropicLLMClient(api_key="k", model="claude-sonnet-4-6")
        client.complete(system="s", user="u")

        call_kwargs = mock_client.messages.create.call_args[1]
        self.assertEqual(call_kwargs["model"], "claude-sonnet-4-6")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package genlab-core pytest genlab-core/tests/test_llm_client.py -v`
Expected: FAIL (ModuleNotFoundError for `genlab_core.writing.llm_client`)

- [ ] **Step 3: Write minimal implementation**

```python
# genlab-core/src/genlab_core/writing/llm_client.py
"""Thin LLM client adapter for write_video_content().

Bridges the .complete(system, user, max_tokens, temperature) interface
that write_video_content() expects to the Anthropic SDK's
client.messages.create() API.
"""
from __future__ import annotations

import logging

try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class AnthropicLLMClient:
    """Adapter: write_video_content() interface -> Anthropic Messages API."""

    def __init__(self, api_key: str, model: str = "claude-haiku-4-5-20251001") -> None:
        if anthropic is None:
            raise ImportError(
                "anthropic package required. Install with: "
                "pip install anthropic  (or uv add anthropic)"
            )
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> str:
        """Call Anthropic Messages API and return the text response."""
        response = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return response.content[0].text
```

- [ ] **Step 4: Add `anthropic` to genlab-core pyproject.toml optional dependencies**

In `genlab-core/pyproject.toml`, add after the `cost` line:
```toml
llm = ["anthropic>=0.40"]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package genlab-core pytest genlab-core/tests/test_llm_client.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
cd /Users/anarchistsid/GenLab/genlab-core
git add src/genlab_core/writing/llm_client.py tests/test_llm_client.py pyproject.toml
git commit -m "feat(core): add AnthropicLLMClient adapter for write_video_content"
```

---

### Task 2: Extend write_video_content() with extra_instructions

**Files:**
- Modify: `genlab-core/src/genlab_core/writing/video_content_writer.py`
- Test: `genlab-core/tests/test_video_content_writer.py`

- [ ] **Step 1: Write the failing test**

```python
# genlab-core/tests/test_video_content_writer.py
"""Tests for write_video_content with extra_instructions."""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock


class TestWriteVideoContentExtraInstructions(unittest.TestCase):

    def test_extra_instructions_appended_to_system_prompt(self):
        from genlab_core.writing.video_content_writer import write_video_content

        mock_client = MagicMock()
        mock_client.complete.return_value = json.dumps({
            "hook": "Specific hook",
            "instagram_caption": "Caption #Sports",
            "twitter_content": "Tweet",
            "youtube_content": "Title?",
            "facebook_content": "FB post",
        })

        video = {"title": "NBA Finals Game 7", "channel_name": "ESPN",
                 "view_count": 500000, "view_velocity": 10000,
                 "description_snippet": "Lakers vs Celtics", "tags": ["NBA"]}

        extra = "BANNED: 'this is what clutch looks like'"

        write_video_content(
            video=video,
            niche_id="sports",
            llm_client=mock_client,
            extra_instructions=extra,
        )

        system_prompt = mock_client.complete.call_args[1]["system"]
        self.assertIn("BANNED: 'this is what clutch looks like'", system_prompt)

    def test_extra_instructions_none_works(self):
        from genlab_core.writing.video_content_writer import write_video_content

        mock_client = MagicMock()
        mock_client.complete.return_value = json.dumps({
            "hook": "H", "instagram_caption": "I",
            "twitter_content": "T", "youtube_content": "Y",
            "facebook_content": "F",
        })

        result = write_video_content(
            video={"title": "Test"},
            niche_id="gaming",
            llm_client=mock_client,
        )
        self.assertIn("hook", result)

    def test_fallback_on_llm_failure(self):
        from genlab_core.writing.video_content_writer import write_video_content

        mock_client = MagicMock()
        mock_client.complete.side_effect = Exception("API down")

        result = write_video_content(
            video={"title": "NBA Finals", "channel_name": "ESPN"},
            niche_id="sports",
            llm_client=mock_client,
        )

        self.assertIn("hook", result)
        self.assertIn("NBA Finals", result["hook"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package genlab-core pytest genlab-core/tests/test_video_content_writer.py -v`
Expected: FAIL (TypeError: write_video_content() got unexpected keyword argument 'extra_instructions')

- [ ] **Step 3: Add extra_instructions parameter to write_video_content()**

In `genlab-core/src/genlab_core/writing/video_content_writer.py`, make these changes:

**Change 1:** Update function signature (line 73-78):
```python
def write_video_content(
    video: dict,
    niche_id: str,
    llm_client: Any,
    existing_hooks: Optional[list[str]] = None,
    extra_instructions: Optional[str] = None,
) -> dict:
```

**Change 2:** Update docstring (add after line 86):
```
        extra_instructions: Optional additional instructions appended to system
            prompt (banned phrases, voice overrides, etc.)
```

**Change 3:** Append extra_instructions to system prompt (insert before line 119 — before the "Respond ONLY" line):
```python
        + (
            f"\nADDITIONAL INSTRUCTIONS:\n{extra_instructions}\n\n"
            if extra_instructions else ""
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package genlab-core pytest genlab-core/tests/test_video_content_writer.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
cd /Users/anarchistsid/GenLab/genlab-core
git add src/genlab_core/writing/video_content_writer.py tests/test_video_content_writer.py
git commit -m "feat(core): add extra_instructions param to write_video_content"
```

---

### Task 3: VideoGate pipeline stage

**Files:**
- Create: `genlab-core/src/genlab_core/pipeline/stages/video_gate.py`
- Test: `genlab-core/tests/test_video_gate.py`

- [ ] **Step 1: Write the failing test**

```python
# genlab-core/tests/test_video_gate.py
"""Tests for VideoGate pipeline stage."""
from __future__ import annotations

import unittest


class TestVideoGate(unittest.TestCase):

    def test_story_with_successful_clip_passes(self):
        from genlab_core.pipeline.stages.video_gate import VideoGate

        context = {
            "stories": [
                {"story_id": "abc123", "title": "NBA Finals"},
            ],
            "clip_index": {
                "clips": {
                    "abc123": {"story_id": "abc123", "success": True, "clip_path": "/tmp/abc.mp4"},
                },
            },
        }

        result = VideoGate().execute(context)
        story = result["stories"][0]
        self.assertNotIn("_skip_llm", story)

    def test_story_without_clip_gets_skip_llm(self):
        from genlab_core.pipeline.stages.video_gate import VideoGate

        context = {
            "stories": [
                {"story_id": "def456", "title": "Random Story"},
            ],
            "clip_index": {
                "clips": {
                    "def456": {"story_id": "def456", "success": False, "clip_path": "", "error": "no video found"},
                },
            },
        }

        result = VideoGate().execute(context)
        story = result["stories"][0]
        self.assertTrue(story.get("_skip_llm"))

    def test_story_missing_from_clip_index_gets_skip_llm(self):
        from genlab_core.pipeline.stages.video_gate import VideoGate

        context = {
            "stories": [
                {"story_id": "ghi789", "title": "No Download Attempted"},
            ],
            "clip_index": {"clips": {}},
        }

        result = VideoGate().execute(context)
        self.assertTrue(result["stories"][0].get("_skip_llm"))

    def test_no_clip_index_in_context_skips_all(self):
        from genlab_core.pipeline.stages.video_gate import VideoGate

        context = {
            "stories": [
                {"story_id": "s1", "title": "Story 1"},
                {"story_id": "s2", "title": "Story 2"},
            ],
        }

        result = VideoGate().execute(context)
        for story in result["stories"]:
            self.assertTrue(story.get("_skip_llm"))

    def test_stats_written_to_context(self):
        from genlab_core.pipeline.stages.video_gate import VideoGate

        context = {
            "stories": [
                {"story_id": "a", "title": "A"},
                {"story_id": "b", "title": "B"},
            ],
            "clip_index": {
                "clips": {
                    "a": {"success": True, "clip_path": "/t/a.mp4"},
                    "b": {"success": False, "clip_path": ""},
                },
            },
        }

        result = VideoGate().execute(context)
        stats = result.get("run_stats", {}).get("video_gate", {})
        self.assertEqual(stats["passed"], 1)
        self.assertEqual(stats["skipped"], 1)

    def test_empty_stories_no_error(self):
        from genlab_core.pipeline.stages.video_gate import VideoGate

        context = {"stories": []}
        result = VideoGate().execute(context)
        self.assertEqual(result["stories"], [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package genlab-core pytest genlab-core/tests/test_video_gate.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Write implementation**

```python
# genlab-core/src/genlab_core/pipeline/stages/video_gate.py
"""Pipeline stage: Video gate — filters stories without clips before LLM writing.

Runs AFTER DownloadTopVideos, BEFORE Writing. Stories without a successfully
downloaded video clip are marked with ``_skip_llm = True`` so the writing
stage skips the expensive LLM call.

This prevents burning LLM tokens on content that will never render because
there is no video to attach. Stories without clips stay at DRAFTED status.

Usage in niche.yaml::

    pipeline:
      stages:
        - class: genlab_core.media.download_top_videos.DownloadTopVideos
        - class: genlab_core.pipeline.stages.video_gate.VideoGate
        - class: cw_strategies.writing.SportWritingStrategy
"""
from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


class VideoGate:
    """Mark stories without a downloaded video clip as _skip_llm."""

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        stories = context.get("stories", [])
        if not stories:
            logger.info("[VideoGate] No stories to gate")
            return context

        clip_index = context.get("clip_index", {})
        clips = clip_index.get("clips", {})

        passed = 0
        skipped = 0

        for story in stories:
            story_id = story.get("story_id", "")
            clip_entry = clips.get(story_id, {})
            has_clip = clip_entry.get("success", False) and bool(clip_entry.get("clip_path"))

            if has_clip:
                passed += 1
            else:
                story["_skip_llm"] = True
                skipped += 1
                logger.info(
                    "[VideoGate] No clip for %s '%s' — skipping LLM",
                    story_id[:12],
                    story.get("title", "")[:50],
                )

        logger.info(
            "[VideoGate] %d passed, %d skipped (no clip)",
            passed, skipped,
        )

        context.setdefault("run_stats", {})["video_gate"] = {
            "passed": passed,
            "skipped": skipped,
        }

        return context
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package genlab-core pytest genlab-core/tests/test_video_gate.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
cd /Users/anarchistsid/GenLab/genlab-core
git add src/genlab_core/pipeline/stages/video_gate.py tests/test_video_gate.py
git commit -m "feat(core): add VideoGate stage — skip LLM for stories without clips"
```

---

## Chunk 2: Writing Configs + Strategy Wiring

### Task 4: Create writing.yaml configs for CW/SR/FD

**Files:**
- Create: `ClutchWire/config/writing.yaml`
- Create: `SpliceReel/config/writing.yaml`
- Create: `FrameDrift/config/writing.yaml`

- [ ] **Step 1: Create ClutchWire/config/writing.yaml**

```yaml
# ClutchWire writing voice config — drives LLM prompt for write_video_content()
niche_id: sports
channel_handle: "@ClutchWire"
audience: "sports fans aged 18-35 who want the most exciting moments"
voice: "electrifying, fan-energy, conversational, stats-aware"

hook:
  max_chars: 60
  max_words: 12
  style: "conversational and surprising — react to the video, not summarize it"
  require_proper_noun: true
  banned_phrases:
    - "This is what clutch looks like"
    - "Nobody saw"
    - "The moment this player changed everything"
    - "The trade that changes the league"
    - "Best Play of the Year"
    - "players need to see this"
    - "community is going wild"
    - "making waves right now"
    - "This changes everything"
    - "The moment everything changed"
    - "nobody expected this"
    - "the GOAT"
    - "is this the greatest"
    - "did this team just"
    - "how did this team"
    - "not even fans expected"
  examples:
    - "Bam Adebayo just dropped 83 in a single game"
    - "Jaylen Brown's reaction to the foul bait call"
    - "The Celtics pulled off the biggest trade of the season"
    - "Saka scored TWICE and Arsenal still lost"

hashtags:
  primary: ["#Sports", "#SportsHighlights", "#Clutch", "#Highlights"]
  secondary: ["#NBA", "#NFL", "#Soccer", "#UFC", "#Tennis", "#F1"]

caption:
  cta_options:
    - "follow for daily sports moments"
    - "comment your hot take below"
    - "tag a fan who needs to see this"
    - "save this for later"
```

- [ ] **Step 2: Create SpliceReel/config/writing.yaml**

```yaml
# SpliceReel writing voice config
niche_id: movies
channel_handle: "@SpliceReel"
audience: "movie fans aged 18-40 who want to know what to watch"
voice: "cinephile but accessible, enthusiastic, opinionated about craft"

hook:
  max_chars: 60
  max_words: 12
  style: "react to the trailer/clip — reference specific visual or story detail"
  require_proper_noun: true
  banned_phrases:
    - "Cinema is back"
    - "How did they even film this"
    - "No more excuses"
    - "you need to watch this"
    - "This scene alone is worth the ticket"
    - "nobody is talking about"
    - "changes everything"
    - "the director cooked"
  examples:
    - "Alien Romulus is somehow the best one in 38 years"
    - "Pixar just dropped a full trailer and it's emotional"
    - "The Brutalist runs 3.5 hours and every minute matters"
    - "Gladiator II's Colosseum sequence is practical, not CGI"

hashtags:
  primary: ["#Movies", "#Film", "#Cinema", "#BoxOffice", "#Trailer"]
  secondary: ["#MovieTrailer", "#NewMovie", "#FilmReview", "#Hollywood"]

caption:
  cta_options:
    - "follow for daily cinema moments"
    - "have you seen this yet?"
    - "watch or skip? comment below"
    - "save this for your watchlist"
```

- [ ] **Step 3: Create FrameDrift/config/writing.yaml**

```yaml
# FrameDrift writing voice config
niche_id: anime
channel_handle: "@FrameDrift"
audience: "anime fans aged 16-30 who follow seasonal anime"
voice: "otaku-fluent, excited, community-aware, emotionally reactive"

hook:
  max_chars: 60
  max_words: 12
  style: "react to the clip — reference specific anime title, character, or moment"
  require_proper_noun: true
  banned_phrases:
    - "The anime community is losing it"
    - "This anime is about to blow up"
    - "Watch or skip"
    - "Tag your most otaku friend"
    - "Must watch"
    - "the anime gods delivered"
    - "making waves"
    - "changes everything"
  examples:
    - "MONOGATARI is getting two new seasons this year"
    - "JJK just hit #2 on the NYT bestseller list"
    - "Crunchyroll dropped the full Spring 2026 lineup"
    - "Chainsaw Man Part 2 trailer has Denji in a suit"

hashtags:
  primary: ["#Anime", "#Manga", "#Otaku", "#AnimeCommunity", "#AnimeFan"]
  secondary: ["#SeasonalAnime", "#NewAnime", "#AnimeNews", "#AnimeClips"]

caption:
  cta_options:
    - "follow for daily anime"
    - "are you watching this?"
    - "tag your anime friend"
    - "rate this 1-10 in the comments"
```

- [ ] **Step 4: Commit**

```bash
cd /Users/anarchistsid/GenLab
git -C ClutchWire add config/writing.yaml && git -C ClutchWire commit -m "feat(config): add writing.yaml for LLM voice config"
git -C SpliceReel add config/writing.yaml && git -C SpliceReel commit -m "feat(config): add writing.yaml for LLM voice config"
git -C FrameDrift add config/writing.yaml && git -C FrameDrift commit -m "feat(config): add writing.yaml for LLM voice config"
```

---

### Task 5: Wire write_video_content() into CW/SR/FD writing strategies

**Files:**
- Modify: `ClutchWire/cw_strategies/writing.py`
- Modify: `SpliceReel/sr_strategies/writing.py`
- Modify: `FrameDrift/fd_strategies/writing.py`
- Test: `ClutchWire/tests/test_writing_llm.py`

The same pattern applies to all 3 channels. The key changes to each writing strategy:

1. Import `write_video_content` and `AnthropicLLMClient`
2. Load `writing.yaml` config
3. Build `extra_instructions` string from config (banned phrases, voice, examples)
4. Map story dict to the `video` dict format that `write_video_content()` expects
5. Call `write_video_content()` for stories that don't have `_skip_llm`
6. Store results in `story["content"]`
7. Fall back to current template generation when no API key or `_skip_llm` is set

- [ ] **Step 1: Write test for CW writing strategy LLM path**

```python
# ClutchWire/tests/test_writing_llm.py
"""Tests for SportWritingStrategy LLM integration."""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch


class TestSportWritingLLM(unittest.TestCase):

    @patch("cw_strategies.writing.AnthropicLLMClient")
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    def test_llm_called_for_story_with_clip(self, MockClient):
        from cw_strategies.writing import SportWritingStrategy

        mock_instance = MagicMock()
        MockClient.return_value = mock_instance
        mock_instance.complete.return_value = json.dumps({
            "hook": "LeBron just broke the record",
            "instagram_caption": "Caption #NBA",
            "twitter_content": "Tweet text",
            "youtube_content": "Title?",
            "facebook_content": "FB post",
        })

        strategy = SportWritingStrategy()
        context = {
            "stories": [
                {"story_id": "s1", "title": "LeBron Scores 50",
                 "summary": "Historic game", "sport": "basketball",
                 "teams": ["Lakers"], "source": "espn"},
            ],
            "clip_index": {
                "clips": {"s1": {"success": True, "clip_path": "/t/s1.mp4"}},
            },
            "niche_id": "sports",
        }

        result = strategy.execute(context)
        story = result["stories"][0]
        self.assertEqual(story["content"]["hook"], "LeBron just broke the record")
        mock_instance.complete.assert_called_once()

    def test_skip_llm_story_uses_template_fallback(self):
        from cw_strategies.writing import SportWritingStrategy

        strategy = SportWritingStrategy()
        context = {
            "stories": [
                {"story_id": "s2", "title": "Some Story",
                 "_skip_llm": True, "summary": "text",
                 "teams": [], "source": "rss"},
            ],
        }

        result = strategy.execute(context)
        story = result["stories"][0]
        content = story.get("content", {})
        # Template fallback still writes something
        self.assertTrue(content.get("written"))

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""})
    def test_no_api_key_falls_back_to_templates(self):
        from cw_strategies.writing import SportWritingStrategy

        strategy = SportWritingStrategy()
        context = {
            "stories": [
                {"story_id": "s3", "title": "Game Story", "summary": "s",
                 "teams": [], "source": "rss"},
            ],
        }

        result = strategy.execute(context)
        stats = result.get("run_stats", {}).get("content_writing", {})
        self.assertEqual(stats["status"], "template_based")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package clutchwire pytest ClutchWire/tests/test_writing_llm.py -v`
Expected: ImportError (AnthropicLLMClient not imported yet)

- [ ] **Step 3: Update ClutchWire/cw_strategies/writing.py**

Replace the entire `execute()` method and add LLM helper methods. Keep `_build_caption()` and `_write_story()` as the template fallback path. Add new methods:

```python
# Add these imports at top of file:
import os
import yaml
from genlab_core.writing.video_content_writer import write_video_content
from genlab_core.writing.llm_client import AnthropicLLMClient

# Add to __init__:
    self._writing_config: dict | None = None
    self._llm_client: AnthropicLLMClient | None = None

# Add new methods:
    def _load_writing_config(self) -> dict:
        if self._writing_config is None:
            path = NICHE_ROOT / "config" / "writing.yaml"
            if path.exists():
                with open(path) as f:
                    self._writing_config = yaml.safe_load(f) or {}
            else:
                self._writing_config = {}
        return self._writing_config

    def _build_extra_instructions(self) -> str:
        cfg = self._load_writing_config()
        hook_cfg = cfg.get("hook", {})
        parts = []
        banned = hook_cfg.get("banned_phrases", [])
        if banned:
            parts.append("BANNED PHRASES (never use these or similar):")
            for phrase in banned:
                parts.append(f"  - \"{phrase}\"")
        examples = hook_cfg.get("examples", [])
        if examples:
            parts.append("\nGOOD HOOK EXAMPLES (use this style):")
            for ex in examples:
                parts.append(f"  - \"{ex}\"")
        if hook_cfg.get("require_proper_noun"):
            parts.append("\nHook MUST contain a specific proper noun (player name, team name, etc.)")
        return "\n".join(parts)

    def _story_to_video_dict(self, story: dict) -> dict:
        """Map a sports story dict to the video dict write_video_content expects."""
        return {
            "title": story.get("title", ""),
            "channel_name": story.get("source", ""),
            "view_count": story.get("upvotes", 0),
            "view_velocity": story.get("upvotes", 0),
            "description_snippet": story.get("summary", ""),
            "tags": story.get("teams", []) + [story.get("sport", ""), story.get("league", "")],
        }

    def _get_llm_client(self) -> AnthropicLLMClient | None:
        if self._llm_client is not None:
            return self._llm_client
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return None
        self._llm_client = AnthropicLLMClient(
            api_key=api_key,
            model="claude-haiku-4-5-20251001",
        )
        return self._llm_client
```

Then update `execute()`:
```python
    def execute(self, context: Any) -> Any:
        self._ensure_config()
        stories = context.get("stories", [])
        if not stories:
            logger.info("[sports] WritingStrategy: no stories to write")
            context.setdefault("run_stats", {})["content_writing"] = {
                "status": "no_stories", "written_count": 0,
            }
            return context

        llm_client = self._get_llm_client()
        extra_instructions = self._build_extra_instructions() if llm_client else ""
        used_hooks: list[str] = []

        llm_count = 0
        template_count = 0
        failed_count = 0

        for story in stories:
            try:
                if story.get("_skip_llm") or llm_client is None:
                    self._write_story(story)
                    template_count += 1
                else:
                    video = self._story_to_video_dict(story)
                    result = write_video_content(
                        video=video,
                        niche_id="sports",
                        llm_client=llm_client,
                        existing_hooks=used_hooks[-5:] if used_hooks else None,
                        extra_instructions=extra_instructions,
                    )
                    content = story.setdefault("content", {})
                    content["hook"] = result.get("hook", "")
                    content["hook_category"] = "llm_generated"
                    content["caption"] = result.get("instagram_caption", "")
                    content["written"] = True
                    content["instagram"] = {"caption": result.get("instagram_caption", "")}
                    content["youtube"] = {"title": result.get("youtube_content", "")[:40], "description": result.get("instagram_caption", "")}
                    content["x_twitter"] = {"tweet": result.get("twitter_content", "")}
                    content["facebook"] = {"caption": result.get("facebook_content", "")}
                    content["tiktok"] = {"caption": result.get("instagram_caption", "")[:2200]}
                    content["threads"] = {"caption": result.get("instagram_caption", "")[:500]}
                    if content["hook"]:
                        used_hooks.append(content["hook"])
                    llm_count += 1
            except Exception:
                logger.exception("[sports] Failed to write story: %s", story.get("title", "?"))
                self._write_story(story)
                template_count += 1
                failed_count += 1

        context.setdefault("run_stats", {})["content_writing"] = {
            "status": "llm" if llm_count > 0 else "template_based",
            "llm_count": llm_count,
            "template_count": template_count,
            "failed_count": failed_count,
            "written_count": llm_count + template_count,
        }
        logger.info(
            "[sports] WritingStrategy: %d LLM, %d template, %d failed",
            llm_count, template_count, failed_count,
        )
        return context
```

- [ ] **Step 4: Apply same pattern to SpliceReel/sr_strategies/writing.py**

Same structure as CW. Key differences in `_story_to_video_dict`:
```python
    def _story_to_video_dict(self, story: dict) -> dict:
        return {
            "title": story.get("film_title", "") or story.get("title", ""),
            "channel_name": story.get("source", ""),
            "view_count": int(story.get("tmdb_popularity") or 0),
            "view_velocity": int(story.get("tmdb_popularity") or 0),
            "description_snippet": story.get("summary", ""),
            "tags": [story.get("franchise", ""), story.get("lifecycle_stage", "")],
        }
```
And `niche_id="movies"` in the write_video_content call.

- [ ] **Step 5: Apply same pattern to FrameDrift/fd_strategies/writing.py**

Also fix the docstring: change `"streetwear-literate"` to `"otaku-literate"`.

Key differences in `_story_to_video_dict`:
```python
    def _story_to_video_dict(self, story: dict) -> dict:
        return {
            "title": story.get("title", ""),
            "channel_name": story.get("source", ""),
            "view_count": story.get("source_mention_count", 1) * 1000,
            "view_velocity": story.get("source_mention_count", 1) * 500,
            "description_snippet": story.get("summary", ""),
            "tags": [story.get("trend_name", ""), story.get("trend_cycle_stage", "")],
        }
```
And `niche_id="anime"` in the write_video_content call.

- [ ] **Step 6: Update all 3 hook strategies to pass through LLM-generated hooks**

In each hook strategy (`hooks.py`), add an early return at the start of the per-story loop in `execute()`:
```python
        for story in stories:
            content = story.get("content", {})
            # If write_video_content already generated a hook, use it
            if content.get("hook"):
                content.setdefault("hook_category", "llm_generated")
                categories_used["llm_generated"] = categories_used.get("llm_generated", 0) + 1
                hooked_count += 1
                continue
            # Otherwise fall back to template generation
            ...
```

This makes the hook strategy a no-op for LLM-written stories and preserves the template fallback.

- [ ] **Step 7: Run tests**

Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package clutchwire pytest ClutchWire/tests/ -x -q`
Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package splicereel pytest SpliceReel/tests/ -x -q`
Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package framedrift pytest FrameDrift/tests/ -x -q`
Expected: All passing (existing tests + new test)

- [ ] **Step 8: Commit all 3 channels**

```bash
cd /Users/anarchistsid/GenLab
git -C ClutchWire add -A && git -C ClutchWire commit -m "feat(writing): wire write_video_content() with LLM hooks + banned phrases"
git -C SpliceReel add -A && git -C SpliceReel commit -m "feat(writing): wire write_video_content() with LLM hooks + banned phrases"
git -C FrameDrift add -A && git -C FrameDrift commit -m "feat(writing): wire write_video_content() with LLM hooks, fix streetwear docstring"
```

---

## Chunk 3: Video Gate Registration, DedupEngine, Sources, Fixes

### Task 6: Register VideoGate in all 4 niche.yaml files

**Files:**
- Modify: `ClutchWire/config/niche.yaml`
- Modify: `SpliceReel/config/niche.yaml`
- Modify: `FrameDrift/config/niche.yaml`
- Modify: `CriticalRush/niches/gaming/config/niche.yaml`

- [ ] **Step 1: Insert VideoGate stage into ClutchWire niche.yaml**

Find the line:
```yaml
    - class: genlab_core.media.download_top_videos.DownloadTopVideos
      retries: 1
      retry_delay_seconds: 30
```
Insert AFTER it (before the Writing stage):
```yaml
    - class: genlab_core.pipeline.stages.video_gate.VideoGate
```

- [ ] **Step 2: Same for SpliceReel/config/niche.yaml**
- [ ] **Step 3: Same for FrameDrift/config/niche.yaml**
- [ ] **Step 4: CriticalRush is EXCLUDED from VideoGate**

CriticalRush uses `ExtractGamingMedia` (not `DownloadTopVideos`) and does NOT populate `context["clip_index"]`. Adding VideoGate to CR would skip ALL stories. CR's video gating must be addressed in a future sprint after aligning `ExtractGamingMedia` to write `clip_index`.

- [ ] **Step 5: Commit**

```bash
cd /Users/anarchistsid/GenLab
git -C ClutchWire add config/niche.yaml && git -C ClutchWire commit -m "feat(pipeline): register VideoGate stage"
git -C SpliceReel add config/niche.yaml && git -C SpliceReel commit -m "feat(pipeline): register VideoGate stage"
git -C FrameDrift add config/niche.yaml && git -C FrameDrift commit -m "feat(pipeline): register VideoGate stage"
```

---

### Task 7: Add DedupEngine to CW/SR ContentResearch

**Files:**
- Modify: `ClutchWire/cw_strategies/content_research.py`
- Modify: `SpliceReel/sr_strategies/content_research.py`

- [ ] **Step 1: Add DedupEngine to CW ContentResearch**

In `ClutchWire/cw_strategies/content_research.py`, in the `execute()` method, after `context["stories"] = existing + stories`, add:

```python
        # Deduplicate merged stories
        from genlab_core.intelligence.dedup_engine import DedupEngine

        niche_cfg = context.get("niche_config", {})
        dedup_cfg = niche_cfg.get("dedup", {})
        dedup = DedupEngine(
            jaccard_threshold=dedup_cfg.get("jaccard_threshold", 0.75),
            tfidf_threshold=dedup_cfg.get("tfidf_threshold", 0.70),
            url_field="source_url",
            text_field="title",
        )
        dedup_result = dedup.run(context["stories"])
        context["stories"] = dedup_result.unique

        context.setdefault("run_stats", {}).setdefault("fetch", {})["dedup_removed"] = (
            dedup_result.pass1_removed + dedup_result.pass2_removed + dedup_result.pass3_removed
        )
```

- [ ] **Step 2: Same for SpliceReel/sr_strategies/content_research.py**

Same pattern, same thresholds (can be tuned later via scoring_weights.yaml).

- [ ] **Step 3: Run tests**

Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package clutchwire pytest ClutchWire/tests/ -x -q`
Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package splicereel pytest SpliceReel/tests/ -x -q`

- [ ] **Step 4: Commit**

```bash
cd /Users/anarchistsid/GenLab
git -C ClutchWire add cw_strategies/content_research.py && git -C ClutchWire commit -m "feat(dedup): add DedupEngine to ContentResearch"
git -C SpliceReel add sr_strategies/content_research.py && git -C SpliceReel commit -m "feat(dedup): add DedupEngine to ContentResearch"
```

---

### Task 8: Fix FrameDrift brand safety to read from niche.yaml

**Files:**
- Modify: `FrameDrift/fd_strategies/visual_render.py`

- [ ] **Step 1: Replace hardcoded fashion brands with config-driven check**

In `FrameDrift/fd_strategies/visual_render.py`, replace the hardcoded `_PROTECTED_BRANDS` set (line ~25):

```python
# REMOVE this:
_PROTECTED_BRANDS = frozenset({
    "nike", "adidas", "gucci", "louis vuitton", "chanel",
    "prada", "supreme", "off-white",
})
```

In `_build_pexels_queries()`, change the brand safety check to read from config:

```python
    def _build_pexels_queries(self, story: dict) -> list[str]:
        self._ensure_config()

        configured = (
            self._sources_config
            .get("media", {})
            .get("pexels", {})
            .get("anime_queries", _DEFAULT_ANIME_QUERIES)
        )
        queries = list(configured[:3])

        # Brand safety — read from niche.yaml
        niche_path = NICHE_ROOT / "config" / "niche.yaml"
        niche_cfg = _load_yaml(niche_path) if niche_path.exists() else {}
        protected = set(
            b.lower()
            for b in niche_cfg.get("brand_sensitivity", {}).get("protected_brands", [])
        )

        safe_queries = []
        for q in queries:
            q_lower = q.lower()
            if any(brand in q_lower for brand in protected):
                logger.warning("[visual] Protected brand in Pexels query '%s' — replaced", q)
                safe_queries.append("anime aesthetic lifestyle urban")
            else:
                safe_queries.append(q)

        return safe_queries[:3]
```

- [ ] **Step 2: Run tests**

Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package framedrift pytest FrameDrift/tests/ -x -q`

- [ ] **Step 3: Commit**

```bash
cd /Users/anarchistsid/GenLab
git -C FrameDrift add fd_strategies/visual_render.py && git -C FrameDrift commit -m "fix(brand): read protected brands from niche.yaml, not hardcoded fashion list"
```

---

### Task 9: Update sources.yaml with new YouTube channels and source filters

**Files:**
- Modify: `ClutchWire/config/sources.yaml`
- Modify: `SpliceReel/config/sources.yaml`
- Modify: `FrameDrift/config/sources.yaml`
- Modify: `CriticalRush/niches/gaming/config/sources.yaml`

- [ ] **Step 1: Update ClutchWire sources.yaml**

Remove the `espn_api.scoreboard` section entirely (0% conversion to clips).

Add `source_filters` section:
```yaml
source_filters:
  reject_title_patterns:
    - "tracker"
    - "fantasy"
    - "DFS"
    - "betting"
    - "power rankings"
    - "best players available"
    - "mock draft"
    - "offseason moves"
  reject_angle_patterns:
    - "^\\d+:\\d+ - \\d"
    - "^Scheduled$"
```

Keep `espn_api.news` section (articles sometimes have associated clips).

- [ ] **Step 2: Update SpliceReel sources.yaml**

Add `source_filters` section:
```yaml
source_filters:
  reject_title_patterns:
    - "^opinion:"
    - "^essay:"
    - "why .* should"
    - "^ranking"
    - "best of \\d{4}"
    - "worst of \\d{4}"
    - "^explained"
```

- [ ] **Step 3: Update FrameDrift sources.yaml**

Add `source_filters` section:
```yaml
source_filters:
  reject_title_patterns:
    - "BAFTA"
    - "video game award"
    - "game award"
    - "fashion"
    - "streetwear"
```

- [ ] **Step 4: Update CriticalRush sources.yaml**

Add `source_filters` section:
```yaml
source_filters:
  reject_title_patterns:
    - "deal"
    - "sale"
    - "discount"
    - "price drop"
    - "peripheral"
    - "headset"
    - "controller review"
```

- [ ] **Step 5: Create shared source filter in genlab-core**

Create `genlab-core/src/genlab_core/intelligence/source_filter.py` (shared by all channels — per Layer 1 principle):
```python
import re

def _apply_source_filters(items, config):
    """Apply reject patterns from source_filters config at ingest time."""
    filters = config.get("source_filters", {})
    reject_title = filters.get("reject_title_patterns", [])
    reject_angle = filters.get("reject_angle_patterns", [])
    if not reject_title and not reject_angle:
        return items

    filtered = []
    rejected = 0
    for item in items:
        title = (item.title if hasattr(item, "title") else item.get("title", "")).lower()
        angle = item.summary if hasattr(item, "summary") else item.get("summary", "")
        skip = False
        for pattern in reject_title:
            if re.search(pattern, title, re.IGNORECASE):
                skip = True
                break
        if not skip:
            for pattern in reject_angle:
                if re.search(pattern, angle):
                    skip = True
                    break
        if skip:
            rejected += 1
        else:
            filtered.append(item)

    if rejected:
        logger.info("[filter] Rejected %d items via source_filters", rejected)
    return filtered
```

In each channel's fetch function, import and call:
```python
from genlab_core.intelligence.source_filter import apply_source_filters
# After fetching all items:
items = apply_source_filters(items, config)
```

- [ ] **Step 6: Run tests for all channels**

Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package clutchwire pytest ClutchWire/tests/ -x -q`
Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package splicereel pytest SpliceReel/tests/ -x -q`
Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package framedrift pytest FrameDrift/tests/ -x -q`

- [ ] **Step 7: Commit**

```bash
cd /Users/anarchistsid/GenLab
git -C ClutchWire add config/sources.yaml cw_strategies/fetch_sports_news.py && git -C ClutchWire commit -m "feat(sources): remove espn_scoreboard, add source_filters"
git -C SpliceReel add config/sources.yaml sr_strategies/fetch_film_news.py && git -C SpliceReel commit -m "feat(sources): add source_filters for editorial noise"
git -C FrameDrift add config/sources.yaml fd_strategies/fetch_anime_news.py && git -C FrameDrift commit -m "feat(sources): add source_filters for cross-niche noise"
git -C CriticalRush add niches/gaming/config/sources.yaml && git -C CriticalRush commit -m "feat(sources): add source_filters for deals/peripherals"
```

---

### Task 10: Integration test + source_filter test

- [ ] **Step 0: Write integration test for VideoGate → Writing → Hooks flow**

Create `genlab-core/tests/test_video_gate_integration.py`:

```python
"""Integration test: VideoGate -> Writing -> Hooks end-to-end flow."""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch


class TestVideoGateWritingHooksIntegration(unittest.TestCase):

    def test_story_with_clip_gets_llm_content(self):
        from genlab_core.pipeline.stages.video_gate import VideoGate

        context = {
            "stories": [
                {"story_id": "s1", "title": "LeBron Scores 50",
                 "summary": "Historic game", "sport": "basketball",
                 "teams": ["Lakers"], "source": "espn"},
            ],
            "clip_index": {
                "clips": {"s1": {"success": True, "clip_path": "/t/s1.mp4"}},
            },
        }

        result = VideoGate().execute(context)
        # Story passes gate — no _skip_llm
        self.assertNotIn("_skip_llm", result["stories"][0])

    def test_story_without_clip_skips_llm_and_gets_template(self):
        from genlab_core.pipeline.stages.video_gate import VideoGate

        context = {
            "stories": [
                {"story_id": "s2", "title": "No Video Story",
                 "summary": "text only", "teams": [], "source": "rss"},
            ],
            "clip_index": {"clips": {}},
        }

        result = VideoGate().execute(context)
        story = result["stories"][0]
        self.assertTrue(story.get("_skip_llm"))

    def test_mixed_stories_gate_correctly(self):
        from genlab_core.pipeline.stages.video_gate import VideoGate

        context = {
            "stories": [
                {"story_id": "a", "title": "Has Video"},
                {"story_id": "b", "title": "No Video"},
                {"story_id": "c", "title": "Also Has Video"},
            ],
            "clip_index": {
                "clips": {
                    "a": {"success": True, "clip_path": "/t/a.mp4"},
                    "b": {"success": False, "clip_path": ""},
                    "c": {"success": True, "clip_path": "/t/c.mp4"},
                },
            },
        }

        result = VideoGate().execute(context)
        self.assertNotIn("_skip_llm", result["stories"][0])
        self.assertTrue(result["stories"][1].get("_skip_llm"))
        self.assertNotIn("_skip_llm", result["stories"][2])
        self.assertEqual(result["run_stats"]["video_gate"]["passed"], 2)
        self.assertEqual(result["run_stats"]["video_gate"]["skipped"], 1)
```

- [ ] **Step 0b: Write test for apply_source_filters**

Create `genlab-core/tests/test_source_filter.py`:

```python
"""Tests for source filter utility."""
from __future__ import annotations

import unittest


class TestApplySourceFilters(unittest.TestCase):

    def test_rejects_matching_title_pattern(self):
        from genlab_core.intelligence.source_filter import apply_source_filters

        items = [
            {"title": "NBA Fantasy Draft Guide 2026", "summary": ""},
            {"title": "LeBron drops 50 in Game 7", "summary": ""},
        ]
        config = {"source_filters": {"reject_title_patterns": ["fantasy", "betting"]}}
        result = apply_source_filters(items, config)
        self.assertEqual(len(result), 1)
        self.assertIn("LeBron", result[0]["title"])

    def test_no_filters_returns_all(self):
        from genlab_core.intelligence.source_filter import apply_source_filters

        items = [{"title": "Story 1"}, {"title": "Story 2"}]
        result = apply_source_filters(items, {})
        self.assertEqual(len(result), 2)

    def test_regex_pattern_works(self):
        from genlab_core.intelligence.source_filter import apply_source_filters

        items = [{"title": "10:33 - 2nd Quarter", "summary": ""}]
        config = {"source_filters": {"reject_title_patterns": [r"\d+:\d+ - \d"]}}
        result = apply_source_filters(items, config)
        self.assertEqual(len(result), 0)
```

- [ ] **Step 1: Run full genlab-core test suite**

Run: `cd /Users/anarchistsid/GenLab && ~/.local/bin/uv run --package genlab-core pytest genlab-core/tests/ -x -q`
Expected: All passing (existing ~695 + new tests)

- [ ] **Step 2: Run all channel test suites**

```bash
for repo in CriticalRush ClutchWire SpliceReel FrameDrift; do
    echo "--- $repo ---"
    ~/.local/bin/uv run --package $(echo $repo | tr '[:upper:]' '[:lower:]') pytest "$repo/tests/" -q --tb=short 2>&1 | tail -3
done
```

- [ ] **Step 3: Update submodule pointers from GenLab root**

```bash
cd /Users/anarchistsid/GenLab
git add genlab-core ClutchWire SpliceReel FrameDrift CriticalRush
git commit -m "chore(platform): Sprint 61 — submodule updates (content quality + video gate)"
```
