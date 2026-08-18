"""Pin hook_thumbnail (2026-08-18):

  * Flag semantics (off / on / canary / wildcard)
  * Empty hook returns (False, None) — never generates
  * Unknown niche returns (False, None) — no prompt seed
  * Belt failure returns (False, None) — fail-open
  * Belt success + download + overlay → returns (True, cost)
  * Deterministic seed: same (hook, niche) → same int
  * Cost telemetry propagates from belt task
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from genlab_core.media.hook_thumbnail import (
    _NICHE_PROMPT_SEEDS,
    _deterministic_seed,
    generate_hook_thumbnail,
    is_enabled_for,
    prepend_intro_to_composite,
)


class TestFlagSemantics:
    @pytest.mark.parametrize("val", ["", "0", "false", "no", "off"])
    def test_off_tokens(self, monkeypatch, val):
        monkeypatch.setenv("GENLAB_HOOK_THUMBNAIL_NICHES", val)
        assert is_enabled_for("ai_creators") is False

    def test_unset_off(self, monkeypatch):
        monkeypatch.delenv("GENLAB_HOOK_THUMBNAIL_NICHES", raising=False)
        assert is_enabled_for("ai_creators") is False

    def test_wildcard(self, monkeypatch):
        monkeypatch.setenv("GENLAB_HOOK_THUMBNAIL_NICHES", "all")
        for n in ("ai_creators", "gaming", "sports", "movies", "anime"):
            assert is_enabled_for(n) is True

    def test_canary_isolation(self, monkeypatch):
        monkeypatch.setenv("GENLAB_HOOK_THUMBNAIL_NICHES", "ai_creators")
        assert is_enabled_for("ai_creators") is True
        assert is_enabled_for("gaming") is False


class TestDeterministicSeed:
    def test_same_inputs_same_seed(self):
        s1 = _deterministic_seed("Why AI cured cancer", "ai_creators")
        s2 = _deterministic_seed("Why AI cured cancer", "ai_creators")
        assert s1 == s2

    def test_different_hook_different_seed(self):
        s1 = _deterministic_seed("hook one", "ai_creators")
        s2 = _deterministic_seed("hook two", "ai_creators")
        assert s1 != s2

    def test_different_niche_different_seed(self):
        s1 = _deterministic_seed("same hook", "ai_creators")
        s2 = _deterministic_seed("same hook", "gaming")
        assert s1 != s2

    def test_seed_is_uint32(self):
        s = _deterministic_seed("any", "any")
        assert 0 <= s < 2**32


class TestPromptSeeds:
    def test_every_niche_has_prompt(self):
        for niche in ("ai_creators", "gaming", "sports", "movies", "anime"):
            assert niche in _NICHE_PROMPT_SEEDS
            assert len(_NICHE_PROMPT_SEEDS[niche]) > 40

    def test_prompts_explicitly_forbid_text(self):
        """Flux can't render readable text — prompts must instruct
        it not to try, and reserve space for our drawtext overlay."""
        for niche, prompt in _NICHE_PROMPT_SEEDS.items():
            assert "no text" in prompt.lower(), (
                f"{niche} prompt allows text: {prompt}"
            )


class TestGenerateHookThumbnail:
    def test_flag_off_returns_false(self, monkeypatch):
        monkeypatch.delenv("GENLAB_HOOK_THUMBNAIL_NICHES", raising=False)
        ok, cost = generate_hook_thumbnail(
            "any", "ai_creators", "/tmp/out.mp4",
        )
        assert ok is False and cost is None

    def test_empty_hook_returns_false(self, monkeypatch):
        monkeypatch.setenv("GENLAB_HOOK_THUMBNAIL_NICHES", "all")
        ok, cost = generate_hook_thumbnail("", "ai_creators", "/tmp/out.mp4")
        assert ok is False and cost is None
        ok, cost = generate_hook_thumbnail("   ", "ai_creators", "/tmp/out.mp4")
        assert ok is False and cost is None

    def test_unknown_niche_returns_false(self, monkeypatch):
        monkeypatch.setenv("GENLAB_HOOK_THUMBNAIL_NICHES", "all")
        ok, cost = generate_hook_thumbnail(
            "any", "nonexistent_niche", "/tmp/out.mp4",
        )
        assert ok is False and cost is None

    def test_belt_failure_returns_false(self, monkeypatch):
        monkeypatch.setenv("GENLAB_HOOK_THUMBNAIL_NICHES", "all")
        with patch(
            "genlab_core.integrations.belt_client.run_app",
            return_value=MagicMock(
                ok=False, output=None, task_id=None, error="belt down",
            ),
        ):
            ok, cost = generate_hook_thumbnail(
                "hook", "ai_creators", "/tmp/out.mp4",
            )
        assert ok is False and cost is None

    def test_success_returns_true_with_cost(self, monkeypatch):
        monkeypatch.setenv("GENLAB_HOOK_THUMBNAIL_NICHES", "all")
        good_result = MagicMock(
            ok=True,
            output={"image": "https://example.test/x.png"},
            task_id="task_123",
            error=None,
        )
        with patch(
            "genlab_core.integrations.belt_client.run_app",
            return_value=good_result,
        ), patch(
            "genlab_core.integrations.belt_client.task_cost_usd",
            return_value=0.005,
        ), patch(
            "genlab_core.media.hook_thumbnail._download",
            return_value=True,
        ), patch(
            "genlab_core.media.hook_thumbnail._overlay_text_and_pad",
            return_value=True,
        ):
            ok, cost = generate_hook_thumbnail(
                "test hook", "ai_creators", "/tmp/out.mp4",
            )
        assert ok is True
        assert cost == 0.005

    def test_download_failure_returns_false(self, monkeypatch):
        monkeypatch.setenv("GENLAB_HOOK_THUMBNAIL_NICHES", "all")
        with patch(
            "genlab_core.integrations.belt_client.run_app",
            return_value=MagicMock(
                ok=True,
                output={"image": "https://example.test/x.png"},
                task_id="t1", error=None,
            ),
        ), patch(
            "genlab_core.media.hook_thumbnail._download",
            return_value=False,
        ):
            ok, cost = generate_hook_thumbnail(
                "hook", "ai_creators", "/tmp/out.mp4",
            )
        assert ok is False and cost is None

    def test_alternate_image_output_key(self, monkeypatch):
        """Some apps return image under 'image_output' key. Support both."""
        monkeypatch.setenv("GENLAB_HOOK_THUMBNAIL_NICHES", "all")
        with patch(
            "genlab_core.integrations.belt_client.run_app",
            return_value=MagicMock(
                ok=True,
                output={"image_output": "https://example.test/x.png"},
                task_id="t2", error=None,
            ),
        ), patch(
            "genlab_core.integrations.belt_client.task_cost_usd",
            return_value=0.005,
        ), patch(
            "genlab_core.media.hook_thumbnail._download", return_value=True,
        ), patch(
            "genlab_core.media.hook_thumbnail._overlay_text_and_pad",
            return_value=True,
        ):
            ok, cost = generate_hook_thumbnail(
                "hook", "ai_creators", "/tmp/out.mp4",
            )
        assert ok is True


class TestPrependIntroToComposite:
    def test_missing_ffmpeg_returns_false(self):
        with patch(
            "genlab_core.media.hook_thumbnail.shutil.which",
            return_value=None,
        ):
            assert prepend_intro_to_composite(
                "/tmp/c.mp4", "/tmp/i.mp4", "/tmp/o.mp4",
            ) is False

    def test_missing_input_returns_false(self, tmp_path):
        # Only composite exists — intro missing → False.
        composite = tmp_path / "c.mp4"
        composite.write_bytes(b"fake")
        with patch(
            "genlab_core.media.hook_thumbnail.shutil.which",
            return_value="/opt/ffmpeg",
        ):
            assert prepend_intro_to_composite(
                str(composite), "/tmp/does-not-exist.mp4",
                str(tmp_path / "o.mp4"),
            ) is False

    def test_subprocess_nonzero_returns_false(self, tmp_path):
        composite = tmp_path / "c.mp4"
        intro = tmp_path / "i.mp4"
        composite.write_bytes(b"fake")
        intro.write_bytes(b"fake")
        with patch(
            "genlab_core.media.hook_thumbnail.shutil.which",
            return_value="/opt/ffmpeg",
        ), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stderr="ffmpeg error", stdout="",
            )
            assert prepend_intro_to_composite(
                str(composite), str(intro), str(tmp_path / "o.mp4"),
            ) is False

    def test_subprocess_success_returns_true(self, tmp_path):
        composite = tmp_path / "c.mp4"
        intro = tmp_path / "i.mp4"
        composite.write_bytes(b"fake")
        intro.write_bytes(b"fake")
        with patch(
            "genlab_core.media.hook_thumbnail.shutil.which",
            return_value="/opt/ffmpeg",
        ), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stderr="", stdout="",
            )
            assert prepend_intro_to_composite(
                str(composite), str(intro), str(tmp_path / "o.mp4"),
            ) is True

    def test_subprocess_timeout_returns_false(self, tmp_path):
        composite = tmp_path / "c.mp4"
        intro = tmp_path / "i.mp4"
        composite.write_bytes(b"fake")
        intro.write_bytes(b"fake")
        import subprocess as _sp
        with patch(
            "genlab_core.media.hook_thumbnail.shutil.which",
            return_value="/opt/ffmpeg",
        ), patch(
            "subprocess.run",
            side_effect=_sp.TimeoutExpired(cmd="ffmpeg", timeout=60),
        ):
            assert prepend_intro_to_composite(
                str(composite), str(intro), str(tmp_path / "o.mp4"),
            ) is False
