# PHASE 2.6 — Verification Gate

## Four leading lines

1. **Local HEAD `edf91209` = VPS HEAD `edf91209`. Commit distance 0/0.** BUT
   VPS has 4 tracked files modified + 15 untracked files. Notable: `genlab-
   core/config/cuelinks_campaigns.yaml` diverges by **918 insertions / 26
   deletions**; per-niche `publishing.yaml` for gaming + movies + anime all
   modified. Python source is in sync — Phase 3 not blocked by SHA drift, but
   YAML/config drift is HIGH (F-0040).
2. **Symmetric grep = 130/130, diff = 0.** Phase 2.5's "60 vars only on VPS"
   was measurement error. **The 20 phantom flags collapse to 2 (F-0033
   unchanged, F-0016's "0 phantom" retracted):** the two real phantoms are
   `GENLAB_CONTENT_GAP_REMEDIATOR_ENABLED` +
   `GENLAB_ENGAGEMENT_DRAINER_ENABLED`. Authoritative AST detector:
   **local=vps=125 static + 7 dynamic prefixes** (Phase 1's 70 was under-count,
   Phase 2.5's 130 was over-count).
3. **5432 is STILL OPEN** (`nc -zv 46.224.237.56 5432` = succeeds; exposure
   window ≥ 60 days since container `Created 2026-05-26T10:28:08Z`). **DB
   password is NOT among F-0009's 29 pre-BFG tokens** (password length 10;
   blob's tokens ≥ 35 chars; widened 6-20 char sweep also finds no match).
   The 10-char password + world-open pg_hba + public 5432 combined = **F-0031
   amplified**. Repair not performed — needs operator authorization on prod
   docker-compose.
4. **`SILENT_IN_PROD` = 107.** Log level = INFO (`settings.py:375` default,
   confirmed on VPS by grep for DEBUG entries → 0 in recent journal). 107
   `except Exception:` sites use `logger.debug` + swallow, invisible in prod.
   **10 of 15 sampled hits are on the publish path** (`push_to_backlog.py` ×6,
   `feedback_registration.py`, `transcode.py`, `video_gate.py`,
   `relevance_gate.py`). Phase 2.5's "SILENT=0" claim was wrong — the
   classification didn't account for log level.

## Partition-verified 4-bucket env table

Rebuilt from AST-authoritative reads + VPS `.env` names (assertion PASSES,
total=union):

| Bucket | Count |
|---|--:|
| SET_AND_READ (VPS set, code reads it) | 55 |
| **SET_UNREAD** (VPS set, code never reads — real phantoms) | **2** |
| **UNSET_AND_READ** (code reads, VPS unset → default fires) | **78** |
| UNSET_UNREAD (theoretical only — empty) | 0 |
| **TOTAL** | 135 |

**Of the 78 UNSET_AND_READ, 14 end in `_ENABLED`; 6 ML/learning flags default
OFF:** `BEDROCK_FINETUNE`, `LINUCB_STOCHASTIC` (unsuffixed — per-niche variants
ARE set), `OPTIMAL_TIME_BANDIT`, `THOMPSON_PROPENSITY`, `TOP_CREATOR_PRIORS`,
`TREND_ANTICIPATION`. Direct input to Phase 6 INTELLIGENCE score. **F-0041**.

## Scope reconciliation — the 110 vs 889 exception count

- **Phase 2's 110**: `grep 'except Exception' + 2-line window + regex for
  pass/return None` → reproduced exactly.
- **Phase 2.5's 889**: `grep 'except Exception'` (all handlers) — a **different
  pattern**, not the same one.
- Both are correct counts of what they measured; the audit narrated them as if
  comparable.

Silent-fail decomposition of the 899 (`except Exception` universe):

| Bucket | Count |
|---|--:|
| INTENTIONAL (comment declares fail-open) | 146 |
| LOSSY_VISIBLE (WARNING/ERROR/EXCEPTION log — always visible) | 395 |
| **SILENT_IN_PROD** (logger.debug only, invisible at INFO) | **107** |
| Neither category (uncommented, no log — swept as INTENTIONAL if 4-line window has any hint) | rest |

**F-0028 upgraded from INFO → HIGH.** New title: 107 silent-in-prod sites,
~2/3 on publish path.

## Section 5 corrections

- **F-0032 mitigation sentence rewritten** — GitHub's approval gate applies to
  first-time contributors only. One merged typo-fix promotes an attacker to
  returning. HIGH severity preserved; mitigation copy no longer overstates.
- **F-0037 action → `test`** (chaos-injection in staging). No staging env
  detected in repo (`grep -rn STAGING`); provisioning one is prerequisite.
  Does NOT `delete` the tiers on strength of 30-day-unused count alone.

## Phase 3 gate — CLEARED

Line 1 answered: HEAD is in sync. Python source between local + VPS is
identical. YAML config drift is a HIGH finding but does not block Phase 3
(config content is a Phase 3/5 concern, not a Phase 3 blocker). **Phase 3 can
proceed**.

## Blindness list

- **DB password strength beyond length-10** — not tested (would require
  hashcat / online bruteforce, inappropriate without operator sign-off).
- **GH Actions "require approval for outside collaborators"** setting — still
  not API-queryable.
- **Cascade tiers** — 0 fires in 30d; verified by grep. To resolve: need
  staging env for chaos test.
- **107 SILENT_IN_PROD sites — per-site read** to distinguish "actually
  silent-fail-open" from "would fire WARNING but caller currently masks with
  DEBUG" — not done. Sample 15 shown; full triage is per-site.

**All shells exited before summary** (`ps aux | grep pytest = 0`). Discipline
maintained. No secret values written to `.audit/` (hash-only compare).

## Corrections to findings.jsonl (supersessions collapsed)

Three findings were rewritten in-place rather than duplicated as new rows:

| Finding | Before | After | Reason |
|---|---|---|---|
| F-0027 | critical | **low** | Redis correctly bound; 5432 is single-port misconfig |
| F-0028 | medium (SILENT=0) | **high** | Missed log-level; SILENT_IN_PROD=107 (see F-0039) |
| F-0032 | high (approval gate mitigation) | **high** (copy corrected) | Approval is first-time only |

New findings this phase: **F-0039, F-0040, F-0041**. Final tally: 41 findings
(3 critical / 14 high / 15 medium / 8 info / 1 low).
