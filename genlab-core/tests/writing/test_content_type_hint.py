"""Contract pins for ``writing.content_type_hint``.

Task #628 (2026-07-09, roadmap E2) — content-type bandit hint
injected into the writer prompt.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from genlab_core.writing.content_type_hint import (
    _CONTENT_ANGLE_HINTS,
    _KNOWN_CONTENT_TYPES,
    format_content_angle_prompt,
    pick_content_type_hint,
)

# ── configuration invariants ─────────────────────────────────────


class TestConfigurationInvariants:
    """Lock the content-type name catalog to the classifier in
    ``push_to_backlog._ARM_KEYWORDS`` + ``_NICHE_ARM_DEFAULTS``.
    If a new content type is added there (or one renamed), the hint
    catalog + KNOWN set must be updated in tandem — this test
    surfaces the drift."""

    def test_every_niche_has_at_least_two_content_types(self):
        """Bandit needs ≥2 arms to Thompson-sample meaningfully. If a
        niche's known-set drops below 2 the whole hint mechanism
        degrades to a constant (whichever arm has more observations)."""
        for niche, types in _KNOWN_CONTENT_TYPES.items():
            assert len(types) >= 2, f"{niche}: needs ≥2 content types"

    def test_every_known_content_type_has_an_angle_hint(self):
        """The catalog is only useful if we have a human-readable
        steering hint for each arm — otherwise ``format_content_angle_prompt``
        returns "" and the writer sees nothing."""
        for niche, types in _KNOWN_CONTENT_TYPES.items():
            for arm_name in types:
                assert arm_name in _CONTENT_ANGLE_HINTS, (
                    f"Missing angle hint for {niche}:{arm_name}. "
                    "Add an entry to _CONTENT_ANGLE_HINTS."
                )


# ── format_content_angle_prompt ─────────────────────────────────


def test_format_returns_empty_for_unknown_arm():
    """Fail-open: unknown arm name → empty string (writer sees
    nothing extra in the prompt) instead of a mangled hint."""
    result = format_content_angle_prompt("unknown_arm")
    assert result == ""


def test_format_includes_arm_name_and_hint_text():
    """When the arm IS known, the emitted prompt section names the
    arm AND describes its intent so the writer has enough to
    interpret the steer."""
    result = format_content_angle_prompt("gameplay_clip")
    assert "gameplay_clip" in result
    # Human-readable hint text also present.
    assert _CONTENT_ANGLE_HINTS["gameplay_clip"] in result
    # And the emitted text includes the "soft steering" wording so
    # future readers know this is opt-out, not opt-in.
    assert "if the story genuinely fits" in result.lower()


# ── pick_content_type_hint — sampling behavior ──────────────────


@pytest.fixture
def _fake_backlog_with_arms():
    """Build a backlog-client stub with a canned bandit_arms table.

    Returns a factory: ``fake(arms_dict) -> patchable BacklogClient
    class``. The dict is ``{arm_id: (alpha, beta)}``."""

    def _factory(arms: dict):
        mock_client = MagicMock()
        mock_client.bandit_arms = MagicMock()

        def _load_all(_proxy, _niche):
            return arms

        return mock_client, _load_all

    return _factory


def test_pick_returns_none_for_unknown_niche():
    """No known content types → immediately return None (no bandit
    query, no crash). Pins the cold-start fallback."""
    assert pick_content_type_hint("crypto_bros") is None


def test_pick_returns_none_when_no_matching_arms(_fake_backlog_with_arms):
    """Known niche but no arms in the DB match — Thompson sampling
    has nothing to draw from. Return None so the writer skips
    injection."""
    mock_client, load_all = _fake_backlog_with_arms({"style:gaming:paradox": (5, 3)})
    with (
        patch(
            "genlab_core.http.backlog_client.BacklogClient",
            return_value=mock_client,
        ),
        patch(
            "genlab_core.learning.arm_loader.load_all_arms",
            side_effect=load_all,
        ),
    ):
        result = pick_content_type_hint("gaming")
    assert result is None


def test_pick_prefers_high_posterior_arm(_fake_backlog_with_arms):
    """Thompson-sampling should — with a very tilted Beta — reliably
    prefer the high-alpha/low-beta arm. Not deterministic but the
    tilt below is severe enough that flakiness under a fixed seed
    is negligible."""
    import random as random_module

    random_module.seed(42)
    mock_client, load_all = _fake_backlog_with_arms(
        {
            # gameplay_clip almost certainly wins: mean ~0.99
            "gameplay_clip": (100, 1),
            # esports_highlight very unlikely to win: mean ~0.01
            "esports_highlight": (1, 100),
        }
    )
    with (
        patch(
            "genlab_core.http.backlog_client.BacklogClient",
            return_value=mock_client,
        ),
        patch(
            "genlab_core.learning.arm_loader.load_all_arms",
            side_effect=load_all,
        ),
    ):
        # Sample 20 times — winner should be gameplay_clip nearly
        # always. Guard against off-by-one bugs in the loop.
        wins = {"gameplay_clip": 0, "esports_highlight": 0}
        for _ in range(20):
            result = pick_content_type_hint("gaming")
            if result in wins:
                wins[result] += 1
    assert wins["gameplay_clip"] >= 18, (
        f"Expected gameplay_clip to dominate given 100:1 posterior; "
        f"got {wins}. Sampling is either broken or the arm-filter is."
    )


def test_pick_aggregates_platform_split_variants(_fake_backlog_with_arms):
    """When a content type appears in multiple platform-split forms
    (e.g. ``gameplay_clip``, ``gameplay_clip__instagram``,
    ``gameplay_clip__youtube``), the picker aggregates their
    posteriors under the bare form. Otherwise the platform-split
    variants would each be treated as a fresh arm and dilute the
    signal."""
    mock_client, load_all = _fake_backlog_with_arms(
        {
            "gameplay_clip": (10, 5),
            "gameplay_clip__instagram": (20, 10),
            "gameplay_clip__youtube": (5, 3),
            # sibling arm — should also be visible for sampling
            "esports_highlight": (30, 15),
        }
    )
    with (
        patch(
            "genlab_core.http.backlog_client.BacklogClient",
            return_value=mock_client,
        ),
        patch(
            "genlab_core.learning.arm_loader.load_all_arms",
            side_effect=load_all,
        ),
    ):
        # We don't assert which arm wins (Thompson is stochastic)
        # but the return value must be one of the KNOWN bare content
        # types — never a raw platform-split ``foo__bar`` string.
        result = pick_content_type_hint("gaming")
    assert result in {"gameplay_clip", "esports_highlight"}, (
        f"Aggregation failed: returned {result!r}. Expected a bare "
        "content-type name after aggregating platform variants."
    )


def test_pick_returns_none_on_import_failure():
    """When the BacklogClient/arm_loader imports blow up (bare test
    env with no config, dev-mode without those deps installed), the
    hint MUST return None instead of crashing the writer stage."""
    with patch(
        "genlab_core.http.backlog_client.BacklogClient",
        side_effect=RuntimeError("no DB"),
    ):
        # ImportError path is exercised by tests already — this
        # exercises the "BacklogClient() constructor raises" path.
        assert pick_content_type_hint("gaming") is None
