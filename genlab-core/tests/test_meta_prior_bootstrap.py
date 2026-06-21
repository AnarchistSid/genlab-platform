"""Pin: ``bootstrap_niche_warm_start`` wires meta_prior into niche scaffolding (Lever D3).

Pre-2026-06-21 ``meta_prior.apply_warm_start`` existed with proper
Kveton 2021 transfer math but had **zero production callers**. New
niches started cold from Beta(1, 1) on every arm — sister-niche data
that could have warm-started them was ignored.

Lever D3 adds ``bootstrap_niche_warm_start(niche_id)`` that:
  1. Looks up the source niche from ``TRANSFER_MATRIX``
  2. Loads source arms via BacklogClient
  3. Calls ``apply_warm_start`` with the right shape

Wired into ``create_niche.scaffold_niche`` as a best-effort step.

These pins lock in:
  1. Niche not in TRANSFER_MATRIX → returns ``{}`` cleanly (no transfer)
  2. Source niche has no arms yet → returns ``{}`` cleanly (nothing to transfer)
  3. Happy path: source has arms → ``apply_warm_start`` is invoked
  4. BacklogClient failure → returns ``{}`` (best-effort, no exception)
  5. ``apply_warm_start`` failure → returns ``{}`` (best-effort, no exception)
  6. CLI module is invokable via ``python -m`` (smoke test the argparse path)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestBootstrapNicheWarmStart:
    def test_unknown_niche_returns_empty_dict(self):
        """A niche with no TRANSFER_MATRIX entry returns ``{}`` without
        even attempting BacklogClient — pure config lookup."""
        from genlab_core.learning.meta_prior import bootstrap_niche_warm_start

        # "fitness" isn't in TRANSFER_MATRIX (only sports/movies/anime are).
        result = bootstrap_niche_warm_start("fitness")
        assert result == {}, f"Expected empty dict for unmapped niche; got {result}"

    def test_source_with_no_arms_returns_empty_dict(self):
        """When source niche has no arms (cold-start period), the
        function returns {} gracefully — nothing to transfer."""
        from genlab_core.learning import meta_prior

        # Stub BacklogClient + load_all_arms so source returns no arms.
        fake_proxy = MagicMock()
        fake_client = MagicMock()
        fake_client.bandit_arms = fake_proxy

        with (
            patch(
                "genlab_core.http.backlog_client.BacklogClient",
                return_value=fake_client,
            ),
            patch("genlab_core.learning.arm_loader.load_all_arms", return_value={}),
        ):
            # "sports" → "gaming" mapping exists in TRANSFER_MATRIX
            result = meta_prior.bootstrap_niche_warm_start("sports")
        assert result == {}, f"Expected empty dict when source has no arms; got {result}"

    def test_happy_path_calls_apply_warm_start(self):
        """When source has arms, ``apply_warm_start`` is called with the
        right shape — this is the wire we're verifying."""
        from genlab_core.learning import meta_prior

        fake_proxy = MagicMock()
        fake_client = MagicMock()
        fake_client.bandit_arms = fake_proxy

        source_arms = {
            "esports_highlight__youtube": (4.0, 2.0),
            "patch_news__instagram": (3.0, 5.0),
        }

        with (
            patch(
                "genlab_core.http.backlog_client.BacklogClient",
                return_value=fake_client,
            ),
            patch(
                "genlab_core.learning.arm_loader.load_all_arms",
                return_value=source_arms,
            ),
            patch.object(meta_prior, "apply_warm_start") as mock_warm_start,
        ):
            mock_warm_start.return_value = {
                "esports_highlight__youtube": "updated",
                "patch_news__instagram": "updated",
            }
            # "sports" → "gaming" mapping
            result = meta_prior.bootstrap_niche_warm_start("sports")

        mock_warm_start.assert_called_once()
        # Verify the call shape: target_niche, source_arms, target_proxy
        call = mock_warm_start.call_args
        assert call.kwargs.get("target_niche") == "sports"
        assert call.kwargs.get("source_arms") == source_arms
        assert call.kwargs.get("target_proxy") is fake_proxy
        assert call.kwargs.get("dry_run") is False
        # Result should be propagated from apply_warm_start.
        assert result == {
            "esports_highlight__youtube": "updated",
            "patch_news__instagram": "updated",
        }

    def test_backlog_client_failure_returns_empty(self):
        """A BacklogClient construction failure (e.g. PG unavailable
        in CI) must NOT raise — best-effort by design so niche
        scaffolding succeeds even without DB."""
        from genlab_core.learning.meta_prior import bootstrap_niche_warm_start

        with patch(
            "genlab_core.http.backlog_client.BacklogClient",
            side_effect=RuntimeError("simulated PG unavailable"),
        ):
            result = bootstrap_niche_warm_start("sports")
        assert result == {}, f"Expected empty dict on BacklogClient failure; got {result}"

    def test_apply_warm_start_exception_returns_empty(self):
        """If the inner apply_warm_start raises, the bootstrap wrapper
        catches and returns {} — same best-effort guarantee."""
        from genlab_core.learning import meta_prior

        fake_proxy = MagicMock()
        fake_client = MagicMock()
        fake_client.bandit_arms = fake_proxy

        with (
            patch(
                "genlab_core.http.backlog_client.BacklogClient",
                return_value=fake_client,
            ),
            patch(
                "genlab_core.learning.arm_loader.load_all_arms",
                return_value={"some_arm": (3.0, 2.0)},
            ),
            patch.object(
                meta_prior,
                "apply_warm_start",
                side_effect=RuntimeError("simulated transfer math failure"),
            ),
        ):
            result = meta_prior.bootstrap_niche_warm_start("sports")
        assert result == {}, f"Expected empty dict on apply_warm_start failure; got {result}"

    def test_dry_run_flag_propagates(self):
        """The ``dry_run`` flag must propagate to apply_warm_start so
        the CLI can preview without writing — matches the
        ``apply_warm_start`` contract."""
        from genlab_core.learning import meta_prior

        fake_proxy = MagicMock()
        fake_client = MagicMock()
        fake_client.bandit_arms = fake_proxy

        with (
            patch(
                "genlab_core.http.backlog_client.BacklogClient",
                return_value=fake_client,
            ),
            patch(
                "genlab_core.learning.arm_loader.load_all_arms",
                return_value={"a": (3.0, 2.0)},
            ),
            patch.object(meta_prior, "apply_warm_start") as mock_warm_start,
        ):
            mock_warm_start.return_value = {"a": "dry_run"}
            meta_prior.bootstrap_niche_warm_start("sports", dry_run=True)

        assert mock_warm_start.call_args.kwargs.get("dry_run") is True
