"""Pin the 2026-07-22 IG business account fix in check_meta_token.

History: `check_meta_token` verified the FB page token by fetching
`/{page_id}?fields=id,name,instagram_business_account{username}` and
returning "healthy" if the response was OK. But when Instagram is
disconnected/reconnected under the FB page (a common Meta operation),
`instagram_business_account` becomes null/empty. Every IG publish
fails with "no IG account linked" — but the token check still
returns "healthy @unknown (page: X) — permanent page token".

This class-of-bug likely explains the 3% IG publish success rate
seen in production (28 failures / 1 success in 30-day window per
audit memo) — the check has been silently green while real IG
publishes hard-fail.

Fixed: if `instagram_business_account.id` is absent, return
`status="error"` with an actionable message pointing operator to
Meta Business Suite reconnect flow.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from genlab_core.monitoring.token_health import check_meta_token


def _mock_resp(json_data: dict, ok: bool = True) -> MagicMock:
    m = MagicMock()
    m.ok = ok
    m.json.return_value = json_data
    m.status_code = 200 if ok else 400
    return m


class TestIGBusinessAccountCheck:
    def test_missing_ig_business_account_returns_error(self, monkeypatch) -> None:
        """When Instagram is not linked to the FB page,
        `instagram_business_account` is absent from response. Must
        return status=error, not healthy."""
        monkeypatch.setenv("META_ACCESS_TOKEN", "fake-token")
        monkeypatch.setenv("META_FB_PAGE_ID", "12345")

        verify_response = _mock_resp({
            "id": "12345",
            "name": "TestPage",
            # NO instagram_business_account field
        })
        with patch("requests.get", return_value=verify_response):
            result = check_meta_token()

        assert result["status"] == "error", (
            f"Missing IG account MUST return error, got: {result}"
        )
        assert "not linked" in result["message"] or "IG business" in result["message"]

    def test_empty_ig_business_account_returns_error(self, monkeypatch) -> None:
        """Sometimes Meta returns `instagram_business_account: {}` — empty
        dict without id. Same class-of-bug: token valid, IG unusable."""
        monkeypatch.setenv("META_ACCESS_TOKEN", "fake-token")
        monkeypatch.setenv("META_FB_PAGE_ID", "12345")

        verify_response = _mock_resp({
            "id": "12345",
            "name": "TestPage",
            "instagram_business_account": {},  # empty
        })
        with patch("requests.get", return_value=verify_response):
            result = check_meta_token()

        assert result["status"] == "error"
        assert result.get("ig_business_account_id") is None

    def test_null_ig_business_account_returns_error(self, monkeypatch) -> None:
        """Meta may return the key with value None."""
        monkeypatch.setenv("META_ACCESS_TOKEN", "fake-token")
        monkeypatch.setenv("META_FB_PAGE_ID", "12345")

        verify_response = _mock_resp({
            "id": "12345",
            "name": "TestPage",
            "instagram_business_account": None,
        })
        with patch("requests.get", return_value=verify_response):
            result = check_meta_token()

        assert result["status"] == "error"

    def test_ig_account_present_with_id_returns_healthy(self, monkeypatch) -> None:
        """Post-fix: valid IG business account + valid token → healthy.
        No regression from the IG-check tightening for the happy path."""
        monkeypatch.setenv("META_ACCESS_TOKEN", "fake-token")
        monkeypatch.setenv("META_FB_PAGE_ID", "12345")

        verify_response = _mock_resp({
            "id": "12345",
            "name": "TestPage",
            "instagram_business_account": {
                "id": "17841400000000000",
                "username": "test_creator",
            },
        })
        debug_response = _mock_resp({"data": {"expires_at": 0}})

        with patch("requests.get", side_effect=[verify_response, debug_response]):
            result = check_meta_token()

        assert result["status"] == "healthy"
        assert "test_creator" in result["message"]

    def test_error_message_directs_operator_to_meta_business_suite(self, monkeypatch) -> None:
        """The alert message must guide operator to the specific fix
        (Meta Business Suite reconnect flow) — otherwise operator sees
        "IG not linked" and doesn't know where to go."""
        monkeypatch.setenv("META_ACCESS_TOKEN", "fake-token")
        monkeypatch.setenv("META_FB_PAGE_ID", "12345")

        verify_response = _mock_resp({"id": "12345", "name": "TestPage"})
        with patch("requests.get", return_value=verify_response):
            result = check_meta_token()

        # Message should include actionable next step
        msg = result["message"]
        assert "Meta Business Suite" in msg
        assert "Reconnect" in msg or "reconnect" in msg
