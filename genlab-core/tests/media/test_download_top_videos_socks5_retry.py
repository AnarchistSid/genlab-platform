"""Pin the 2026-07-22 stage-runner retry activation for SOCKS5/WARP failures.

History: `pipeline_template.yaml:92` has had `retries: 1,
retry_delay_seconds: 30` declared on the DownloadTopVideos stage since
that config-driven retry framework landed. But `DownloadTopVideos.execute()`
caught every yt-dlp error internally (returned `{success: False, error:
"..."}` in the entries dict) and never raised — so `LocalStageRunner`
at `stage_runner.py:164` (which retries only on `Exception`) never fired.
The config knob was DEAD.

Today's 2× WARP flaps (09:00 IST + 15:51 IST) killed 100% of movies
downloads because yt-dlp's own 2 retries × 30s socket-timeout couldn't
ride a 30-minute proxy outage. If the stage-runner retry had fired,
even a 30-second delay might have caught the flap's tail.

Fix: `execute()` now raises `ProxyOutageDetected` when EVERY attempted
download failed AND every failure carries a SOCKS5-shaped error string.
The stage runner then activates its dormant retry.

Partial success is NOT treated as a proxy outage — some downloads
succeeding while others hit SOCKS5 typically means per-video quirks
(bot detection wall, deleted video, signature-decryption). Retrying
those doesn't help.

These pins lock the raise/no-raise contract so a future refactor of
`execute()` can't silently regress back to the DEAD-KNOB state.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from genlab_core.media.download_top_videos import (
    DownloadTopVideos,
    ProxyOutageDetected,
    _is_socks5_shaped_error,
)


class TestSOCKS5ErrorDetection:
    def test_socks5_error_keyword(self) -> None:
        assert _is_socks5_shaped_error("SOCKS5 host unreachable") is True

    def test_host_unreachable_keyword(self) -> None:
        assert _is_socks5_shaped_error("Errno 4 host unreachable") is True

    def test_connection_refused_keyword(self) -> None:
        assert _is_socks5_shaped_error("connection refused via proxy") is True

    def test_case_insensitive(self) -> None:
        assert _is_socks5_shaped_error("HOST UNREACHABLE") is True
        assert _is_socks5_shaped_error("Socks5 tunnel error") is True

    def test_youtube_bot_detection_not_socks5(self) -> None:
        """YouTube's 'Sign in to confirm youre not a bot' isn't a SOCKS5
        failure — retrying via the same proxy won't help."""
        err = "ERROR: [youtube] Sign in to confirm you're not a bot"
        assert _is_socks5_shaped_error(err) is False

    def test_video_404_not_socks5(self) -> None:
        """A per-video 404 (deleted video) isn't a proxy outage."""
        assert _is_socks5_shaped_error("HTTP Error 404: Not Found") is False

    def test_empty_string_returns_false(self) -> None:
        assert _is_socks5_shaped_error("") is False

    def test_none_input_returns_false(self) -> None:
        assert _is_socks5_shaped_error(None) is False  # type: ignore[arg-type]


class TestExecuteRaisesOnAllSocks5(FailureBase := object):
    """`execute()` MUST raise ProxyOutageDetected iff all failed downloads
    are SOCKS5-shaped. Any other case must NOT raise."""

    def _run_execute(self, tmp_path: Path, entries: dict) -> dict | None:
        """Invoke execute() with mocked download_videos_for_stories.
        Returns context on success, or lets exceptions propagate."""
        stage = DownloadTopVideos()
        stories = [{"story_id": f"s{i}"} for i in range(len(entries))]
        context = {
            "stories": stories,
            "run_dir": str(tmp_path),
            "niche_id": "gaming",
            "run_id": "test-run",
        }
        with patch(
            "genlab_core.media.download_top_videos.download_videos_for_stories",
            return_value=entries,
        ):
            return stage.execute(context)

    def test_all_socks5_failure_raises(self, tmp_path: Path) -> None:
        """Real regression from today: WARP flap makes all downloads fail
        with SOCKS5 error. Must raise so stage-runner retries."""
        entries = {
            "s0": {"success": False, "error": "SOCKS5 Host unreachable"},
            "s1": {"success": False, "error": "Errno 4 host unreachable"},
            "s2": {"success": False, "error": "SOCKS5 connection refused"},
        }
        with pytest.raises(ProxyOutageDetected) as exc_info:
            self._run_execute(tmp_path, entries)
        assert "3 downloads failed" in str(exc_info.value)
        # clip_index.json MUST still be written before the raise so the
        # operator can inspect the failure via the run artifacts.
        clip_index_path = tmp_path / "clip_index.json"
        assert clip_index_path.exists()
        payload = json.loads(clip_index_path.read_text())
        assert payload["videos_downloaded"] == 0

    def test_partial_success_does_not_raise(self, tmp_path: Path) -> None:
        """One success + 2 SOCKS5 failures = NOT a proxy outage. Retry won't
        help the failures. Don't waste a retry cycle."""
        entries = {
            "s0": {"success": True, "clip_path": "/tmp/a.mp4"},
            "s1": {"success": False, "error": "SOCKS5 host unreachable"},
            "s2": {"success": False, "error": "SOCKS5 host unreachable"},
        }
        context = self._run_execute(tmp_path, entries)
        assert context is not None
        assert "clip_index" in context

    def test_all_fail_non_socks5_does_not_raise(self, tmp_path: Path) -> None:
        """All fail but for legit per-video reasons (bot wall, 404, etc.) —
        must NOT raise. Retry via the same proxy won't recover these."""
        entries = {
            "s0": {"success": False, "error": "Sign in to confirm you're not a bot"},
            "s1": {"success": False, "error": "HTTP Error 404: Not Found"},
            "s2": {"success": False, "error": "Video unavailable — private"},
        }
        context = self._run_execute(tmp_path, entries)
        assert context is not None

    def test_mixed_socks5_and_non_socks5_does_not_raise(self, tmp_path: Path) -> None:
        """All-fail-but-mixed-errors is inconclusive. Only ALL-SOCKS5
        signals a proxy outage. Any non-SOCKS5 mixed in breaks the pattern —
        don't raise."""
        entries = {
            "s0": {"success": False, "error": "SOCKS5 host unreachable"},
            "s1": {"success": False, "error": "HTTP Error 404: Not Found"},
        }
        context = self._run_execute(tmp_path, entries)
        assert context is not None

    def test_all_succeed_does_not_raise(self, tmp_path: Path) -> None:
        """Happy path — no raise, no drama."""
        entries = {
            "s0": {"success": True, "clip_path": "/tmp/a.mp4"},
            "s1": {"success": True, "clip_path": "/tmp/b.mp4"},
        }
        context = self._run_execute(tmp_path, entries)
        assert context is not None

    def test_zero_stories_does_not_raise(self, tmp_path: Path) -> None:
        """Empty story list is a normal upstream-drop scenario (all filtered
        by relevance/dedup), not a proxy outage. Must not raise."""
        stage = DownloadTopVideos()
        context = {
            "stories": [],
            "run_dir": str(tmp_path),
            "niche_id": "gaming",
            "run_id": "test-run",
        }
        result = stage.execute(context)
        assert result is not None
        assert "clip_index" in result


class TestStageRunnerRetryIntegration:
    """Verify the raise wires into LocalStageRunner's retry mechanism.

    This is the whole point of the fix — proving the KNOB is now LIVE."""

    def test_local_stage_runner_retries_on_proxy_outage(self, tmp_path: Path) -> None:
        """When execute() raises, the runner's retry mechanism (fired
        per `max_retries` config from niche.yaml/template) must activate.

        This is the whole point of the fix: prior to today the raise
        never happened so the retry knob was dormant. Two attempts
        expected (initial + 1 retry per `max_retries=1`) before the
        runner gives up + records the error.
        """
        from genlab_core.pipeline.stage_runner import LocalStageRunner

        stage = DownloadTopVideos()
        runner = LocalStageRunner(max_retries=1, retry_delay=0.0)

        stories = [{"story_id": "s0"}, {"story_id": "s1"}]
        context = {
            "stories": stories,
            "run_dir": str(tmp_path),
            "niche_id": "gaming",
            "run_id": "test-run",
        }

        # Both attempts return all-SOCKS5 — simulate a WARP outage that
        # doesn't recover within the retry window. Runner exhausts retries
        # then the pipeline_ctx records the error.
        all_socks5_entries = {
            "s0": {"success": False, "error": "SOCKS5 host unreachable"},
            "s1": {"success": False, "error": "SOCKS5 host unreachable"},
        }

        # Minimal pipeline_ctx stub — record_error is what the runner
        # calls on final failure per fail_mode.
        from unittest.mock import MagicMock

        pipeline_ctx = MagicMock()
        pipeline_ctx.is_aborted = False

        with patch(
            "genlab_core.media.download_top_videos.download_videos_for_stories",
            return_value=all_socks5_entries,
        ) as mock_download:
            result = runner.run_stage(stage, context, pipeline_ctx)

        # 2 attempts: initial + 1 retry. If retry_knob was still DEAD,
        # would be 1.
        assert mock_download.call_count == 2, (
            f"Stage-runner retry didn't fire — call_count={mock_download.call_count}. "
            "The KNOB is still dead."
        )
        # Final result MUST reflect failure — the runner recorded an error.
        assert not result.success
