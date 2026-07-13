"""Pin: monitoring.token_health.check_tiktok must live-probe the API.

Post-2026-07-13 audit follow-up (proxy-signal masking class-of-bug —
sibling to test_token_health_threads_live_probe.py). Same rationale
as check_threads: previously the timestamp-only proxy would report
"healthy" even for revoked tokens.

TikTok is stub-only in prod today, so the fix is preemptive — closes
the class-of-bug before the audit lands + TikTok starts publishing.

Tests here pin:

  1. Primary signal: live probe to open.tiktokapis.com/v2/user/info/
  2. 401 / expired keywords → status=expired
  3. Secondary signal: TIKTOK_TOKEN_ISSUED_AT for expiring-soon
     warnings AFTER live probe passes
  4. Missing timestamp → healthy iff probe passed
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _mock_response(*, ok=True, status_code=200, json_data=None, text=""):
    resp = MagicMock()
    resp.ok = ok
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text
    return resp


class TestLivePrimarySignal:
    def test_calls_tiktok_user_info_endpoint(self, monkeypatch):
        """The live probe target is TikTok's public /v2/user/info/
        endpoint. If a refactor changes the URL, this pin fires
        because we'd lose the audience-facing invariant check."""
        from genlab_core.monitoring import token_health

        monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "test_token")
        monkeypatch.delenv("TIKTOK_TOKEN_ISSUED_AT", raising=False)
        monkeypatch.setenv("TIKTOK_AUDIT_APPROVED", "false")

        with patch("genlab_core.monitoring.token_health.requests.get") as mock_get:
            mock_get.return_value = _mock_response(
                json_data={"data": {"user": {"open_id": "test_open_123"}}},
            )
            result = token_health.check_tiktok()

        assert mock_get.called, (
            "check_tiktok must live-probe the API — no probe = same class-of-bug"
        )
        call_url = mock_get.call_args[0][0]
        assert "open.tiktokapis.com" in call_url
        assert "/v2/user/info/" in call_url
        assert result["status"] == "healthy"

    def test_401_returns_expired_not_healthy(self, monkeypatch):
        """The whole point of the fix: 401 from TikTok means the
        token is dead. NEVER return healthy on a live-probe failure,
        regardless of what TIKTOK_TOKEN_ISSUED_AT says."""
        from genlab_core.monitoring import token_health

        monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "revoked_token")
        # Even with a recent timestamp:
        monkeypatch.setenv("TIKTOK_TOKEN_ISSUED_AT", "2026-07-13T18:00:00+00:00")

        with patch("genlab_core.monitoring.token_health.requests.get") as mock_get:
            mock_get.return_value = _mock_response(
                ok=False,
                status_code=401,
                json_data={
                    "error": {"code": "access_token_invalid", "message": "Invalid access token"}
                },
            )
            result = token_health.check_tiktok()

        assert result["status"] == "expired", (
            "Token rejected by API MUST NOT report healthy — "
            "class-of-bug pin. Reverting to timestamp-only would let "
            "this fail silently."
        )


class TestSecondaryExpirySignal:
    def test_recent_timestamp_healthy_with_remaining_hours(self, monkeypatch):
        from datetime import UTC, datetime, timedelta

        from genlab_core.monitoring import token_health

        # 5 hours old = 19 hours remaining (well above 6h threshold)
        issued = (datetime.now(UTC) - timedelta(hours=5)).isoformat()
        monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "test_token")
        monkeypatch.setenv("TIKTOK_TOKEN_ISSUED_AT", issued)

        with patch("genlab_core.monitoring.token_health.requests.get") as mock_get:
            mock_get.return_value = _mock_response(
                json_data={"data": {"user": {"open_id": "test_open"}}},
            )
            result = token_health.check_tiktok()

        assert result["status"] == "healthy"

    def test_near_expiry_flags_expiring(self, monkeypatch):
        from datetime import UTC, datetime, timedelta

        from genlab_core.monitoring import token_health

        # 23.5h old = 0.5h remaining (below 1h critical threshold)
        issued = (datetime.now(UTC) - timedelta(hours=23, minutes=30)).isoformat()
        monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "test_token")
        monkeypatch.setenv("TIKTOK_TOKEN_ISSUED_AT", issued)

        with patch("genlab_core.monitoring.token_health.requests.get") as mock_get:
            mock_get.return_value = _mock_response(
                json_data={"data": {"user": {"open_id": "test_open"}}},
            )
            result = token_health.check_tiktok()

        assert result["status"] == "expiring"

    def test_missing_timestamp_still_healthy_when_probe_passes(self, monkeypatch):
        """The old code returned "healthy" on missing ISSUED_AT
        WITHOUT any probe. New code requires the probe to pass."""
        from genlab_core.monitoring import token_health

        monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "test_token")
        monkeypatch.delenv("TIKTOK_TOKEN_ISSUED_AT", raising=False)

        with patch("genlab_core.monitoring.token_health.requests.get") as mock_get:
            mock_get.return_value = _mock_response(
                json_data={"data": {"user": {"open_id": "test_open"}}},
            )
            result = token_health.check_tiktok()

        assert result["status"] == "healthy"


class TestClassOfBugPin:
    """Explicit pin for the exact scenarios the old code masked."""

    def test_stale_env_but_token_revoked(self, monkeypatch):
        from datetime import UTC, datetime, timedelta

        from genlab_core.monitoring import token_health

        # Recent timestamp
        issued = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "revoked_token")
        monkeypatch.setenv("TIKTOK_TOKEN_ISSUED_AT", issued)

        with patch("genlab_core.monitoring.token_health.requests.get") as mock_get:
            mock_get.return_value = _mock_response(
                ok=False,
                status_code=401,
                json_data={"error": {"message": "Invalid access token"}},
            )
            result = token_health.check_tiktok()

        assert result["status"] != "healthy"
        assert result["status"] == "expired"

    def test_missing_env_no_longer_healthy_without_probing(self, monkeypatch):
        """Old code returned "healthy" on missing ISSUED_AT without
        touching the API. This test fires if that regression is
        reintroduced."""
        from genlab_core.monitoring import token_health

        monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "some_token")
        monkeypatch.delenv("TIKTOK_TOKEN_ISSUED_AT", raising=False)

        with patch("genlab_core.monitoring.token_health.requests.get") as mock_get:
            mock_get.return_value = _mock_response(
                ok=False,
                status_code=401,
                json_data={"error": {"message": "Invalid access token"}},
            )
            result = token_health.check_tiktok()

        assert result["status"] != "healthy"
        assert result["status"] == "expired"


class TestAuditNoteIncluded:
    """The SELF_ONLY audit note must appear in all messages when
    TIKTOK_AUDIT_APPROVED=false. Preserves the operator hint from the
    pre-fix code."""

    def test_audit_note_on_healthy(self, monkeypatch):
        from datetime import UTC, datetime, timedelta

        from genlab_core.monitoring import token_health

        issued = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "test")
        monkeypatch.setenv("TIKTOK_TOKEN_ISSUED_AT", issued)
        monkeypatch.setenv("TIKTOK_AUDIT_APPROVED", "false")

        with patch("genlab_core.monitoring.token_health.requests.get") as mock_get:
            mock_get.return_value = _mock_response(
                json_data={"data": {"user": {"open_id": "test"}}},
            )
            result = token_health.check_tiktok()

        assert "SELF_ONLY" in result["message"]

    def test_no_audit_note_when_approved(self, monkeypatch):
        from datetime import UTC, datetime, timedelta

        from genlab_core.monitoring import token_health

        issued = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        monkeypatch.setenv("TIKTOK_ACCESS_TOKEN", "test")
        monkeypatch.setenv("TIKTOK_TOKEN_ISSUED_AT", issued)
        monkeypatch.setenv("TIKTOK_AUDIT_APPROVED", "true")

        with patch("genlab_core.monitoring.token_health.requests.get") as mock_get:
            mock_get.return_value = _mock_response(
                json_data={"data": {"user": {"open_id": "test"}}},
            )
            result = token_health.check_tiktok()

        assert "SELF_ONLY" not in result["message"]
