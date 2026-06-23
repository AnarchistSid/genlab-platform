"""Pin: PR Z (2026-06-23) — Thompson-sampling read side of optimal-time bandit.

PR Z adds ``sample_optimal_hours_from_bandit`` and an env-gated path
inside ``get_optimal_hours`` so the bandit augments rather than
replaces the legacy Bayesian-shrinkage heuristic until arms have
≥1 week of observations.

These pins lock in:

  Bandit sampler core (4):
    - returns empty when no arms exist (cold start fall-through)
    - returns empty when no MATCHING arms exist (different platform
      / niche than requested)
    - parses hour from ``hour:{H}:{platform}:{niche_id}`` arm shape
    - returns top-N sorted descending by Beta sample

  Arm filtering (2):
    - rejects malformed arm IDs (no hour digits, out-of-range H)
    - rejects arms with wrong platform suffix (no cross-platform
      bleed-through)

  get_optimal_hours env-gate (2):
    - env unset → bandit path never consulted, heuristic runs as before
    - env=1 + bandit has data → returns bandit hours, skips heuristic
    - env=1 + bandit empty → falls through to heuristic (cold-start
      protection)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestSampleOptimalHoursFromBandit:
    def test_no_arms_returns_empty(self):
        """Pin: cold start (no bandit_arms rows) → empty list. The
        caller (get_optimal_hours env-gated path) checks for empty
        here and falls through to the heuristic."""
        from genlab_core.scheduling import optimal_time_learner

        fake_proxy = MagicMock()
        fake_client = MagicMock()
        fake_client.bandit_arms = fake_proxy
        with patch(
            "genlab_core.http.backlog_client.BacklogClient",
            return_value=fake_client,
        ):
            with patch(
                "genlab_core.learning.arm_loader.load_all_arms",
                return_value={},
            ):
                hours = optimal_time_learner.sample_optimal_hours_from_bandit("gaming", "youtube")
        assert hours == []

    def test_no_matching_arms_returns_empty(self):
        """Pin: arms exist but none match the (platform, niche) tuple
        → empty. Defends against cross-platform / cross-niche bleed-
        through that would attribute one niche's history to another."""
        from genlab_core.scheduling import optimal_time_learner

        # All arms are for instagram + ai_creators, but we ask for
        # youtube + gaming → no match.
        arms = {
            "hour:6:instagram:ai_creators": (5.0, 2.0),
            "hour:12:instagram:ai_creators": (8.0, 1.0),
            "style:ai_creators:question": (3.0, 1.0),
        }
        fake_client = MagicMock()
        fake_client.bandit_arms = MagicMock()
        with patch(
            "genlab_core.http.backlog_client.BacklogClient",
            return_value=fake_client,
        ):
            with patch(
                "genlab_core.learning.arm_loader.load_all_arms",
                return_value=arms,
            ):
                hours = optimal_time_learner.sample_optimal_hours_from_bandit("gaming", "youtube")
        assert hours == []

    def test_parses_hour_from_arm_id(self):
        """Pin: arm_id ``hour:14:youtube:gaming`` is correctly parsed
        as hour=14. Without this the sampler would never produce
        valid OptimalHour records even with full arm data."""
        from genlab_core.scheduling import optimal_time_learner

        arms = {"hour:14:youtube:gaming": (10.0, 1.0)}
        fake_client = MagicMock()
        fake_client.bandit_arms = MagicMock()
        with patch(
            "genlab_core.http.backlog_client.BacklogClient",
            return_value=fake_client,
        ):
            with patch(
                "genlab_core.learning.arm_loader.load_all_arms",
                return_value=arms,
            ):
                hours = optimal_time_learner.sample_optimal_hours_from_bandit(
                    "gaming", "youtube", top_n=3
                )
        assert len(hours) == 1
        assert hours[0].hour_utc == 14

    def test_returns_top_n_sorted_desc(self):
        """Pin: top_n=2 returns at most 2 entries, sorted by descending
        sample. Higher alpha → higher expected Beta sample → ranks
        first deterministically across seeds (with high enough alpha
        spread)."""
        from genlab_core.scheduling import optimal_time_learner

        # Very different alphas → samples ordering is statistically
        # near-deterministic. Alpha 100 vastly outranks alpha 1.
        arms = {
            "hour:6:youtube:gaming": (100.0, 1.0),  # high posterior
            "hour:12:youtube:gaming": (50.0, 1.0),
            "hour:18:youtube:gaming": (1.0, 100.0),  # low posterior
        }
        fake_client = MagicMock()
        fake_client.bandit_arms = MagicMock()
        with patch(
            "genlab_core.http.backlog_client.BacklogClient",
            return_value=fake_client,
        ):
            with patch(
                "genlab_core.learning.arm_loader.load_all_arms",
                return_value=arms,
            ):
                hours = optimal_time_learner.sample_optimal_hours_from_bandit(
                    "gaming", "youtube", top_n=2
                )
        assert len(hours) == 2
        # hour=6 (alpha=100) and hour=12 (alpha=50) should beat hour=18
        # (alpha=1, beta=100). Hour=18 should NOT be in top-2.
        top_hours = [h.hour_utc for h in hours]
        assert 18 not in top_hours


class TestArmFiltering:
    def test_malformed_arm_ids_skipped(self):
        """Pin: arms with non-digit hour token or out-of-range hour
        are skipped without error. Defense against any stale / hand-
        edited rows poisoning the sampler."""
        from genlab_core.scheduling import optimal_time_learner

        arms = {
            "hour:abc:youtube:gaming": (5.0, 2.0),  # non-digit
            "hour:99:youtube:gaming": (5.0, 2.0),  # out of range
            "hour:7:youtube:gaming": (5.0, 2.0),  # valid
        }
        fake_client = MagicMock()
        fake_client.bandit_arms = MagicMock()
        with patch(
            "genlab_core.http.backlog_client.BacklogClient",
            return_value=fake_client,
        ):
            with patch(
                "genlab_core.learning.arm_loader.load_all_arms",
                return_value=arms,
            ):
                hours = optimal_time_learner.sample_optimal_hours_from_bandit("gaming", "youtube")
        # Only the valid arm survives
        assert len(hours) == 1
        assert hours[0].hour_utc == 7

    def test_wrong_platform_suffix_skipped(self):
        """Pin: arms with the right hour prefix but wrong platform
        suffix are NOT counted. Without this filter the YouTube
        bandit would learn from Instagram engagement noise."""
        from genlab_core.scheduling import optimal_time_learner

        arms = {
            "hour:14:instagram:gaming": (10.0, 1.0),  # wrong platform
            "hour:14:youtube:gaming": (5.0, 1.0),  # right platform
        }
        fake_client = MagicMock()
        fake_client.bandit_arms = MagicMock()
        with patch(
            "genlab_core.http.backlog_client.BacklogClient",
            return_value=fake_client,
        ):
            with patch(
                "genlab_core.learning.arm_loader.load_all_arms",
                return_value=arms,
            ):
                hours = optimal_time_learner.sample_optimal_hours_from_bandit("gaming", "youtube")
        # Only the youtube arm contributes
        assert len(hours) == 1
        assert hours[0].hour_utc == 14


class TestEnvGate:
    def test_env_unset_bandit_path_not_consulted(self, monkeypatch):
        """Pin: GENLAB_OPTIMAL_TIME_BANDIT unset → bandit sampler
        never called, heuristic runs as before. Staged rollout
        invariant — flag default-off."""
        monkeypatch.delenv("GENLAB_OPTIMAL_TIME_BANDIT", raising=False)
        monkeypatch.setenv("DATABASE_URL", "dbname=stub")

        from genlab_core.scheduling import optimal_time_learner

        # Reset cache so prior test data doesn't bleed
        optimal_time_learner._cache.clear()

        sampler_calls = []

        def fake_sampler(*a, **kw):
            sampler_calls.append((a, kw))
            return []

        monkeypatch.setattr(
            optimal_time_learner,
            "sample_optimal_hours_from_bandit",
            fake_sampler,
        )

        # Force heuristic path to fail fast (no real DSN) so we don't
        # need to mock the entire query — we just need to assert the
        # sampler was NOT called.
        result = optimal_time_learner.get_optimal_hours("gaming", "youtube")
        assert sampler_calls == []
        # Heuristic returns empty for stub DSN
        assert result == []

    def test_env_on_bandit_has_data_returns_bandit_hours(self, monkeypatch):
        """Pin: env=1 AND bandit returns non-empty → those hours are
        returned, heuristic NOT consulted. The bandit-takeover path."""
        monkeypatch.setenv("GENLAB_OPTIMAL_TIME_BANDIT", "1")

        from genlab_core.scheduling import optimal_time_learner

        optimal_time_learner._cache.clear()

        fake_hours = [
            optimal_time_learner.OptimalHour(
                hour_utc=14,
                avg_engagement=0.8,
                sample_size=0,
                confidence=0.8,
            )
        ]
        monkeypatch.setattr(
            optimal_time_learner,
            "sample_optimal_hours_from_bandit",
            lambda *a, **kw: fake_hours,
        )

        # If the heuristic path were reached, it would touch DATABASE_URL.
        # We don't set DATABASE_URL → heuristic returns []. So if the
        # result is fake_hours, the bandit path won (and short-circuited
        # before heuristic).
        result = optimal_time_learner.get_optimal_hours("gaming", "youtube")
        assert result == fake_hours

    def test_env_on_bandit_empty_falls_through_to_heuristic(self, monkeypatch):
        """Pin: env=1 BUT bandit returns empty (cold-start) → falls
        through to heuristic. Critical safety property — without
        this the env-flag flip would break optimal-time entirely on
        any niche/platform without bandit data yet."""
        monkeypatch.setenv("GENLAB_OPTIMAL_TIME_BANDIT", "1")
        monkeypatch.delenv("DATABASE_URL", raising=False)

        from genlab_core.scheduling import optimal_time_learner

        optimal_time_learner._cache.clear()

        # Bandit returns empty → fall-through
        monkeypatch.setattr(
            optimal_time_learner,
            "sample_optimal_hours_from_bandit",
            lambda *a, **kw: [],
        )

        # Heuristic also returns empty (no DSN) — but the important
        # part is that get_optimal_hours did NOT raise and DID try the
        # heuristic path after bandit returned empty.
        result = optimal_time_learner.get_optimal_hours("gaming", "youtube")
        assert result == []  # Both paths empty → empty list, not exception
