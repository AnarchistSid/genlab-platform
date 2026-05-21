"""Mock pipeline stages for integration smoke tests.

These stages simulate each pipeline phase without calling external APIs.
They are referenced from niche configs via importlib-compatible class paths
like ``tests.integration.mock_stages.MockFetchTrendingVideos``.

Each stage follows the ``execute(context: dict) -> dict`` interface
expected by GenericPipelineRunner and StageRunner.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict[str, Any]:
    """Load a JSON fixture file by name."""
    path = FIXTURES_DIR / name
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class MockFetchTrendingVideos:
    """Simulate FetchTrendingVideos — injects mock stories from fixture data."""

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        niche_id = context.get("niche_id", "unknown")
        fixture = _load_fixture("mock_youtube_trending.json")
        stories: list[dict[str, Any]] = []
        now = datetime.now(UTC)

        for item in fixture.get("items", []):
            snippet = item.get("snippet", {})
            stats = item.get("statistics", {})
            video_id = item.get("id", "")
            views = int(stats.get("viewCount", 0))

            stories.append(
                {
                    "story_id": f"yt_{video_id}",
                    "title": snippet.get("title", ""),
                    "summary": snippet.get("description", ""),
                    "source": "youtube",
                    "source_url": f"https://www.youtube.com/watch?v={video_id}",
                    "published_at": snippet.get("publishedAt", now.isoformat()),
                    "video_id": video_id,
                    "views": views,
                    "likes": int(stats.get("likeCount", 0)),
                    "comments_count": int(stats.get("commentCount", 0)),
                    "view_velocity": views / max(1, 24),
                    "score": min(1.0, views / 5_000_000),
                    "niche_id": niche_id,
                    "clip_path": None,
                }
            )

        context.setdefault("stories", []).extend(stories)
        context.setdefault("run_stats", {})["fetch_trending"] = {
            "videos_found": len(stories),
            "source": "mock_youtube",
        }
        return context


class MockScoreAndFilter:
    """Simulate scoring — assigns composite scores to stories."""

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        stories = context.get("stories", [])
        scored = 0

        for i, story in enumerate(stories):
            story["composite_score"] = max(0.1, 1.0 - (i * 0.2))
            story["final_score"] = story["composite_score"]
            story["trend_multiplier"] = 1.2
            scored += 1

        context.setdefault("run_stats", {})["scoring"] = {
            "scored": scored,
            "avg_score": sum(s.get("composite_score", 0) for s in stories) / max(1, len(stories)),
        }
        return context


class MockVideoGate:
    """Simulate video gate — marks all stories as having valid clips."""

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        stories = context.get("stories", [])
        for story in stories:
            story.setdefault("media", {})["clip_path"] = "/tmp/mock_clip.mp4"
            story["_skip_llm"] = False

        clip_index: dict[str, Any] = {"clips": {}}
        for story in stories:
            sid = story.get("story_id", "")
            clip_index["clips"][sid] = {
                "success": True,
                "clip_path": "/tmp/mock_clip.mp4",
                "source_url": story.get("source_url", ""),
            }
        context["clip_index"] = clip_index

        context.setdefault("run_stats", {})["video_gate"] = {
            "passed": len(stories),
            "skipped": 0,
        }
        return context


class MockWriteContent:
    """Simulate LLM content writing — injects mock content from fixture."""

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        fixture = _load_fixture("mock_anthropic_response.json")
        stories = context.get("stories", [])
        content_template = fixture.get("content", {})
        written = 0

        for story in stories:
            if story.get("_skip_llm"):
                continue
            title_words = story.get("title", "").split()[:3]
            hook = " ".join(title_words) if title_words else content_template.get("hook", "")
            story["content"] = {
                "hook": hook[:60],
                "body": content_template.get("body", ""),
                "instagram": content_template.get("instagram", {}),
                "youtube": content_template.get("youtube", {}),
                "x_twitter": content_template.get("x_twitter", {}),
                "facebook": content_template.get("facebook", {}),
            }
            story["hook"] = hook[:60]
            written += 1

        context.setdefault("run_stats", {})["writing"] = {
            "written": written,
            "skipped": len(stories) - written,
        }
        return context


class MockRenderVisuals:
    """Simulate video rendering — sets rendered_path without real FFmpeg."""

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        stories = context.get("stories", [])
        rendered = 0

        for story in stories:
            media = story.setdefault("media", {})
            if story.get("content"):
                media["rendered_path"] = "/tmp/mock_reel.mp4"
                media["compositor"] = "frame_compositor"
                rendered += 1

        blueprints = context.setdefault("blueprints", [])
        for story in stories:
            if story.get("content") and story.get("media", {}).get("rendered_path"):
                content = story["content"]
                blueprints.append(
                    {
                        "candidate_id": f"bp_{story.get('story_id', 'unknown')}",
                        "story_id": story.get("story_id", ""),
                        "hook": content.get("hook", ""),
                        "body": content.get("body", ""),
                        "caption": content.get("instagram", {}).get("caption", ""),
                        "format": "reel",
                        "sources": [story.get("source_url", "")],
                        "source_urls": [story.get("source_url", "")],
                        "priority_score": story.get("final_score", 0.5),
                        "niche_id": context.get("niche_id", "unknown"),
                        "media": story.get("media", {}),
                    }
                )

        context.setdefault("run_stats", {})["rendering"] = {
            "rendered": rendered,
            "blueprints_created": len(blueprints),
        }
        return context


class MockPushToBacklog:
    """Simulate backlog push — records counts without real SharePoint calls."""

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        stories = context.get("stories", [])
        blueprints = context.get("blueprints", [])
        stories_with_content = [s for s in stories if s.get("content")]

        context.setdefault("run_stats", {})["backlog_push"] = {
            "stories_pushed": len(stories_with_content),
            "blueprints_pushed": len(blueprints),
            "video_dedup_skipped": 0,
            "errors": [],
            "status": "ok",
        }
        return context


class MockFetchInsights:
    """Simulate insights fetch — no-op (no previously published posts)."""

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        context.setdefault("run_stats", {})["insights"] = {
            "fetched": 0,
            "skipped": 0,
            "errors": 0,
            "platforms": {},
        }
        return context


class MockPerformanceLearner:
    """Simulate performance learner — no-op (no engagement data yet)."""

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        context.setdefault("run_stats", {})["learning"] = {
            "status": "no_engagement_data",
        }
        return context


class MockEmptyFetch:
    """Stage that fetches zero stories (for edge case testing)."""

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        context.setdefault("stories", [])
        context.setdefault("run_stats", {})["fetch_trending"] = {
            "videos_found": 0,
            "source": "mock_empty",
        }
        return context
