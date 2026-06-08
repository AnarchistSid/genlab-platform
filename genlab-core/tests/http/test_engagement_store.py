"""Tests for :mod:`genlab_core.http.engagement_store`.

Tier 2 / audit S-2, slice 2.2 — dedicated test file for the second
extracted BacklogClient sub-store. Tests the new module directly,
bypassing BacklogClient entirely, so future BacklogClient refactors
don't drag engagement_store with them.

Coverage targets:
* All three methods × the proxy-configured + proxy-not-configured branches
* All seven valid statuses round-trip + invalid status is rejected
* niche_id filter shapes the formula correctly
* Optional reply_text / error_msg fields propagate (and get truncated)
* Backend exceptions don't propagate (must log + return safe default)
"""

from __future__ import annotations

from unittest.mock import MagicMock

from genlab_core.http.engagement_store import (
    _VALID_ENGAGEMENT_STATUSES,
    EngagementStore,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UNSET = object()  # sentinel so the default branch never collapses falsy values


def _make_store(*, create_returns=_UNSET, find_returns=_UNSET):
    """Build an EngagementStore wired to a MagicMock proxy.

    Uses a sentinel default so passing ``create_returns=None`` actually
    forces the proxy to return ``None`` (instead of falsy-defaulting to
    the {"id": "rec_new"} placeholder).

    Returns ``(store, proxy)`` so individual tests can assert on the
    proxy's call_args.
    """
    proxy = MagicMock()
    proxy.create.return_value = {"id": "rec_new"} if create_returns is _UNSET else create_returns
    proxy.find.return_value = [] if find_returns is _UNSET else find_returns
    proxy.update.return_value = None
    return EngagementStore(proxy), proxy


def _unconfigured_store() -> EngagementStore:
    return EngagementStore(None)


# ---------------------------------------------------------------------------
# write_pending_engagement
# ---------------------------------------------------------------------------


class TestWritePendingEngagement:
    def test_returns_record_id_on_success(self) -> None:
        store, proxy = _make_store(create_returns={"id": "rec_abc"})
        rid = store.write_pending_engagement(
            {
                "platform": "youtube",
                "post_id": "vid_1",
                "niche_id": "gaming",
                "comment_id": "c_1",
                "text": "first!",
                "author_name": "viewer1",
            }
        )
        assert rid == "{'id': 'rec_abc'}"  # stringified dict — matches legacy
        proxy.create.assert_called_once()

    def test_stringifies_truthy_non_dict_return(self) -> None:
        store, proxy = _make_store(create_returns="rec_abc")
        rid = store.write_pending_engagement({"platform": "x", "post_id": "p"})
        assert rid == "rec_abc"

    def test_returns_none_when_backend_returns_falsy(self) -> None:
        store, _ = _make_store(create_returns=None)
        assert store.write_pending_engagement({"platform": "x"}) is None

    def test_returns_none_and_warns_when_proxy_missing(self, caplog) -> None:
        store = _unconfigured_store()
        with caplog.at_level("WARNING"):
            assert store.write_pending_engagement({"platform": "x"}) is None
        assert "PendingEngagement table not configured" in caplog.text

    def test_truncates_comment_text_to_2000_chars(self) -> None:
        store, proxy = _make_store()
        store.write_pending_engagement({"text": "x" * 5000})
        fields = proxy.create.call_args[0][0]
        assert len(fields["comment_text"]) == 2000

    def test_handles_none_text_gracefully(self) -> None:
        store, proxy = _make_store()
        store.write_pending_engagement({"text": None, "platform": "x"})
        fields = proxy.create.call_args[0][0]
        assert fields["comment_text"] == ""

    def test_status_defaults_to_pending(self) -> None:
        store, proxy = _make_store()
        store.write_pending_engagement({"platform": "x"})
        fields = proxy.create.call_args[0][0]
        assert fields["status"] == "pending"

    def test_writes_promoted_columns_correctly(self) -> None:
        store, proxy = _make_store()
        store.write_pending_engagement({"platform": "ig", "post_id": "p1", "niche_id": "anime"})
        fields = proxy.create.call_args[0][0]
        assert fields["platform"] == "ig"
        assert fields["post_id"] == "p1"
        assert fields["niche_id"] == "anime"

    def test_backend_exception_is_swallowed(self, caplog) -> None:
        store, proxy = _make_store()
        proxy.create.side_effect = RuntimeError("boom")
        with caplog.at_level("WARNING"):
            assert store.write_pending_engagement({"platform": "x"}) is None
        assert "write_pending_engagement failed" in caplog.text


# ---------------------------------------------------------------------------
# list_pending_engagement
# ---------------------------------------------------------------------------


class TestListPendingEngagement:
    def test_returns_empty_list_when_proxy_missing(self, caplog) -> None:
        with caplog.at_level("WARNING"):
            result = _unconfigured_store().list_pending_engagement()
        assert result == []
        assert "PendingEngagement table not configured" in caplog.text

    def test_uses_simple_status_formula_when_no_niche(self) -> None:
        store, proxy = _make_store()
        store.list_pending_engagement()
        formula = proxy.find.call_args.kwargs["formula"]
        assert formula == "{status}='pending'"

    def test_combines_status_and_niche_in_formula(self) -> None:
        store, proxy = _make_store()
        store.list_pending_engagement(niche_id="gaming", status="pending_review")
        formula = proxy.find.call_args.kwargs["formula"]
        assert formula == "AND({status}='pending_review', {niche_id}='gaming')"

    def test_passes_limit_through(self) -> None:
        store, proxy = _make_store()
        store.list_pending_engagement(limit=200)
        assert proxy.find.call_args.kwargs["max_records"] == 200

    def test_returns_find_results(self) -> None:
        store, _ = _make_store(find_returns=[{"id": "1"}, {"id": "2"}])
        assert store.list_pending_engagement() == [{"id": "1"}, {"id": "2"}]

    def test_escapes_quote_in_status_filter(self) -> None:
        store, proxy = _make_store()
        store.list_pending_engagement(status="bad's")
        formula = proxy.find.call_args.kwargs["formula"]
        assert "bad\\'s" in formula  # backslash-escaped single quote

    def test_backend_exception_is_swallowed(self, caplog) -> None:
        store, proxy = _make_store()
        proxy.find.side_effect = RuntimeError("kaboom")
        with caplog.at_level("WARNING"):
            assert store.list_pending_engagement() == []
        assert "list_pending_engagement failed" in caplog.text


# ---------------------------------------------------------------------------
# update_engagement_status
# ---------------------------------------------------------------------------


class TestUpdateEngagementStatus:
    def test_noop_and_warns_when_proxy_missing(self, caplog) -> None:
        store = _unconfigured_store()
        with caplog.at_level("WARNING"):
            store.update_engagement_status("rec_1", "replied")
        assert "pending_engagement proxy not configured" in caplog.text

    def test_rejects_invalid_status(self, caplog) -> None:
        store, proxy = _make_store()
        with caplog.at_level("WARNING"):
            store.update_engagement_status("rec_1", "definitely-not-valid")
        proxy.update.assert_not_called()
        assert "invalid engagement status" in caplog.text

    def test_all_seven_statuses_round_trip(self) -> None:
        # Sanity that pending_review (added in audit) is accepted —
        # the dashboard's review queue silently broke for weeks
        # because this status was missing from the valid set.
        for status in _VALID_ENGAGEMENT_STATUSES:
            store, proxy = _make_store()
            store.update_engagement_status("rec_1", status)
            proxy.update.assert_called_once()
            fields = proxy.update.call_args[0][1]
            assert fields["status"] == status

    def test_pending_review_in_valid_set(self) -> None:
        assert "pending_review" in _VALID_ENGAGEMENT_STATUSES

    def test_writes_processed_at_iso_timestamp(self) -> None:
        store, proxy = _make_store()
        store.update_engagement_status("rec_1", "replied")
        fields = proxy.update.call_args[0][1]
        # ISO 8601 with timezone: YYYY-MM-DDTHH:MM:SS+00:00
        assert "T" in fields["processed_at"]
        assert "+00:00" in fields["processed_at"]

    def test_includes_reply_text_when_provided(self) -> None:
        store, proxy = _make_store()
        store.update_engagement_status("rec_1", "replied", reply_text="ty!")
        fields = proxy.update.call_args[0][1]
        assert fields["reply_text"] == "ty!"

    def test_omits_reply_text_when_empty(self) -> None:
        store, proxy = _make_store()
        store.update_engagement_status("rec_1", "skipped")
        fields = proxy.update.call_args[0][1]
        assert "reply_text" not in fields

    def test_truncates_reply_text_to_2000(self) -> None:
        store, proxy = _make_store()
        store.update_engagement_status("rec_1", "replied", reply_text="x" * 5000)
        fields = proxy.update.call_args[0][1]
        assert len(fields["reply_text"]) == 2000

    def test_includes_error_msg_when_provided(self) -> None:
        store, proxy = _make_store()
        store.update_engagement_status("rec_1", "failed", error_msg="timeout")
        fields = proxy.update.call_args[0][1]
        assert fields["error_message"] == "timeout"

    def test_truncates_error_msg_to_500(self) -> None:
        store, proxy = _make_store()
        store.update_engagement_status("rec_1", "failed", error_msg="x" * 1200)
        fields = proxy.update.call_args[0][1]
        assert len(fields["error_message"]) == 500

    def test_passes_item_id_through(self) -> None:
        store, proxy = _make_store()
        store.update_engagement_status("rec_special", "liked")
        assert proxy.update.call_args[0][0] == "rec_special"

    def test_backend_exception_is_swallowed(self, caplog) -> None:
        store, proxy = _make_store()
        proxy.update.side_effect = RuntimeError("explode")
        with caplog.at_level("WARNING"):
            store.update_engagement_status("rec_1", "replied")
        assert "failed to update engagement" in caplog.text
