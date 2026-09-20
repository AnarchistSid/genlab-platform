"""Compare two pytest runs and answer one question: which failures are MINE?

A suite that already fails cannot tell you whether your change broke anything.
You get a number — "194 failed" — and the number means nothing without the
number from before. So the honest options are to classify 194 failures by hand,
or to ship on a hunch. Most people ship on a hunch.

This does the classification: baseline in, current in, out come the tests that
went from passing to failing. Those, and only those, are yours.

## The check that matters most

**A comparison between runs that collected different test sets is not a
comparison.** If the baseline collected 516 tests and the current run collected
11,225, every one of the 10,709 tests the baseline never ran will look "newly
failing" the moment it fails — and a test that vanished from collection looks
"newly passing" when it simply stopped existing.

That is not hypothetical. It is the ordinary consequence of running a baseline
on a laptop and the current suite on a build box, where a missing optional
dependency silently changes what gets collected. So collection totals are
compared first, and a mismatch downgrades the verdict to `unsound` no matter how
clean the failure diff looks. A tool that reported "no regressions" from two
incomparable runs would be worse than no tool.

## What it reads

Standard pytest console output from both runs — `FAILED`/`ERROR` lines plus the
summary line. Nothing needs `-v`, no plugin, no JSON report. Paste what you
already have in your CI log.

Node IDs are compared exactly. A test that moved file or class counts as one
disappearance and one appearance rather than silently matching, because a moved
test is a changed test and pretending otherwise hides renames that dropped
coverage.
"""
import logging
import re
from typing import Dict, List, Optional, Set, Tuple

from inferencesh import BaseApp, BaseAppSetup
from pydantic import BaseModel, Field

logger = logging.getLogger("pytest-baseline-diff")

# `FAILED tests/x.py::Cls::test_y - AssertionError: ...` / `ERROR tests/x.py::test_z`
_OUTCOME = re.compile(r"^(FAILED|ERROR)\s+(\S+?)(?:\s+-\s+(.*))?$", re.MULTILINE)

# `194 failed, 11031 passed, 101 skipped, 46 deselected, 11 warnings, 12 errors in 534.17s`
_COUNT = re.compile(r"(\d+)\s+(passed|failed|skipped|deselected|errors?|xfailed|xpassed)")


def _parse(text: str) -> Tuple[Dict[str, str], Dict[str, int]]:
    """Return (node_id -> reason, counts). Reason is "" when pytest gave none."""
    outcomes: Dict[str, str] = {}
    for kind, node, reason in _OUTCOME.findall(text or ""):
        # ERROR lines are kept alongside FAILED: a collection error is a test
        # that did not run, which is a worse outcome than one that ran and
        # failed -- not a category to quietly drop.
        outcomes[node] = (reason or "").strip() or kind
    counts: Dict[str, int] = {}
    for n, label in _COUNT.findall(text or ""):
        key = "errors" if label.startswith("error") else label
        counts[key] = max(counts.get(key, 0), int(n))
    return outcomes, counts


def _collected(counts: Dict[str, int]) -> Optional[int]:
    """Total tests the run actually executed or skipped. None if unknowable."""
    keys = ("passed", "failed", "skipped", "xfailed", "xpassed", "errors")
    if not any(k in counts for k in keys):
        return None
    return sum(counts.get(k, 0) for k in keys)


class Finding(BaseModel):
    node_id: str = Field(description="pytest node id")
    reason: str = Field(description="Failure reason as pytest reported it")


class AppSetup(BaseAppSetup):
    collection_tolerance: int = Field(
        default=0,
        description="Allowed difference in collected-test totals before the "
                    "comparison is called unsound. 0 is the honest default; raise "
                    "it only if you know why the counts differ.",
    )
    max_listed: int = Field(
        default=40, description="Cap on node ids listed per category"
    )


class RunInput(BaseModel):
    baseline: str = Field(description="pytest console output from the BEFORE run")
    current: str = Field(description="pytest console output from the AFTER run")
    baseline_label: str = Field(default="baseline", description="Name for the before run")
    current_label: str = Field(default="current", description="Name for the after run")


class RunOutput(BaseModel):
    verdict: str = Field(
        description="clean | regressions | unsound — unsound means the two runs "
                    "are not comparable and the failure diff must not be trusted"
    )
    newly_failing: List[Finding] = Field(description="Passed before, fails now. YOURS.")
    newly_passing: List[str] = Field(description="Failed before, passes now")
    still_failing: int = Field(description="Failed in both — the pre-existing baseline")
    disappeared: List[str] = Field(
        description="Failing before, absent from the current run entirely — not fixed, gone"
    )
    baseline_counts: Dict[str, int] = Field(description="Parsed totals, before")
    current_counts: Dict[str, int] = Field(description="Parsed totals, after")
    collection_delta: Optional[int] = Field(
        None, description="current collected minus baseline collected"
    )
    warnings: List[str] = Field(description="Reasons the comparison may be unsound")
    summary: str = Field(description="One-line human-readable result")


class App(BaseApp):

    async def setup(self, config: AppSetup):
        self.tolerance = max(0, config.collection_tolerance)
        self.max_listed = max(1, config.max_listed)
        logger.info("pytest-baseline-diff ready (tolerance=%d)", self.tolerance)

    async def run(self, input_data: RunInput) -> RunOutput:
        base_out, base_counts = _parse(input_data.baseline)
        curr_out, curr_counts = _parse(input_data.current)
        logger.info("baseline: %d failing, counts=%s", len(base_out), base_counts)
        logger.info("current:  %d failing, counts=%s", len(curr_out), curr_counts)

        warnings: List[str] = []
        if not base_counts and not base_out:
            warnings.append(
                f"{input_data.baseline_label}: no FAILED/ERROR lines and no summary "
                f"line parsed — is this pytest output?"
            )
        if not curr_counts and not curr_out:
            warnings.append(
                f"{input_data.current_label}: no FAILED/ERROR lines and no summary "
                f"line parsed — is this pytest output?"
            )

        # --- the check that decides whether anything below means anything ---
        b_tot, c_tot = _collected(base_counts), _collected(curr_counts)
        delta: Optional[int] = None
        unsound = False
        if b_tot is None or c_tot is None:
            warnings.append(
                "collected totals unknown for at least one run — cannot confirm the "
                "two runs cover the same tests, so the diff below is unverified"
            )
            unsound = True
        else:
            delta = c_tot - b_tot
            if abs(delta) > self.tolerance:
                unsound = True
                warnings.append(
                    f"COLLECTION MISMATCH: {input_data.baseline_label} ran {b_tot} "
                    f"tests, {input_data.current_label} ran {c_tot} ({delta:+d}). "
                    f"Tests the baseline never executed appear as 'newly failing' "
                    f"and tests that stopped being collected appear as fixed. "
                    f"Reconcile the environments before trusting the diff."
                )

        base_ids, curr_ids = set(base_out), set(curr_out)
        newly = sorted(curr_ids - base_ids)
        still = sorted(curr_ids & base_ids)
        was_failing_now_not = sorted(base_ids - curr_ids)

        # "Failed before, not failing now" splits two ways and the distinction
        # matters: a test that now passes is progress; a test that is no longer
        # collected is lost coverage wearing the same clothes.
        curr_all_known = _collected(curr_counts) is not None
        newly_passing, disappeared = [], []
        for node in was_failing_now_not:
            # Without per-test pass records, presence in the current run can only
            # be inferred from collection totals. When collection shrank, absence
            # is more likely removal than repair -- say so rather than claim a fix.
            if curr_all_known and delta is not None and delta < 0:
                disappeared.append(node)
            else:
                newly_passing.append(node)

        if unsound:
            verdict = "unsound"
        elif newly:
            verdict = "regressions"
        else:
            verdict = "clean"

        if verdict == "unsound":
            summary = (f"unsound — runs are not comparable; "
                       f"{len(newly)} apparent new failure(s) cannot be attributed")
        elif verdict == "regressions":
            summary = (f"regressions — {len(newly)} test(s) newly failing, "
                       f"{still and len(still) or 0} pre-existing, "
                       f"{len(newly_passing)} newly passing")
        else:
            summary = (f"clean — no new failures; {len(still)} pre-existing failure(s) "
                       f"unchanged, {len(newly_passing)} newly passing")

        logger.info("verdict=%s newly=%d still=%d", verdict, len(newly), len(still))
        return RunOutput(
            verdict=verdict,
            newly_failing=[Finding(node_id=n, reason=curr_out[n]) for n in newly[: self.max_listed]],
            newly_passing=newly_passing[: self.max_listed],
            still_failing=len(still),
            disappeared=disappeared[: self.max_listed],
            baseline_counts=base_counts,
            current_counts=curr_counts,
            collection_delta=delta,
            warnings=warnings,
            summary=summary,
        )
