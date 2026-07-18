"""Pin tests for Layer 3 S5 — variant_type_hint bandit sampling.

Mirrors content_type_hint's test surface with variant-specific semantics:

1. Cold-start (no arms yet) → None
2. Single_clip-only observations → None (would just re-state fallback)
3. Non-default variant observations → returns picked variant
4. Multi-variant → Thompson-sampled winner (probabilistic; test many draws)
5. Fail-open on BacklogClient / arm_loader failures
6. Prompt formatter handles all valid variants + returns empty for unknown
"""

from __future__ import annotations

import random
from unittest.mock import MagicMock, patch

from genlab_core.writing.variant_type_hint import (
    format_variant_type_prompt,
    pick_variant_type_hint,
)


class TestPickVariantTypeHint:
    def test_empty_niche_id_returns_none(self) -> None:
        assert pick_variant_type_hint("") is None

    def test_backlog_client_failure_returns_none(self) -> None:
        with patch(
            "genlab_core.http.backlog_client.BacklogClient",
            side_effect=RuntimeError("simulated"),
        ):
            assert pick_variant_type_hint("gaming") is None

    def test_arm_load_failure_returns_none(self) -> None:
        fake_client = MagicMock()
        fake_client.bandit_arms = MagicMock()
        with (
            patch(
                "genlab_core.http.backlog_client.BacklogClient",
                return_value=fake_client,
            ),
            patch(
                "genlab_core.learning.arm_loader.load_all_arms",
                side_effect=RuntimeError("query fail"),
            ),
        ):
            assert pick_variant_type_hint("gaming") is None

    def test_no_variant_arms_returns_none(self) -> None:
        """Cold start — only content_type + style arms exist, no variant:*."""
        fake_client = MagicMock()
        fake_client.bandit_arms = MagicMock()
        with (
            patch(
                "genlab_core.http.backlog_client.BacklogClient",
                return_value=fake_client,
            ),
            patch(
                "genlab_core.learning.arm_loader.load_all_arms",
                return_value={
                    "gameplay_clip": (5.0, 3.0),
                    "style:gaming:bold_claim": (4.0, 2.0),
                },
            ),
        ):
            assert pick_variant_type_hint("gaming") is None

    def test_only_single_clip_arm_returns_none(self) -> None:
        """Even with real observations, if ONLY single_clip has data,
        the hint would just say 'prefer single_clip' — redundant with
        the pipeline's fallback. Wait for a non-default variant to
        have observations."""
        fake_client = MagicMock()
        fake_client.bandit_arms = MagicMock()
        with (
            patch(
                "genlab_core.http.backlog_client.BacklogClient",
                return_value=fake_client,
            ),
            patch(
                "genlab_core.learning.arm_loader.load_all_arms",
                return_value={
                    "variant:gaming:single_clip": (10.0, 5.0),
                },
            ),
        ):
            assert pick_variant_type_hint("gaming") is None

    def test_non_default_variant_can_be_picked(self) -> None:
        """When a non-default variant has data alongside single_clip,
        Thompson sampling can return the non-default variant."""
        fake_client = MagicMock()
        fake_client.bandit_arms = MagicMock()

        with (
            patch(
                "genlab_core.http.backlog_client.BacklogClient",
                return_value=fake_client,
            ),
            patch(
                "genlab_core.learning.arm_loader.load_all_arms",
                return_value={
                    "variant:gaming:single_clip": (2.0, 8.0),
                    # series_part with very high alpha → very likely to win
                    "variant:gaming:series_part": (100.0, 1.0),
                },
            ),
        ):
            # Multiple draws — verify series_part wins majority
            random.seed(0)
            picks = [pick_variant_type_hint("gaming") for _ in range(50)]
        # Overwhelming posterior → should pick series_part >90% of the time
        series_wins = picks.count("series_part")
        assert series_wins > 45, f"expected series_part dominance, got {series_wins}/50"

    def test_unknown_variant_string_filtered(self) -> None:
        """Guard against typos or stale enum drift (rule #22 sibling).
        A row like ``variant:gaming:not_a_real_variant`` should be
        silently skipped, not returned to caller."""
        fake_client = MagicMock()
        fake_client.bandit_arms = MagicMock()
        with (
            patch(
                "genlab_core.http.backlog_client.BacklogClient",
                return_value=fake_client,
            ),
            patch(
                "genlab_core.learning.arm_loader.load_all_arms",
                return_value={
                    "variant:gaming:not_a_real_variant": (100.0, 1.0),
                },
            ),
        ):
            # Only "invalid" arm exists → no valid variants → None
            assert pick_variant_type_hint("gaming") is None

    def test_niche_isolation(self) -> None:
        """variant:{niche}:X arms for OTHER niches must not be
        considered for this niche's hint (regression pin — cross-niche
        leak would give sports hints on gaming, etc)."""
        fake_client = MagicMock()
        fake_client.bandit_arms = MagicMock()
        with (
            patch(
                "genlab_core.http.backlog_client.BacklogClient",
                return_value=fake_client,
            ),
            patch(
                "genlab_core.learning.arm_loader.load_all_arms",
                return_value={
                    "variant:sports:series_part": (100.0, 1.0),
                    "variant:gaming:single_clip": (2.0, 8.0),
                },
            ),
        ):
            # gaming has only single_clip arm → None
            # (sports arm should NOT be considered)
            assert pick_variant_type_hint("gaming") is None

    def test_platform_split_arms_aggregate(self) -> None:
        """Per-platform arms come through as ``variant:{niche}:{v}__{platform}``
        (mirrors bandit_platform_split.py). Should aggregate the posteriors
        under one variant key rather than count as separate arms."""
        fake_client = MagicMock()
        fake_client.bandit_arms = MagicMock()
        with (
            patch(
                "genlab_core.http.backlog_client.BacklogClient",
                return_value=fake_client,
            ),
            patch(
                "genlab_core.learning.arm_loader.load_all_arms",
                return_value={
                    "variant:gaming:single_clip": (5.0, 5.0),
                    "variant:gaming:series_part__youtube": (30.0, 1.0),
                    "variant:gaming:series_part__instagram": (30.0, 1.0),
                },
            ),
        ):
            # Both series_part entries should aggregate (α=60, β=2 → strong)
            random.seed(0)
            picks = [pick_variant_type_hint("gaming") for _ in range(30)]
        assert picks.count("series_part") > 25, (
            "platform-split variant arms should aggregate under one variant name"
        )


class TestFormatVariantTypePrompt:
    def test_empty_string_returns_empty(self) -> None:
        assert format_variant_type_prompt("") == ""

    def test_unknown_variant_returns_empty(self) -> None:
        assert format_variant_type_prompt("not_a_real_variant") == ""

    def test_known_variants_produce_prompt(self) -> None:
        for variant in [
            "single_clip",
            "series_part",
            "question_reveal",
            "watch_till_end",
            "split_screen",
            "storytime",
        ]:
            section = format_variant_type_prompt(variant)
            assert "VARIANT FRAME PREFERENCE" in section
            assert variant in section

    def test_prompt_states_informational_not_routing(self) -> None:
        """The prompt must explicitly disclaim that this doesn't override
        pipeline routing — otherwise the LLM might reject stories that
        don't naturally fit the recommended variant."""
        section = format_variant_type_prompt("series_part")
        # Must call out "TONE" or "routing" concept
        assert "TONE" in section or "routing" in section.lower()
