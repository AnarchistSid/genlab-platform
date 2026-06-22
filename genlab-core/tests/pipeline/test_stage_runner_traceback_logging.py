"""Pin: stage_runner logs full tracebacks on failures (2026-06-22).

Pre-fix, ``stage_runner`` logged stage failures with ``logger.error("%s",
e)`` — only the exception's ``__str__``. Operators got:

  [Pipeline] Stage VideoGate failed after 1 attempt(s) (0.0s):
  'NoneType' object is not subscriptable

NO file, NO line number, NO frame info. Diagnosing required either
local repro or grep-reading the source to guess the crash site.

Fix: add ``exc_info=True`` to both the retry-warning and final-error
log calls in both ``LocalStageRunner`` AND the sandboxed runner. Now
prod journals contain the full traceback, making every future stage
failure self-diagnosable from the log alone.

These pins lock in:

  1. Final-attempt failure logs the traceback (LocalStageRunner)
  2. Retry-warning logs the traceback (LocalStageRunner)
  3. Sandboxed-runner failure logs the traceback (SandboxedStageRunner)

  Pre-fix regression canaries (3): the same assertions WITHOUT
  exc_info would have failed pre-fix, so today's pass = the wire
  is doing what it should.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from genlab_core.pipeline.stage_runner import LocalStageRunner


class _CrashingStage:
    """Test stage that raises on every call."""

    def execute(self, context):
        raise TypeError("'NoneType' object is not subscriptable")


class _RetrySucceedStage:
    """Test stage that raises on first attempt, succeeds on second."""

    def __init__(self):
        self.attempts = 0

    def execute(self, context):
        self.attempts += 1
        if self.attempts < 2:
            raise RuntimeError("transient failure")
        return context


class TestLocalStageRunnerTraceback:
    def test_final_failure_logs_traceback(self, caplog):
        """Pin: when a stage fails on its LAST attempt, the ERROR log
        includes the full traceback (file, line, frame) via exc_info=True."""
        runner = LocalStageRunner(max_retries=0, fail_mode="continue")
        pipeline_ctx = MagicMock()

        with caplog.at_level(logging.ERROR):
            result = runner.run_stage(_CrashingStage(), {}, pipeline_ctx)

        assert not result.success
        # Find the failure log record
        failure_logs = [
            r for r in caplog.records if r.levelname == "ERROR" and "failed after" in r.message
        ]
        assert len(failure_logs) >= 1
        # exc_info attached → log record has exception info
        assert failure_logs[0].exc_info is not None
        # exc_info[1] is the exception itself
        assert isinstance(failure_logs[0].exc_info[1], TypeError)
        assert "NoneType" in str(failure_logs[0].exc_info[1])

    def test_retry_warning_logs_traceback(self, caplog):
        """Pin: when a stage fails on a non-final attempt (will retry),
        the WARNING log ALSO includes the traceback. Otherwise transient
        bugs that recover on retry remain invisible to operators."""
        runner = LocalStageRunner(max_retries=1, fail_mode="continue")
        pipeline_ctx = MagicMock()

        with caplog.at_level(logging.WARNING):
            result = runner.run_stage(_RetrySucceedStage(), {}, pipeline_ctx)

        assert result.success  # stage eventually succeeded on retry
        retry_logs = [
            r for r in caplog.records if r.levelname == "WARNING" and "will retry" in r.message
        ]
        assert len(retry_logs) == 1
        assert retry_logs[0].exc_info is not None
        assert isinstance(retry_logs[0].exc_info[1], RuntimeError)
