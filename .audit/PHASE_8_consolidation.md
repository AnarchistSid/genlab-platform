# Phase 8 — Consolidation and upgrade candidates (final discovery phase)

**Audit run:** A (2026-07-29)
**Session:** 9 (Pre-Session-9 corrections + Phase 8)
**Status:** COMPLETE — 9 findings carried (A-0080…A-0088) under 12-cap
**§0.5 self-scan:** PASSED (run LAST per §0.10)
**§0.10 shell quiescence:** verified before summary write
**Related artifacts:** all prior phase artifacts, `OPERATOR_ACTIONS.md`, `DEFERRALS.md` (153+ lines)

**Prior-artifact posture (§0.8) — Pre-Session-9 corrections applied:**
- **A-0073 REFUTED** (in place, `PHASE_7_findings.yaml`): writer, invoker, and emission all exist. `link_tracker.py:144: pg.create("affiliate_clicks", record)` — backend abstraction my greps missed. Downgraded S1 → S2, situation 2 (wired but no conversions). `A-0068 → A-0073` supersession chain: `A-0068 S2/medium → A-0073 (was S1/high) → A-0073 (now S2/high)`.
- **A-0062 ⊕ A-0069 merged** (in place, `PHASE_6_findings.yaml`): mechanism (prune not firing, S2) + timeline (~113 days at low load, ~48 when BB recovers) collapsed to one finding. Sequencing rule: fix before OPERATOR_ACTIONS row #2 (BB recovery), not before next week.
- **A-0043** (durable-error retraction) and **A-0054 + runbook Step 5b** (GRANTs ≠ isolation-fires) carried forward CLOSED — Phase 9 verifies gate results, does not re-derive.

---

## 0. Pre-Session-9 gate + rotation status

- **Scrub held:** grep exit 1 across `.audit/`.
- **Rotation status:** still PENDING; hash still `c7b89bffef3a`. Runbook not yet executed.
- Shell quiescence verified.

## 1. SaaS multi-tenant layer — the honest tally (Phase 8 §3, heaviest weight)

The addendum highlighted this as the single most decision-relevant Phase 8 output. Converged findings:
- **A-0032 / A-0054** — role architecture: `genlab_app` role exists, GRANTs on all 45 tables. Fix ready.
- **A-0038** — `tenants` (64 KB, 0 rows), `tenant_niches` (48 KB, 0 rows). Schema drafted but no code populates them.
- **A-0066** — YT quota tracker has `per_niche` schema field but populates only "all" bucket.
- **A-0060** — Dashboard uses single-shared-secret HTTP Basic Auth, no RBAC, no tenant-aware access.
- **A-0028** — Dual `publishing.yaml` layout — 6th channel gets 2 conflicting templates.
- **A-0073 (corrected)** — affiliate revenue tracking wired at code level; 0 rows = wired-but-no-conversions.

**The honest tally for a 6th channel / first external tenant:**

| Layer | Built | Stub | Missing | Notes |
|---|---|---|---|---|
| DB tenant tables | ✓ schema | 0 rows | writer code | `tenants`/`tenant_niches` designed, nothing populates them |
| Role isolation (`genlab_app`) | ✓ | needs DSN swap | app doesn't `SET LOCAL app.niche_id` per request (runbook 5b) | GRANTs ready, session-context wiring maybe not |
| Per-niche credential prefix | ✓ | | | works today (A-0056/D26 confirmed) |
| Per-niche config layout | ⚠️ dual | | | A-0028 blocker — pick one before 6th channel |
| Dashboard tenant awareness | | ✗ | RBAC, per-route niche filter | A-0060 — everything is cross-tenant admin |
| API quota per-niche | | ⚠️ schema | actual populate | A-0066 — YT tracker has field, doesn't use it |
| Affiliate/revenue attribution | ✓ code | 0 conversions | probably | A-0073 corrected — wired end-to-end |
| Audit-events per-tenant | ✓ | | | via `SET LOCAL` — same as role isolation |

**Verdict:** the multi-tenant story is **half-built**. The DB schema and role architecture are ready or near-ready. The application layer (dashboard RBAC, per-request session-context, per-niche quota) has 3-4 unfinished pieces. **A 6th channel would work as an operator-owned niche today** (using per-niche prefix + running the same operator's dashboard). **An external tenant is not deployable** — the dashboard has no tenant boundary, and the runbook 5b outcome will decide whether the role-switch alone establishes DB-level isolation.

→ **A-0080** — SaaS-readiness tally (S1 documentation of the gap).

## 2. Env pinning — one root cause, one fix (Phase 8 §5)

A-0005 (Python 3.14 mac vs 3.12 vps), A-0006 (FFmpeg 8.1 vs 6.1), A-0007 (Postgres 14 vs 18) collapse to one root cause: **no dev/prod environment pinning**. Test outcomes don't transfer (A-0074 collection errors likely include Python version drift). Media renders can produce different output. SQL/DDL that lints on Mac 14 may misbehave on 18.

**Proposed one fix (packaged for Phase 9):**
- `.python-version` file at repo root pinning `3.12` (matches prod)
- `uv` respects `.python-version` on `uv run` — enforces dev-side alignment
- FFmpeg pin: either downgrade Mac to `brew install ffmpeg@6` OR (better) `docker compose` a render container using same base image as VPS
- Postgres: docker-compose Postgres 18 for local dev; delete/ignore the Homebrew 14
- **Deliverable in one Phase 9 FIX file: FIX_env-pinning.md** covering all three

→ **A-0081** — env-pinning class fix, one entry per spec.

## 3. Git-blind in-place config writers — class fix (Phase 8, from A-0012+A-0020+A-0023)

Three findings, one class: components that write git-tracked YAML files in place, then git shows only the endpoints of a series of overwrites.

- **A-0012** — 3 channels' `publishing.yaml` `rollout_pct 0.0 → 1.0` uncommitted
- **A-0020** — `cuelinks_campaigns.yaml` regenerated weekly by `genlab-cuelinks-campaign-refresh.timer`
- **A-0023** — writer identified: `scripts/auto_ramp_auto2.py::write_rollout_pct`

**Proposed architectural fix (one, not three):**
- Split each affected file into **schema/skeleton (in git) + generated state (NOT in git, gitignored)**. Reads merge the two at boot.
- Alternative: **commit-back bot** that opens a PR when auto_ramp writes; git records the ladder history properly.
- Alternative (least effort): **prominent "AUTO-GENERATED — actual truth lives on VPS at /opt/genlab/…" header + operator-facing `gen-status` command that prints the current on-disk truth**.
- Recommendation: option 1 (schema-in-git + state-out-of-git). Effort M per file, ~2 files = one week.

→ **A-0082** — git-blind-writers class fix.

## 4. Line-exists-doesn't-fire — meta-finding on how the system verifies its own guardrails (from A-0026+A-0062+A-0072)

**Three independent instances now** of "configured but not enforced":
- **A-0026** — `pg_backup.sh:29` `find -mtime +14 -delete` not firing (20 files retained)
- **A-0062** — `backup_visual_assets.sh:121` same predicate not firing (33-day-old files, 421 pruneable)
- **A-0072** — `.pre-commit-config.yaml` gitleaks v8.24.3 configured but 2026-07-22 commit added 3× `PGPASSWORD=genlab_***` anyway (hook not enforced, either `pre-commit install` not run or `--no-verify`)

**Meta-finding: GenLab has a systemic gap in verifying its own guardrails fire.** Each of the three would be caught by a "test the mechanism itself" pass — e.g. a periodic sentinel check ("if there are files > N days old in .backups/visuals/, alert") separate from the retention script. The pattern that catches all three: **for every guard, add a check that the guard has fired at least once in expected-fire-window**.

**Proposed class-fix (one, spanning the three):**
- For prunes: add a `find … -mtime +N | wc -l` sentinel alert that fires when the count is non-zero after expected prune interval.
- For hooks: add a CI-side `pre-commit run --all-files` that fails the PR if any pre-commit hook would trigger. Doesn't rely on developer's `pre-commit install`.
- For future guards (auto-approver kill switch, disk-quota monitor, token expiry): every guard adds a paired "verify-fired" alert.

→ **A-0083** — line-exists-doesn't-fire meta-finding + class-fix.

## 5. Module consolidation — thin because cross-channel imports = 0/5

Phase 1 established: cross-channel imports 0/5, 3-layer architecture holds. Consolidation targets aren't tangles — they're **parallel re-implementations** (each channel writing its own version of the same logic per Layer 2 strategy pattern). But the strategy pattern is by design: it's the pluggable interface CLAUDE.md describes.

**Real consolidation candidates:**
- FB_APP_SECRET / META_APP_SECRET duplicate (A-0056) — one env var, one fix
- Two Postgres instances (A-0058) — likely 5433 is dead, one instance to remove
- SharePoint fallback (A-0039) — 4 legacy code refs to purge alongside CLAUDE.md text update
- `.env.bak.*` sprawl (A-0016, A-0052) — 14 files across hosts, delete post-rotation

Clone detection (`jscpd`/`copydetect`) not run this session — context tight. Deferred as low-priority (0/5 cross-imports means no hidden coupling).

→ **A-0084** — actionable consolidation list.

## 6. Runtime/library upgrades (Phase 8 §6)

Beyond A-0081's env-pinning, the upgrade currency question:
- **Python 3.12** (prod) — LTS through 2028. Not urgent.
- **Node 22** (prod) — LTS through 2027. Not urgent.
- **Postgres 18** (prod) — released 2025, current. Good.
- **FFmpeg 6.1** (prod, Ubuntu 24.04 stock) — 2023 release. Not deprecated but 2 major versions behind. Upgrade optional; pin instead.
- **Anthropic SDK 0.102.0** (Mac uv tree) — current at inspection.
- **detoxify + torch CPU wheel** — CLAUDE.md rationale (~200 MB vs ~8 GB CUDA). Correct choice.

No upgrade within 6 months of deprecation ⇒ no S1 finding here.

→ **A-0085** — upgrade currency summary (S3 informational).

## 7. Deletion candidates — ranked (Phase 8 §7)

Ordered by (verified-removable × cumulative bytes ÷ effort). Only items with §0.3 both-evidence carry S1/S2; static-only items cap S3.

| Rank | Item | Est. bytes/LOC | Confidence | Blocked by |
|---|---|---:|---|---|
| 1 | 7 world-readable `.env.bak.*` files (A-0052) | ~100 KB | high | post-rotation |
| 2 | 14 total `.env.bak.*` after rotation (A-0016) | ~180 KB | high | post-rotation |
| 3 | 2 `docker-compose*.bak-2026-07-24-*` on VPS (A-0017) | ~2 KB | high | none |
| 4 | 421 stale files in `.backups/visuals/` (A-0062) | ~2.5 GB | high | prune-fix (A-0062) |
| 5 | 20-14 = 13 extra `.sql.gz` in `.backups/` (A-0026) | ~200 MB | high | prune-fix (A-0026) |
| 6 | `verify-2026-07-22-fixes.timer` + `.service` (A-0045) | trivial | high | none |
| 7 | 2 `.pre-2026-07-05` orphaned systemd unit files (A-0004) | trivial | high | none |
| 8 | Nested `genlab-core/.git` (A-0011) | 12 MB | high | commit-set diff first |
| 9 | libx265 dead branch in ffmpeg.py (A-0063) | ~30 LOC | high | pin the invariant first |
| 10 | 4 SharePoint legacy code refs (A-0039) | ~40 LOC | high | none |
| 11 | 6 ruff-auto-fixable dead imports (A-0075 subset) | ~6 LOC | high | one command |
| 12 | 92 remaining ruff hits (A-0075) | ~92 LOC | S3 STATIC_ONLY | per-hit runtime pass |
| 13 | 15 BB `docs/plans/`+`docs/archive/` md (A-0018) | ~14 K LOC | high | consolidate not delete |

**Total verified-removable disk: ~2.7 GB** (mostly items 4 + 5, blocked by their prune-fix). LOC deletable in Phase 9 with no blockers: ~70 lines.

→ **A-0086** — deletion queue (with sequencing constraints).

## 8. Findings carried (9 of 12 cap)

Ranking: severity × confidence ÷ effort; S1 first.

| id | S | title |
|---|---|---|
| **A-0080** | S1 | SaaS multi-tenant layer half-built — DB/role architecture ready, dashboard/quota/per-request session-context unfinished. 6th operator-owned channel deployable; external tenant NOT deployable |
| **A-0081** | S2 | Env-pinning class fix — A-0005+A-0006+A-0007 collapse. One `FIX_env-pinning.md` covering `.python-version`, container-parity render, docker-compose Postgres |
| **A-0082** | S2 | Git-blind in-place config writers class fix (A-0012+A-0020+A-0023) — schema-in-git + state-out-of-git for auto_ramp_auto2.py + cuelinks_campaigns.yaml regenerator |
| **A-0083** | S2 | META: line-exists-doesn't-fire — 3 independent instances (A-0026 pg_backup prune, A-0062 visual-backup prune, A-0072 gitleaks hook). Class-fix: pair every guard with a "verify-fired" alert |
| A-0084 | S2 | Actionable consolidation list (short) — FB/META dup dedup, 5433 native Postgres retire, SharePoint refs purge, `.env.bak.*` cleanup post-rotation |
| A-0085 | S3 | Upgrade currency summary — no runtime/library within 6 months of deprecation; no S1 upgrade finding |
| A-0086 | S3 | Deletion queue ranked — ~2.7 GB verified-removable (mostly blocked by A-0062 prune fix); ~70 LOC deletable with no blockers |
| A-0087 | S3 | Positive-architecture — cross-channel imports 0/5, per-niche prefix architecture holds, `genlab_app` role designed. The 3-layer discipline is honored where it was built |
| A-0088 | S3 | Class-of-bug taxonomy this run — 5 named recurring classes across 79 findings: (i) durable-error-file-stale-state (A-0043 lesson), (ii) grep-pattern-doesnt-match-codebase (A-0073 corr, A-0078), (iii) git-blind writers (A-0082), (iv) line-exists-doesn't-fire (A-0083), (v) hook-configured-but-not-enforced (A-0072 sub-case of iv). Phase 9 methodology ledger inherits this |

## 9. Doc-delta closures

No new Dxx closures this phase — all remaining Dxx items are in DEFERRALS with Phase 9 destination.

## 10. Deferrals added this session

- Clone detection full pass → post-audit backlog (unlikely to change findings given 0/5 cross-imports)
- Alembic head verification (A-0040) → Phase 9 fix queue
- pg_stat_statements install → OPERATOR_ACTIONS
- Redis maxmemory ceiling → OPERATOR_ACTIONS  
- Anthropic proactive balance-poll → Phase 9 code fix
- Per-niche YT quota attribution → Phase 9 SaaS-completion track

## 11. Methodology issues this phase

1. **A-0073 was my worst methodology error of the run** — hardened at S1 despite my own note in Phase 7 §12 flagging "I searched for the finding I expected" as a Phase 5 error. The addendum's re-test caught it. **Class of bug: recognizing a class of methodology error is not the same as not repeating it.** The write-up in A-0088 has this as taxonomy item (ii).
2. **A-0062 ⊕ A-0069 filing them separately was a register-alarmism failure** — S1 mechanism + S3 timeline in one truth read more alarming than the evidence. Merged.
3. **Phase 8 skipped clone detection** — context tight; the 0/5 cross-imports finding upstream deprioritized the deeper pass. Documented as a defer.

---

## 12. §Phase-9 SPLIT DECISION — surface for user to confirm before v1.5 is written

Per the addendum, the audit-side numbers make the case:

- **~79 findings** created across Phases 0-8 (~15 retracted/corrected, ~64 net carried)
- **DEFERRALS.md is 153+ lines** — 60+ PENDING + 9 ESCAPED + ~20 LANDED/RESOLVED
- Phase 7 alone escalated 10 of 14 inherited items to Phase 9
- Phase 8 escalates ~6 more

**Phase 9 as currently written (v1.4) is expected to both (a) triage ~64 findings AND (b) execute ~60+ deferred investigations. That's not one session.**

The addendum offers three options:
1. **9A/9B split** (recommended): 9A triages the ~64 findings → REGISTER + SUMMARY + fixes; 9B dispositions the 60+ ledger entries — most as `DEFER + trigger`, some promoted to register, some explicitly de-scoped as never-audit-work.
2. **Single Phase 9, findings-only**: triage the ~64 findings; ledger becomes one bulk "deferred to post-audit backlog" line.
3. **Extend the audit**: add work-sessions for A-0074 (test suite fix), SaaS-schema tally completion, affiliate-emission URL check before Phase 9.

**Awaiting operator decision.** I will not run Phase 9 until confirmed. Once confirmed, v1.5 gets written to that shape and Phase 9 executes.
