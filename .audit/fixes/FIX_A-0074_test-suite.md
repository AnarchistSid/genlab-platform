# FIX_A-0074 — Test suite unrunnable (77 collection errors on 10,783 tests)

## Closes
`uv run pytest --collect-only -q` fails with 77 file-level import errors, blocking any test run and any coverage measurement. Errors concentrate in `genlab-core/tests/publishing/` and `scripts/tests/`.

## Exact change

Root cause is not yet known (Phase 7 didn't chase). Likely causes, in order of probability:
1. **Python 3.14/3.12 drift** on Mac (per A-0006) — imports work on 3.14 but fail on the Ruff-declared 3.12 target, OR test collection ends up loading modules with 3.14-only syntax
2. **Cross-module refactor drift** — a module was moved or renamed; test imports point at the old path
3. **Missing dev dependency** — some `dev` extra not installed in the current venv

### Recommended attack (iterative):

1. **Land FIX_A-0081 first** (env pinning). If Mac is now on 3.12, re-run `uv run pytest --collect-only -q` and check the delta. Expected: N < 77 (maybe substantially).

2. **For remaining collection errors, pick one representative:**
   ```bash
   uv run pytest --collect-only genlab-core/tests/publishing/test_publishing_imports.py 2>&1 | head -30
   ```
   Read the traceback. Apply the fix (import path, missing dep, config). Iterate.

3. **Batch by traceback pattern**: if 20 files fail because module `X` moved to `Y`, one `find + sed` fixes all 20.

### Anti-pattern (don't do):
- `pytest --ignore=<file>` to make the suite "run" — that hides the problem.
- Comment out failing imports — the tests are load-bearing.

## Verification gate

```bash
uv run pytest --collect-only -q 2>&1 | tail -1
```
Expected: `NNNNN tests collected` with **NO** "N errors during collection" suffix and **NO** `!!! Interrupted !!!` line.

Then, secondary gate (measure coverage — the point of unblocking the suite):
```bash
uv run pytest --collect-only -q --cov=genlab_core --cov=dashboard 2>&1 | tail -20
```
Expected: coverage table prints without exceptions. This becomes the input for A-0049 (silent-except full classification) and any code-quality follow-up.

## Sequencing
- Must land after: A-0081 (env pinning may resolve a subset)
- Blocks: A-0049 (silent-except classification needs test coverage cross-check), CI test-workflow health analysis (A-0079), property-test CI vs local check

## Blast radius if wrong
Fixing test-imports is standard dev work; blast if wrong is contained to test code (not production). The one risk: if a fix hides a real problem (see anti-patterns above), the coverage measurement becomes misleading — mitigated by requiring the secondary gate to actually produce a coverage report, not just "the suite runs."

## Effort
M-L (depends on root cause; if Python-drift-only then M with FIX_A-0081; if 77 independent breaks then L)

## Owner
dev
