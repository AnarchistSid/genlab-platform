"""Tests for :mod:`genlab_core.http.analytics_store`.

Tier 2 / audit S-2 — the first dedicated test file for an extracted
BacklogClient sub-store. Tests the new module directly, bypassing
BacklogClient entirely, so future BacklogClient refactors don't drag
analytics_store with them.
"""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock

from genlab_core.http.analytics_store import (
    _ANALYTICS_OPTIONAL_FIELDS,
    AnalyticsStore,
    _esc,
)
from genlab_core.http.circuit_breaker import CircuitOpenError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(*, find_returns=None, create_returns=None, update_returns=None):
    """Build an AnalyticsStore wired to a mock backend.

    The injected ``sp_call`` is the identity wrapper (call the function
    directly) so we don't have to set up a circuit breaker for each test.
    """
    backend_mock = MagicMock()
    backend_mock.find.return_value = find_returns or []
    backend_mock.create.return_value = create_returns or {"id": "new_rec"}
    backend_mock.update.return_value = update_returns or {"id": "updated_rec"}

    backend_lookup = MagicMock(return_value=backend_mock)
    sp_call = lambda fn, *a, **kw: fn(*a, **kw)  # noqa: E731

    store = AnalyticsStore(sp_call=sp_call, backend=backend_lookup)
    return store, backend_mock, backend_lookup


# ---------------------------------------------------------------------------
# _esc — formula-escaping helper
# ---------------------------------------------------------------------------


class TestEsc:
    def test_escapes_single_quote(self) -> None:
        assert _esc("hi 'world") == r"hi \'world"

    def test_passthrough_when_no_quote(self) -> None:
        assert _esc("hello") == "hello"


# ---------------------------------------------------------------------------
# log_publish_result
# ---------------------------------------------------------------------------


class TestLogPublishResult:
    def test_creates_new_record_when_not_found(self) -> None:
        store, backend, _ = _make_store(create_returns={"id": "rec_new"})
        result = store.log_publish_result(
            candidate_id="cand1", platform="instagram", status="SUCCESS"
        )
        assert result == "rec_new"
        backend.create.assert_called_once()
        assert backend.update.call_count == 0

    def test_updates_existing_record(self) -> None:
        store, backend, _ = _make_store(find_returns=[{"id": "rec_existing"}])
        result = store.log_publish_result(
            candidate_id="cand1", platform="instagram", status="SUCCESS"
        )
        assert result == "rec_existing"
        backend.update.assert_called_once()
        assert backend.create.call_count == 0

    def test_analytics_id_is_stable_sha256(self) -> None:
        """The analytics_id is the row's idempotency key — re-running a
        publish must update the same row."""
        store, backend, _ = _make_store()
        store.log_publish_result(candidate_id="cand1", platform="instagram", status="SUCCESS")
        expected = hashlib.sha256(b"cand1:instagram").hexdigest()
        # First call to find() used the formula
        find_args = backend.find.call_args
        assert expected in find_args.kwargs["formula"]

    def test_included_status_fields(self) -> None:
        store, backend, _ = _make_store()
        store.log_publish_result(
            candidate_id="cand1",
            platform="instagram",
            status="SUCCESS",
            post_id="ig_123",
            niche_id="gaming",
            error_message="x" * 5000,  # >2000 truncation guard
        )
        fields = backend.create.call_args.args[1]
        assert fields["post_id"] == "ig_123"
        assert fields["niche_id"] == "gaming"
        assert len(fields["error_message"]) == 2000  # truncated
        assert "published_at" in fields  # SUCCESS triggers timestamp

    def test_omits_optional_fields_when_falsy(self) -> None:
        store, backend, _ = _make_store()
        store.log_publish_result(candidate_id="cand1", platform="instagram", status="FAILED")
        fields = backend.create.call_args.args[1]
        assert "post_id" not in fields  # empty post_id → omitted
        assert "niche_id" not in fields
        assert "published_at" not in fields  # FAILED → no timestamp

    def test_circuit_open_returns_none_silently(self) -> None:
        backend = MagicMock()

        def raise_circuit(*args, **kwargs):
            raise CircuitOpenError("sharepoint open")

        store = AnalyticsStore(sp_call=raise_circuit, backend=lambda _: backend)
        result = store.log_publish_result(
            candidate_id="cand1", platform="instagram", status="SUCCESS"
        )
        assert result is None

    def test_arbitrary_exception_returns_none_silently(self) -> None:
        """A SharePoint hiccup must NEVER unwind the publish path —
        analytics logging is best-effort."""
        store, backend, _ = _make_store()
        backend.find.side_effect = RuntimeError("graph down")
        result = store.log_publish_result(
            candidate_id="cand1", platform="instagram", status="SUCCESS"
        )
        assert result is None


# ---------------------------------------------------------------------------
# get_publishing_analytics
# ---------------------------------------------------------------------------


class TestGetPublishingAnalytics:
    def test_no_filters_uses_no_formula(self) -> None:
        store, backend, _ = _make_store(find_returns=[{"id": "1"}, {"id": "2"}])
        result = store.get_publishing_analytics()
        assert len(result) == 2
        # No filters → formula=None
        assert backend.find.call_args.kwargs["formula"] is None

    def test_platform_filter_only(self) -> None:
        store, backend, _ = _make_store()
        store.get_publishing_analytics(platform="instagram")
        formula = backend.find.call_args.kwargs["formula"]
        assert "platform" in formula
        assert "instagram" in formula

    def test_both_filters_combined_with_AND(self) -> None:
        store, backend, _ = _make_store()
        store.get_publishing_analytics(platform="instagram", status="SUCCESS")
        formula = backend.find.call_args.kwargs["formula"]
        assert formula.startswith("AND(")
        assert "instagram" in formula and "SUCCESS" in formula

    def test_niche_id_passed_as_kwarg_not_formula(self) -> None:
        """The niche_id cross-channel guard is in the backend's find(),
        not the formula string — verify it lands in the right param."""
        store, backend, _ = _make_store()
        store.get_publishing_analytics(niche_id="gaming")
        assert backend.find.call_args.kwargs["niche_id"] == "gaming"

    def test_limit_passed_through_as_max_records(self) -> None:
        store, backend, _ = _make_store()
        store.get_publishing_analytics(limit=42)
        assert backend.find.call_args.kwargs["max_records"] == 42


# ---------------------------------------------------------------------------
# upsert_analytics
# ---------------------------------------------------------------------------


class TestUpsertAnalytics:
    def test_creates_new_record_when_not_found(self) -> None:
        store, backend, _ = _make_store(create_returns={"id": "an_new"})
        result = store.upsert_analytics(
            post_id="ABC123", platform="youtube", insights={"views": 100, "likes": 5}
        )
        assert result == "an_new"
        backend.create.assert_called_once()

    def test_composite_id_built_from_platform(self) -> None:
        store, backend, _ = _make_store()
        store.upsert_analytics(post_id="ABC", platform="youtube", insights={})
        fields = backend.create.call_args.args[1]
        assert fields["post_id"] == "youtube:ABC"

    def test_composite_id_not_double_prefixed(self) -> None:
        """post_id may already carry the prefix — don't double up."""
        store, backend, _ = _make_store()
        store.upsert_analytics(post_id="youtube:ABC", platform="youtube", insights={})
        fields = backend.create.call_args.args[1]
        assert fields["post_id"] == "youtube:ABC"

    def test_zero_reach_produces_zero_rates(self) -> None:
        """A divide-by-tiny-reach would inflate rates — test the guard."""
        store, backend, _ = _make_store()
        store.upsert_analytics(
            post_id="x",
            platform="instagram",
            insights={"reach": 0, "engagement": 100, "saves": 50, "shares": 20},
        )
        fields = backend.create.call_args.args[1]
        assert fields["engagement_rate"] == 0.0
        assert fields["save_rate"] == 0.0
        assert fields["share_rate"] == 0.0

    def test_rates_computed_when_reach_nonzero(self) -> None:
        store, backend, _ = _make_store()
        store.upsert_analytics(
            post_id="x",
            platform="instagram",
            insights={"reach": 1000, "engagement": 100, "saves": 50, "shares": 20},
        )
        fields = backend.create.call_args.args[1]
        assert fields["engagement_rate"] == 0.1  # 100/1000
        assert fields["save_rate"] == 0.05  # 50/1000
        assert fields["share_rate"] == 0.02  # 20/1000

    def test_viral_score_default_formula(self) -> None:
        store, backend, _ = _make_store()
        store.upsert_analytics(
            post_id="x",
            platform="instagram",
            insights={"reach": 1000, "engagement": 100, "saves": 50, "shares": 20},
        )
        fields = backend.create.call_args.args[1]
        # engagement_rate*0.25 + share_rate*0.40 + save_rate*0.35
        # = 0.1*0.25 + 0.02*0.40 + 0.05*0.35 = 0.025 + 0.008 + 0.0175 = 0.0505
        assert fields["viral_score"] == 0.0505

    def test_viral_score_override_honoured(self) -> None:
        store, backend, _ = _make_store()
        store.upsert_analytics(
            post_id="x",
            platform="instagram",
            insights={"reach": 100},
            viral_score=0.42,
        )
        fields = backend.create.call_args.args[1]
        assert fields["viral_score"] == 0.42

    def test_engagement_falls_back_to_likes_comments_shares_when_key_missing(self) -> None:
        """REGRESSION (post-2026-06-25 audit): platform metric fetchers in
        learning/metrics/*.py never provide an "engagement" key — they return
        likes/comments/shares separately. The prior code path
        ``engagement = insights.get("engagement", 0) or 0`` made engagement
        always 0 → engagement_rate = 0 / reach = 0.0 for ~16 days of records.

        The bug originated at commit f32b6189 (2026-06-09) and was caught by
        the 2026-06-25 prod audit. Tests passed because the existing
        ``test_rates_computed_when_reach_nonzero`` artificially injected the
        ``engagement`` key, which production fetchers never do.

        Pin: when "engagement" key is absent, the upsert must derive it from
        the sum of base engagement metrics (likes + comments + shares).
        """
        store, backend, _ = _make_store()
        store.upsert_analytics(
            post_id="x",
            platform="instagram",
            # Realistic IG fetcher output: NO "engagement" key.
            insights={"reach": 1000, "likes": 50, "comments": 30, "shares": 20},
        )
        fields = backend.create.call_args.args[1]
        # Derived: 50 + 30 + 20 = 100
        assert fields["engagement"] == 100
        # Rate: 100 / 1000 = 0.1
        assert fields["engagement_rate"] == 0.1
        # Viral score formula uses engagement_rate, save_rate, share_rate:
        #   0.1 * 0.25 + 0.02 * 0.40 + 0 * 0.35 = 0.025 + 0.008 = 0.033
        assert fields["viral_score"] == 0.033

    def test_engagement_explicit_zero_falls_back_to_sum(self) -> None:
        """Defensive: explicit ``engagement: 0`` is treated the same as
        missing — both fall back to (likes + comments + shares). This is the
        same ``or`` semantics that drove the original bug, but now applied
        intentionally on the correct side of the lookup."""
        store, backend, _ = _make_store()
        store.upsert_analytics(
            post_id="x",
            platform="facebook",
            insights={"reach": 500, "engagement": 0, "likes": 10, "comments": 5, "shares": 2},
        )
        fields = backend.create.call_args.args[1]
        # 10 + 5 + 2 = 17, not 0
        assert fields["engagement"] == 17

    def test_explicit_engagement_takes_precedence_over_sum(self) -> None:
        """Backward-compatible: callers that DO pass an explicit ``engagement``
        value (e.g., a future pre-aggregated source) still see it preserved."""
        store, backend, _ = _make_store()
        store.upsert_analytics(
            post_id="x",
            platform="instagram",
            insights={"reach": 1000, "engagement": 200, "likes": 50, "comments": 30, "shares": 20},
        )
        fields = backend.create.call_args.args[1]
        # 200 takes precedence over the 100 sum
        assert fields["engagement"] == 200

    def test_legacy_saved_alias_treated_as_saves(self) -> None:
        """IG insights use the legacy ``saved`` key; the new canonical
        is ``saves``. Both must work."""
        store, backend, _ = _make_store()
        store.upsert_analytics(
            post_id="x",
            platform="instagram",
            insights={"reach": 100, "saved": 10},
        )
        fields = backend.create.call_args.args[1]
        assert fields["saved"] == 10  # row column name
        assert fields["save_rate"] == 0.1  # rate computed from the alias

    def test_legacy_retweets_alias_treated_as_shares(self) -> None:
        """X insights use the legacy ``retweets`` key; the new canonical
        is ``shares``. Both must work."""
        store, backend, _ = _make_store()
        store.upsert_analytics(
            post_id="x",
            platform="twitter",
            insights={"reach": 100, "retweets": 7},
        )
        fields = backend.create.call_args.args[1]
        assert fields["shares"] == 7

    def test_schema_drift_strips_optional_fields_and_retries(self) -> None:
        """When SharePoint reports a missing column, the upsert must strip
        the optional fields and retry — this is the dev/prod-schema-drift
        defence; without it the metric collector breaks on every column
        addition."""
        store, backend, _ = _make_store()
        # First create raises UNKNOWN_FIELD_NAME, second succeeds.
        backend.create.side_effect = [
            Exception("UNKNOWN_FIELD_NAME: niche_id"),
            {"id": "an_after_retry"},
        ]
        result = store.upsert_analytics(
            post_id="x",
            platform="instagram",
            insights={"reach": 100},
            niche_id="gaming",
        )
        assert result == "an_after_retry"
        assert backend.create.call_count == 2
        # Second call's fields dict had niche_id stripped.
        retry_fields = backend.create.call_args_list[1].args[1]
        for stripped in _ANALYTICS_OPTIONAL_FIELDS:
            assert stripped not in retry_fields

    def test_returns_none_on_arbitrary_failure(self) -> None:
        """Best-effort: a non-schema-drift exception is logged and dropped."""
        store, backend, _ = _make_store()
        backend.find.side_effect = RuntimeError("graph down")
        result = store.upsert_analytics(post_id="x", platform="instagram", insights={"reach": 100})
        assert result is None

    def test_postgres_uuid_return_coerced_to_string(self) -> None:
        """Postgres create() returns a UUID string directly; SharePoint
        returns a dict. Both code paths must surface a string."""
        store, backend, _ = _make_store(create_returns="00000000-uuid-string")
        result = store.upsert_analytics(post_id="x", platform="instagram", insights={"reach": 100})
        assert result == "00000000-uuid-string"


# ---------------------------------------------------------------------------
# Optional-fields contract
# ---------------------------------------------------------------------------


class TestOptionalFieldsContract:
    def test_frozenset_membership(self) -> None:
        """A future refactor that drops one of these names breaks the
        schema-drift defence."""
        for name in (
            "platform",
            "candidate_id",
            "published_at",
            "format",
            "fetch_window",
            "story_title",
            "niche_id",
        ):
            assert name in _ANALYTICS_OPTIONAL_FIELDS, f"{name} missing from drift-defence set"

    def test_is_frozenset_not_mutable(self) -> None:
        """Caller-side mutation of this set would corrupt the contract."""
        assert isinstance(_ANALYTICS_OPTIONAL_FIELDS, frozenset)
