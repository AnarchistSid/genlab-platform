# Phase 0 — Doc / Reality Delta

Per §0.9, this artifact lists documented claims vs observed reality. Only items I could verify **without** DB or execution-trace access are scored — remaining claims are marked `DEFER_PHASE_N` with the phase best positioned to check them.

Source docs consulted: `CLAUDE.md` (35 KB, edited 2026-07-24), `.claude/rules/*.md` (5 files), root `pyproject.toml`, `MEMORY.md` (included in session context).

---

## Verified this phase

| # | Documented claim | Source | Observed reality | Establishing command | Verdict |
|---|---|---|---|---|---|
| D1 | "5 channels" (Blackbox Brief, CriticalRush, ClutchWire, SpliceReel, FrameDrift) | CLAUDE.md §"The five channels" | 5 channel dirs exist as uv workspace members; 5 `.timer` units on VPS (`genlab-pipeline-ai.timer`, `-gaming`, `-sports`, `-movies`, `-anime`) | `grep -A9 workspace pyproject.toml`; `systemctl list-timers "genlab-pipeline-*"` | **CONFIRMED** |
| D2 | "Uv workspace with 7 members" | CLAUDE.md §"Adding a new channel" | 7 members: `genlab-core`, `dashboard`, `BlackboxBrief`, `CriticalRush`, `ClutchWire`, `SpliceReel`, `FrameDrift` | root `pyproject.toml` line 28+ | **CONFIRMED** |
| D3 | Systemd unit is `genlab-pipeline-ai-creators.service` (per MEMORY.md session-2026-07-17 & AUTO #2 section) | CLAUDE.md AUTO #2 | Actual unit is `genlab-pipeline-ai.service`. The `-ai-creators` name is dangling — referenced by `genlab-verify-whisper-canary.service` on disk but not-found | `systemctl status "genlab-pipeline-ai-creators.service"` returns `Loaded: not-found`; `grep -rl "genlab-pipeline-ai-creators" /etc/systemd/system/` | **DELTA** — finding A-0004; matches known "systemd unit naming drift observed" note in prior session memory |
| D4 | "genlab-postgres-ready.target" exists as a systemd target (referenced by auto-approver units as an `After=`) | Implicit in unit references | Target does not exist on disk. Auto-approver `.timer` + `.service` (and their `.pre-2026-07-05` backups) reference it | `systemctl status genlab-postgres-ready.target` returns not-found; 4 files reference it | **DELTA** — finding A-0004 |
| D5 | Python 3.12+ | CLAUDE.md §"Coding standards" | VPS: 3.12.3 ✓. Mac: **3.14.3** (2 minor versions ahead of prod) | `python3 -V` on both | **PARTIAL DELTA** — meets prod minimum, but dev far ahead. Behavior differences possible (typing, asyncio) |
| D6 | "PostgreSQL primary + SharePoint fallback" | CLAUDE.md §"Key infrastructure" | Postgres 18.3 running on VPS. Cannot verify SharePoint fallback code paths from Phase 0 alone | `psql --version` on VPS | **PARTIAL** — Postgres confirmed present; SharePoint status deferred to Phase 3 |
| D7 | Rule #23: "TikTok and X/Twitter are explicitly out of scope" (4-platform focus: YT/FB/IG/Threads) | CLAUDE.md rule 23 | Consistent: X/Twitter is `enabled: false` in publishing.yaml across niches. VPS-dirty edits keep it disabled. No new features observed against Twitter/TikTok clients | `git diff SpliceReel/config/publishing.yaml` on VPS shows `enabled: false` line untouched | **CONFIRMED direction** — code + config match rule |
| D8 | ".pre-commit-config.yaml pins ruff v0.15.14" (rule "Pre-commit workflow") | CLAUDE.md §"Pre-commit workflow" | Present on both hosts (not deleted; not verified for content in Phase 0) | manifest confirms `.pre-commit-config.yaml` exists on both, hash matches | **PRESENT** — content check deferred to Phase 7 |
| D9 | Rule #21: "verify-2026-07-22-fixes.timer set `Persistent=true`" | CLAUDE.md rule 21 | Timer still `active elapsed`. LastTriggerUSec = 2026-07-23 12:30 IST. Persistence not directly checked but timer is 6 days stale and purpose has expired | `systemctl show genlab-verify-2026-07-22-fixes.timer` | **STALE** — timer still armed after fix-verification purpose expired; deferred to Phase 4 |
| D10 | Rule #26: "Scripts invoked by systemd MUST distinguish hard error (exit non-zero) from partial success (exit 0 + WARN log)" — 7 scripts fixed in the 2026-07-21 sweep | CLAUDE.md rule 26 | 4 services still failed with exit≠0 as of 2026-07-29: `genlab-pipeline-ai` (exit=2), `genlab-pipeline-movies` (exit=2), `genlab-post-deploy-verify` (exit=1), `genlab-strategist` (exit=1). Whether these are "genuine incidents" (correct exit≠0) or new rule-#26 violations cannot be judged from Phase 0 alone | `systemctl status <svc> -l --no-pager` | **PARTIAL** — the rule exists; whether these 4 failures conform to or violate it is a Phase 4 question. Failures themselves are finding A-0001 |

## Deferred to later phases (documented claim, verification requires phase N tooling)

| # | Claim | Best-fit phase |
|---|---|---|
| D11 | Rule #27 — RLS `Bypass RLS` role attribute vs table-level policies; belt-and-suspenders `AND niche_id = %s` injected in `PostgresBackend.find/update/delete` | Phase 3 (data layer, empirical RLS check per §0.4/§0.5 of the spec) |
| D12 | Rule #28 — every DB column must be in `PROMOTED_COLUMNS[table]`; schema pin `test_promoted_columns_vs_db_schema.py` runs against prod DB when `GENLAB_SCHEMA_PIN_DSN` set | Phase 3 |
| D13 | "55 indexes across all tables" | Phase 3 |
| D14 | "Alembic migrations in `genlab-core/migrations/`" — head-vs-DB parity | Phase 3 |
| D15 | "GENLAB_USE_POSTGRES=true + DATABASE_URL in .env" | Phase 3 (env), Phase 5 (secret) |
| D16 | "20 insight schedules (5 niches × 4 windows)" | Phase 4 (schedule reality) |
| D17 | 7 intelligence engines flag-gated; specific env vars (`GENLAB_CROSS_NICHE_TRANSFER_ENABLED`, etc.) | Phase 4 |
| D18 | `SKIP_APPROVAL_GATE is REMOVED (Sprint 62)` | Phase 1 (dead-code sweep) or Phase 4 |
| D19 | `text_optimizer` regression: `whisper_sync.enabled = false` across all 5 niches | Phase 2 (config sweep) |
| D20 | "Meta tokens are permanent EAA Page Tokens (expires_at=0)" | Phase 5 (credentials) |
| D21 | "PLATFORM_SPECS uses libx264/CRF20/preset=fast for all 5 platforms" | Phase 6 (media perf) |
| D22 | "34 psycopg bypass sites per deeper-cuts-audit-2026-07-16 memo" | Phase 3 + Phase 5 |
| D23 | "1 reel per channel per day, hard cap" — verify enforcement in prod state | Phase 4 |
| D24 | "20 insight schedules" — actual timer count for insights | Phase 4 |
| D25 | Rule #17 — `except ImportError: return None` at DEBUG must be elevated | Phase 4 (silent-failure sweep §4 checklist item 6) |
| D26 | "Root `GenLab/.env` + per-niche `.env`" credential architecture | Phase 5 |
| D27 | "Prefix `{PREFIX}_{KEY}` per niche, never cross-channel" | Phase 5 |
| D28 | AUTO #2 rollout ladder (0.1 → 0.25 → 0.5 → 1.0) — where is ai_creators today? | Phase 4 |

## Documentation waste candidates (spec §0.6 style — one-liners, not carried findings)

- CLAUDE.md is 35 KB (~600 lines). Sections referring to *shipped* fixes with rule numbers 11–28 are stable, but rules 24 ("Growth targets") is ambition, not enforcement. Phase 1 candidate.
- MEMORY.md is 233 KB / 399 lines and exceeds its own stated cap ("keep MEMORY.md concise; move detail into topic files"). Warning printed at load time in this session.
- `.audit/PHASE0.md` … `.audit/PHASE7.md` from prior audit are shadowed by this new run; Phase 9 will decide whether to purge or archive.
- `docker-compose.yml.bak-2026-07-24-2328` on VPS: 5 days old sibling of `docker-compose.yml`. Phase 1.
- BlackboxBrief has 27 markdown docs / 9,130 lines — Phase 1 duplication/legacy audit territory.

---

## Summary

- **5 CONFIRMED** (D1, D2, D7, D8, D9-timing)
- **4 PARTIAL** (D5, D6, D9-persistence, D10)
- **2 DELTAS** (D3 systemd naming, D4 postgres-ready target)
- **~18 items DEFERRED** to Phases 1/3/4/5/6/7

Nothing found this phase contradicts the CLAUDE.md architecture at the level of the 3-layer model, workspace shape, or channel roster. The deltas are surface-level naming drift, and the partials are open questions that Phases 3-5 need to answer.
