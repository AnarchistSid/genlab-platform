"""Pin: monitoring.token_health.check_threads must live-probe the API.

Post-2026-07-13 audit follow-up (proxy-signal masking class-of-bug).
The previous implementation of ``check_threads`` used
``THREADS_TOKEN_ISSUED_AT`` as a proxy for token validity — a stale
env-var timestamp reported "healthy" even if the token was:

  - Revoked server-side (issued_at recent, but API returns 401)
  - Rotated by another process (issued_at not updated, but old token
    stopped working)
  - Refreshed correctly but ISSUED_AT env var not synced (issued_at
    stale, but token actually works)

Same class-of-bug as the 2026-07-13 Layer 5 attribution masking:
proxy signal populated via a different code path than the invariant.

Fix: mirror ``check_meta_token`` / ``check_anthropic`` / ``check_openai``
which all live-probe the API. Timestamp is now a SECONDARY signal
used only for "expiring soon" warnings after the primary invariant
(live-probe success) is verified.

If a future refactor drops the live probe and re-inlines the
timestamp-only path, these pins fire.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _mock_response(*, ok=True, status_code=200, json_data=None, text=""):
    """Build a MagicMock that behaves like requests.Response."""
    resp = MagicMock()
    resp.ok = ok
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text
    resp.headers = {"content-type": "application/json"}
    return resp


class TestLivePrimarySignal:
    """Primary invariant: hit /me on graph.threads.net + treat 200 with
    ``id`` field as healthy. Any other response is NOT healthy — that's
    the whole point of the audit-driven rewrite."""

    def test_calls_graph_threads_net_me_endpoint(self, monkeypatch):
        """The live probe target — NOT graph.instagram.com, NOT some
        other proxy endpoint. If a refactor changes the URL, this pin
        fires because we'd lose the audience-facing invariant check."""
        from genlab_core.monitoring import token_health

        monkeypatch.setenv("THREADS_ACCESS_TOKEN", "test_token")
        monkeypatch.delenv("THREADS_TOKEN_ISSUED_AT", raising=False)

        with patch("genlab_core.monitoring.token_health.requests.get") as mock_get:
            mock_get.return_value = _mock_response(
                json_data={"id": "12345", "username": "testuser"},
            )
            result = token_health.check_threads()

        # The URL must include graph.threads.net + /me
        assert mock_get.called, (
            "check_threads must hit the API — no live probe = same class-of-bug as before"
        )
        call_url = mock_get.call_args[0][0]
        assert "graph.threads.net" in call_url
        assert "/me" in call_url
        assert result["status"] == "healthy"

    def test_401_or_190_returns_expired_not_healthy(self, monkeypatch):
        """Token rejected by API → status='expired'. The whole point
        of the fix: never report healthy on a live-probe failure."""
        from genlab_core.monitoring import token_health

        monkeypatch.setenv("THREADS_ACCESS_TOKEN", "revoked_token")
        # Even with a "recent" issued_at, live-probe failure wins
        monkeypatch.setenv("THREADS_TOKEN_ISSUED_AT", "2026-07-13T00:00:00+00:00")

        with patch("genlab_core.monitoring.token_health.requests.get") as mock_get:
            mock_get.return_value = _mock_response(
                ok=False,
                status_code=400,
                json_data={
                    "error": {
                        "code": 190,
                        "message": "Session has expired",
                    }
                },
            )
            result = token_health.check_threads()

        assert result["status"] == "expired", (
            "Live-probe returned 190 → must report 'expired' regardless "
            "of THREADS_TOKEN_ISSUED_AT freshness. Reverting to a "
            "timestamp-only proxy would let this fail silently."
        )

    def test_unexpected_response_shape_returns_error(self, monkeypatch):
        """200 OK but missing 'id' → error, not healthy. Defensive
        against Threads API contract change."""
        from genlab_core.monitoring import token_health

        monkeypatch.setenv("THREADS_ACCESS_TOKEN", "test_token")
        monkeypatch.delenv("THREADS_TOKEN_ISSUED_AT", raising=False)

        with patch("genlab_core.monitoring.token_health.requests.get") as mock_get:
            mock_get.return_value = _mock_response(
                json_data={"unexpected": "shape"},
            )
            result = token_health.check_threads()

        assert result["status"] == "error"


class TestSecondaryExpirySignal:
    """Timestamp is now a SECONDARY signal used only for "expiring soon"
    warnings when the primary (live-probe) has passed."""

    def test_recent_timestamp_reports_healthy_with_days_remaining(self, monkeypatch):
        from datetime import UTC, datetime, timedelta

        from genlab_core.monitoring import token_health

        # 20 days old → 40 days remaining (well above 15-day threshold)
        issued = (datetime.now(UTC) - timedelta(days=20)).isoformat()
        monkeypatch.setenv("THREADS_ACCESS_TOKEN", "test_token")
        monkeypatch.setenv("THREADS_TOKEN_ISSUED_AT", issued)

        with patch("genlab_core.monitoring.token_health.requests.get") as mock_get:
            mock_get.return_value = _mock_response(
                json_data={"id": "12345", "username": "testuser"},
            )
            result = token_health.check_threads()

        assert result["status"] == "healthy"
        assert result["days_remaining"] == 40

    def test_near_expiry_flags_expiring(self, monkeypatch):
        from datetime import UTC, datetime, timedelta

        from genlab_core.monitoring import token_health

        # 59 days old → 1 day remaining (below 2-day critical threshold)
        issued = (datetime.now(UTC) - timedelta(days=59)).isoformat()
        monkeypatch.setenv("THREADS_ACCESS_TOKEN", "test_token")
        monkeypatch.setenv("THREADS_TOKEN_ISSUED_AT", issued)

        with patch("genlab_core.monitoring.token_health.requests.get") as mock_get:
            mock_get.return_value = _mock_response(
                json_data={"id": "12345", "username": "testuser"},
            )
            result = token_health.check_threads()

        # Even with live-probe passing, near-expiry surfaces the warning
        assert result["status"] == "expiring"

    def test_missing_timestamp_still_healthy_when_live_probe_passes(self, monkeypatch):
        """The old code reported "healthy" on missing ISSUED_AT
        WITHOUT any live probe. The new code reports healthy only
        AFTER the live probe passes. Delete this test to make it
        pass would be a class-of-bug regression."""
        from genlab_core.monitoring import token_health

        monkeypatch.setenv("THREADS_ACCESS_TOKEN", "test_token")
        monkeypatch.delenv("THREADS_TOKEN_ISSUED_AT", raising=False)

        with patch("genlab_core.monitoring.token_health.requests.get") as mock_get:
            mock_get.return_value = _mock_response(
                json_data={"id": "12345", "username": "testuser"},
            )
            result = token_health.check_threads()

        # Healthy iff live probe passed AND we couldn't compute
        # a days-remaining countdown (which is fine — the primary
        # signal is the live probe)
        assert result["status"] == "healthy"
        # No days_remaining key when ISSUED_AT was unavailable
        assert "days_remaining" not in result


class TestFailureModesTheProxyMissed:
    """Class-of-bug pin: each of these scenarios would have reported
    "healthy" under the old timestamp-only implementation. New
    implementation catches them via the live probe."""

    def test_stale_env_var_but_token_revoked_server_side(self, monkeypatch):
        """Token was revoked by Meta after issued_at was recorded.
        Old code: healthy (recent timestamp). New code: expired."""
        from datetime import UTC, datetime, timedelta

        from genlab_core.monitoring import token_health

        issued = (datetime.now(UTC) - timedelta(days=5)).isoformat()
        monkeypatch.setenv("THREADS_ACCESS_TOKEN", "revoked_token")
        monkeypatch.setenv("THREADS_TOKEN_ISSUED_AT", issued)

        with patch("genlab_core.monitoring.token_health.requests.get") as mock_get:
            mock_get.return_value = _mock_response(
                ok=False,
                status_code=401,
                json_data={"error": {"code": 190, "message": "Token has been revoked"}},
            )
            result = token_health.check_threads()

        assert result["status"] == "expired"
        assert "revoked" in result["message"].lower()

    def test_missing_env_var_no_longer_reports_healthy_without_probing(self, monkeypatch):
        """Old code returned "Token set but age unknown" as HEALTHY
        without any API check. That let a bad token skate by. New
        code MUST at least attempt the probe."""
        from genlab_core.monitoring import token_health

        monkeypatch.setenv("THREADS_ACCESS_TOKEN", "some_token")
        monkeypatch.delenv("THREADS_TOKEN_ISSUED_AT", raising=False)

        with patch("genlab_core.monitoring.token_health.requests.get") as mock_get:
            # Live probe FAILS (would have been masked before)
            mock_get.return_value = _mock_response(
                ok=False,
                status_code=401,
                json_data={"error": {"code": 190, "message": "Invalid token"}},
            )
            result = token_health.check_threads()

        # Old code would have said "healthy" without probing.
        # New code correctly reports "expired".
        assert result["status"] != "healthy"
        assert result["status"] == "expired"
