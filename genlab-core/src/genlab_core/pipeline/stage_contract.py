"""What each stage reads from and writes to the context, and whether the order works.

THE DEFECT THIS EXISTS FOR
--------------------------
`CraftRenderStage` read ``context["blueprints"]``. The stage that creates
blueprints -- `PushToBacklog` -- ran five stages LATER and never published them
under that name anyway. So the read returned nothing, the stage took its
empty-input branch, logged at INFO, and reported success on every fire since it
shipped, including five-story runs. Nothing errored: both sides are
``dict[str, Any]``, and the unit test built the context by hand and put the key
in.

Three consumers keyed to nothing in one month, each a plausible no-op. The
symptom fix is "no input is a WARNING when upstream had input". This is the
class fix: a stage declares what it reads, and a test fails if nothing earlier
in its niche's order writes that.

DECLARING
---------
    class MyStage:
        context_reads = ("stories", "niche_id")
        context_writes = ("blueprints",)

Undeclared stages are reported by `coverage`, not silently treated as writing
everything -- a contract check that assumes the undeclared case is fine would
pass the exact bug it was written for.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Keys the runner puts in the context before any stage runs. A read of one of
#: these needs no earlier writer.
SEEDED_KEYS: frozenset[str] = frozenset(
    {
        "niche_id",
        "niche_config",
        "niche_root",
        "project_root",
        "run_id",
        "run_dir",
        "feature_flags",
        "dry_run",
        "run_stats",
        "backlog_config_path",
    }
)


#: Reads of a key nothing writes, kept deliberately and named.
#:
#: These are the SAME defect as the one this module exists for -- a consumer
#: keyed to nothing -- and they are benign only because each has a fallback that
#: happens to be the real path. They are listed rather than deleted so the debt
#: is visible and the list cannot grow silently; deleting them is a separate
#: change from wiring the stage that was actually broken.
KNOWN_DEAD_READS: dict[tuple[str, str], str] = {
    ("CraftRenderStage", "storyboards"): (
        "craft_render.py:135 `bp.get('storyboard') or context.get('storyboards', {}).get(bid)`. "
        "No stage writes `storyboards`; the builder is invoked inline when the blueprint has "
        "none, so the clause is dead. Remove the clause, do not add a writer."
    ),
    ("PushToBacklog", "metrics"): (
        "push_to_backlog.py `context.get('metrics')`. No stage writes `metrics` into the "
        "context -- PipelineMetrics writes metrics.jsonl to the run dir instead. The read "
        "returns None on every run."
    ),
}


class OpaqueStage:
    """A niche-injected stage whose contract is not declared here.

    The template carries ``- inject: phase1_content_research`` and each niche
    fills it with its own classes, so what those write is not knowable from the
    template alone. Guessing would make the check unsound in the dangerous
    direction: a wrong guess that a key IS written is exactly the assumption
    that let `blueprints` through.

    So an opaque stage makes later reads UNPROVABLE rather than fine, and the
    check only reports what it can prove: a key whose declared writer runs after
    its reader. That is the shape of every defect in this class so far.
    """

    __name__ = "<niche inject>"


@dataclass(frozen=True)
class Violation:
    stage: str
    key: str
    detail: str

    def __str__(self) -> str:
        return f"{self.stage} reads '{self.key}': {self.detail}"


def declared_reads(stage) -> tuple[str, ...] | None:
    return getattr(stage, "context_reads", None)


def declared_writes(stage) -> tuple[str, ...] | None:
    return getattr(stage, "context_writes", None)


def check_order(stages: list, *, seeded: frozenset[str] = SEEDED_KEYS) -> list[Violation]:
    """Violations for a list of stage classes IN THEIR INJECTED ORDER.

    A stage may read a key if it is seeded by the runner, or written by a stage
    strictly earlier in the list, or written by the stage itself (read-modify-
    write). Undeclared stages contribute nothing to `available` -- so a declared
    reader downstream of an undeclared writer will be reported. That is the
    intended direction of the error: it asks for a declaration rather than
    assuming one.
    """
    violations: list[Violation] = []
    available: set[str] = set(seeded)
    dead = set(KNOWN_DEAD_READS)
    opaque_seen = False
    for stage in stages:
        name = getattr(stage, "__name__", str(stage))
        reads = declared_reads(stage)
        writes = declared_writes(stage) or ()
        if reads is not None:
            for key in reads:
                if key in available or key in writes or (name, key) in dead:
                    continue
                later = [
                    getattr(s, "__name__", str(s))
                    for s in stages[stages.index(stage) + 1 :]
                    if key in (declared_writes(s) or ())
                ]
                if later:
                    violations.append(
                        Violation(
                            stage=name,
                            key=key,
                            detail=(
                                f"written by {', '.join(later)}, which runs LATER — "
                                f"the read is a no-op on every run"
                            ),
                        )
                    )
                elif not opaque_seen:
                    violations.append(
                        Violation(
                            stage=name,
                            key=key,
                            detail=(
                                "no stage in this order writes it, and it is not seeded "
                                "by the runner"
                            ),
                        )
                    )
        if isinstance(stage, OpaqueStage) or stage is OpaqueStage:
            opaque_seen = True
        available.update(writes)
    return violations


def coverage(stages: list) -> tuple[list[str], list[str]]:
    """(declared, undeclared) stage names — so the check cannot silently cover nothing."""
    declared = [
        getattr(s, "__name__", str(s))
        for s in stages
        if declared_reads(s) is not None or declared_writes(s) is not None
    ]
    undeclared = [
        getattr(s, "__name__", str(s))
        for s in stages
        if getattr(s, "__name__", str(s)) not in declared
    ]
    return declared, undeclared
