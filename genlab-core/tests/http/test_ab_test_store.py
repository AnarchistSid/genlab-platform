"""Tests for :mod:`genlab_core.http.ab_test_store`.

Tier 2 / audit S-2, slice 2.3 — dedicated test file for the third
extracted BacklogClient sub-store.

Coverage targets:
* All three methods × the proxy-configured + proxy-not-configured branches
* Status formula construction (with + without status)
* niche_id passthrough on find
* update no-ops when no record matches
* Exception swallowing on create
"""

from __future__ import annotations

from unittest.mock import MagicMock

from genlab_core.http.ab_test_store import ABTestStore


def _make_store(*, configured: bool = True):
    """Build an ABTestStore with a MagicMock backend + proxy.

    Returns ``(store, backend_mock, proxy)`` so tests can assert on
    either side. When ``configured=False``, the proxy is ``None``
    (matching how BacklogClient.__init__ leaves it when the SP list
    isn't in ``lists_config.yaml``).
    """
    backend_mock = MagicMock()
    backend_mock.create.return_value = {"id": "ab_1"}
    backend_mock.find.return_value = [{"id": "ab_existing", "fields": {}}]
    backend_mock.update.return_value = {"id": "ab_existing"}

    backend_lookup = MagicMock(return_value=backend_mock)
    proxy = MagicMock() if configured else None
    store = ABTestStore(ab_tests=proxy, backend=backend_lookup)
    return store, backend_mock, proxy


# ---------------------------------------------------------------------------
# create_ab_test
# ---------------------------------------------------------------------------


class TestCreateAbTest:
    def test_returns_record_id_on_success(self) -> None:
        store, backend, _ = _make_store()
        backend.create.return_value = {"id": "ab_42"}
        result = store.create_ab_test({"test_id": "t1", "status": "running"})
        assert result == "ab_42"
        # PR #530 adds niche_id kwarg. Test dict here has no niche_id
        # → kwarg is None (backward-compat admin-mode path).
        backend.create.assert_called_once_with(
            "AB_Tests",
            {"test_id": "t1", "status": "running"},
            typecast=True,
            niche_id=None,
        )

    def test_returns_none_and_warns_when_unconfigured(self, caplog) -> None:
        store, backend, _ = _make_store(configured=False)
        with caplog.at_level("WARNING"):
            assert store.create_ab_test({"test_id": "t1"}) is None
        assert "AB_Tests table not configured" in caplog.text
        backend.create.assert_not_called()

    def test_returns_none_and_warns_when_backend_raises(self, caplog) -> None:
        store, backend, _ = _make_store()
        backend.create.side_effect = RuntimeError("boom")
        with caplog.at_level("WARNING"):
            assert store.create_ab_test({"test_id": "t1"}) is None
        assert "Failed to create AB test" in caplog.text


# ---------------------------------------------------------------------------
# get_ab_tests
# ---------------------------------------------------------------------------


class TestGetAbTests:
    def test_returns_empty_list_when_unconfigured(self) -> None:
        store, backend, _ = _make_store(configured=False)
        assert store.get_ab_tests() == []
        backend.find.assert_not_called()

    def test_no_status_means_no_formula(self) -> None:
        store, backend, _ = _make_store()
        store.get_ab_tests()
        assert backend.find.call_args.kwargs["formula"] is None

    def test_status_filter_shapes_formula(self) -> None:
        store, backend, _ = _make_store()
        store.get_ab_tests(status="running")
        assert backend.find.call_args.kwargs["formula"] == "{status}='running'"

    def test_status_filter_escapes_quote(self) -> None:
        store, backend, _ = _make_store()
        store.get_ab_tests(status="paused's")
        assert "paused\\'s" in backend.find.call_args.kwargs["formula"]

    def test_niche_id_passthrough(self) -> None:
        store, backend, _ = _make_store()
        store.get_ab_tests(niche_id="gaming")
        assert backend.find.call_args.kwargs["niche_id"] == "gaming"

    def test_max_records_pinned_to_50(self) -> None:
        store, backend, _ = _make_store()
        store.get_ab_tests()
        assert backend.find.call_args.kwargs["max_records"] == 50

    def test_returns_find_results_verbatim(self) -> None:
        store, backend, _ = _make_store()
        backend.find.return_value = [{"id": "a"}, {"id": "b"}]
        assert store.get_ab_tests() == [{"id": "a"}, {"id": "b"}]


# ---------------------------------------------------------------------------
# update_ab_test
# ---------------------------------------------------------------------------


class TestUpdateAbTest:
    def test_noop_when_unconfigured(self) -> None:
        store, backend, _ = _make_store(configured=False)
        store.update_ab_test("t1", {"status": "complete"})
        backend.find.assert_not_called()
        backend.update.assert_not_called()

    def test_finds_by_test_id_then_updates(self) -> None:
        store, backend, _ = _make_store()
        backend.find.return_value = [{"id": "ab_1", "fields": {}}]
        store.update_ab_test("t1", {"status": "complete"})

        # Two backend calls: find + update
        assert backend.find.call_args.kwargs["formula"] == "{test_id}='t1'"
        assert backend.find.call_args.kwargs["max_records"] == 1
        # PR #534 adds niche_id kwarg to backend.update. No niche_id
        # arg in this test → kwarg is None (admin-mode backward compat).
        backend.update.assert_called_once_with(
            "AB_Tests", "ab_1", {"status": "complete"}, typecast=True, niche_id=None
        )

    def test_noop_when_no_record_matches(self) -> None:
        store, backend, _ = _make_store()
        backend.find.return_value = []
        store.update_ab_test("t_missing", {"status": "complete"})
        backend.find.assert_called_once()
        backend.update.assert_not_called()

    def test_escapes_quote_in_test_id_filter(self) -> None:
        store, backend, _ = _make_store()
        backend.find.return_value = []
        store.update_ab_test("t'1", {"x": 1})
        assert "t\\'1" in backend.find.call_args.kwargs["formula"]
