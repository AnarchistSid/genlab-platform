"""Tests for the ab_tests producer wire (2026-06-16, closes half-wired table)."""

from __future__ import annotations

from unittest.mock import MagicMock

from genlab_core.monetization.ab_test_recorder import record_cta_variant_assignment


class _FakeClient:
    """Minimal stand-in for BacklogClient with the create_ab_test surface."""

    def __init__(self, *, has_proxy: bool = True, return_id: str | None = "abc-123"):
        self.ab_tests = MagicMock() if has_proxy else None
        self.create_ab_test = MagicMock(return_value=return_id)


class TestRecordCtaVariantAssignment:
    def test_writes_one_row_per_variant(self):
        client = _FakeClient()
        written = record_cta_variant_assignment(
            client,
            niche_id="gaming",
            candidate_id="cand-aaa",
            variants_csv="cta_v1_emoji,cta_v1_curiosity",
            story_id="story-001",
        )
        assert written == 2
        assert client.create_ab_test.call_count == 2

    def test_payload_has_required_fields(self):
        client = _FakeClient()
        record_cta_variant_assignment(
            client,
            niche_id="movies",
            candidate_id="cand-bbb",
            variants_csv="cta_v1_emoji",
            story_id="story-002",
            platforms_csv="instagram,youtube",
        )
        payload = client.create_ab_test.call_args[0][0]
        assert payload["niche_id"] == "movies"
        assert payload["test_name"] == "cta_v1_emoji"
        assert payload["variant_a"] == "cta_v1_emoji"
        assert payload["status"] == "active"
        extra = payload["extra"]
        assert extra["candidate_id"] == "cand-bbb"
        assert extra["story_id"] == "story-002"
        assert extra["platforms"] == "instagram,youtube"
        assert extra["variant_arm_id"] == "cta_v1_emoji"
        assert "recorded_at" in extra

    def test_empty_variants_is_noop(self):
        client = _FakeClient()
        assert (
            record_cta_variant_assignment(
                client, niche_id="gaming", candidate_id="cand-ccc", variants_csv=""
            )
            == 0
        )
        client.create_ab_test.assert_not_called()

    def test_blank_variants_csv_is_noop(self):
        client = _FakeClient()
        assert (
            record_cta_variant_assignment(
                client, niche_id="gaming", candidate_id="cand-ccc", variants_csv="   "
            )
            == 0
        )
        client.create_ab_test.assert_not_called()

    def test_missing_niche_is_noop_with_warning(self):
        client = _FakeClient()
        assert (
            record_cta_variant_assignment(
                client, niche_id="", candidate_id="cand-ddd", variants_csv="cta_v1_emoji"
            )
            == 0
        )
        client.create_ab_test.assert_not_called()

    def test_missing_proxy_is_clean_noop(self):
        client = _FakeClient(has_proxy=False)
        assert (
            record_cta_variant_assignment(
                client, niche_id="gaming", candidate_id="cand-eee", variants_csv="cta_v1_emoji"
            )
            == 0
        )
        # create_ab_test should never even be attempted when proxy is None
        client.create_ab_test.assert_not_called()

    def test_create_returning_none_counts_as_skipped(self):
        """Some store paths return None when the underlying write was
        rejected (e.g. SP path with mis-configured list). Don't count
        those as written."""
        client = _FakeClient(return_id=None)
        written = record_cta_variant_assignment(
            client,
            niche_id="gaming",
            candidate_id="cand-fff",
            variants_csv="cta_v1_emoji,cta_v1_curiosity",
        )
        assert written == 0
        assert client.create_ab_test.call_count == 2  # both were attempted

    def test_exception_per_variant_does_not_break_loop(self):
        """If the first variant write raises, the second variant
        still gets a fair attempt. The recorder is best-effort."""
        client = _FakeClient()
        client.create_ab_test.side_effect = [Exception("DB down"), "row-2"]
        written = record_cta_variant_assignment(
            client,
            niche_id="gaming",
            candidate_id="cand-ggg",
            variants_csv="cta_a,cta_b",
        )
        assert written == 1  # only the second succeeded
        assert client.create_ab_test.call_count == 2

    def test_filters_whitespace_only_entries_in_csv(self):
        """A trailing comma or whitespace shouldn't write a blank-variant row."""
        client = _FakeClient()
        written = record_cta_variant_assignment(
            client,
            niche_id="gaming",
            candidate_id="cand-hhh",
            variants_csv="cta_a, ,cta_b,",
        )
        assert written == 2
        # Verify variants captured cleanly
        names = [call.args[0]["test_name"] for call in client.create_ab_test.call_args_list]
        assert names == ["cta_a", "cta_b"]
