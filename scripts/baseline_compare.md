# baseline_compare.sh

Answers one question on a suite that already fails: **which failures are mine?**

The deployed tree carries ~201 pre-existing failures (measured 2026-09-11,
identical in both trees). A raw count after a change tells you nothing. You need
the count from before — and the comparison is only meaningful if both runs
collected the same tests in the same environment.

```
baseline_compare.sh HEAD~1 HEAD
baseline_compare.sh HEAD~1 HEAD --collect-only     # cheap soundness check
```

Exit 0 = PASS (set (b) empty) · 1 = FAIL with IDs · 2 = unsound, refused.

## The verdict rule

**Set (b) — tests failing at head but not at base — is the whole verdict.** Sets
(a) and (c) are reported for context. The script refuses to compute any of them
unless the skipped delta is 0, because a collection difference makes all three
meaningless in both directions.

## Five approaches that were tried and failed

Each produced numbers that looked like results. They are listed so nobody
retries them.

**1. One venv across two trees.** A venv created by `uv sync` in worktree X
imports X's `genlab_core` no matter which tree pytest runs from — the workspace
packages are installed editable, pointed at that path. Running the deployed
tree's tests with a worktree venv collected deployed *files* and imported
worktree *code*. It reproduced the deployed numbers exactly, which I read as
"the venv is not the variable." The tell was set (b) containing tests from a
file that does not exist at base. Now asserted up front: each venv must print a
`genlab_core.__file__` inside its own worktree, or the script aborts.

**2. Config-only mirror.** Copying the five dirty `publishing.yaml` files left
the skipped delta at 13, unmoved. Config was never the variable.

**3. Untracked-state-only mirror.** Copying `genlab-core/models`, `.genlab`,
`.runtime`, `.belt`, `.inferencesh` and matching `.tmp` as a symlink also left
the delta at 13. Conclusion drawn at the time — "path-dependence, irreducible" —
was wrong. It was two files: `genlab-core/config/affiliate_catalog.yaml` (12
tests) and BB `persona.yaml` (1). Found by diffing `-rs` skip *reasons*, which
name the gating path. Both are now mirrored, and the delta is 0.

**4. Parsing failures without a `::` filter.** `^ERROR ` matches ERROR-level
*log lines* that pytest captures and prints at column 0. Three such lines
collapsed to one bogus id and put both sides off by one against their own
summaries. Node ids always contain `::`; the filter is not cosmetic.

**5. `pgrep -f` waiters.** `until ! pgrep -f "pytest -q --timeout=300"` matches
the waiter's own command line, so it never exits; three spun for 30 minutes and
one silently prevented a launch. A minute later `pkill -f` on the same pattern
killed the ssh session. Waits are PID-captured, always.

## Two more traps worth knowing

**Shared `.tmp`.** Pointing both trees' `.tmp` at the same mount deadlocked two
concurrent runs at 0 bytes for 23 minutes. Each worktree gets its own, and runs
are sequential regardless.

**Prod-path writes.** 19 test files reference literal `/opt/genlab` paths; two
can *write*. Those are deselected via `baseline_compare.deselect` — 17 tests,
0.150% of collected. Widen that file only with the same justification.

## When the skipped delta is not 0

The script aborts and tells you to diff `-rs` skip reasons. The reason strings
name the path or artifact being gated on; add it to `UNTRACKED_DIRS` or
`GITIGNORED_FILES`. That is how the last 13 were found, and it is a two-minute
fix rather than a re-derivation.

## Provenance

Derived from the live comparison that produced gate 4's PASS for FIX-T01 on
2026-09-11: base `7b761a94` / head `12e7d7f7`, collected delta +6 (exactly the
six new tests), skipped delta 0, set (b) empty, 201 failures in both trees.
