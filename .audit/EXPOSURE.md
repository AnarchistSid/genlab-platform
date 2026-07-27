# .audit/ Publication — Exposure Inventory & Options

**Confirmed 2026-07-24 23:50 IST.** Repo `visibility: PUBLIC`, `isPrivate: false`. Audit pushed at `2026-07-24 18:05Z` (commit `3b7b1d08`). Exposure window at time of writing: **~5 hours 45 minutes**. Traffic 14d: 22,550 clones / 448 uniques (CI-heavy but 448 is high); 69 views / 50 uniques. Fork `TBROS68/genlab-platform` created 2026-07-09, `pushedAt: null` — **not synced since creation, does not contain the audit.**

## Disclosure inventory

| Class | Example locations | Live risk |
|---|---|---|
| **Unpatched findings named at path:line** | `findings.jsonl` F-0024 (RCE-chain description with COPY TO PROGRAM), F-0065 (RLS fail-open + `tenant_context.py:5` bypass site count), CVE package names in `SCORECARD.md:43` (`yt-dlp`, `aiohttp`, `pillow`, `cryptography`) | **exploitable if unpatched — F-0024 external reachability CLOSED in Phase 8.2, but F-0045 (10-char pw) + F-0048 (BYPASSRLS) + F-0065 (fail-open policies) still live** |
| Host / topology | 95 refs to `46.224.237.56` / `genlab-prod` / `/opt/genlab` across `phase*/`, `PHASE*.md`, `findings.jsonl` | reconnaissance / targeting |
| Auth posture | 29 refs to `rolsuper`, `rolbypassrls`, `pg_hba`, `scram-sha-256`, "10-char password" in `PHASE2.5.md`, `PHASE3A.md`, `SCORECARD.md` | targeting; specifies exact defense posture |
| Self-hosted runner | 5 files (`SCORECARD.md`, `PHASE2.5.md`, `BACKLOG.md`, `findings.jsonl`) reference F-0032 (public repo + self-hosted runner on prod host) | supply-chain attack vector |
| Token hashes | `phase26/pre_bfg_hashes.txt` — 29 SHA256 lines, no values | offline verification if a token format is known |
| Business data | Aggregate cost + revenue prose (`$13.61/30d`, `$0.063/run`, revenue ~$0), no per-account detail | commercial |
| Methodology + scorecard | `SCORECARD.md`, `phase7/DECISION.md`, `phase*/*.md` narrative | **none — this is the publishable part** |
| **Live secret values** | zero shape matches for `sk-`, `ghp_`, `AIza`, `glpat-`, `hooks.slack.com/services/T*`, or `postgres://user:pass@` | none — no live tokens exposed |

## Three costed options (execute none)

### Option 1 — make the repo private (1 command)

```bash
gh repo edit --visibility private --accept-visibility-change-consequences
```
**Costs:** breaks GitHub Pages (if configured), external CI referencing raw URLs, unauthenticated `git clone` from CI runners, dependency-scanning services reading public repos. `TBROS68/genlab-platform` fork remains publicly readable (their unsynced snapshot pre-dates the audit — safe). **Forgoes:** star/fork visibility, ability for outside contributors to open PRs.

**Does NOT remove:** history from any cloner who pulled in the ~6-hour public window. Best guess: 20-100 fresh clones based on daily rate.

**Prerequisite:** none. Reversible.

### Option 2 — publish redacted subset (history rewrite)

Keep `SCORECARD.md`, `DECISION.md`, `BACKLOG.md`, `OPERATOR_TASKS.md`, `EXPOSURE.md`. Remove `findings.jsonl`, `evidence/`, `phase2*/`, `phase3*/`, `phase7*/`, `phase8/`, `phase26/`, `phase27/`. Precedent: BFG ran on this repo 2026-04-03 for a similar reason.

**Costs:** ~2h operator time. Force-push required. `TBROS68` fork retains original history (they can't sync); the audit still exists in their timeline if they fetch commit `3b7b1d08` by hash. Downstream tooling that indexed on specific commit SHAs breaks. Local dev clones need `git fetch --force`.

**Forgoes:** traceability of the finding→evidence chain that made the scorecard defensible.

**Prerequisite:** decide what "publishable methodology" means; the redaction list above is a starting point.

### Option 3 — accept deliberately, patch first

Patch F-0065 (Block 2, ~2 sessions) + exploitable CVEs (Block 3, 1 session). Then unpatched-finding disclosure becomes historical. Anthropic auto-reload is a prerequisite for Block work (`BACKLOG.md` Block 0 item 1).

**Costs:** ~1 week operator + engineering time. Nothing prevents accidental disclosure during that week.

**Forgoes:** nothing structural — this is the "correct" outcome, just slow.

**Prerequisite:** Block 0 item 1 landed; Blocks 2 + 3 executed.

## Two corrections (D.1, D.2)

**D.1 Compose path** — corrected in `OPERATOR_TASKS.md` task 2 in place; new **F-0078 MEDIUM** filed. Stale `/opt/genlab/docker-compose.yml` still on VPS (last touched Phase 8.2 rollback); zero runtime risk (nothing runs it) but exposes `0.0.0.0:5432/6379/5151` if anyone `docker compose up`s in that dir. Fix: delete or `mv /opt/genlab/legacy/`.

**D.2 Phase 4 gap** — filed **F-0079 MEDIUM**. Phase 2.6 answered *code* drift (SHA match) but never covered *deployment topology*. Two topology surprises hit within one session (Phase 8.1 wrong compose file, Phase 8.2 orphan volume). A narrow one-session Phase 4 (services + compose projects + volumes + port bindings) is now a prerequisite for Block 2 DSN cutover — a topology miscount there breaks more than a port bind attempt did.

**Findings:** 79 (8C / 24H / 30M / 14I / 3L). New: F-0077 (this disclosure), F-0078 (stale compose), F-0079 (Phase 4 gap). No commit, no push, no repo change.
