# AUDIT A — Deferrals Ledger (append-only)

Every "deferred to Phase N" line in every artifact + every doc-delta item lands here.
Phase 9 audits this file — each entry must terminate as LANDED (with the artifact/finding
that closed it) or ESCAPED (goes into the register as its own line — an escaped deferral
is a finding about the audit).

Format: `| item | source phase | destination phase | added | status | landed_as |`

| item | source | dest | added | status | landed_as |
|---|---|---|---|---|---|
| Full DB schema inventory / RLS empirical check | Phase 0 | Phase 3 | 2026-07-29 | PENDING | — |
| pg_stat_statements top-cost queries | Phase 0 | Phase 3 | 2026-07-29 | PENDING | — |
| 34 psycopg bypass sites audit (D22) | Phase 0 | Phase 3+5 | 2026-07-29 | PENDING | — |
| PROMOTED_COLUMNS parity check (D12) | Phase 0 | Phase 3 | 2026-07-29 | PENDING | — |
| 55-indexes claim (D13) | Phase 0 | Phase 3 | 2026-07-29 | PENDING | — |
| SharePoint legacy fallback reachability (D6) | Phase 0 | Phase 3 | 2026-07-29 | PENDING | — |
| Alembic head vs DB (D14) | Phase 0 | Phase 3 | 2026-07-29 | PENDING | — |
| Prefect deployment registry — pending confirmation "does not exist" | Phase 0 | Phase 4 | 2026-07-29 | PENDING | — |
| A-0001 incident reconstruction (root cause + missed publish count + detection latency) | Phase 0 | Phase 4 | 2026-07-29 | PENDING | — |
| Rule #17: except ImportError DEBUG→WARN elevation audit (D25) | Phase 0 | Phase 4 | 2026-07-29 | PENDING | — |
| 20 insight schedules verification (D16, D24) | Phase 0 | Phase 4 | 2026-07-29 | PENDING | — |
| verify-2026-07-22-fixes.timer stale (D9) | Phase 0 | Phase 4 | 2026-07-29 | PENDING | — |
| 7 intelligence engines flag-gated env vars (D17) | Phase 0 | Phase 4 | 2026-07-29 | PENDING | — |
| SKIP_APPROVAL_GATE removed? (D18) | Phase 0 | Phase 4 | 2026-07-29 | PENDING | — |
| 1-reel/day cap enforced in prod (D23) | Phase 0 | Phase 4 | 2026-07-29 | PENDING | — |
| AUTO #2 rollout ladder position (D28) — coord with Phase 2 writer-trace | Phase 0 | Phase 4 | 2026-07-29 | PENDING | — |
| Meta permanent EAA Page Tokens verification (D20) | Phase 0 | Phase 5 | 2026-07-29 | PENDING | — |
| Per-niche credential architecture (D26, D27) | Phase 0 | Phase 5 | 2026-07-29 | PENDING | — |
| PLATFORM_SPECS libx264 verification (D21) | Phase 0 | Phase 6 | 2026-07-29 | PENDING | — |
| .pre-commit-config.yaml content verification (D8) | Phase 0 | Phase 7 | 2026-07-29 | PENDING | — |
| A-0010 detail sub-audit (CriticalRush 9.1 GB Mac breakdown) | Phase 0 | Phase 1 (or later) | 2026-07-29 | PARTIAL | Phase 1 §7.2 confirmed size without action; ESCAPED unless Phase 9 wants to force |
| Dead-code sweep (vulture + ruff F401/F811/F841/ARG) | Phase 1 | Phase 7 | 2026-07-29 | PENDING | — |
| Duplication (jscpd, copydetect, clone detection) | Phase 1 | Phase 8 | 2026-07-29 | PENDING | — |
| Duplicate/near-duplicate config sweep | Phase 1 | Phase 2 | 2026-07-29 | LANDED | PHASE_2_config.md §7 |
| deptry per-member declared-vs-imported diff | Phase 1 | Phase 7 | 2026-07-29 | PENDING | — |
| npm audit / dashboard lockfile investigation (ex-A-0021) | Phase 1 | Phase 7 | 2026-07-29 | PENDING | — |
| BFG-report exposure audit (April 2026 purge, VPS pre-rewrite history) | Phase 1 | Phase 5 | 2026-07-29 | PENDING | — |
| Nested genlab-core/.git commit-set diff vs parent (A-0011 Phase 9 disposition prep) | Phase 1 | Phase 9 | 2026-07-29 | PENDING | — |
| Unused React components sweep (v1.2 reassigned orphan class) | Phase 1 | Phase 7 | 2026-07-29 | PENDING | — |
| Dead YAML/JSON schemas sweep (v1.2 reassigned orphan class) | Phase 1 | Phase 2 | 2026-07-29 | LANDED | PHASE_2_config.md §8 |
| A-0015 visual-backup restore consumer verification | Phase 1 | Phase 4 (grep pipeline for `.backups/visuals/` reads) | 2026-07-29 | RESOLVED-EARLY | Pre-Session-3: `backup_visual_assets.sh` line 121 has 14-day prune; A-0015 downgrades to tolerated steady-state ~3.5 GB |
| Missing-key safety empirical test on top 10 configs | Phase 2 | Phase 2 (this session) | 2026-07-29 | PARTIAL | Sampled but not full 10 — see PHASE_2_config.md §3 |
| Rule #22 gaming operator-agreement 53.4% follow-up | (CLAUDE.md context) | Phase 4 | 2026-07-29 | PENDING | — |
| Hardcoded prod DB password in shell scripts (`pg_backup.sh`, `verify_policy_block_l1.sh`) | Phase 2 | Phase 5 | 2026-07-29 | PENDING | rotation + secret-scanner sweep |
| KeyError: 0 in psycopg3 rows — `auto_accept_strategist_proposals.py`, `parse_testable_predictions.py` | Phase 2 | Phase 4 (silent-failure sweep) | 2026-07-29 | PENDING | — |
| BlackboxBrief 758 JSON files inventory | Phase 2 | Phase 1 (retro) or Phase 7 | 2026-07-29 | PENDING | — |
| pg_backup.sh 14-day retention prune not firing (A-0014 v2) | Phase 2 | Phase 4 (runtime why-not-firing) | 2026-07-29 | PENDING | — |
| Dual-config-layout (niches/<id>/config vs config) migration cost | Phase 2 | Phase 8 | 2026-07-29 | PENDING | — |
| snapshot verify_policy_block_l1.sh commit-or-delete decision | Pre-Session-3 | Phase 9 | 2026-07-29 | PENDING | snapshot in `.audit/snapshots/` (gitignored, redacted) |

## Phase 3 additions (2026-07-29 session 4)

| item | source | dest | added | status | landed_as |
|---|---|---|---|---|---|
| RLS empirical check (D11, D22) | Phase 0 | Phase 3 | 2026-07-29 | **LANDED as DELTA** | A-0032 headline S1 (role bypass) |
| SharePoint fallback deletion cost (D6) | Phase 0 | Phase 3 | 2026-07-29 | **LANDED** | A-0039 |
| 55-indexes claim (D13) | Phase 0 | Phase 3 | 2026-07-29 | **LANDED as DELTA** | A-0037 (actual 152) |
| Alembic head vs DB (D14) | Phase 0 | Phase 3 | 2026-07-29 | **PARTIAL** | A-0040 (VPS alembic CLI blocked) |
| PROMOTED_COLUMNS parity (D12) | Phase 0 | Phase 3→7 | 2026-07-29 | ESCALATED | needs GENLAB_SCHEMA_PIN_DSN env |
| Publishing gap for A-0001 (§9) | Phase 0/3 | Phase 3 | 2026-07-29 | **LANDED** | A-0034 (BB: 32 attempts, 0 published) |
| pg_stat_statements install | Phase 3 | Phase 4 | 2026-07-29 | PENDING | A-0035 (blocked query-cost analysis) |
| Alembic script_location config on VPS | Phase 3 | Phase 4 | 2026-07-29 | PENDING | A-0040 |
| App role switch from genlab → genlab_app | Phase 3 | Phase 5 or 9 | 2026-07-29 | PENDING | A-0032 (real RLS fix) |
| tenants/tenant_niches SaaS schema disposition | Phase 3 | Phase 8 | 2026-07-29 | PENDING | A-0038 (finish or delete) |
| affiliate_clicks / affiliate_revenue wire verify (0 rows contradicts monetization loop) | Phase 3 | Phase 4 | 2026-07-29 | PENDING | A-0038 |
| Pre-Session-4 GATE process failure (operator scrub did not happen) | Session 4 | Phase 9 methodology ledger | 2026-07-29 | PENDING | protocol amendment candidate |

## Phase 4 additions (2026-07-29 session 5)

| item | source | dest | added | status | landed_as |
|---|---|---|---|---|---|
| Silent-except full classification (132 sites) | Phase 4 | Phase 7 | 2026-07-29 | PENDING | A-0049 samples |
| Guard historical-firing verification | Phase 4 | Phase 7 | 2026-07-29 | PENDING | needs test coverage cross-check |
| Journal retention increase / per-unit stderr logging | Phase 4 | OPERATOR_ACTIONS | 2026-07-29 | RECOMMENDED | A-0050 |
| BB pipeline root-cause (needs journal or live fresh failure) | Phase 4 | OPERATOR_ACTIONS row #2 | 2026-07-29 | ESCALATED | A-0034 measured, root-cause dark |
| Intelligence engine default-when-unconfigured verification | Phase 4 | Phase 7 | 2026-07-29 | PENDING | A-0046 (4 flags missing) |
| A-0004 dangling systemd refs disposition | Phase 0/4 | Phase 9 | 2026-07-29 | PENDING | sequenced with A-0011 nested-.git cleanup |
| D9 verify-2026-07-22-fixes.timer removal | Phase 4 | Phase 9 | 2026-07-29 | LANDED | A-0045 (proposed_action=delete) |
| D17 intelligence engine env vars (4 missing) | Phase 4 | Phase 7 | 2026-07-29 | ESCALATED | A-0046 |
| D18 SKIP_APPROVAL_GATE grep verify | Phase 4 | Phase 4 | 2026-07-29 | LANDED | A-0047 accepted |
| D23 1-reel/day cap live verification | Phase 4 | Phase 7 or blocked | 2026-07-29 | PARTIAL | needs live cap-hit event; BB pipeline dark |
| D25 silent-failure sweep full pass | Phase 4 | Phase 7 | 2026-07-29 | PARTIAL | A-0049 samples |
| D28 rollout ladder position | Phase 4 | Phase 4 | 2026-07-29 | LANDED | A-0048 |

## Phase 5 additions (2026-07-29 session 6)

| item | source | dest | added | status | landed_as |
|---|---|---|---|---|---|
| A-0025 rotation verification | Phase 2 | Phase 5 | 2026-07-29 | **NOT DONE** | A-0053 (hash-verified live) |
| A-0032 fix design (genlab_app GRANTs) | Phase 3 | Phase 5 | 2026-07-29 | **LANDED** | A-0054 (fix sketch delivered to Phase 9) |
| BFG April 2026 purge cross-check | Phase 5 | Phase 5 | 2026-07-29 | **LANDED** | A-0055 (genlab_*** not in purge list) |
| VPS pre-BFG history recoverability | Phase 5 | Phase 5 | 2026-07-29 | **LANDED REFUTED** | A-0059 (BFG propagated to VPS); A-0008 gc ready |
| Nested genlab-core/.git secret sweep | Phase 5 | Phase 5 | 2026-07-29 | **LANDED CLEAN** | A-0061 (no genlab_*** in nested history) |
| Untracked prod cred file modes | Phase 5 | Phase 5 | 2026-07-29 | **LANDED** | A-0057 |
| Manifest exposure re-verify | Phase 5 | Phase 5 | 2026-07-29 | **CLEAR** | never committed |
| 42 apt upgradable packages security-critical filter | Phase 5 | OPERATOR_ACTIONS | 2026-07-29 | RECOMMENDED | `unattended-upgrades --dry-run` |
| Meta EAA token expiry monitoring in prod (D20 completion) | Phase 5 | Phase 7 | 2026-07-29 | PARTIAL | tokens length-consistent with EAA; expiry-alert check deferred |
| Full cross-host .env diff by hash-prefix name-join | Phase 5 | Phase 7 | 2026-07-29 | PENDING | 208 keys × 2 hosts |
| Static-file traversal on 173 dashboard routes | Phase 5 | Phase 7 | 2026-07-29 | PENDING | A-0060 depth |
| Native Postgres 18 on 5433 disposition | Phase 5 | Phase 4 revisit or Phase 9 | 2026-07-29 | PENDING | A-0058 |
| Dashboard per-niche access wrapping (pair with A-0032) | Phase 5 | Phase 9 | 2026-07-29 | PENDING | A-0060 |
| gitleaks/secret-scan pre-commit hook installation | Phase 5 | OPERATOR_ACTIONS | 2026-07-29 | RECOMMENDED | A-0055 (would have caught pg_backup DSN) |

## Runbook archive (2026-07-29 session 6 tail)

| item | source | dest | added | status | landed_as |
|---|---|---|---|---|---|
| Credential-rotation runbook execution (Row #1 + #3) | operator-supplied | operator (out-of-band) | 2026-07-29 | **RUNBOOK_ARCHIVED** | `.audit/RUNBOOK_credential_rotation.md` (scrubbed); Row #1 + #3 status updated with runbook link + Step-5 gates |
| Post-runbook audit verification (Step 5a/5b/5c gate results) | Phase 5 injected | next session or user-signal | 2026-07-29 | **AWAITING_OPERATOR** | `OPERATOR_ACTIONS.md` post-runbook verification checklist |
| A-0058 pre-flight decision (5432 vs 5433 disposition) | runbook pre-flight | operator | 2026-07-29 | **BLOCKS_RUNBOOK** | must be answered before Step 0 |

## Phase 6 additions (2026-07-29 session 7)

| item | source | dest | added | status | landed_as |
|---|---|---|---|---|---|
| A-0015 fire verification | Pre-Session-3 → Phase 6 | Phase 6 | 2026-07-29 | **LANDED REFUTED** | A-0062 (NOT FIRING; 421 files pruneable) |
| D21 PLATFORM_SPECS libx264 | Phase 0 | Phase 6 | 2026-07-29 | **LANDED DELTA** | A-0063 (CRF 18 vs 20; preset medium vs fast) |
| A-0042 network-dashboard cross-check | Phase 3/4 | Phase 6 | 2026-07-29 | **ESCALATED** | A-0071 → OPERATOR_ACTIONS row #10 |
| A-0042 wire trace (INSERT INTO affiliate_clicks) | Phase 6 | Phase 7 | 2026-07-29 | PARTIAL | A-0068 sample-only |
| Fallback-router historical verification | Phase 6 §3 | Phase 7 | 2026-07-29 | PENDING | not covered this session |
| Meta API quota + rate-limit posture | Phase 6 §5 | Phase 7 | 2026-07-29 | PENDING | context-tight this session |
| pg_stat_statements install | A-0035 → Phase 6 | OPERATOR_ACTIONS | 2026-07-29 | **RECOMMENDED** | A-0070 |
| Redis maxmemory ceiling | Phase 6 | OPERATOR_ACTIONS | 2026-07-29 | **RECOMMENDED** | A-0064 (30-sec fix) |
| Anthropic proactive balance-poll | Phase 6 | Phase 9 code fix | 2026-07-29 | PENDING | A-0065 |
| Per-niche YT quota attribution wiring | Phase 6 | Phase 8 | 2026-07-29 | PENDING | A-0066 SaaS blocker |
| A-0062 + A-0026 batched root-cause investigation (both prune scripts wired-not-firing) | Phase 6 | Phase 4 revisit OR Phase 9 | 2026-07-29 | PENDING | class-of-bug shared |

## Phase 7 additions (2026-07-29 session 8)

| item | source | dest | added | status | landed_as |
|---|---|---|---|---|---|
| D8 pre-commit content verification | Phase 5 → Phase 7 | Phase 7 | 2026-07-29 | **LANDED CONFIRMED** | A-0076 (config good) |
| A-0055 gitleaks presence re-check | Phase 5 correction | Phase 7 | 2026-07-29 | **LANDED CORRECTED** | A-0072 (hook exists but not enforced) |
| A-0030 BB 758 JSONs re-check | Phase 2 correction | Phase 7 | 2026-07-29 | **LANDED REFUTED** | A-0078 (manifest noise) |
| A-0068 full-repo wire trace | Phase 6 → Phase 7 | Phase 7 | 2026-07-29 | **LANDED CONFIRMED** | A-0073 (situation 1) |
| Test suite runnability | Phase 7 §1 | Phase 9 fix queue | 2026-07-29 | **BLOCKED** | A-0074 (77 collection errors) |
| Coverage per member (§3) | Phase 7 §3 | Phase 9 | 2026-07-29 | BLOCKED_BY_A-0074 | needs suite fix first |
| Dead-code full triage (98 hits, needs per-hit runtime) | Phase 7 → Phase 9 | Phase 9 | 2026-07-29 | PENDING | A-0075 |
| Full-workspace dead-code (all 7 members) | Phase 7 | Phase 9 | 2026-07-29 | PENDING | genlab-core only surveyed |
| Silent-except full classification (132 sites) | Phase 4 → Phase 7 | Phase 9 | 2026-07-29 | ESCAPED | A-0049 samples only |
| Guard historical-firing verification | Phase 4 → Phase 7 | Phase 9 or blocked by A-0050 | 2026-07-29 | ESCAPED | needs journal that was rotated |
| Meta EAA expiry monitoring (D20 completion) | Phase 5 → Phase 7 | Phase 9 | 2026-07-29 | ESCAPED | requires token-refresh code trace |
| Full cross-host .env diff by hash-prefix name-join | Phase 5 → Phase 7 | Phase 9 | 2026-07-29 | ESCAPED | 208 keys × 2 hosts |
| Static-file traversal on 173 dashboard routes | Phase 5 → Phase 7 | Phase 9 | 2026-07-29 | ESCAPED | per-route analysis |
| Intelligence engine default-when-unconfigured (4 missing flags) | Phase 4 → Phase 7 | Phase 9 | 2026-07-29 | ESCAPED | A-0046 |
| PROMOTED_COLUMNS parity D12 | Phase 3 → Phase 7 | Phase 9 | 2026-07-29 | ESCAPED | needs GENLAB_SCHEMA_PIN_DSN |
| deptry per-member declared-vs-imported | Phase 1 → Phase 7 | Phase 9 tooling install | 2026-07-29 | PENDING | not installed |
| Dashboard lockfile question (ex-A-0021) | Phase 1 → Phase 7 | Phase 9 | 2026-07-29 | PENDING | not chased |
| Unused React components sweep | Phase 1 → Phase 7 | Phase 9 | 2026-07-29 | PENDING | requires TS-aware tool |
| Property-tests (`.hypothesis/`) CI-vs-local | Phase 7 §4 | Phase 9 | 2026-07-29 | PENDING | ties to A-0074 fix |
| CI workflow analysis (what merges broken) | Phase 7 §7 | Phase 9 | 2026-07-29 | PENDING | A-0079 depth |
| pre-commit install enforcement (A-0072 fix) | Phase 7 | OPERATOR_ACTIONS | 2026-07-29 | **RECOMMENDED** | add to onboarding + CI |

## Phase 8 additions (2026-07-29 session 9)

| item | source | dest | added | status | landed_as |
|---|---|---|---|---|---|
| A-0073 re-test with backend patterns (Pre-Session-9) | addendum | Phase 8 | 2026-07-29 | **LANDED REFUTED** | A-0073 corrected in-place; downgraded S1→S2 situation 2 |
| A-0062 ⊕ A-0069 merge (Pre-Session-9) | addendum | Phase 8 | 2026-07-29 | **LANDED MERGED** | A-0062 supersedes A-0015 + A-0069; timeline conditional |
| SaaS multi-tenant honest tally (Phase 8 §3, heavy weight) | addendum | Phase 8 | 2026-07-29 | **LANDED** | A-0080 |
| Env pinning one fix (Phase 8 §5) | v1.4 | Phase 8 | 2026-07-29 | **LANDED** | A-0081 |
| Git-blind writers class architectural fix | v1.4 | Phase 8 | 2026-07-29 | **LANDED** | A-0082 |
| Line-exists-doesn't-fire META finding | addendum | Phase 8 | 2026-07-29 | **LANDED** | A-0083 |
| Clone detection full pass (jscpd/copydetect) | Phase 1 → Phase 8 | post-audit backlog | 2026-07-29 | DEFERRED | 0/5 cross-imports deprioritizes |
| Class-of-bug taxonomy | Phase 8 emergent | Phase 9 methodology ledger | 2026-07-29 | **LANDED** | A-0088 |
