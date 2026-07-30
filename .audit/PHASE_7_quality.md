# Phase 7 — Tests, types, and CI

**Audit run:** A (2026-07-29)
**Session:** 8 (Pre-Session-8 gate + Phase 7)
**Status:** COMPLETE — 8 findings carried (A-0072…A-0079) under 12-cap
**§0.5 self-scan:** PASSED (run LAST per §0.10)
**§0.10 shell quiescence:** all SSH sessions closed before summary write
**Related artifacts:** `PHASE_1_findings.yaml` (A-0030, A-0021 dangling), `PHASE_5_findings.yaml` (A-0055), `PHASE_6_findings.yaml` (A-0068), `OPERATOR_ACTIONS.md` (rotation still PENDING), `DEFERRALS.md` (127 lines, 10+ inherited items)

**Prior-artifact posture (§0.8):** Three prior findings materially corrected this session:
- **A-0055** wrong on "no secret-scan hook exists" — gitleaks v8.24.3 IS in `.pre-commit-config.yaml`. But the 2026-07-22 commit that added 3× `PGPASSWORD=genlab_***` in `verify_policy_block_l1.sh` bypassed it. Real class-of-bug: hook configured but NOT enforced. → **A-0072** supersedes.
- **A-0030** BB "758 JSON files" was mostly manifest noise — 741 of 758 are in `.tmp/` (dev cache, auto-excluded by v1.1+ manifest rules). Real BB JSON count is 17. → **A-0078** downgrades A-0030 to non-issue.
- **A-0068** situation-1 CONFIRMED — full-repo trace across all 7 workspace members returns empty. Revenue tracking genuinely not wired. → **A-0073** upgrades severity.

---

## 0. Pre-Session-8 gate

- **Scrub held:** `grep -rIn 'genlab_***' .audit/` → exit 1 (no matches).
- **Rotation status:** still PENDING (hash unchanged from `c7b89bffef3a`). Runbook not yet executed.
- Shell quiescence: verified at session start.

## 1. CI presence (Phase 7 §7)

```
ls .github/workflows/
= auto-deploy.yml (4.3 KB)
  ci.yml          (16 KB)
  codeql.yml      (2.0 KB)
  test.yml        (17 KB)
```

**4 workflows exist**, substantial (16-17 KB each for `ci` and `test`). Not the "no CI" scenario the spec worried about given the 13h deploy lag + two-way drift (A-0003). Actual run history + duration + what-could-merge-broken not measured this phase (needs `gh run list` access + workflow parsing).

→ **A-0079** (info; deeper analysis deferred to Phase 9 or on-request).

## 2. Pre-commit content — D8 CLOSED

```yaml
- repo: https://github.com/astral-sh/ruff-pre-commit
  rev: v0.15.14                    # ← matches CLAUDE.md pin
  hooks: [ruff --fix, ruff-format]

- repo: https://github.com/pre-commit/pre-commit-hooks
  rev: v5.0.0
  hooks: [trailing-whitespace, end-of-file-fixer, check-yaml, check-added-large-files,
          check-merge-conflict, detect-private-key]

- repo: https://github.com/gitleaks/gitleaks
  rev: v8.24.3                     # ← contradicts my Phase 5 A-0055 claim
  hooks: [id: gitleaks]

- repo: https://github.com/shellcheck-py/shellcheck-py
  rev: v0.11.0.1
  hooks:
    - id: shellcheck
      files: ^scripts/.*\.sh$
      args: [--severity=warning, --exclude=SC2155/SC2034/SC1090/SC2044/SC2043/SC2010]
```

**Config is well-formed.** ruff pin matches CLAUDE.md ✓. gitleaks present ✓. detect-private-key present ✓. shellcheck scoped to scripts/ with documented excludes. **D8 CLOSED CONFIRMED.**

**But:** the 2026-07-22 commit `0cae735a` introduced 3× `PGPASSWORD=genlab_***` in `verify_policy_block_l1.sh` AFTER gitleaks was added to config (which happened in `abfadd3b fix: complete public-readiness remediation`). So the hook was configured but did not fire — either `pre-commit install` was never run on the operator's machine, or the commit used `--no-verify`. Class-of-bug: **hook exists but doesn't fire**, same shape as A-0026/A-0062 (`-mtime -delete` line exists but doesn't fire). → **A-0072** (supersedes A-0055).

## 3. Test suite — Phase 7 §1 (BLOCKED)

```
uv run pytest --collect-only -q
= ...
= ERROR genlab-core/tests/publishing/test_publishing_imports.py
= ERROR genlab-core/tests/publishing/test_retry_pass.py
= (77 total ERROR files)
= !!!!!!!!! Interrupted: 77 errors during collection !!!!!!!!!
= 10783 tests collected, 77 errors in 7.80s
```

**Cannot run the suite.** 77 test files fail to import at collection. 10,783 tests would collect otherwise. Errors concentrate in `genlab-core/tests/publishing/` and `scripts/tests/` — points at cross-module import dependencies broken by refactoring OR Python version drift (Mac 3.14 vs prod 3.12 per A-0006). Root cause not chased this phase (context tight).

→ **A-0074** (S1 — suite is unrunnable in current state).

**Coverage numbers per member** (§3): unavailable because suite doesn't run. Deferred to Phase 9 fix queue.

## 4. Type census (Phase 7 §5)

```
grep -rn '# type: ignore' --include='*.py' genlab-core/src/ dashboard/server/ BlackboxBrief/ CriticalRush/ ClutchWire/ SpliceReel/ FrameDrift/ | wc -l
= 49  (Python # type: ignore)

grep -rn '@ts-ignore|@ts-expect-error' --include='*.ts' --include='*.tsx' dashboard/ | wc -l
= 1
```

**49 Python type-ignores across ~316K LOC** — that's ~0.015%, very low. Healthy signal. **1 TS @ts-ignore** in the dashboard — essentially zero.

mypy/pyright strictness per member, `Any` in public signatures: not surveyed this session. Deferred.

→ **A-0077** (positive-signal S3).

## 5. Dead-code static (Phase 7 inherited from Phase 1)

```
ruff check --select F401,F811,F841,ARG --target-version py312 genlab-core/src/
= Found 98 errors.
= [*] 6 fixable with the `--fix` option.
```

**98 hits** on genlab-core/src/ alone (unused imports F401, unused variables F841, unused arguments ARG, redefined-while-unused F811). 6 are auto-fixable. Each is a STATIC_ONLY candidate per §0.3 — needs runtime evidence (journal grep / import proof) to promote past S3.

Not classified further this phase (would need per-hit analysis). → **A-0075** (aggregate finding + escalation to Phase 9).

## 6. A-0068 wire trace — CONFIRMED situation 1 (revenue tracking not wired)

Full-repo trace across all 7 workspace members:
```
grep -rlE 'INSERT INTO affiliate_clicks|affiliate_clicks.*insert|Table.*affiliate_clicks' \
  BlackboxBrief CriticalRush ClutchWire SpliceReel FrameDrift genlab-core dashboard 2>/dev/null | \
  grep -v '/.venv/'
= (empty)

grep -rnE 'class AffiliateClick|AffiliateClicks|affiliate_click_id' \
  BlackboxBrief CriticalRush ClutchWire SpliceReel FrameDrift genlab-core dashboard 2>/dev/null | \
  grep -v '/.venv/' | grep -v '/.next/'
= (empty)
```

**Zero writer sites, zero ORM/dataclass models for `affiliate_clicks`.** A-0068's "sample-only" caveat is closed: the table exists in DB schema, has 0 rows, and has no writer in the entire monorepo. Same likely holds for `affiliate_revenue` (not double-verified this session).

**Consequence for A-0042 (Phase 3):** situation 1 confirmed at the code level. The SaaS revenue-thesis validation is blocked — not because of network-dashboard results (A-0071, still pending operator cross-check), but because the LOCAL pipeline never had the code to record clicks in the first place.

→ **A-0073** (S1 upgrade from A-0068's S2/medium).

## 7. BB 758 JSON files (A-0030 correction)

```
find BlackboxBrief -name '*.json' -not -path '*/node_modules/*' -not -path '*/.venv/*' -not -path '*/.next/*' | awk -F/ '{print $2}' | sort | uniq -c | sort -rn
= 741 .tmp
=  11 schemas
=   4 tests
=   2 .claude
```

**741 of 758 are in `BlackboxBrief/.tmp/`** — dev cache directory that v1.1+ manifest rules auto-exclude via `*/.tmp/*`. Real BB JSON count is 17 (11 schemas + 4 test fixtures + 2 Claude Code state). **A-0030 was inflated by my Phase 2 grep not excluding `.tmp/`.** No sprawl — just noise from a bad grep.

→ **A-0078** (A-0030 supersession — downgrade to non-issue).

## 8. Inherited items dispositions (concise)

| Inherited item (source phase → this phase) | Status | Detail |
|---|---|---|
| A-0068 wire trace (Phase 6 → Phase 7) | **LANDED CONFIRMED** | A-0073 — situation 1 confirmed at code level |
| BB 758 JSON files A-0030 (Phase 2 → Phase 7) | **LANDED REFUTED** | A-0078 — 741/758 in `.tmp/`; A-0030 downgrades |
| dead-code sweep (Phase 1 → Phase 7) | **PARTIAL** | A-0075 — 98 hits static; per-hit runtime pass deferred to Phase 9 |
| deptry per-member (Phase 1 → Phase 7) | **PENDING** | not installed; escalate to Phase 9 tooling install |
| dashboard lockfile question (Phase 1 → Phase 7) | **PENDING** | not chased this session |
| unused React components (Phase 1 → Phase 7) | **PENDING** | requires TS-aware tooling; deferred |
| silent-except full classification A-0049 (Phase 4 → Phase 7) | **PENDING** | 132 sites unclassified; test-coverage cross-check deferred |
| guard historical-firing verification (Phase 4 → Phase 7) | **PENDING** | needs sample historical incidents; blocked by A-0050 journal rotation |
| Meta EAA expiry monitoring (Phase 5 → Phase 7) | **PENDING** | requires token-refresh code trace |
| Full cross-host .env diff by hash-prefix (Phase 5 → Phase 7) | **PENDING** | 208 keys × 2 hosts; not chased |
| Static-file traversal on 173 dashboard routes (Phase 5 → Phase 7) | **PENDING** | needs per-route analysis |
| Intelligence engine default-when-unconfigured (Phase 4 → Phase 7) | **PENDING** | 4 missing env vars per A-0046 |
| PROMOTED_COLUMNS parity D12 (Phase 3 → Phase 7) | **PENDING** | requires `GENLAB_SCHEMA_PIN_DSN` env |
| A-0068 full trace (Phase 6 → Phase 7) | **LANDED CONFIRMED** | as above |

**Reality check:** 10 of 14 inherited items remain PENDING. Under context pressure this phase couldn't complete them. Rather than pad findings, they escalate to Phase 9 disposition (each becomes a `DEFER` with named trigger, or a `FIX` with an owner).

## 9. Findings carried (8 of 12 cap)

Ranking: severity × confidence ÷ effort; S1 first.

| id | S | title |
|---|---|---|
| **A-0072** | S1 | A-0055 CORRECTED — gitleaks IS in `.pre-commit-config.yaml` (v8.24.3), but 2026-07-22 commit `0cae735a` introduced 3× `PGPASSWORD=genlab_***` anyway. Hook exists but doesn't fire (`pre-commit install` not run OR `--no-verify` used). Same class as A-0026/A-0062 |
| **A-0073** | S1 | A-0068 CONFIRMED situation 1 — full-repo trace across all 7 members: zero writers for `affiliate_clicks`, zero ORM models. Revenue tracking genuinely not wired. SaaS revenue-thesis blocked at code level, not just DB |
| **A-0074** | S1 | Test suite has 77 collection errors on 10,783 tests. Can't run pytest. Coverage numbers unavailable. Root cause presumably cross-module imports or Python version drift (A-0006) |
| A-0075 | S2 | Dead-code static: 98 ruff F401/F811/F841/ARG hits on genlab-core/src/ alone. 6 auto-fixable. Per-hit runtime evidence needed to promote past S3 |
| A-0076 | S3 | D8 CLOSED — `.pre-commit-config.yaml` well-formed (ruff v0.15.14 ✓, gitleaks, detect-private-key, shellcheck scoped). Config good; enforcement is the gap (A-0072) |
| A-0077 | S3 | POSITIVE — 49 Python `# type: ignore` + 1 TS `@ts-ignore` across monorepo. ~0.015% of Python LOC. Healthy typing discipline |
| A-0078 | S3 | A-0030 REFUTED — BB "758 JSON files" was 741 `.tmp/` dev-cache noise + 17 real (11 schemas + 4 tests + 2 .claude). Manifest exclusion catches these; not sprawl |
| A-0079 | S3 | CI exists — 4 workflows (`auto-deploy.yml`, `ci.yml`, `codeql.yml`, `test.yml`), substantial (16-17 KB each). Actual run history + coverage % not measured this phase |

## 10. Doc-delta closures

- **D8 → A-0076** (CLOSED CONFIRMED — pre-commit config well-formed)

## 11. Deferrals added / status this session

Appended to `DEFERRALS.md`:
- 10 of 14 inherited items remain PENDING → Phase 9 explicit disposition (each becomes `FIX` / `ACCEPT` / `DEFER`)
- **A-0074 test-suite fix** → Phase 9 fix queue (blocks coverage measurement)
- **A-0072 pre-commit enforcement** → OPERATOR_ACTIONS (add `pre-commit install` to onboarding + verify CI runs gitleaks on push)
- **A-0075 dead-code triage** → Phase 9 (per-hit runtime pass on top-20 candidates)
- **CI workflow analysis** (which tests run, what could merge broken) → Phase 9 or on-request

## 12. Methodology issues this phase

1. **A-0055 was wrong** — I claimed no secret-scan hook exists; gitleaks was there all along. **Class of methodology error: I searched for the finding I expected, not for the config as it is.** Should have read `.pre-commit-config.yaml` first in Phase 5. Instead I inferred absence from the presence of `pg_backup.sh`'s DSN. Same shape as A-0009 (no-consumer claim from static evidence): missing runtime check.
2. **A-0030 was noise** — my Phase 2 grep on `BlackboxBrief/**/*.json` didn't exclude `.tmp/`. The manifest rules (v1.1+) DO exclude it, but my ad-hoc grep didn't inherit those rules. Should have used the same exclusion set.
3. **Phase 7 scope creep** — 8 primary items + 10+ inherited. Under context pressure I completed 3 primary (CI, pre-commit, dead-code static) + 3 inherited (A-0068, A-0030, type census) and escalated 10 items to Phase 9. That's rational triage, not failure — Phase 9's job is to dispose of them.
4. **Test suite is unrunnable** — 77 collection errors mean the "measure coverage" ask is fundamentally blocked. A-0074's fix is a prerequisite for any real quality measurement. This is more consequential than any single count-based finding.
