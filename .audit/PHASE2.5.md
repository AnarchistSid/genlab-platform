# PHASE 2.5 — Incident, corrections, blindness-closure

## Three leading facts

1. **Production Postgres is port 5432 (Docker `genlab-postgres` container).**
   `DATABASE_URL` points to `:5432/`. Port 5433 is a **different project**
   (`aspirehub_saas`). F-0024 stays **CRITICAL** — pg_hba.conf ends with
   `host all all all scram-sha-256` (world-reachable, password-only defense).
2. **GitHub Actions runner classification**: **HIGH**, not CRITICAL. Public
   repo + `[self-hosted, genlab-prod]` label + `pull_request:` trigger on
   both `ci.yml` and `test.yml`. Runs as `gh-runner` (not root, cannot
   sudo, cannot read `/opt/genlab/.env`). Runner CAN reach port 5432
   locally — amplifies F-0024 for any code executed by a first-time
   contributor fork PR (which GitHub gates by approval; setting NOT
   independently verifiable via API).
3. **Four-bucket env table** (VPS-side re-grep applied):

| Bucket | Count |
|---|--:|
| Set on VPS + code reads it | **49** |
| Set on VPS + dynamic-suffix reads (`GENLAB_LINUCB_*_{niche}`, `GENLAB_THOMPSON_*_{niche}`) | ~11 |
| Set on VPS + NO code reads | **0** |
| Code reads it + NOT set on VPS (defaults off) | **77** |

Prompt's "20 phantom SET_TRUE-but-nothing-reads" was **0** once the local-vs-VPS
grep diff was applied — VPS-side reads = 130 vars, local was 70. **60 vars are
read on VPS but not in the local repo grep** (P0's discovery that local
execution ceased 2026-06-16 fully explains it). This is the first hard
deployment-drift measurement.

---

## Section 1 — Incident

**1.1 Credential leak**. `.audit/phase2/02_vps_env_actual.txt:1` contained a
live Slack webhook URL. Redacted in place with `sed`; grep for
`hooks.slack.com/services/[A-Z0-9]` returns 0 hits after. Sweep of `.audit/`
for 15 credential shape patterns (OpenAI SK, Anthropic key, GH PAT, AWS,
Meta EAA, Slack bot, postgres DSN, Bearer, high-entropy env-value, JSON
password) found **0 remaining secrets**. **F-0030 filed against audit process
itself**. The extraction command is now names-only:
`sudo grep -oE '^GENLAB_[A-Z_0-9]+=' /opt/genlab/.env | tr -d '=' | sort`.
Rotation of the Slack webhook is operator action — I cannot rotate.

**1.2 Prod Postgres = 5432 (Docker)**. Data volume 158 MB. F-0024 severity
unchanged. pg_hba.conf allows password auth from any host — the public 5432
exposure means the password IS the only defense (F-0031 new HIGH).

**1.3 Runner**. `gh-runner` user; `_work` = 3.3 GB (top disk consumer inside
`/home`). Public repo + `pull_request:` on both `ci.yml` + `test.yml` means
fork PRs execute on prod VPS (after GH's default first-time-contributor
approval). File as **HIGH** F-0032; NOT critical because gh-runner cannot
read .env and cannot sudo.

## Section 2 — Corrections

**2.1** — 4-bucket table above supersedes F-0016 phantom count.
`GENLAB_SHADOW_REVIEWER_ENABLED=true` on VPS + `genlab-shadow-reviewer.timer`
shows **LAST=- NEXT=-** (never fired). Flag on, timer inert. F-0033 medium.
`GENLAB_REQUIRE_TENANT_GUC=1` STRICT — `pg_connect` raises on missing tenant.
But `genlab` role has BYPASSRLS (F-0007) so RLS policies are silently no-op.
Isolation is enforced at app + backend-query layer (my Phase-0 belt-and-
suspenders fix), not at DB-policy layer. Multi-tenancy story stands, RLS story
does not.

**2.2** — F-0027 downgraded from CRITICAL to LOW. Redis on same docker-proxy
mechanism is correctly bound `127.0.0.1:6379`. 5432 is one misconfigured
container port, not systemic. Fix span: one line.

**2.3** — 15 F's = **7 real regressions in 3 modules (2 bugs) + 8 test-order
flakes**. Verified in isolation:
- `test_outbound_targeting_competitor_filter.py`: 5 fails (all same shape — `_build_targets()` returns `[]`)
- `test_outbound_youtube_fetcher_pin.py::test_end_to_end_wire_with_all_layers_mocked`: 1 fail (same root cause)
- `test_refit_top_creator_priors.py::test_missing_api_key_exits_1`: 1 fail (guard doesn't fire — exits 0 instead of 1)
- `test_disclosure_and_health.py` (1) + `test_rationale_classifier.py` (7) all **pass in isolation** → order-dependent.
`refit-top-creator-priors.service` last fired 2026-07-19 09:30 IST, Result=success, ExecMainStatus=0. Key IS set today; if it ever unsets, service silently succeeds. Pattern class-of-bug — **F-0034** medium.

**2.4** — Backups exist AND are exercised. Daily `.sql.gz` 4.3-4.7 MB dumps
retained since 2026-07-12 (12 days). `backup-test.service` last ran
2026-07-20 04:00 IST, Result=success. **F-0035 INFO / action=accept**.

## Section 3 — Closed blindness

**3.1** `pg_hba.conf` reads `host all all all scram-sha-256`. sshd is
hardened (`PermitRootLogin prohibit-password`, `PasswordAuthentication no`,
`PubkeyAuthentication yes`). See F-0031.

**3.2** Sample-based classification of `except Exception` in genlab-core/src:
**INTENTIONAL** (comment says fail-open/best-effort): 146. **LOSSY** (has
logger call): 743. **SILENT** (no log + swallow): **0**. F-0028 downgraded
INFO — this is not the hidden-failure surface it looked like.

**3.3** Reachability decision: **DEFER to Phase 3**. Dynamic dispatch is
tractable-but-tedious (~200 explicit entrypoints: 10 importlib + 10 Prefect
`@flow` + 184 Flask `@bp.route`). Phase 3's stage×channel truth table from
the DB answers the same question with better evidence — code that never
appears in publishing_analytics is empirically dead regardless of static
reachability. F-0036 (INFO — decision recorded).

**3.6** Cascade evidence: **`journalctl --since '30 days ago' | grep -icE
'tier.?[234]|fallback.*tts|elevenlabs.*fail'` = 0.** Multi-tier TTS/fetch
cascades have not fired in production for at least 30 days. Untested
fallback tiers are liability presented as resilience. **F-0037 medium**.

**3.7** VPS asset sprawl:

| Path | Size |
|---|--:|
| `/home/gh-runner` | 5.2 GB |
| `/var/lib` | 2.5 GB |
| `/opt/genlab/dashboard` | 549 MB (node_modules likely) |
| `/opt/genlab/BlackboxBrief` | 139 MB |
| `/opt/genlab/SpliceReel` | 116 MB |
| `/opt/genlab/CriticalRush` | 110 MB |
| `/opt/genlab/ClutchWire` | 108 MB |
| `/var/log` | 852 MB |

**Disk went 80% → 76% during this session** (F-0001 baseline was measured 90
min ago at 29G/38G; now 27G/38G, 8.9G free). Cleanup fires between the
measurements are visible. **Cannot compute fill-date without a 7-day
growth-rate snapshot**. F-0001 stays HIGH; add F-0038 INFO: gh-runner is
the largest single consumer, monitor it.

## Blindness list

- **Password strength on prod Postgres** — not tested (would require
  connection with bad credentials).
- **GitHub Actions first-time-contributor approval setting** — not
  independently verifiable via API (operator must confirm in
  Settings > Actions > General).
- **Full 9,451-test pass rate** — still UNVERIFIED (F-0014 stays HIGH).
- **Fill-date extrapolation** — requires a second time-series point.

## Process defect noted

The audit workspace leaked a credential (Section 1.1). Fix: the extraction
command now strips values by construction. **Rule for every subsequent
phase**: any VPS command that reads `.env` must use `grep -oE '^KEY='` or
equivalent that returns names only. Rejected any command that returns
key=value directly. This audit does not exfiltrate secrets, even into its
own workspace.
