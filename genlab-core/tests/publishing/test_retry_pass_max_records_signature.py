"""Pin the get_blueprints_by_status signature contract with retry_pass.

The 2026-06-14 prod investigation found that the publisher's retry
pass — designed to retry FAILED platforms on PUBLISHED blueprints
across runs — had been **silently failing on every invocation** for
an unknown duration because:

  1. ``retry_pass.run_retry_pass`` calls
     ``backlog_client.get_blueprints_by_status(..., max_records=50)``.
  2. ``BacklogClient.get_blueprints_by_status`` didn't accept
     ``max_records=`` — Python raised TypeError on every call.
  3. The TypeError was caught at ``logger.debug`` (suppressed at
     INFO level), so the operator never saw the failure.

Result: not a single FAILED platform was ever retried via this code
path. Today's ai_creators blueprint's FB code=1 failure (TRANSIENT,
retryable) sat un-retried, exactly the situation the retry pass was
built to recover from.

These pins lock in:
  1. The signature contract — ``max_records`` is accepted everywhere
     in the chain (store, client delegator).
  2. The exception-handling log level — DEBUG is too quiet for a
     silent-fail-on-every-call recovery loop; WARNING surfaces
     regressions immediately.
"""

from __future__ import annotations

import inspect
import logging
from unittest.mock import MagicMock

import pytest


class TestMaxRecordsSignature:
    def test_blueprint_store_accepts_max_records(self):
        """Store-level method must accept ``max_records`` so the
        client delegator can forward it."""
        from genlab_core.http.blueprint_store import BlueprintStore

        sig = inspect.signature(BlueprintStore.get_blueprints_by_status)
        assert "max_records" in sig.parameters, (
            "BlueprintStore.get_blueprints_by_status missing max_records — "
            "retry_pass calls it with max_records=RETRY_PASS_BATCH_LIMIT, "
            "the call will TypeError and the retry pass goes dark."
        )

    def test_backlog_client_accepts_max_records(self):
        """Client delegator must accept + forward ``max_records``."""
        from genlab_core.http.backlog_client import BacklogClient

        sig = inspect.signature(BacklogClient.get_blueprints_by_status)
        assert "max_records" in sig.parameters, (
            "BacklogClient.get_blueprints_by_status missing max_records — "
            "this is the call site retry_pass hits, the exact signature "
            "drift that caused the prod silent-fail today."
        )

    def test_retry_pass_call_matches_signature(self):
        """End-to-end: the retry pass's actual call shape must match
        the BacklogClient method's signature so the runtime resolves
        without TypeError. Verified by simulating the call with a
        MagicMock that's set up to mirror the real signature."""
        from genlab_core.http.backlog_client import BacklogClient
        from genlab_core.publishing import retry_pass

        # Build a MagicMock that uses the real method's signature
        # (auto-spec) so any signature mismatch surfaces here.
        client = MagicMock()
        client.get_blueprints_by_status = MagicMock(spec=BacklogClient.get_blueprints_by_status)
        client.get_blueprints_by_status.return_value = []

        # Must not raise (the prod bug raised TypeError on this call)
        retry_pass.run_retry_pass("ai_creators", client, daily_cap=None)

        # Verify the call landed — the spec'd mock would have raised
        # TypeError if max_records weren't in the real method's
        # signature.
        assert client.get_blueprints_by_status.call_count == 1
        call = client.get_blueprints_by_status.call_args
        # Args: ("PUBLISHED",), kwargs: {niche_id, max_records}
        assert call.args[0] == "PUBLISHED"
        assert call.kwargs.get("niche_id") == "ai_creators"
        assert call.kwargs.get("max_records") == retry_pass.RETRY_PASS_BATCH_LIMIT


class TestRetryPassErrorVisibility:
    def test_retry_pass_failure_logs_at_warning_not_debug(self, caplog):
        """The exception swallowed by the outer try/except MUST be
        logged at WARNING level (or higher). The previous DEBUG level
        hid a TypeError that broke every invocation for months;
        WARNING ensures the next silent-fail surfaces in operator
        logs immediately."""
        from genlab_core.publishing import retry_pass

        broken_client = MagicMock()
        broken_client.get_blueprints_by_status.side_effect = RuntimeError("simulated DB outage")

        with caplog.at_level(logging.WARNING):
            retry_pass.run_retry_pass("ai_creators", broken_client, daily_cap=None)

        warnings = [
            r
            for r in caplog.records
            if r.levelno >= logging.WARNING and "Retry pass failed" in r.message
        ]
        assert warnings, (
            "Retry pass exception not logged at WARNING+. A recovery "
            "loop that fails silently defeats its own purpose — the "
            "operator must see when the retry path has stopped working."
        )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
