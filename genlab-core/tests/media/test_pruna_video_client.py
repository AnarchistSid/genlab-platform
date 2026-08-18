"""Pin pruna_video_client (2026-08-18):

  * Flag semantics (off / on / canary / wildcard)
  * Deterministic seed: same (prompt, niche) → same int
  * Prompt builder shape (visual-focused, no narrative padding)
  * Empty topic → ok=False
  * Belt failure → ok=False, error propagates
  * Missing video URL in output → ok=False
  * Download failure → ok=False
  * Success → ok=True, cost + task_id populated
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from genlab_core.media.pruna_video_client import (
    VideoGenResult,
    _build_anime_prompt,
    _deterministic_seed,
    generate_backfill_clip,
    is_enabled_for,
)


class TestFlagSemantics:
    @pytest.mark.parametrize("val", ["", "0", "false", "no", "off"])
    def test_off_tokens(self, monkeypatch, val):
        monkeypatch.setenv("GENLAB_ANIME_BACKFILL_NICHES", val)
        assert is_enabled_for("anime") is False

    def test_unset_off(self, monkeypatch):
        monkeypatch.delenv("GENLAB_ANIME_BACKFILL_NICHES", raising=False)
        assert is_enabled_for("anime") is False

    def test_wildcard_enables_all(self, monkeypatch):
        monkeypatch.setenv("GENLAB_ANIME_BACKFILL_NICHES", "all")
        for n in ("anime", "gaming", "sports", "movies", "ai_creators"):
            assert is_enabled_for(n) is True

    def test_canary_isolation(self, monkeypatch):
        monkeypatch.setenv("GENLAB_ANIME_BACKFILL_NICHES", "anime")
        assert is_enabled_for("anime") is True
        assert is_enabled_for("gaming") is False


class TestDeterministicSeed:
    def test_same_inputs_same_seed(self):
        s1 = _deterministic_seed("prompt A", "anime")
        s2 = _deterministic_seed("prompt A", "anime")
        assert s1 == s2

    def test_different_prompt_different_seed(self):
        s1 = _deterministic_seed("prompt A", "anime")
        s2 = _deterministic_seed("prompt B", "anime")
        assert s1 != s2

    def test_different_niche_different_seed(self):
        s1 = _deterministic_seed("same prompt", "anime")
        s2 = _deterministic_seed("same prompt", "gaming")
        assert s1 != s2

    def test_seed_is_uint32(self):
        s = _deterministic_seed("any", "any")
        assert 0 <= s < 2**32


class TestPromptBuilder:
    def test_prompt_includes_topic(self):
        p = _build_anime_prompt("One Piece Chapter 1150 reveal")
        assert "One Piece Chapter 1150 reveal" in p

    def test_prompt_has_visual_language(self):
        """p-video docs prefer visual + composition language over
        narrative. Verify our seeds carry visual vocabulary."""
        p = _build_anime_prompt("random anime topic")
        assert "anime-style" in p.lower()
        assert "vertical" in p.lower()  # 9:16 hint

    def test_prompt_truncates_long_titles(self):
        long_title = "x" * 200
        p = _build_anime_prompt(long_title)
        # Truncated to 87 chars + ellipsis in the injected portion
        assert "x" * 100 not in p

    def test_prompt_strips_trailing_punctuation(self):
        p = _build_anime_prompt("Big anime moment!!!")
        assert "!!!" not in p


class TestGenerateBackfillClip:
    def test_flag_off_returns_ok_false(self, monkeypatch):
        monkeypatch.delenv("GENLAB_ANIME_BACKFILL_NICHES", raising=False)
        r = generate_backfill_clip("topic", "anime", "/tmp/out.mp4")
        assert r.ok is False
        assert "not enabled" in (r.error or "")

    def test_empty_topic_returns_ok_false(self, monkeypatch):
        monkeypatch.setenv("GENLAB_ANIME_BACKFILL_NICHES", "all")
        r = generate_backfill_clip("", "anime", "/tmp/out.mp4")
        assert r.ok is False
        r = generate_backfill_clip("   ", "anime", "/tmp/out.mp4")
        assert r.ok is False

    def test_belt_failure_returns_ok_false(self, monkeypatch):
        monkeypatch.setenv("GENLAB_ANIME_BACKFILL_NICHES", "all")
        with patch(
            "genlab_core.integrations.belt_client.run_app",
            return_value=MagicMock(
                ok=False, output=None, task_id=None, error="belt down",
            ),
        ):
            r = generate_backfill_clip(
                "topic", "anime", "/tmp/out.mp4",
            )
        assert r.ok is False
        assert "belt down" in (r.error or "")

    def test_missing_video_url_returns_ok_false(self, monkeypatch):
        monkeypatch.setenv("GENLAB_ANIME_BACKFILL_NICHES", "all")
        with patch(
            "genlab_core.integrations.belt_client.run_app",
            return_value=MagicMock(
                ok=True,
                output={"unexpected_key": "some value"},
                task_id="t1", error=None,
            ),
        ):
            r = generate_backfill_clip(
                "topic", "anime", "/tmp/out.mp4",
            )
        assert r.ok is False
        assert "no video URL" in (r.error or "")

    def test_download_failure_returns_ok_false(self, monkeypatch):
        monkeypatch.setenv("GENLAB_ANIME_BACKFILL_NICHES", "all")
        with patch(
            "genlab_core.integrations.belt_client.run_app",
            return_value=MagicMock(
                ok=True,
                output={"video": "https://example.test/v.mp4"},
                task_id="t1", error=None,
            ),
        ), patch(
            "genlab_core.media.pruna_video_client._download",
            return_value=False,
        ):
            r = generate_backfill_clip(
                "topic", "anime", "/tmp/out.mp4",
            )
        assert r.ok is False
        assert "download failed" in (r.error or "")

    def test_success_returns_ok_with_cost_and_task(self, monkeypatch):
        monkeypatch.setenv("GENLAB_ANIME_BACKFILL_NICHES", "all")
        with patch(
            "genlab_core.integrations.belt_client.run_app",
            return_value=MagicMock(
                ok=True,
                output={"video": "https://example.test/v.mp4"},
                task_id="task_xyz", error=None,
            ),
        ), patch(
            "genlab_core.integrations.belt_client.task_cost_usd",
            return_value=0.025,
        ), patch(
            "genlab_core.media.pruna_video_client._download",
            return_value=True,
        ):
            r = generate_backfill_clip(
                "topic", "anime", "/tmp/out.mp4",
            )
        assert r.ok is True
        assert r.cost_usd == 0.025
        assert r.task_id == "task_xyz"
        assert r.video_url == "https://example.test/v.mp4"

    def test_alternate_output_key(self, monkeypatch):
        """Handle apps that use 'video_output' or 'output' instead of 'video'."""
        monkeypatch.setenv("GENLAB_ANIME_BACKFILL_NICHES", "all")
        for key in ("video_output", "output"):
            with patch(
                "genlab_core.integrations.belt_client.run_app",
                return_value=MagicMock(
                    ok=True,
                    output={key: "https://example.test/v.mp4"},
                    task_id="t", error=None,
                ),
            ), patch(
                "genlab_core.integrations.belt_client.task_cost_usd",
                return_value=0.02,
            ), patch(
                "genlab_core.media.pruna_video_client._download",
                return_value=True,
            ):
                r = generate_backfill_clip("t", "anime", "/tmp/o.mp4")
            assert r.ok is True, f"key {key!r} should be recognized"


class TestVideoGenResult:
    def test_default_fields(self):
        r = VideoGenResult(ok=False, prompt="x")
        assert r.video_url is None
        assert r.local_path is None
        assert r.cost_usd is None
        assert r.task_id is None
        assert r.error is None
