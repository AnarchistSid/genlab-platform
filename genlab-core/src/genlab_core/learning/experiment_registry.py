"""Experiment registry — load + filter ABTest definitions (Lever I3 wire prep).

PR #433 shipped the experimentation primitives (``ABTest`` dataclass,
``assign_to_experiment``, ``is_active``) but with NO way to load
experiment definitions from anywhere. Operators define experiments
"in their head" and would have to instantiate ``ABTest`` objects in
code to test the primitive — not useful for the wire-up into
``push_to_backlog._classify_arm``.

This module closes that gap. It loads ``ABTest`` definitions from a
YAML file, validates each entry, and exposes:

  load_experiments_from_yaml(yaml_path) → list[ABTest]
  find_active_experiment(niche_id, experiments, now=None) → ABTest | None

Once wired into ``_classify_arm`` (follow-up PR), the registry sits at
the top of the bandit decision chain:

  1. Force-explore
  2. Keyword match → matches list
  3. **Active experiment check (NEW)** — if a registered experiment is
     active for this niche AND assigns this blueprint to one of the
     experiment's arms that's also in `matches`, return that arm
  4. LinUCB tie-break
  5. Thompson tie-break
  6. Random-control wrap

THIS PR ships the registry as a primitive. The wire-up is a separate
PR — same "ship the primitive first, wire later" pattern that proved
itself in PRs #434/#435 (LinUCB picker) and PRs #433/#436 (random
control).

## YAML format

```yaml
experiments:
  - experiment_id: hook_style_question_vs_imperative_2026_07
    arms: [question, imperative]
    allocation:
      question: 0.5
      imperative: 0.5
    start_date: 2026-07-01T00:00:00+00:00
    end_date: 2026-08-01T00:00:00+00:00
    niche_ids: [sports, gaming]
```

``niche_ids`` defaults to ``[]`` (all niches participate) if omitted.
``end_date`` defaults to ``None`` (open-ended experiment).

Run via:
    python -m genlab_core.learning.experiment_registry path/to/experiments.yaml
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from genlab_core.learning.experimentation import ABTest, is_active, validate_allocation

logger = logging.getLogger(__name__)


def load_experiments_from_yaml(yaml_path: str | Path) -> list[ABTest]:
    """Load ABTest definitions from a YAML file.

    Returns an empty list when:
    - File doesn't exist
    - File is unreadable
    - YAML parse fails
    - ``experiments:`` key is missing or not a list

    Per-entry validation is silently skip + log — one malformed entry
    doesn't poison the rest. Fail-quiet contract: the caller treats
    "no experiments" as the default (bandit + random control) and never
    sees a crash from a config typo.
    """
    path = Path(yaml_path)
    if not path.is_file():
        logger.debug("[experiment_registry] file not found: %s", path)
        return []

    try:
        import yaml
    except ImportError:
        logger.warning("[experiment_registry] PyYAML not installed; cannot load %s", path)
        return []

    try:
        with path.open() as f:
            raw = yaml.safe_load(f) or {}
    except (yaml.YAMLError, OSError) as exc:
        logger.warning("[experiment_registry] parse failure for %s: %s", path, exc)
        return []

    raw_entries = raw.get("experiments")
    if not isinstance(raw_entries, list):
        logger.debug("[experiment_registry] no 'experiments:' list in %s", path)
        return []

    experiments: list[ABTest] = []
    for idx, entry in enumerate(raw_entries):
        exp = _parse_entry(entry, source=path.name, idx=idx)
        if exp is not None:
            experiments.append(exp)

    logger.info("[experiment_registry] loaded %d experiments from %s", len(experiments), path)
    return experiments


def _parse_entry(entry: object, *, source: str, idx: int) -> ABTest | None:
    """Parse one YAML entry into an ABTest. Returns None on validation
    failure with a debug log — keeps the loader fail-quiet."""
    if not isinstance(entry, dict):
        logger.debug("[experiment_registry] entry %d in %s is not a dict, skipping", idx, source)
        return None

    experiment_id = entry.get("experiment_id")
    arms = entry.get("arms")
    allocation = entry.get("allocation")
    start_date = entry.get("start_date")

    if not (
        isinstance(experiment_id, str)
        and experiment_id.strip()
        and isinstance(arms, list)
        and arms
        and isinstance(allocation, dict)
        and isinstance(start_date, str)
    ):
        logger.debug(
            "[experiment_registry] entry %d in %s missing required fields, skipping",
            idx,
            source,
        )
        return None

    # Coerce allocation values to float — YAML may emit ints (0/1)
    try:
        allocation_f = {str(k): float(v) for k, v in allocation.items()}
    except (TypeError, ValueError):
        logger.debug(
            "[experiment_registry] entry %d in %s has non-numeric allocation, skipping",
            idx,
            source,
        )
        return None

    if not validate_allocation(allocation_f):
        logger.debug(
            "[experiment_registry] entry %d in %s has invalid allocation %s, skipping",
            idx,
            source,
            allocation_f,
        )
        return None

    end_date = entry.get("end_date")
    if end_date is not None and not isinstance(end_date, str):
        logger.debug(
            "[experiment_registry] entry %d in %s has non-string end_date, skipping",
            idx,
            source,
        )
        return None

    niche_ids = entry.get("niche_ids", []) or []
    if not isinstance(niche_ids, list):
        niche_ids = []
    niche_ids = [str(n) for n in niche_ids]

    return ABTest(
        experiment_id=experiment_id.strip(),
        arms=[str(a) for a in arms],
        allocation=allocation_f,
        start_date=start_date,
        end_date=end_date,
        niche_ids=niche_ids,
    )


def find_active_experiment(
    niche_id: str,
    experiments: list[ABTest],
    *,
    now: datetime | None = None,
) -> ABTest | None:
    """Return the first active experiment for this niche, or None.

    "Active" = ``is_active(experiment, now=now)`` is True AND either
    ``experiment.niche_ids`` is empty (all niches participate) or
    ``niche_id`` appears in the list.

    Returns the FIRST match. Operators are expected to design non-
    overlapping experiments per niche; if two are active simultaneously
    only one will run. This keeps assignment deterministic + prevents
    cross-experiment interference in reward attribution.
    """
    now = now or datetime.now(UTC)
    for exp in experiments:
        if not is_active(exp, now=now):
            continue
        if exp.niche_ids and niche_id not in exp.niche_ids:
            continue
        return exp
    return None


if __name__ == "__main__":
    # CLI: load + dump experiments from a YAML file.
    import argparse

    parser = argparse.ArgumentParser(
        description="Load + filter experiments from YAML (Lever I3 wire prep)"
    )
    parser.add_argument("yaml_path", help="Path to experiments.yaml")
    parser.add_argument(
        "--niche-id",
        default="",
        help="If given, also report the active experiment for this niche",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    experiments = load_experiments_from_yaml(args.yaml_path)
    print(f"\nLoaded {len(experiments)} experiments from {args.yaml_path}:")
    for exp in experiments:
        active = "ACTIVE" if is_active(exp) else "inactive"
        niches = ",".join(exp.niche_ids) if exp.niche_ids else "(all)"
        print(f"  - {exp.experiment_id} [{active}] niches={niches}")

    if args.niche_id:
        active_exp = find_active_experiment(args.niche_id, experiments)
        if active_exp is None:
            print(f"\nNo active experiment for niche={args.niche_id}")
        else:
            print(f"\nActive experiment for niche={args.niche_id}: {active_exp.experiment_id}")
