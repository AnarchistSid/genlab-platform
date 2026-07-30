# FIX_batched — Small-effort fixes bundled (register S3 + trivial S2)

**One file batching FIX-dispositioned register entries with S effort each.** Full spec per finding in the register + the referenced phase YAMLs.

Sequencing note: entries marked **[post-rotation]** wait on FIX_A-0053. Everything else is independent.

---

## A-0008 — VPS `git gc` (post-A-0053)
- **Change:** `ssh genlab-prod 'cd /opt/genlab && git gc --aggressive'`
- **Gate:** `git count-objects -vH` shows `packs: <5` and `size-pack: <60 MiB` (was 25 packs / 133.84 MiB)
- **Owner:** operator. **Effort:** S. **[post-rotation]**

## A-0029 — auto_approver.py:529 default `rollout_pct: 1.0 → 0.0`
- **Change:** `genlab-core/src/genlab_core/scheduling/auto_approver.py:529` change `rollout_pct: float = 1.0` → `rollout_pct: float = 0.0`. Add pin test in `tests/scheduling/test_auto_approver_defaults.py`.
- **Gate:** pin test asserts default = 0.0; a publishing.yaml missing `auto_publish.rollout_pct` results in no auto-approvals (currently would be 100%).
- **Owner:** dev. **Effort:** S. Removes fail-open leg of A-0027 interaction.

## A-0045 — Delete `verify-2026-07-22-fixes.timer`
- **Change:** `sudo systemctl disable --now genlab-verify-2026-07-22-fixes.timer && sudo rm /etc/systemd/system/genlab-verify-2026-07-22-fixes.{timer,service} && sudo systemctl daemon-reload`
- **Gate:** `systemctl list-unit-files 'genlab-verify-2026-07-22-fixes*'` returns 0 units.
- **Owner:** operator. **Effort:** S.

## A-0017 — Delete `docker-compose*.bak-2026-07-24-*`
- **Change:** `ssh genlab-prod 'sudo rm /opt/genlab/docker-compose.yml.bak-2026-07-24-2328 /opt/genlab/deploy/docker-compose.prod.yml.bak-2026-07-24-2330'`
- **Gate:** `find /opt/genlab -name 'docker-compose*.bak-*'` returns 0 files.
- **Owner:** operator. **Effort:** S.

## A-0004 — Fix dangling systemd refs + delete .pre-2026-07-05 orphans
- **Change:**
  1. Edit `/etc/systemd/system/genlab-verify-whisper-canary.service`: replace `After=genlab-pipeline-ai-creators.service` with `After=genlab-pipeline-ai.service`.
  2. Either create `genlab-postgres-ready.target` OR remove `After=genlab-postgres-ready.target` from `genlab-auto-approver.timer`/`.service`.
  3. `rm /etc/systemd/system/genlab-auto-approver.{service,timer}.pre-2026-07-05`
- **Gate:** `systemctl list-units --all --plain 'genlab-*' | awk '$2=="not-found"'` returns empty; no `.pre-2026-07-05` files on disk.
- **Owner:** operator. **Effort:** S.

## A-0031 — CLAUDE.md D19 whisper_sync update
- **Change:** Edit CLAUDE.md rule referencing "whisper_sync.enabled = false across all 5 niches" → note BB flipped to `true` (2026-07-22 canary).
- **Gate:** `git diff CLAUDE.md` shows the update; `git commit`.
- **Owner:** operator. **Effort:** S.

## A-0037 — CLAUDE.md D13 index count update
- **Change:** Edit CLAUDE.md "55 indexes across all tables" → either "152 indexes" OR "see `pg_indexes` on prod for current count."
- **Gate:** `git diff` shows update; `git commit`.
- **Owner:** operator. **Effort:** S.

## A-0039 — Delete SharePoint legacy code refs + CLAUDE.md update
- **Change:** Remove/comment the 4 SharePoint references in `genlab-core/src/genlab_core/{monitoring/token_health.py, monitoring/system_health.py, monitoring/monetisation_tracker.py, utils/text_sanitizer.py}` (each is a legacy comment per Phase 3 sample). Update CLAUDE.md "SharePoint (LEGACY — kept as fallback)" → "SharePoint (REMOVED)".
- **Gate:** `grep -rln 'sharepoint\|SharePoint' /opt/genlab/genlab-core/src/` returns 0.
- **Owner:** dev. **Effort:** S.

## A-0040 — Fix Alembic `script_location` on VPS
- **Change:** Locate/create `alembic.ini` in `/opt/genlab/` (or wherever the invocation cwd is). Ensure `script_location = genlab-core/migrations` is set.
- **Gate:** `cd /opt/genlab && uv run alembic current` returns `n1i2j3k4l5m6 (head)`.
- **Owner:** operator. **Effort:** S.

## A-0035 — Install pg_stat_statements
- **Change:** As operator, edit `/etc/postgresql/18/main/postgresql.conf` (or the Docker Postgres equivalent config): add `shared_preload_libraries = 'pg_stat_statements'`. Restart Postgres. Then `psql "$DATABASE_URL" -c 'CREATE EXTENSION IF NOT EXISTS pg_stat_statements'`.
- **Gate:** `psql "$DATABASE_URL" -tAc "SELECT calls, total_exec_time FROM pg_stat_statements LIMIT 1"` succeeds (returns any row or 0 rows without ERROR).
- **Owner:** operator. **Effort:** S (one restart, ~5s downtime).

## A-0056 — Dedup FB_APP_SECRET / META_APP_SECRET
- **Change:** Grep code for both env var names. Migrate all `FB_APP_SECRET` refs to `META_APP_SECRET`. Remove `FB_APP_SECRET` from `.env`.
- **Gate:** `grep -rn 'FB_APP_SECRET' genlab-core dashboard BlackboxBrief 2>/dev/null | wc -l` = 0; app boots without error.
- **Owner:** dev. **Effort:** S.

## A-0058 — Retire native Postgres 18 on 5433
- **Change:** Identify what runs on 5433 (`sudo systemctl list-units | grep postgres`). Confirm no consumer (grep code + configs for `5433`). Stop + disable.
- **Gate:** `ss -tlnp | grep 5433` returns empty; Docker Postgres 5432 still serving.
- **Owner:** operator. **Effort:** S (assumes no consumer; investigation is the risk).

## A-0057 — chmod 600 on 4 untracked cred files
- **Change:** `ssh genlab-prod 'chmod 600 /opt/genlab/.youtube_session.json /opt/genlab/.youtube_cookies.txt /opt/genlab/.conformal_router_state.json /opt/genlab/.version.env'`
- **Gate:** `stat -c '%a' <each file>` = `600`.
- **Owner:** operator. **Effort:** S.

## A-0064 — Redis maxmemory ceiling
- **Change:** Edit `docker-compose.yml` Redis service: add `command: ["redis-server", "--maxmemory", "256mb", "--maxmemory-policy", "allkeys-lru"]`. `docker compose up -d --force-recreate genlab-redis`.
- **Gate:** `docker exec genlab-redis redis-cli config get maxmemory` returns `268435456`; `maxmemory-policy` returns `allkeys-lru`.
- **Owner:** operator. **Effort:** S.

## A-0018 — Consolidate BB docs/plans + docs/archive
- **Change:** Move both dirs' contents under `BlackboxBrief/docs/history/`. Create `BlackboxBrief/docs/history/INDEX.md` with one line per doc (title, shipped date, superseded-by).
- **Gate:** `find BlackboxBrief/docs -name '*.md' | wc -l` reduced by ≥10 (via consolidation, not deletion); INDEX.md exists.
- **Owner:** dev. **Effort:** M (bit longer than S — 15 docs to index).

## A-0086 — Post-A-0062 disk cleanup (~2.7 GB deletable, ~70 LOC deletable)
- **Change:** After A-0062 prune fix lands and 24h passes: verify `du -sh /opt/genlab/.backups/visuals` < 500 MB. Run `ruff check --fix genlab-core/src/` (6 auto-fixables). Delete libx265 dead branch in `ffmpeg.py:120` (contingent on A-0063 pin verification).
- **Gate:** disk delta + `ruff check` clean on those 6 sites + ffmpeg.py no libx265 branch.
- **Owner:** dev. **Effort:** M.

---

## Not-in-this-file (has its own FIX file)
- A-0053 rotation → `FIX_A-0053_rotation.md`
- A-0083 line-exists-doesn't-fire class → `FIX_A-0083_line-exists-doesnt-fire-class.md`
- A-0081 env pinning → `FIX_A-0081_env-pinning.md`
- A-0074 test suite → `FIX_A-0074_test-suite.md`
- A-0011 nested `.git` → `FIX_A-0011_nested-git.md`
- A-0032 role switch → bundled into `FIX_A-0053_rotation.md` (same runbook)

## FIX-dispositioned but no fix file yet (register carries the gate; no separate file needed for these)
- A-0028 dual publishing.yaml layout — L effort, standalone; owner should read register + PHASE_2_config.md §2.1 for context
- A-0050 journal retention increase — one-line systemd config + verification
- A-0060 dashboard RBAC — L effort, standalone SaaS work
- A-0062 prune fix — component of A-0083 class-fix
- A-0026 prune fix — component of A-0083 class-fix
- A-0048 auto-approver dashboard observability — dashboard-only change
- A-0034 BB pipeline diagnosis — pending journal + fresh failure
- A-0044 3 failed services — pending journal + fresh failure
- A-0046 4 missing intelligence engine env flags — config-side, one-line per flag
- A-0063 PLATFORM_SPECS CRF drift resolve — pick code-vs-docs, one-line
- A-0065 Anthropic proactive balance-poll — dev work
- A-0072 pre-commit CI enforcement — component of A-0083 class-fix
- A-0084 consolidation umbrella — components each above
- A-0041 GENLAB_POLICY_BLOCK_RCA_ENABLED activation — one env var + observation window
- A-0025 hardcoded creds in scripts — component of A-0053 runbook Step 6
- A-0016 / A-0052 .env.bak cleanup — component of A-0053 runbook Step 6
