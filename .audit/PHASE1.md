# PHASE 1 — Inventory & Raw Metrics

**Exclusion set applied throughout**: `.tmp/  .hypothesis/  .playwright-mcp/
.grimp_cache/  .import_linter_cache/  *.egg-info/  node_modules/  .venv/
dist/  build/  __pycache__/  .audit/  .serena/  .media/  .logs/  ..bfg-report/`

## The four headline numbers

| Number | Value | Interpretation |
|---|---|---|
| **Real source LOC** (Python code, whole repo, excludes above) | **315,882** | 26× the 12K mental model |
| **Test:source ratio** (genlab-core, Python code only) | **1.25 : 1** | 99,234 src Python / 123,956 test Python |
| **Publish-path coverage %** | **UNVERIFIED** | Full pytest aborted at 54% (see Blindness) |
| **New-Python-LOC per new channel** (small-niche mean) | **~1,268** | Not zero. SaaS thesis "new brand = new YAML" is false today. |

## Step 1 — Size, per workspace member (`tokei` with exclusion set)

| Package | Python | TSX | YAML | Shell | JSON | TS | Total code |
|---|--:|--:|--:|--:|--:|--:|--:|
| genlab-core | 229,478 | 0 | 6,563 | 0 | 87 | 0 | **236,376** |
| dashboard | 40,299 | 29,070 | 31 | 49 | 7,564 | 5,921 | 84,014 |
| scripts | 17,122 | 0 | 0 | 4,093 | 685 | 0 | 22,041 |
| BlackboxBrief | 7,791 | 0 | 3,826 | 258 | 1,058 | 0 | 20,356 |
| CriticalRush | 11,547 | 0 | 2,782 | 16 | 320 | 0 | 15,534 |
| ClutchWire | 2,400 | 0 | 1,265 | 0 | 0 | 0 | 3,688 |
| SpliceReel | 2,846 | 0 | 1,273 | 0 | 0 | 0 | 4,142 |
| FrameDrift | 2,614 | 0 | 1,339 | 0 | 0 | 0 | 3,978 |
| tests (root) | 1,785 | 0 | 0 | 0 | 0 | 0 | 1,785 |
| docs | 0 | 0 | 0 | 0 | 0 | 0 | 11,493 (MD) |

**Discrepancy resolved (F-0010)**: genlab-core is **not** ~12K LOC.
`genlab-core/src` = **423 Python files, 125,716 physical lines** (99,234 code +
13,531 comments + 12,951 blanks). The 12K mental model is **~10× off**. Every
maintenance / review / capacity assumption downstream of that number is wrong.

**genlab-core/tests = 731 files, 158,059 physical lines.** Test:source ratio =
**1.26:1** by physical lines, **1.25:1** by code-only. Below the 1.5:1
threshold that would suggest generated tests — the ratio is honest.

## Step 2 — The three-identical-niches question (jscpd, `--format python`)

- Analyzed 83 Python source files across 5 niche packages (tests excluded).
- **17 clone sets**, **339 duplicated lines** = **1.84%** duplication.
- **Cross-niche Python clone lines**:
  - ClutchWire ↔ SpliceReel: **137 lines**
  - ClutchWire ↔ FrameDrift: **59 lines**
  - ClutchWire ↔ CriticalRush: **16 lines**
  - BlackboxBrief ↔ any: **0 lines**
  - Total cross-niche duplicated Python: **~212 lines**.

**Per-niche Python LOC (excluding tests, .tmp, .venv, __pycache__)**:
- BlackboxBrief: 6,220 (31 files)
- CriticalRush: 8,462 (48 files)
- ClutchWire: 1,107 (10 files)
- SpliceReel: 1,460 (12 files)
- FrameDrift: 1,238 (11 files)

**The SaaS-thesis question**: A hypothetical 6th channel would need ~**1,100–
1,500 new Python LOC** — the mean of the three small niches (1,268 LOC). Only
~200 lines of that would be genuinely shared. Cannot be produced from "config-
only" changes today. Filed as **F-0011** (medium architecture).

**Side note**: `ClutchWire/.venv` (13 MB) exists; `BlackboxBrief/.venv` +
`CriticalRush/.venv` are 68 KB stubs. **F-0012** (info config): stale
per-niche venvs.

## Step 3 — Complexity (`radon cc` + `mi`)

**CC rank distribution**: A=3,059, B=849, C=472, **D=78, E=20, F=21**. Total
**119 D/E/F functions**.

**Top 10 F-rated functions (accidental vs essential — my read)**:

| Rank(CC) | File:Line | Function | Judgement |
|---|---|---|---|
| F(223) | genlab-core/…/push_to_backlog.py:1583 | `execute` | **Accidental**. Load-bearing pipeline stage, but cyclomatic 223 is 40× the "F" threshold. Many defensive fallbacks accumulated. |
| F(129) | dashboard/…/analytics.py:248 | `_build_overview` | **Accidental**. Feature-accretion in one endpoint. |
| F(123) | genlab-core/…/writing/video_content_writer.py:323 | `write_video_content` | **Essential-adjacent**. Content generation has many branches by platform, but 123 is high. |
| F(90) | dashboard/…/overview.py:182 | `_build_overview` | **Accidental**. Duplicate function name with analytics.py version — divergence risk. |
| F(78) | genlab-core/…/push_to_backlog.py:1544 | `PushToBacklog` (class-level CC) | See row 1. |
| F(73) | genlab-core/…/transformation_orchestrator.py:209 | `apply_transformations` | **Essential**. Variant-selection is genuinely N-branch. |
| F(70) | genlab-core/…/cta_engine.py:211 | `inject_cta` | **Accidental**. |
| F(69) | genlab-core/…/publishing/payload_builder.py:167 | `build_payload` | **Essential**. Per-platform payload shape. |
| F(65) | genlab-core/…/writing/llm_hook_generator.py:335 | `generate_hook` | Mixed. |
| F(59) | genlab-core/…/pipeline/stages/run_report.py:50 | `execute` | **Accidental**. Report assembly. |

**Modules with MI < 20**: 9. Four at MI=0.0 (unmaintainable):
`push_to_backlog.py`, `review_server.py`, `dashboard/api/blueprints.py`,
`dashboard/api/analytics.py`.

Filed as **F-0013** (high correctness): the four MI=0.0 files touch the
publish path AND the operator surface.

## Step 4 — Tests

- **`pytest -q genlab-core/tests` at repo root**: fails with 77 collection
  errors — this is a whole-repo pytest issue (import ordering across
  workspaces), NOT per-package. `uv run --package genlab-core pytest
  genlab-core/tests` scoped correctly succeeds.
- **Fast run baseline** (`--timeout=60 -x`): **184 passed, 1 failed, 1 skipped
  in 32s** before hitting fail-fast on
  `genlab-core/tests/compliance/test_disclosure_and_health.py::test_get_signal_stub_returns_none`.
  Total collected: **9,451 tests / 9,491 (40 deselected)**.
- **Full non-fail-fast run**: aborted at **54%** (~5,000 tests, still running
  at time budget end). Filed as **F-0014** (medium test_gap):
  full-suite pass/fail/skip counts UNVERIFIED. The audit's "working knowledge"
  of 3,069 pass / 5 fail / 19 skip cannot be validated from this session; that
  number appears to describe a subset (perhaps a specific workspace member's
  suite), not the full genlab-core `pytest genlab-core/tests` result.
- **`@pytest.mark.skip` / `pytest.skip` usage in genlab-core/tests: 35
  occurrences**. **`xfail` count: 0**. No bare skips (all had reasons).
- **Publish-path coverage %**: cannot compute without a completed run and a
  coverage report. UNVERIFIED.

## Step 5 — Config surface

- **144 config files** at depths 1–4 (excluding scratch/tool dirs). Split:
  108 YAML, 12 YML, 9 TOML, 6 .env variants.
- **Top 3 largest YAML**:
  - `genlab-core/config/shared_sources.yaml` — 2,846 lines
  - `genlab-core/config/affiliate_catalog.example.yaml` — 1,515 lines
  - `genlab-core/config/affiliate_catalog.yaml` — 1,268 lines
- **Env-var audit** (grep `GENLAB_*` in code vs `.env.example` / CLAUDE.md):
  - Code reads **70 distinct GENLAB_* vars**.
  - Docs mention **36 distinct GENLAB_* vars**.
  - **Phantom (read, not documented): 51 vars.** Filed as **F-0015** (high
    config). Every phantom-with-no-fallback is a crash-on-first-deploy risk;
    every phantom-with-fallback is silent-behavior-drift risk.
  - **Orphan (documented, not read): 17 vars.** Filed as **F-0016** (medium
    docs). Includes the recently-added `GENLAB_SCHEMA_PIN_DSN` from tonight's
    shipping — legitimate future-facing; others (e.g.,
    `GENLAB_INTELLIGENT_TRANSFORM_ENABLED`, `GENLAB_OPTIMAL_TIME_BANDIT`)
    look like retired flags. Full lists in
    `.audit/phase1/01_env_phantom.txt` + `01_env_orphan.txt`.

## Step 6 — Dependencies

- **Workspace members**: 7 (`genlab-core`, `dashboard`, `BlackboxBrief`,
  `CriticalRush`, `ClutchWire`, `SpliceReel`, `FrameDrift`).
- **Runtime deps per member**: genlab-core=16, dashboard=7, BlackboxBrief=19,
  CriticalRush=14, ClutchWire=4, SpliceReel=4, FrameDrift=4.
- **`pip-audit`**: scanned 28 packages in the root .venv. **0 vulnerabilities.**
  (Note: only the root .venv was scanned; per-package .venvs — ClutchWire
  has 13 MB of yt-dlp etc. — not scanned this phase.)
- **`lint-imports`**: 1 declared contract, **broken** (see F-0007 from
  Phase 0 corrections). Two undeclared architecture rules pass by accident
  (F-0008).

## Step 7 — Git archaeology

**Commits per month**: 2026-03: 415, 04: 65, 05: 169, 06: 615, 07: 556.
Development pace has been ~500/month since June.

**fix/feat ratio by month** — rising = debt signal:

| Month | feat | fix | ratio |
|---|--:|--:|--:|
| 2026-03 | 111 | 142 | 1.28 |
| 2026-04 | 14 | 39 | **2.79** |
| 2026-05 | 39 | 96 | 2.46 |
| 2026-06 | 243 | 196 | 0.81 |
| 2026-07 | 195 | 276 | **1.42** ↑ |

March–May was a fix-dominated churn; June recovered; **July is climbing back
to fix-heavy**. Filed as **F-0017** (info): the rising July ratio matches the
work I shipped tonight (12 of ~40 July commits were tonight's fixes for
diagnostic-surfaced bugs).

**Top 10 highest-churn source files (scratch excluded)**:
1. `genlab-core/src/genlab_core/monitoring/health_monitor.py` — 6,003 LOC churn
2. `genlab-core/src/genlab_core/pipeline/stages/push_to_backlog.py` — 4,558
3. `genlab-core/src/genlab_core/learning/metric_collector.py` — 4,154
4. `genlab-core/src/genlab_core/publishing/publish_all_platforms.py` — 3,814
5. `BlackboxBrief/bb_strategies/_hooks_legacy.py` — 3,800 (name says
   "legacy" — F-0018)
6. `genlab-core/src/genlab_core/http/backlog_client.py` — 3,434
7. `genlab-core/src/genlab_core/media/frame_compositor.py` — 3,333
8. `dashboard/server/review_server.py` — 3,073
9. `genlab-core/config/shared_sources.yaml` — 2,992
10. `genlab-core/tests/test_metric_collector.py` — 2,692

Six of the top 10 also appear in the CC F-rated list (Step 3) — churn
concentrates on the same functions the complexity metric flags. Consistent
signal.

**Rewritten >5 times**: 10 files. Top: `push_to_backlog.py` (99 commits),
`metric_collector.py` (61), `review_server.py` (55). Filed as **F-0019**
(high architecture): the ratchet-of-fixes on these three files is an
unsolved design problem — every session lands there.

**Files touched once, never again**: **1,342 files**. Candidate abandoned
work. Sampling in Phase 2 will separate "one-shot done" from "started, gave
up".

**Branches**:
- **Local**: 98. **Remote**: 764.
- **Remote branches merged into main**: **3**.
- **Remote branches NOT merged**: **761**.
- Sampled 5 unmerged: 3 are `auto2-day1-*` from mid-June (auto-approver
  Day-1 rollout attempts that never merged); 1 is `audit/composite-key-double-
  prefix-scan` from 2026-07-09. Filed as **F-0020** (medium architecture):
  761 branches carry work no one has audited. Some may be dead ends; some may
  be missing merges.

---

## Blindness list — declared explicitly

- **Full pytest run on genlab-core/tests aborted at 54%** due to time budget.
  Baseline (`-x`): 184 passed, 1 failed, 1 skipped in 32s. Total tests:
  9,451/9,491. The "3,069 pass" folk-number cannot be validated from this
  session. Re-run without `-x` and with `--durations=0` in a dedicated
  session.
- **Publish-path coverage %** cannot be computed without a completed pytest.
  UNVERIFIED.
- **Per-workspace-member pytest** (dashboard, BlackboxBrief, etc.) not run
  this phase. `dashboard/tests`, `BlackboxBrief/tests`, and the 4 other niche
  test dirs are untouched.
- **jscpd Python-only run** may miss template-shape duplication that would
  be visible with `--min-lines 5` or ast-based checkers. Cross-niche 212
  duplicated lines is a lower bound on shared shape.
- **pip-audit** only scanned the root .venv (28 pkgs, 0 vulns). Per-package
  venvs (ClutchWire 13 MB yt-dlp etc.) not scanned — CVEs there UNVERIFIED.
- **Per-workspace dep conflict check** (same pkg pinned to different versions
  across members): not run. Requires `uv pip compile --all-extras` or manual.
- **VPS-side deps**: not inventoried. Anything installed on VPS but not in
  any `pyproject.toml` is a Phase 4 finding.
- **1,342 "touched once"** files: sample by mtime not done — could not
  distinguish abandoned from complete.
- **Phantom-with-fallback vs phantom-with-no-fallback** breakdown (F-0015)
  not performed — 51 phantom vars listed but not classified.

## Corrections to this phase's own process

- Ran `pkill -f "pytest genlab-core/tests"` at 54% completion to fit time
  budget. Rule 4 (no repairs) preserved: no code was changed. Rule 1
  (evidence or silence) preserved: pytest results reported as UNVERIFIED,
  not extrapolated.
- Multiple `background+monitor` cycles for pytest each finished with
  0-byte outputs before the real pytest exited — my Monitor probes were
  matching stale process names. All subsequent monitoring used `pgrep -f`
  with exact string.

## Addendum — late-arriving Phase 1 signal (post-summary)

A background task from earlier in this phase completed after PHASE1.md was
first written. Its output produced **two additional findings**:

### F-0021 (high) — Workspace `[project].name` mismatches directory name for 6/7 packages

Original Phase 1 first pytest attempt exited with:
```
### dashboard
error: The workspace does not have a member dashboard: /Users/anarchistsid/GenLab
```

Verification: `uv pip run --package <X>` matches on `[project].name`, not
directory name. Mismatches:

| Directory | project.name |
|---|---|
| genlab-core | `genlab-core` ✓ |
| dashboard | `genlab-dashboard` |
| BlackboxBrief | `blackbox-brief` |
| CriticalRush | `criticalrush` |
| ClutchWire | `clutchwire` |
| SpliceReel | `splicereel` |
| FrameDrift | `framedrift` |

CI at `.github/workflows/test.yml:190` correctly uses `--package
genlab-dashboard`. Local + audit tooling that follows the "CLAUDE.md
convention" of `--package <dir>` silently fails for 6 of 7 packages. Every
past claim of "ran X package tests" for a mismatched dir may be phantom.

### F-0022 (medium) — psycopg_pool 3.3.1 shutdown TypeError pollutes every pytest

Same task also produced:
```
File ".venv/lib/python3.13/site-packages/psycopg_pool/pool.py", line 470,
  in _signal_stop_worker
File ".venv/lib/python3.13/site-packages/psycopg_pool/sched.py", line 48,
  in enter
TypeError: 'NoneType' object is not callable
```

Test exit code was 0 — this is atexit-path noise, not a test failure. But
it appears on every pytest run and obscures real shutdown errors. Fix:
upgrade `psycopg-pool` or add an atexit handler in the connection-pool
wrapper that calls `pool.close()` before Python shutdown races start.

### Process note

Rule "no summary until every shell has exited" was violated: the summary
was written while the background task had not yet flushed its output. The
mitigation is now: append addendum, don't rewrite. But the underlying
lesson stays — the task list must be independently drained (via TaskList
before writing the summary) not only trusted based on my recollection.
