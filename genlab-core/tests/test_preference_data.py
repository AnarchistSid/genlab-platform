"""Pin: preference data + DPO export primitive (Lever J1+J2 MVP).

DPO trains a language model to prefer "chosen" outputs over "rejected"
ones. The HuggingFace TRL DPOTrainer expects training data as a list
of {"prompt", "chosen", "rejected"} dicts. Today the system has two
natural sources of preference pairs (operator edits + bandit reward
deltas) but no data model to collect them.

These pins lock in:

  Source coercion (2):
  1. Known sources pass through
  2. Free-text / None → "unknown"

  is_valid_pair (4):
  3. Valid pair → True
  4. Empty chosen → False
  5. Empty rejected → False
  6. chosen == rejected → False (no learning signal)

  build_pair_from_edit (3):
  7. EditDiff with operation="reworded" → valid pair with metadata
  8. EditDiff with operation="unchanged" → None (no signal)
  9. EditDiff with empty fields → None (validation failure)

  build_pair_from_reward (3):
  10. Reward delta above min → valid pair with metadata
  11. Reward delta below min → None (signal too weak)
  12. Same text on both sides → None (validation failure)

  export_for_dpo (2):
  13. Empty list → empty list
  14. Pairs → list of {"prompt", "chosen", "rejected"} dicts (TRL format)

  summarize_pairs (1):
  15. Aggregations correct (by_source, by_niche, by_field)

  InMemoryPreferenceBackend (2):
  16. Satisfies Protocol via isinstance
  17. store + query with niche_id filter works
"""

from __future__ import annotations

from genlab_core.learning.preference_data import (
    SOURCE_BANDIT_REWARD,
    SOURCE_OPERATOR_EDIT,
    SOURCE_OPERATOR_REVIEW,
    SOURCE_UNKNOWN,
    InMemoryPreferenceBackend,
    PreferenceDataBackend,
    build_pair_from_edit,
    build_pair_from_reward,
    coerce_source,
    export_for_dpo,
    is_valid_pair,
    new_pair,
    summarize_pairs,
)


class _FakeEditDiff:
    """Stand-in for genlab_core.learning.edit_diff.EditDiff — duck-typed
    via attribute access in build_pair_from_edit."""

    def __init__(self, *, before, after, operation, field="hook", blueprint_id="bp_1", ratio=0.5):
        self.before = before
        self.after = after
        self.operation = operation
        self.field = field
        self.blueprint_id = blueprint_id
        self.edit_distance_ratio = ratio


class TestCoerceSource:
    def test_known_sources_pass_through(self):
        for known in (SOURCE_OPERATOR_EDIT, SOURCE_BANDIT_REWARD, SOURCE_OPERATOR_REVIEW):
            assert coerce_source(known) == known

    def test_free_text_and_none_coerce_to_unknown(self):
        assert coerce_source(None) == SOURCE_UNKNOWN
        assert coerce_source("") == SOURCE_UNKNOWN
        assert coerce_source("made_up_source") == SOURCE_UNKNOWN


class TestIsValidPair:
    def test_valid_pair_returns_true(self):
        p = new_pair(
            prompt_context="Generate a hook",
            chosen_text="Insane clutch from ESL One",
            rejected_text="Players losing minds",
            source=SOURCE_OPERATOR_EDIT,
        )
        assert is_valid_pair(p) is True

    def test_empty_chosen_returns_false(self):
        p = new_pair(
            prompt_context="P",
            chosen_text="",
            rejected_text="something",
            source=SOURCE_OPERATOR_EDIT,
        )
        assert is_valid_pair(p) is False

    def test_empty_rejected_returns_false(self):
        p = new_pair(
            prompt_context="P",
            chosen_text="something",
            rejected_text="",
            source=SOURCE_OPERATOR_EDIT,
        )
        assert is_valid_pair(p) is False

    def test_chosen_equals_rejected_returns_false(self):
        """Pin: identical chosen + rejected has zero learning signal.
        DPO would waste compute + might bias toward null outputs."""
        p = new_pair(
            prompt_context="P",
            chosen_text="same",
            rejected_text="same",
            source=SOURCE_OPERATOR_EDIT,
        )
        assert is_valid_pair(p) is False


class TestBuildPairFromEdit:
    def test_reworded_edit_produces_valid_pair(self):
        diff = _FakeEditDiff(
            before="Players losing their minds over this",
            after="Insane 1v5 clutch from ESL One Cologne",
            operation="reworded",
        )
        pair = build_pair_from_edit(
            diff, prompt_context="Generate a gaming hook", niche_id="gaming"
        )
        assert pair is not None
        assert pair.chosen_text == "Insane 1v5 clutch from ESL One Cologne"
        assert pair.rejected_text == "Players losing their minds over this"
        assert pair.source == SOURCE_OPERATOR_EDIT
        assert pair.metadata["niche_id"] == "gaming"
        assert pair.metadata["field"] == "hook"
        assert pair.metadata["operation"] == "reworded"

    def test_unchanged_edit_returns_none(self):
        """Pin: unchanged edits have no learning signal — skip."""
        diff = _FakeEditDiff(before="same", after="same", operation="unchanged")
        assert build_pair_from_edit(diff, prompt_context="P", niche_id="gaming") is None

    def test_empty_field_diff_returns_none_via_validation(self):
        diff = _FakeEditDiff(before="", after="something", operation="added")
        # Validation requires non-empty rejected — empty before → invalid
        result = build_pair_from_edit(diff, prompt_context="P", niche_id="gaming")
        assert result is None


class TestBuildPairFromReward:
    def test_reward_delta_above_min_produces_pair(self):
        pair = build_pair_from_reward(
            prompt_context="Generate a hook for gaming clip",
            winner_text="Madrid 3-2 PSG in stoppage time",
            loser_text="An incredible match",
            winner_reward=0.10,
            loser_reward=0.02,
            niche_id="sports",
            blueprint_id="bp_1",
        )
        assert pair is not None
        assert pair.chosen_text == "Madrid 3-2 PSG in stoppage time"
        assert pair.rejected_text == "An incredible match"
        assert pair.source == SOURCE_BANDIT_REWARD
        assert pair.metadata["reward_delta"] == 0.08

    def test_reward_delta_below_min_returns_none(self):
        """Pin: weak reward signal → no pair. DPO needs material
        preference to learn from."""
        pair = build_pair_from_reward(
            prompt_context="P",
            winner_text="A",
            loser_text="B",
            winner_reward=0.05,
            loser_reward=0.04,  # delta 0.01 < default min 0.05
        )
        assert pair is None

    def test_same_text_returns_none_via_validation(self):
        """Pin: same text on both sides fails is_valid_pair even when
        reward delta is large."""
        pair = build_pair_from_reward(
            prompt_context="P",
            winner_text="same text",
            loser_text="same text",
            winner_reward=0.10,
            loser_reward=0.02,
        )
        assert pair is None


class TestExportForDpo:
    def test_empty_list_returns_empty(self):
        assert export_for_dpo([]) == []

    def test_produces_trl_format(self):
        """Pin: TRL DPOTrainer needs EXACTLY {prompt, chosen, rejected}
        keys. Extra metadata fields are dropped."""
        pairs = [
            new_pair(
                prompt_context="P1",
                chosen_text="C1",
                rejected_text="R1",
                source=SOURCE_OPERATOR_EDIT,
                metadata={"niche_id": "gaming"},
            ),
            new_pair(
                prompt_context="P2",
                chosen_text="C2",
                rejected_text="R2",
                source=SOURCE_BANDIT_REWARD,
                metadata={"reward_delta": 0.08},
            ),
        ]
        result = export_for_dpo(pairs)
        assert result == [
            {"prompt": "P1", "chosen": "C1", "rejected": "R1"},
            {"prompt": "P2", "chosen": "C2", "rejected": "R2"},
        ]
        # Metadata fields NOT in output
        assert "niche_id" not in result[0]
        assert "reward_delta" not in result[1]


class TestSummarizePairs:
    def test_aggregations_correct(self):
        pairs = [
            new_pair(
                prompt_context="P1",
                chosen_text="C",
                rejected_text="R",
                source=SOURCE_OPERATOR_EDIT,
                metadata={"niche_id": "gaming", "field": "hook"},
            ),
            new_pair(
                prompt_context="P2",
                chosen_text="C",
                rejected_text="R",
                source=SOURCE_OPERATOR_EDIT,
                metadata={"niche_id": "sports", "field": "hook"},
            ),
            new_pair(
                prompt_context="P3",
                chosen_text="C",
                rejected_text="R",
                source=SOURCE_BANDIT_REWARD,
                metadata={"niche_id": "gaming"},
            ),
        ]
        s = summarize_pairs(pairs)
        assert s["total_pairs"] == 3
        assert s["by_source"] == {SOURCE_OPERATOR_EDIT: 2, SOURCE_BANDIT_REWARD: 1}
        assert s["by_niche"] == {"gaming": 2, "sports": 1}
        assert s["by_field"] == {"hook": 2}


class TestInMemoryPreferenceBackend:
    def test_satisfies_protocol_via_isinstance(self):
        """Pin: runtime_checkable Protocol works without explicit
        subclass declaration."""
        backend = InMemoryPreferenceBackend()
        assert isinstance(backend, PreferenceDataBackend)

    def test_store_and_query_with_niche_filter(self):
        backend = InMemoryPreferenceBackend()
        backend.store(
            new_pair(
                prompt_context="P",
                chosen_text="C",
                rejected_text="R",
                source=SOURCE_OPERATOR_EDIT,
                metadata={"niche_id": "gaming"},
            )
        )
        backend.store(
            new_pair(
                prompt_context="P",
                chosen_text="C",
                rejected_text="R",
                source=SOURCE_OPERATOR_EDIT,
                metadata={"niche_id": "sports"},
            )
        )
        gaming_only = backend.query(niche_id="gaming")
        assert len(gaming_only) == 1
        assert gaming_only[0].metadata["niche_id"] == "gaming"
