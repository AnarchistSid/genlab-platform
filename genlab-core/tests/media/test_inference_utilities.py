"""Pin inference_utilities (task #210, 2026-08-18):

Four fail-open helpers backed by the shared belt_client wrapper.
Coverage: flag gate, belt failure paths, output URL extraction,
download failure, success path, per-helper input schema shape.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from genlab_core.media.inference_utilities import (
    UtilityResult,
    _flag_enabled,
    _run_and_download,
    edit_image,
    isolate_voice,
    remove_background,
    upscale_image,
)


class TestFlagGate:
    @pytest.mark.parametrize("val", ["", "0", "false", "no", "off"])
    def test_off_tokens(self, monkeypatch, val):
        monkeypatch.setenv("GENLAB_INFERENCE_UTILITIES_ENABLED", val)
        assert _flag_enabled() is False

    def test_unset_off(self, monkeypatch):
        monkeypatch.delenv(
            "GENLAB_INFERENCE_UTILITIES_ENABLED", raising=False,
        )
        assert _flag_enabled() is False

    @pytest.mark.parametrize("val", ["1", "true", "yes", "on"])
    def test_on_tokens(self, monkeypatch, val):
        monkeypatch.setenv("GENLAB_INFERENCE_UTILITIES_ENABLED", val)
        assert _flag_enabled() is True

    def test_flag_off_short_circuits_all_helpers(self, monkeypatch):
        monkeypatch.delenv(
            "GENLAB_INFERENCE_UTILITIES_ENABLED", raising=False,
        )
        for helper, args in (
            (isolate_voice, ("https://x/a.mp3", "/tmp/o.mp3")),
            (remove_background, ("https://x/i.jpg", "/tmp/o.png")),
            (upscale_image, ("https://x/i.jpg", "/tmp/o.jpg")),
        ):
            r = helper(*args)
            assert r.ok is False
            assert "not enabled" in (r.error or "")
        # edit_image has a different signature
        r = edit_image("prompt", ["https://x/i.jpg"], "/tmp/o.jpg")
        assert r.ok is False


class TestSharedRunner:
    def _mock_belt_success(self, output: dict):
        return MagicMock(
            ok=True, output=output, task_id="task_xyz", error=None,
        )

    def _mock_belt_failure(self):
        return MagicMock(
            ok=False, output=None, task_id=None, error="belt down",
        )

    def test_belt_failure_returns_ok_false(self, monkeypatch):
        monkeypatch.setenv("GENLAB_INFERENCE_UTILITIES_ENABLED", "1")
        with patch(
            "genlab_core.integrations.belt_client.run_app",
            return_value=self._mock_belt_failure(),
        ):
            r = _run_and_download("test/app", {"x": 1}, "/tmp/out.mp4")
        assert r.ok is False
        assert "belt down" in (r.error or "")

    def test_no_output_url_returns_ok_false(self, monkeypatch):
        monkeypatch.setenv("GENLAB_INFERENCE_UTILITIES_ENABLED", "1")
        with patch(
            "genlab_core.integrations.belt_client.run_app",
            return_value=self._mock_belt_success({"unrelated": "value"}),
        ):
            r = _run_and_download("test/app", {"x": 1}, "/tmp/out.mp4")
        assert r.ok is False
        assert "no output URL" in (r.error or "")

    def test_download_failure_returns_ok_false(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_INFERENCE_UTILITIES_ENABLED", "1")
        with patch(
            "genlab_core.integrations.belt_client.run_app",
            return_value=self._mock_belt_success({"output": "https://x/y"}),
        ), patch(
            "genlab_core.media.inference_utilities._download",
            return_value=False,
        ):
            r = _run_and_download(
                "test/app", {"x": 1}, str(tmp_path / "o.mp4"),
            )
        assert r.ok is False
        assert "download failed" in (r.error or "")
        # output_url still returned so caller can retry with alternative
        assert r.output_url == "https://x/y"

    def test_success_returns_ok_with_cost(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_INFERENCE_UTILITIES_ENABLED", "1")
        with patch(
            "genlab_core.integrations.belt_client.run_app",
            return_value=self._mock_belt_success({"image": "https://x/y.jpg"}),
        ), patch(
            "genlab_core.integrations.belt_client.task_cost_usd",
            return_value=0.008,
        ), patch(
            "genlab_core.media.inference_utilities._download",
            return_value=True,
        ):
            r = _run_and_download(
                "test/app", {"x": 1}, str(tmp_path / "o.jpg"),
                output_url_keys=("image", "output"),
            )
        assert r.ok is True
        assert r.cost_usd == 0.008
        assert r.task_id == "task_xyz"

    def test_output_list_shape_extracted(self, monkeypatch, tmp_path):
        """Some apps return `{'images': ['https://...']}` — support list-of."""
        monkeypatch.setenv("GENLAB_INFERENCE_UTILITIES_ENABLED", "1")
        with patch(
            "genlab_core.integrations.belt_client.run_app",
            return_value=self._mock_belt_success({"image": ["https://a", "https://b"]}),
        ), patch(
            "genlab_core.integrations.belt_client.task_cost_usd",
            return_value=0.008,
        ), patch(
            "genlab_core.media.inference_utilities._download",
            return_value=True,
        ):
            r = _run_and_download(
                "test/app", {"x": 1}, str(tmp_path / "o.jpg"),
                output_url_keys=("image",),
            )
        assert r.ok is True
        assert r.output_url == "https://a"


class TestIsolateVoice:
    def test_belt_call_uses_correct_app_and_audio_key(
        self, monkeypatch, tmp_path,
    ):
        monkeypatch.setenv("GENLAB_INFERENCE_UTILITIES_ENABLED", "1")
        with patch(
            "genlab_core.integrations.belt_client.run_app",
            return_value=MagicMock(
                ok=True, output={"audio": "https://x/clean.mp3"},
                task_id="t", error=None,
            ),
        ) as mock_run, patch(
            "genlab_core.integrations.belt_client.task_cost_usd",
            return_value=0.05,
        ), patch(
            "genlab_core.media.inference_utilities._download",
            return_value=True,
        ):
            r = isolate_voice(
                "https://x/noisy.mp3", str(tmp_path / "clean.mp3"),
            )
        assert r.ok is True
        # First positional arg = app name
        args, _ = mock_run.call_args
        assert args[0] == "elevenlabs/voice-isolator"
        # Second positional arg = input dict with audio key
        assert args[1]["audio"] == "https://x/noisy.mp3"


class TestRemoveBackground:
    def test_default_returns_transparent_no_bg_fill(
        self, monkeypatch, tmp_path,
    ):
        monkeypatch.setenv("GENLAB_INFERENCE_UTILITIES_ENABLED", "1")
        with patch(
            "genlab_core.integrations.belt_client.run_app",
            return_value=MagicMock(
                ok=True, output={"image": "https://x/no-bg.png"},
                task_id="t", error=None,
            ),
        ) as mock_run, patch(
            "genlab_core.integrations.belt_client.task_cost_usd",
            return_value=0.01,
        ), patch(
            "genlab_core.media.inference_utilities._download",
            return_value=True,
        ):
            remove_background("https://x/i.jpg", str(tmp_path / "o.png"))
        args, _ = mock_run.call_args
        assert args[0] == "infsh/birefnet"
        assert args[1]["fill_background"] is False
        assert "background_color" not in args[1]

    def test_bg_color_enables_fill(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_INFERENCE_UTILITIES_ENABLED", "1")
        with patch(
            "genlab_core.integrations.belt_client.run_app",
            return_value=MagicMock(
                ok=True, output={"image": "https://x/y.png"},
                task_id="t", error=None,
            ),
        ) as mock_run, patch(
            "genlab_core.integrations.belt_client.task_cost_usd",
            return_value=0.01,
        ), patch(
            "genlab_core.media.inference_utilities._download",
            return_value=True,
        ):
            remove_background(
                "https://x/i.jpg", str(tmp_path / "o.png"),
                background_color="#FF0000FF",
            )
        args, _ = mock_run.call_args
        assert args[1]["fill_background"] is True
        assert args[1]["background_color"] == "#FF0000FF"


class TestUpscaleImage:
    def test_default_2x_face_enhance_standard_model(
        self, monkeypatch, tmp_path,
    ):
        monkeypatch.setenv("GENLAB_INFERENCE_UTILITIES_ENABLED", "1")
        with patch(
            "genlab_core.integrations.belt_client.run_app",
            return_value=MagicMock(
                ok=True, output={"image": "https://x/big.jpg"},
                task_id="t", error=None,
            ),
        ) as mock_run, patch(
            "genlab_core.integrations.belt_client.task_cost_usd",
            return_value=0.08,
        ), patch(
            "genlab_core.media.inference_utilities._download",
            return_value=True,
        ):
            upscale_image("https://x/small.jpg", str(tmp_path / "big.jpg"))
        args, _ = mock_run.call_args
        assert args[0] == "falai/topaz-image-upscaler"
        assert args[1]["upscale_factor"] == 2
        assert args[1]["face_enhancement"] is True
        assert args[1]["model"] == "Standard V2"


class TestEditImage:
    def test_prompt_and_images_list_passed_through(
        self, monkeypatch, tmp_path,
    ):
        monkeypatch.setenv("GENLAB_INFERENCE_UTILITIES_ENABLED", "1")
        with patch(
            "genlab_core.integrations.belt_client.run_app",
            return_value=MagicMock(
                ok=True, output={"image": "https://x/edited.jpg"},
                task_id="t", error=None,
            ),
        ) as mock_run, patch(
            "genlab_core.integrations.belt_client.task_cost_usd",
            return_value=0.01,
        ), patch(
            "genlab_core.media.inference_utilities._download",
            return_value=True,
        ):
            edit_image(
                "brighten the background",
                ["https://x/i.jpg"],
                str(tmp_path / "o.jpg"),
            )
        args, _ = mock_run.call_args
        assert args[0] == "pruna/p-image-edit"
        assert args[1]["prompt"] == "brighten the background"
        assert args[1]["images"] == ["https://x/i.jpg"]
        assert args[1]["turbo"] is True  # default recommended


class TestUtilityResult:
    def test_default_fields_are_none(self):
        r = UtilityResult(ok=False)
        assert r.output_path is None
        assert r.output_url is None
        assert r.cost_usd is None
        assert r.task_id is None
        assert r.error is None
