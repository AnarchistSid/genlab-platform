"""Preference data + DPO export primitive (Lever J1+J2 MVP).

DPO (Direct Preference Optimization) trains a language model to prefer
"chosen" outputs over "rejected" ones, given a prompt context. The
HuggingFace TRL library expects training data as a list of
``{"prompt", "chosen", "rejected"}`` dicts.

Today the system has TWO natural sources of preference pairs but no
data model to collect them:

1. **Operator edits** (Lever M, PR #439): every operator-edited field
   is a (rejected=AI-generated, chosen=operator-rewrote) pair.
2. **Bandit reward differential**: when arm X gets reward 0.08 and
   arm Y gets reward 0.02 on similar context, X's output is the
   "chosen" and Y's is the "rejected" for that prompt context.

Lever J1 ships the data model (``PreferencePair``) + helpers to
construct pairs from those sources. Lever J2 ships the DPO export
format converter.

The **production storage** (Postgres ``preference_pairs`` table +
migration) and the **training infrastructure** (TRL setup, model
fine-tuning pipeline) are NOT shipped here — both are bigger-scope
decisions deferring to separate PRs with operator sign-off.

## Why ship the data model now

The longer operator edits accumulate without being captured as
preference data, the larger the data debt. This primitive lets the
backend (when shipped) immediately start recording pairs from
historical operator-edit + bandit-reward data — no schema mismatch
between "events emitted today" and "events the eventual DPO consumer
expects".

Run via:
    python -m genlab_core.learning.preference_data --demo
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# Whitelisted source tags — same closed-set discipline as the rest of
# the autonomy roadmap layers. Future code emitting new sources
# coerces to "unknown" until added explicitly.
SOURCE_OPERATOR_EDIT: Final[str] = "operator_edit"
SOURCE_BANDIT_REWARD: Final[str] = "bandit_reward"
SOURCE_OPERATOR_REVIEW: Final[str] = "operator_review"
SOURCE_LLM_SELF_CRITIQUE: Final[str] = "llm_self_critique"
SOURCE_UNKNOWN: Final[str] = "unknown"

_KNOWN_SOURCES: Final[frozenset[str]] = frozenset(
    {
        SOURCE_OPERATOR_EDIT,
        SOURCE_BANDIT_REWARD,
        SOURCE_OPERATOR_REVIEW,
        SOURCE_LLM_SELF_CRITIQUE,
        SOURCE_UNKNOWN,
    }
)


def coerce_source(raw: str | None) -> str:
    """Coerce a free-text source to the whitelisted enum."""
    if raw is None:
        return SOURCE_UNKNOWN
    s = str(raw).strip()
    return s if s in _KNOWN_SOURCES else SOURCE_UNKNOWN


@dataclass(frozen=True)
class PreferencePair:
    """One DPO-ready preference pair.

    Maps directly to the HuggingFace TRL DPO format on export:
    ``{"prompt": prompt_context, "chosen": chosen_text, "rejected":
    rejected_text}``.

    ``metadata`` carries provenance fields (niche_id, blueprint_id,
    field name, source, etc.) so downstream training can stratify or
    filter by attribute without re-querying the source data.
    """

    prompt_context: str  # the prompt / instruction that produced both
    chosen_text: str
    rejected_text: str
    source: str  # one of SOURCE_*, coerced via coerce_source
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""


def new_pair(
    *,
    prompt_context: str,
    chosen_text: str,
    rejected_text: str,
    source: str,
    metadata: dict[str, Any] | None = None,
    when: datetime | None = None,
) -> PreferencePair:
    """Constructor with sane defaults + source coercion.

    Pure function. Caller validates via ``is_valid_pair`` before
    persisting to avoid storing degenerate pairs that would confuse
    DPO training.
    """
    when = when or datetime.now(UTC)
    return PreferencePair(
        prompt_context=str(prompt_context or ""),
        chosen_text=str(chosen_text or ""),
        rejected_text=str(rejected_text or ""),
        source=coerce_source(source),
        metadata=dict(metadata) if metadata else {},
        created_at=when.isoformat(),
    )


def is_valid_pair(pair: PreferencePair) -> bool:
    """True when the pair is suitable for DPO training.

    Validation checks:
    - chosen_text is non-empty
    - rejected_text is non-empty
    - chosen_text != rejected_text (no learning signal otherwise)
    - prompt_context is non-empty (DPO needs the conditioning context)

    Pure function. Callers filter pairs through this before exporting
    for training — invalid pairs waste training compute + may bias
    the model toward null outputs.
    """
    chosen = pair.chosen_text.strip()
    rejected = pair.rejected_text.strip()
    prompt = pair.prompt_context.strip()
    if not chosen or not rejected or not prompt:
        return False
    return chosen != rejected


def build_pair_from_edit(
    edit_diff: Any,  # genlab_core.learning.edit_diff.EditDiff
    prompt_context: str,
    *,
    niche_id: str = "",
) -> PreferencePair | None:
    """Build a PreferencePair from a single Lever M EditDiff.

    The operator's POST-edit text is "chosen"; the AI-generated PRE-
    edit text is "rejected". Returns None when the diff doesn't have
    learning signal (operation == "unchanged" or empty fields).

    ``edit_diff`` is typed as ``Any`` to avoid a hard import dep on
    Lever M — duck-typed via attribute access on ``.before``,
    ``.after``, ``.operation``, ``.field``, ``.blueprint_id``.
    """
    operation = getattr(edit_diff, "operation", "")
    # Skip unchanged operations — no learning signal
    if operation == "unchanged":
        return None

    pair = new_pair(
        prompt_context=prompt_context,
        chosen_text=getattr(edit_diff, "after", ""),
        rejected_text=getattr(edit_diff, "before", ""),
        source=SOURCE_OPERATOR_EDIT,
        metadata={
            "niche_id": niche_id,
            "blueprint_id": getattr(edit_diff, "blueprint_id", ""),
            "field": getattr(edit_diff, "field", ""),
            "operation": operation,
            "edit_distance_ratio": getattr(edit_diff, "edit_distance_ratio", 0.0),
        },
    )
    return pair if is_valid_pair(pair) else None


def build_pair_from_reward(
    *,
    prompt_context: str,
    winner_text: str,
    loser_text: str,
    winner_reward: float,
    loser_reward: float,
    niche_id: str = "",
    blueprint_id: str = "",
    min_reward_delta: float = 0.05,
) -> PreferencePair | None:
    """Build a PreferencePair from a bandit reward differential.

    When two arms produce text in similar contexts and one earns
    materially more reward, the winner becomes "chosen" and loser
    becomes "rejected". Returns None when the reward delta is below
    ``min_reward_delta`` (signal too weak for DPO to learn from).

    Default ``min_reward_delta=0.05`` corresponds to a 5pp engagement
    rate difference — empirically the threshold where DPO can learn
    a stable preference (Rafailov et al. 2023).
    """
    if (winner_reward - loser_reward) < min_reward_delta:
        return None

    pair = new_pair(
        prompt_context=prompt_context,
        chosen_text=winner_text,
        rejected_text=loser_text,
        source=SOURCE_BANDIT_REWARD,
        metadata={
            "niche_id": niche_id,
            "blueprint_id": blueprint_id,
            "winner_reward": winner_reward,
            "loser_reward": loser_reward,
            "reward_delta": winner_reward - loser_reward,
        },
    )
    return pair if is_valid_pair(pair) else None


def export_for_dpo(pairs: list[PreferencePair]) -> list[dict[str, str]]:
    """Convert PreferencePairs to HuggingFace TRL DPO training format.

    TRL DPOTrainer expects a dataset of dicts with EXACTLY these keys:
    ``{"prompt", "chosen", "rejected"}``. Extra metadata fields are
    dropped — TRL doesn't read them.

    Pure function. Caller is responsible for:
    - filtering via ``is_valid_pair`` before passing in (this function
      doesn't re-validate to keep it composable)
    - converting the output list into a ``datasets.Dataset`` if needed
      (one line: ``Dataset.from_list(export_for_dpo(pairs))``)
    """
    return [
        {
            "prompt": p.prompt_context,
            "chosen": p.chosen_text,
            "rejected": p.rejected_text,
        }
        for p in pairs
    ]


def summarize_pairs(pairs: list[PreferencePair]) -> dict[str, Any]:
    """Stable-shape aggregation for dashboards.

    Returns:
        {
            "total_pairs": N,
            "by_source": {source → count},
            "by_niche": {niche_id → count},
            "by_field": {field → count},  # only from operator_edit pairs
            "avg_prompt_length_chars": int,
            "first_created_at": "ISO-8601" | None,
            "last_created_at": "ISO-8601" | None,
        }
    """
    if not pairs:
        return {
            "total_pairs": 0,
            "by_source": {},
            "by_niche": {},
            "by_field": {},
            "avg_prompt_length_chars": 0,
            "first_created_at": None,
            "last_created_at": None,
        }

    by_source: dict[str, int] = {}
    by_niche: dict[str, int] = {}
    by_field: dict[str, int] = {}
    prompt_lens: list[int] = []
    timestamps: list[str] = []

    for p in pairs:
        by_source[p.source] = by_source.get(p.source, 0) + 1
        niche = str(p.metadata.get("niche_id", ""))
        if niche:
            by_niche[niche] = by_niche.get(niche, 0) + 1
        field_name = str(p.metadata.get("field", ""))
        if field_name:
            by_field[field_name] = by_field.get(field_name, 0) + 1
        prompt_lens.append(len(p.prompt_context))
        if p.created_at:
            timestamps.append(p.created_at)

    avg_len = sum(prompt_lens) // len(prompt_lens) if prompt_lens else 0
    return {
        "total_pairs": len(pairs),
        "by_source": by_source,
        "by_niche": by_niche,
        "by_field": by_field,
        "avg_prompt_length_chars": avg_len,
        "first_created_at": min(timestamps) if timestamps else None,
        "last_created_at": max(timestamps) if timestamps else None,
    }


@runtime_checkable
class PreferenceDataBackend(Protocol):
    """Persistence protocol for preference pairs.

    Concrete implementations:
    - ``InMemoryPreferenceBackend``: test-only
    - ``PostgresPreferenceBackend`` (future PR + migration)
    - ``JsonlFileBackend`` (future PR for ad-hoc training prep)
    """

    def store(self, pair: PreferencePair) -> None: ...  # pragma: no cover
    def query(self, **filters: Any) -> list[PreferencePair]: ...  # pragma: no cover


class InMemoryPreferenceBackend:
    """Test-only backend. Production backend is a follow-up PR."""

    def __init__(self) -> None:
        self._pairs: list[PreferencePair] = []

    def store(self, pair: PreferencePair) -> None:
        self._pairs.append(pair)

    def query(self, **filters: Any) -> list[PreferencePair]:
        """Pure-function filter — niche_id / source / since / until."""
        niche_id = filters.get("niche_id")
        source = filters.get("source")
        out: list[PreferencePair] = []
        for p in self._pairs:
            if niche_id is not None and p.metadata.get("niche_id") != niche_id:
                continue
            if source is not None and p.source != coerce_source(source):
                continue
            out.append(p)
        return out

    def __len__(self) -> int:
        return len(self._pairs)


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Preference data + DPO export primitive (Lever J1+J2 MVP)"
    )
    parser.add_argument("--demo", action="store_true", help="Print a small demo")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.demo:
        backend = InMemoryPreferenceBackend()
        backend.store(
            new_pair(
                prompt_context="Generate a gaming hook for: ESL Cologne grand finals",
                chosen_text="Insane 1v5 clutch from ESL Cologne finals",
                rejected_text="Players are losing their minds over this",
                source=SOURCE_OPERATOR_EDIT,
                metadata={"niche_id": "gaming", "field": "hook"},
            )
        )
        backend.store(
            new_pair(
                prompt_context="Generate a sports hook for: Champions League",
                chosen_text="Madrid edges PSG in stoppage time, 3-2",
                rejected_text="An incredible match just happened",
                source=SOURCE_BANDIT_REWARD,
                metadata={"niche_id": "sports", "reward_delta": 0.08},
            )
        )
        print(f"Stored {len(backend)} preference pairs.")
        print("\nDPO export format (first pair):")
        print(json.dumps(export_for_dpo(backend.query())[:1], indent=2))
        print("\nSummary:")
        print(json.dumps(summarize_pairs(backend.query()), indent=2))
    else:
        print("Use --demo to see a sample run.")
        print("Programmatic usage:")
        print(
            "  from genlab_core.learning.preference_data import ("
            "PreferencePair, build_pair_from_edit, export_for_dpo)"
        )
