"""Pin: vision judge gates pre-publish frames (Lever G2).

Sibling of test_video_enrichment.py (Lever G1) but exercises the
verdict path instead of the descriptive metadata path.

These pins lock in:

  Pure-logic (5):
  1. is_enabled returns False by default (no env), True when env=1
  2. load_frame_bytes returns None for missing file
  3. load_frame_bytes returns None when file exceeds size cap
  4. load_frame_bytes returns bytes for a small valid file
  5. build_judge_prompt embeds hook + niche + has known issue tags

  Fail-open (4):
  6. judge_frame returns None when env unset
  7. judge_frame returns None when ANTHROPIC_API_KEY missing
  8. judge_frame returns None on API exception
  9. judge_frame returns None on malformed JSON response

  Verdict shape (5):
  10. Valid response → VisionVerdict with parsed fields
  11. Unknown issue tags filtered (not coerced) → only known tags pass
  12. quality_score clamped to [0, 10] when LLM emits 15
  13. confidence clamped to [0, 1] when LLM emits 1.5
  14. Code-fence-wrapped JSON tolerated
  15. Empty recommendation coerces to "no_recommendation" sentinel
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _write_tiny_jpeg(path: Path) -> None:
    """Write a tiny valid-ish bytes blob — enough for load_frame_bytes
    to succeed. Not a real JPEG, but the loader only checks size +
    readability, not magic bytes."""
    path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 1024)


class TestIsEnabled:
    def test_default_off(self, monkeypatch):
        from genlab_core.media.vision_judge import is_enabled

        monkeypatch.delenv("GENLAB_VISION_JUDGE_ENABLED", raising=False)
        assert is_enabled() is False

    def test_env_one_is_on(self, monkeypatch):
        from genlab_core.media.vision_judge import is_enabled

        monkeypatch.setenv("GENLAB_VISION_JUDGE_ENABLED", "1")
        assert is_enabled() is True


class TestLoadFrameBytes:
    def test_missing_file_returns_none(self, tmp_path):
        from genlab_core.media.vision_judge import load_frame_bytes

        assert load_frame_bytes(tmp_path / "nope.jpg") is None

    def test_small_file_returns_bytes(self, tmp_path):
        from genlab_core.media.vision_judge import load_frame_bytes

        p = tmp_path / "ok.jpg"
        _write_tiny_jpeg(p)
        data = load_frame_bytes(p)
        assert data is not None
        assert len(data) > 0

    def test_oversized_file_returns_none(self, tmp_path, monkeypatch):
        """Pin: 4MB cap is enforced — avoids OOM + API rejection."""
        from genlab_core.media import vision_judge

        # Patch the cap down so we don't actually write a 4MB file
        monkeypatch.setattr(vision_judge, "_MAX_FRAME_BYTES", 512)
        p = tmp_path / "big.jpg"
        p.write_bytes(b"x" * 2048)  # 2KB > 512B cap
        assert vision_judge.load_frame_bytes(p) is None


class TestBuildJudgePrompt:
    def test_includes_hook_and_niche(self):
        from genlab_core.media.vision_judge import build_judge_prompt

        prompt = build_judge_prompt(hook_text="Insane 1v5 clutch", niche_id="gaming")
        assert "Insane 1v5 clutch" in prompt
        assert "gaming" in prompt

    def test_advertises_known_issue_tags(self):
        """Pin: the prompt enumerates the closed set so the LLM stays
        within the whitelist (filters that follow are belt+suspenders)."""
        from genlab_core.media.vision_judge import _KNOWN_ISSUES, build_judge_prompt

        prompt = build_judge_prompt(hook_text="x", niche_id="anime")
        for tag in _KNOWN_ISSUES:
            assert tag in prompt


class TestJudgeFrameDisabled:
    def test_returns_none_when_env_unset(self, monkeypatch, tmp_path):
        from genlab_core.media.vision_judge import judge_frame

        monkeypatch.delenv("GENLAB_VISION_JUDGE_ENABLED", raising=False)
        p = tmp_path / "x.jpg"
        _write_tiny_jpeg(p)
        assert judge_frame(p, blueprint_id="bp_001") is None


class TestJudgeFrameFailOpen:
    @pytest.fixture(autouse=True)
    def _enable(self, monkeypatch):
        monkeypatch.setenv("GENLAB_VISION_JUDGE_ENABLED", "1")

    def test_no_api_key_returns_none(self, monkeypatch, tmp_path):
        from genlab_core.media.vision_judge import judge_frame

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        p = tmp_path / "x.jpg"
        _write_tiny_jpeg(p)
        assert judge_frame(p, blueprint_id="bp_001") is None

    def test_missing_frame_returns_none(self, monkeypatch, tmp_path):
        from genlab_core.media.vision_judge import judge_frame

        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
        # File never written
        assert judge_frame(tmp_path / "missing.jpg", blueprint_id="bp_001") is None

    def test_api_exception_returns_none(self, monkeypatch, tmp_path):
        from genlab_core.media.vision_judge import judge_frame

        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
        p = tmp_path / "x.jpg"
        _write_tiny_jpeg(p)

        with patch("anthropic.Anthropic", side_effect=RuntimeError("simulated outage")):
            assert judge_frame(p, blueprint_id="bp_001") is None

    def test_malformed_json_returns_none(self, monkeypatch, tmp_path):
        from genlab_core.media.vision_judge import judge_frame

        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
        p = tmp_path / "x.jpg"
        _write_tiny_jpeg(p)

        fake_resp = MagicMock()
        fake_resp.content = [MagicMock(text="not valid json {[}")]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_resp

        with patch("anthropic.Anthropic", return_value=fake_client):
            assert judge_frame(p, blueprint_id="bp_001") is None


class TestJudgeFrameVerdicts:
    @pytest.fixture(autouse=True)
    def _enable(self, monkeypatch):
        monkeypatch.setenv("GENLAB_VISION_JUDGE_ENABLED", "1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

    def test_valid_response_returns_verdict(self, tmp_path):
        from genlab_core.media.vision_judge import judge_frame

        p = tmp_path / "x.jpg"
        _write_tiny_jpeg(p)

        fake_resp = MagicMock()
        fake_resp.content = [
            MagicMock(
                text='{"quality_score": 8.5, "issues": ["low_contrast"], '
                '"recommendation": "Increase hook text size", "confidence": 0.9}'
            )
        ]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_resp

        with patch("anthropic.Anthropic", return_value=fake_client):
            verdict = judge_frame(p, blueprint_id="bp_001", hook_text="x", niche_id="gaming")

        assert verdict is not None
        assert verdict.quality_score == 8.5
        assert verdict.issues == ["low_contrast"]
        assert "hook text size" in verdict.recommendation
        assert verdict.confidence == 0.9
        assert verdict.blueprint_id == "bp_001"

    def test_unknown_issue_tags_filtered(self, tmp_path):
        """Pin: free-text LLM creativity stays out of downstream
        aggregations. ``platform_drift`` is not in the whitelist."""
        from genlab_core.media.vision_judge import judge_frame

        p = tmp_path / "x.jpg"
        _write_tiny_jpeg(p)

        fake_resp = MagicMock()
        fake_resp.content = [
            MagicMock(
                text='{"quality_score": 5, "issues": ["low_contrast", "made_up_tag", '
                '"vibes_off"], "recommendation": "x", "confidence": 0.5}'
            )
        ]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_resp

        with patch("anthropic.Anthropic", return_value=fake_client):
            verdict = judge_frame(p, blueprint_id="bp_001")

        assert verdict is not None
        # Only the whitelisted tag survives
        assert verdict.issues == ["low_contrast"]

    def test_quality_score_clamped(self, tmp_path):
        """Pin: LLM emits 15.0 → clamped to 10.0 so downstream consumers
        can rely on the [0, 10] invariant."""
        from genlab_core.media.vision_judge import judge_frame

        p = tmp_path / "x.jpg"
        _write_tiny_jpeg(p)

        fake_resp = MagicMock()
        fake_resp.content = [
            MagicMock(
                text='{"quality_score": 15, "issues": [], "recommendation": "x", "confidence": 0.5}'
            )
        ]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_resp

        with patch("anthropic.Anthropic", return_value=fake_client):
            verdict = judge_frame(p, blueprint_id="bp_001")

        assert verdict is not None
        assert verdict.quality_score == 10.0  # Clamped from 15

    def test_confidence_clamped(self, tmp_path):
        from genlab_core.media.vision_judge import judge_frame

        p = tmp_path / "x.jpg"
        _write_tiny_jpeg(p)

        fake_resp = MagicMock()
        fake_resp.content = [
            MagicMock(
                text='{"quality_score": 5, "issues": [], "recommendation": "x", "confidence": 1.5}'
            )
        ]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_resp

        with patch("anthropic.Anthropic", return_value=fake_client):
            verdict = judge_frame(p, blueprint_id="bp_001")

        assert verdict is not None
        assert verdict.confidence == 1.0  # Clamped from 1.5

    def test_tolerates_code_fence_wrapped_json(self, tmp_path):
        from genlab_core.media.vision_judge import judge_frame

        p = tmp_path / "x.jpg"
        _write_tiny_jpeg(p)

        fake_resp = MagicMock()
        fake_resp.content = [
            MagicMock(
                text='```json\n{"quality_score": 7, "issues": ["nsfw_risk"], '
                '"recommendation": "regen", "confidence": 0.8}\n```'
            )
        ]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_resp

        with patch("anthropic.Anthropic", return_value=fake_client):
            verdict = judge_frame(p, blueprint_id="bp_001")

        assert verdict is not None
        assert verdict.quality_score == 7.0
        assert verdict.issues == ["nsfw_risk"]

    def test_empty_recommendation_coerced_to_sentinel(self, tmp_path):
        """Pin: empty/whitespace recommendation collapses to the
        sentinel string so downstream string-rendering never has to
        handle empty values."""
        from genlab_core.media.vision_judge import judge_frame

        p = tmp_path / "x.jpg"
        _write_tiny_jpeg(p)

        fake_resp = MagicMock()
        fake_resp.content = [
            MagicMock(
                text='{"quality_score": 5, "issues": [], '
                '"recommendation": "   ", "confidence": 0.5}'
            )
        ]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_resp

        with patch("anthropic.Anthropic", return_value=fake_client):
            verdict = judge_frame(p, blueprint_id="bp_001")

        assert verdict is not None
        assert verdict.recommendation == "no_recommendation"
