"""Pin the engagement-question A/B canary rollout logic.

Contract:

  * `GENLAB_ENGAGEMENT_QUESTION_ROLLOUT_PCT` env var (0-100 int):
      unset / >= 100 / parse-error -> "with_q" always (backward compat)
      <= 0 -> "without_q" always (kill switch)
      50 -> ~50/50 deterministic split by candidate_id hash
  * Deterministic: same candidate_id always lands in the same bucket
    across pipeline re-runs. Uses sha256 hash-mod pattern (matches
    AUTO #2 ladder from CLAUDE.md).
  * Empty candidate_id -> "with_q" (fail-open).
  * Bucket recorded as `{slot}__ab_bucket` field on the blueprint
    so downstream reward attribution can query the assignment.
  * Structured log line at INFO for post-hoc lift analysis:
    `engagement_question_ab niche=X candidate=... bucket=with_q|without_q`
"""

from __future__ import annotations

import logging
from collections import Counter
from unittest.mock import patch

import pytest

from genlab_core.monetization.cta_engine import (
    _apply_engagement_question_fallback,
    _engagement_question_ab_bucket,
)


class TestBucketingLogic:
    def test_no_env_var_returns_with_q(self, monkeypatch):
        monkeypatch.delenv("GENLAB_ENGAGEMENT_QUESTION_ROLLOUT_PCT", raising=False)
        assert _engagement_question_ab_bucket("candidate_abc") == "with_q"

    def test_pct_100_always_with_q(self, monkeypatch):
        monkeypatch.setenv("GENLAB_ENGAGEMENT_QUESTION_ROLLOUT_PCT", "100")
        for i in range(20):
            assert _engagement_question_ab_bucket(f"cand_{i}") == "with_q"

    def test_pct_0_always_without_q(self, monkeypatch):
        monkeypatch.setenv("GENLAB_ENGAGEMENT_QUESTION_ROLLOUT_PCT", "0")
        for i in range(20):
            assert _engagement_question_ab_bucket(f"cand_{i}") == "without_q"

    def test_pct_50_approximate_split(self, monkeypatch):
        monkeypatch.setenv("GENLAB_ENGAGEMENT_QUESTION_ROLLOUT_PCT", "50")
        buckets = Counter(
            _engagement_question_ab_bucket(f"blueprint_{i:06d}") for i in range(1000)
        )
        # sha256 uniformity — expect ~500/500, tolerate 400-600 range
        assert 400 <= buckets["with_q"] <= 600
        assert 400 <= buckets["without_q"] <= 600

    def test_deterministic_across_calls(self, monkeypatch):
        """Same candidate_id + same pct -> same bucket every call.
        Idempotent A/B assignment is critical for pipeline re-runs."""
        monkeypatch.setenv("GENLAB_ENGAGEMENT_QUESTION_ROLLOUT_PCT", "50")
        first = _engagement_question_ab_bucket("blueprint_xyz")
        for _ in range(10):
            assert _engagement_question_ab_bucket("blueprint_xyz") == first

    def test_empty_candidate_id_returns_with_q(self, monkeypatch):
        """Fail-open: don't punish blueprints missing an ID."""
        monkeypatch.setenv("GENLAB_ENGAGEMENT_QUESTION_ROLLOUT_PCT", "50")
        assert _engagement_question_ab_bucket("") == "with_q"

    def test_parse_error_returns_with_q(self, monkeypatch):
        monkeypatch.setenv("GENLAB_ENGAGEMENT_QUESTION_ROLLOUT_PCT", "not_a_number")
        assert _engagement_question_ab_bucket("cand") == "with_q"

    def test_pct_above_100_treated_as_100(self, monkeypatch):
        monkeypatch.setenv("GENLAB_ENGAGEMENT_QUESTION_ROLLOUT_PCT", "150")
        for i in range(20):
            assert _engagement_question_ab_bucket(f"cand_{i}") == "with_q"

    def test_pct_negative_treated_as_zero(self, monkeypatch):
        monkeypatch.setenv("GENLAB_ENGAGEMENT_QUESTION_ROLLOUT_PCT", "-5")
        for i in range(20):
            assert _engagement_question_ab_bucket(f"cand_{i}") == "without_q"


class TestFallbackWireAB:
    _FIELDS_TEMPLATE = {
        "niche_id": "sports",
        "candidate_id": "blueprint_test_id_12345",
        "hook": "Game-winning shot",
        "title": "Buzzer beater from half-court",
        "summary": "Down by 2, shot from beyond half-court.",
    }

    def _apply(self, fields, monkeypatch, mock_return="which surprised you more?"):
        from genlab_core.publishing import first_comment_question as fcq
        monkeypatch.setattr(
            fcq, "generate_engagement_question", lambda **_: mock_return
        )
        _apply_engagement_question_fallback(fields)

    def test_pct_100_populates_slot(self, monkeypatch):
        monkeypatch.setenv("GENLAB_YT_ENGAGEMENT_QUESTION_ENABLED", "1")
        monkeypatch.setenv("GENLAB_ENGAGEMENT_QUESTION_ROLLOUT_PCT", "100")
        fields = dict(self._FIELDS_TEMPLATE)
        self._apply(fields, monkeypatch)
        assert fields.get("youtube_first_comment", "").endswith("?")
        assert fields.get("youtube_first_comment__ab_bucket") == "with_q"

    def test_pct_0_leaves_slot_empty_but_records_bucket(self, monkeypatch):
        monkeypatch.setenv("GENLAB_YT_ENGAGEMENT_QUESTION_ENABLED", "1")
        monkeypatch.setenv("GENLAB_ENGAGEMENT_QUESTION_ROLLOUT_PCT", "0")
        fields = dict(self._FIELDS_TEMPLATE)
        self._apply(fields, monkeypatch)
        assert not fields.get("youtube_first_comment")
        assert fields.get("youtube_first_comment__ab_bucket") == "without_q"

    def test_bucket_log_line_emitted(self, monkeypatch, caplog):
        monkeypatch.setenv("GENLAB_YT_ENGAGEMENT_QUESTION_ENABLED", "1")
        monkeypatch.setenv("GENLAB_ENGAGEMENT_QUESTION_ROLLOUT_PCT", "100")
        fields = dict(self._FIELDS_TEMPLATE)
        with caplog.at_level(logging.INFO):
            self._apply(fields, monkeypatch)
        msg = next(
            r.message for r in caplog.records if "engagement_question_ab" in r.message
        )
        assert "niche=sports" in msg
        assert "candidate=" in msg
        assert "bucket=with_q" in msg

    def test_all_platforms_share_same_bucket(self, monkeypatch):
        """One bucket assignment per blueprint applies to all platforms.
        Guards against 'YT gets question, IG doesn't' inconsistency
        that would confuse the A/B measurement."""
        monkeypatch.setenv("GENLAB_YT_ENGAGEMENT_QUESTION_ENABLED", "1")
        monkeypatch.setenv("GENLAB_IG_ENGAGEMENT_QUESTION_ENABLED", "1")
        monkeypatch.setenv("GENLAB_THREADS_ENGAGEMENT_QUESTION_ENABLED", "1")
        monkeypatch.setenv("GENLAB_ENGAGEMENT_QUESTION_ROLLOUT_PCT", "100")
        fields = dict(self._FIELDS_TEMPLATE)
        self._apply(fields, monkeypatch)
        # Same bucket recorded across all 3 platforms
        assert fields.get("youtube_first_comment__ab_bucket") == "with_q"
        assert fields.get("instagram_first_comment__ab_bucket") == "with_q"
        assert fields.get("threads_first_comment__ab_bucket") == "with_q"

    def test_affiliate_slot_not_overridden_regardless_of_bucket(self, monkeypatch):
        """When YT slot already has affiliate CTA, wire skips YT entirely
        (never bucket-records; that slot is out of the A/B experiment).
        IG + Threads still bucket normally."""
        monkeypatch.setenv("GENLAB_YT_ENGAGEMENT_QUESTION_ENABLED", "1")
        monkeypatch.setenv("GENLAB_IG_ENGAGEMENT_QUESTION_ENABLED", "1")
        monkeypatch.setenv("GENLAB_ENGAGEMENT_QUESTION_ROLLOUT_PCT", "100")
        fields = dict(self._FIELDS_TEMPLATE)
        fields["youtube_first_comment"] = "🔗 Get Foo: https://x.co/y"
        self._apply(fields, monkeypatch)
        assert fields["youtube_first_comment"] == "🔗 Get Foo: https://x.co/y"
        # YT slot was already populated -> no bucket recorded
        assert "youtube_first_comment__ab_bucket" not in fields
        # IG was empty + flag on -> gets bucketed + populated
        assert fields.get("instagram_first_comment", "").endswith("?")
        assert fields.get("instagram_first_comment__ab_bucket") == "with_q"

    def test_no_platform_flags_no_bucket_record(self, monkeypatch):
        """When all per-platform flags are off, the A/B log still fires
        (so operator sees the experiment is running) but no slot gets
        bucketed. Zero effect on fields."""
        monkeypatch.delenv("GENLAB_YT_ENGAGEMENT_QUESTION_ENABLED", raising=False)
        monkeypatch.delenv("GENLAB_IG_ENGAGEMENT_QUESTION_ENABLED", raising=False)
        monkeypatch.delenv("GENLAB_THREADS_ENGAGEMENT_QUESTION_ENABLED", raising=False)
        monkeypatch.setenv("GENLAB_ENGAGEMENT_QUESTION_ROLLOUT_PCT", "50")
        fields = dict(self._FIELDS_TEMPLATE)
        self._apply(fields, monkeypatch)
        assert not any(k.endswith("__ab_bucket") for k in fields)
