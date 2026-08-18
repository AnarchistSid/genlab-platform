"""Pin belt_client (2026-08-18 inference.sh subprocess wrapper):

  * Missing binary → fail-open with clear error
  * Non-JSON-serializable input → fail-open
  * Subprocess timeout → fail-open with timeout message
  * Subprocess non-zero exit → fail-open with stderr snippet
  * Empty stdout → fail-open
  * Malformed stdout → fail-open
  * App-reported error field → fail-open with app message
  * App status != 10 (not completed) → fail-open
  * Success → returns ok=True with parsed output + task_id
  * task_cost_usd parses JSON + plain-text formats
"""
from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from genlab_core.integrations.belt_client import (
    BeltResult,
    run_app,
    task_cost_usd,
)


class TestRunApp:
    def test_missing_binary_fails_open(self):
        with patch(
            "genlab_core.integrations.belt_client._belt_binary",
            return_value=None,
        ):
            r = run_app("any/app", {"prompt": "x"})
        assert r.ok is False
        assert "belt binary" in (r.error or "").lower()

    def test_non_serializable_input(self):
        r = run_app("any/app", {"bad": object()}, binary_override="/bin/true")
        assert r.ok is False
        assert "json" in (r.error or "").lower()

    def test_subprocess_timeout(self):
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="belt", timeout=1),
        ):
            r = run_app(
                "any/app", {"p": "x"}, timeout_seconds=1,
                binary_override="/opt/belt",
            )
        assert r.ok is False
        assert "timed out" in (r.error or "").lower()

    def test_subprocess_nonzero_exit(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=2, stdout="", stderr="something broke",
            )
            r = run_app(
                "any/app", {"p": "x"},
                binary_override="/opt/belt",
            )
        assert r.ok is False
        assert "something broke" in (r.error or "")

    def test_empty_stdout(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="", stderr="",
            )
            r = run_app(
                "any/app", {"p": "x"}, binary_override="/opt/belt",
            )
        assert r.ok is False

    def test_malformed_stdout(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="not json here", stderr="",
            )
            r = run_app(
                "any/app", {"p": "x"}, binary_override="/opt/belt",
            )
        assert r.ok is False

    def test_app_reported_error_field(self):
        response = json.dumps({
            "id": "task123",
            "error": "quota exceeded",
            "status": 20,
        })
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=response, stderr="",
            )
            r = run_app(
                "any/app", {"p": "x"}, binary_override="/opt/belt",
            )
        assert r.ok is False
        assert r.task_id == "task123"
        assert "quota exceeded" in (r.error or "")

    def test_status_not_completed(self):
        response = json.dumps({
            "id": "task_progress",
            "error": None,
            "status": 5,  # in-progress or similar
            "status_text": "running",
        })
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=response, stderr="",
            )
            r = run_app(
                "any/app", {"p": "x"}, binary_override="/opt/belt",
            )
        assert r.ok is False
        assert r.task_id == "task_progress"

    def test_success(self):
        response = json.dumps({
            "id": "task_ok",
            "error": None,
            "status": 10,
            "status_text": "completed",
            "output": {"image": "https://x.test/a.png"},
        })
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=response, stderr="",
            )
            r = run_app(
                "pruna/flux-dev", {"prompt": "x"},
                binary_override="/opt/belt",
            )
        assert r.ok is True
        assert r.task_id == "task_ok"
        assert r.output == {"image": "https://x.test/a.png"}

    def test_success_with_progress_noise_before_json(self):
        """Some apps print progress log lines before the final JSON.
        Wrapper must extract the LAST JSON line from stdout."""
        response = json.dumps({
            "id": "task_ok",
            "status": 10,
            "output": {"r": 1},
        })
        stdout = f"progress: 50%\nprogress: 100%\n{response}\n"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=stdout, stderr="",
            )
            r = run_app(
                "any/app", {"p": "x"}, binary_override="/opt/belt",
            )
        assert r.ok is True
        assert r.task_id == "task_ok"


class TestTaskCost:
    def test_no_binary_returns_none(self):
        with patch(
            "genlab_core.integrations.belt_client._belt_binary",
            return_value=None,
        ):
            assert task_cost_usd("t1") is None

    def test_empty_task_id_returns_none(self):
        assert task_cost_usd("") is None

    def test_subprocess_error_returns_none(self):
        with patch(
            "genlab_core.integrations.belt_client._belt_binary",
            return_value="/opt/belt",
        ), patch("subprocess.run", side_effect=Exception("boom")):
            assert task_cost_usd("t1") is None

    def test_parses_json_response_converts_credits_to_usd(self):
        # belt --json returns cost in integer "credits" where
        # 100_000_000 credits == $1.00 (verified 2026-08-18 against
        # `belt task cost <id> --json`). Bare-float interpretation
        # produced $500000.0000 for a $0.005 pruna/flux-dev image.
        response = json.dumps(
            {"total": 500000, "discount": 0, "charged": 500000},
        )
        with patch(
            "genlab_core.integrations.belt_client._belt_binary",
            return_value="/opt/belt",
        ), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=response, stderr="",
            )
            assert task_cost_usd("t1") == 0.005

    def test_parses_json_cost_usd_key_as_dollars(self):
        # cost_usd (hypothetical future field) is already dollars —
        # don't double-divide by the credit conversion factor.
        response = json.dumps({"cost_usd": 0.75})
        with patch(
            "genlab_core.integrations.belt_client._belt_binary",
            return_value="/opt/belt",
        ), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=response, stderr="",
            )
            assert task_cost_usd("t1") == 0.75

    def test_parses_plain_text_charged_line(self):
        stdout = "Task: t1\nTotal: $0.00\nCharged: $0.500\n"
        with patch(
            "genlab_core.integrations.belt_client._belt_binary",
            return_value="/opt/belt",
        ), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout=stdout, stderr="",
            )
            assert task_cost_usd("t1") == 0.5
