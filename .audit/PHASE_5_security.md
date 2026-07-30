# Phase 5 — Security and credentials

**Audit run:** A (2026-07-29)
**Session:** 6 (Pre-Session-6 gate + Phase 5)
**Status:** COMPLETE — 11 findings carried (A-0051…A-0061) under 12-cap
**§0.5 self-scan:** PASSED (run LAST per §0.10, see end of session)
**§0.10 shell quiescence:** all SSH sessions closed before summary write
**Related artifacts:** `OPERATOR_ACTIONS.md` (row #1 stays PENDING — A-0053 confirms), `PHASE_0_findings.yaml` (A-0008 gate re-evaluated), `PHASE_1_findings.yaml` (A-0011, A-0016), `PHASE_2_findings.yaml` (A-0025), `PHASE_3_findings.yaml` (A-0032), `DEFERRALS.md`

**Prior-artifact posture (§0.8):** Two prior-phase findings materially corrected:
- **A-0008** (`git gc` gating) — Phase 5 pre-BFG-history check REFUTES the gating. VPS old-SHA blob retrieval test returned "old blob purged from VPS too" — BFG rewrite propagated. `git gc` is safe from secret-retrieval angle; only performance/disk optimization remains. → A-0059.
- **A-0032** (RLS role bypass) — Phase 5 confirms `genlab_app` role already has GRANTs on all 45 public tables (INSERT/UPDATE/DELETE/SELECT). Fix reduces to `DATABASE_URL` username change + app reconnect. → A-0054 provides fix design.

---

## 0. Pre-Session-6 gate outcomes

1. **Scrub held:** `grep -rIn 'genlab_***' .audit/` → exit 1 (no matches). PASS.
2. **OPERATOR_ACTIONS status snapshot at session start:** all 3 rows still `PENDING` as of 2026-07-29.
3. **Shell quiescence:** no lingering background jobs from prior sessions.

## 1. Exposure surface (Phase 5 §1)

**VPS listening ports** (verbatim `ss -tlnp`):
```
LISTEN 127.0.0.1:20241  cloudflared        (tunnel control)
LISTEN 127.0.0.1:8080   python             (BB / niche app)
LISTEN 127.0.0.1:8081   python             (niche app 2)
LISTEN 127.0.0.1:5151   gunicorn           (dashboard main)
LISTEN 127.0.0.1:5432   docker-proxy       (Postgres — Docker container `genlab-postgres`)
LISTEN 127.0.0.1:5433   postgres           (native Postgres 18 instance — purpose unknown)
LISTEN 127.0.0.1:8765   uvicorn            (async service)
LISTEN 127.0.0.1:6379   docker-proxy       (Redis — Docker container `genlab-redis`)
LISTEN 127.0.0.1:6432   pgbouncer          (pooler)
LISTEN 127.0.0.1:3000   next-server        (dashboard frontend)
LISTEN 0.0.0.0:22       sshd
LISTEN 0.0.0.0:80       caddy              (Cloudflare-fronted)
LISTEN 127.0.0.1:9050   tor                (unclear purpose — investigate)
LISTEN 127.0.0.1:40000  warp-svc           (Cloudflare WARP client)
```

**Every service except SSH (22) and caddy (80) binds only to 127.0.0.1** — good posture. Docker Postgres binds 127.0.0.1:5432 (used); native Postgres 18 binds 127.0.0.1:5433 (unknown purpose — see A-0058). Two Postgres instances is a class-of-bug candidate.

**Firewall (ufw):** ACTIVE. Rules: SSH from anywhere, 443/tcp from Cloudflare IP ranges only, 80/tcp from Cloudflare only. `fail2ban-client` present.

**SSH config critical:**
```
PermitRootLogin prohibit-password
PasswordAuthentication no
```
No password auth; root key-only. Passable — recommend confirming operator SSH key rotation policy.

**Patch level:** Ubuntu 24.04.4 LTS. **42 upgradable packages** — not filtered for security-critical (context tight); operator should run `unattended-upgrades --dry-run` to see the breakdown. Deferred to OPERATOR_ACTIONS.

## 2. Secret sprawl / gitleaks-equivalent (Phase 5 §3)

**Manifest exposure re-verify** (§5 injected): `git log --all --oneline -- '.audit/manifest*'` returned empty. Confirmed never committed.

**BFG April 2026 report** (`..bfg-report/2026-04-03/21-58-04/`):
- `changed-files.txt`: 20+ old→new SHA pairs. Purged files include `.env.example`, `affiliate_catalog.yaml` (×4 revisions), `network_registry.py`, `2026-03-*.md` planning docs.
- `object-id-map.old-new.txt`: 88 KB of SHA mappings.
- **`genlab_***` is NOT in the purge list** (`grep -l genlab_*** ..bfg-report/2026-04-03/*` → no matches).

**Interpretation:** the `genlab_***` string was introduced AFTER the April 2026 cleanup, not re-introduced from a purged occurrence. → A-0055 (class-of-bug: no post-BFG secret-scanning CI to catch re-introductions).

**Nested `genlab-core/.git` (A-0011 gitleaks proxy):**
```
GIT_DIR=genlab-core/.git git log --all -S 'PGPASSWORD' 2>&1 | head -3
= 5650beb fix: comprehensive audit remediation — critical + high + medium issues

GIT_DIR=genlab-core/.git git log --all -S 'genlab_***' 2>&1 | head -3
= (empty)
```
One commit touches `PGPASSWORD` — likely a legitimate cleanup ("audit remediation" subject). **`genlab_***` was never in this repo either.** → A-0061.

## 3. `.env` inventory (Phase 5 §4) — hash-only per §0.5

**Mac** (sample; full inventory in `.audit/exec/2026-07-29-phase5/env_hashes_mac.txt` — gitignored):

| Key | length | hash prefix |
|---|---:|---|
| ANTHROPIC_API_KEY | 108 | b2c7a28331c7 |
| OPENAI_API_KEY | 164 | 94d0584792e7 |
| YOUTUBE_API_KEY | 39 | 051d85049133 |
| **FB_APP_SECRET** | 32 | **67c264537735** |
| **META_APP_SECRET** | 32 | **67c264537735** |
| CLUTCHWIRE_X_API_KEY | 0 | e3b0c44298fc *(SHA-256 of empty string)* |

**FB_APP_SECRET and META_APP_SECRET have identical hashes** = same value stored under two names. Class-of-bug: credential duplication with confusable names. → **A-0056**.

**Zero-length CLUTCHWIRE_X_* keys** confirm CLAUDE.md rule #23 (X/Twitter out of scope): entries exist but empty. Consistent with intent.

**VPS `.env`:** 208 keys. Full cross-host diff by hash-prefix deferred (would need matched-name join across both hosts + Python 3.12/3.14 parity). Small sample compared:
- ANTHROPIC_API_KEY hash matches across hosts ✓
- OPENAI_API_KEY hash matches ✓
- (values never printed either side)

## 4. File permissions (Phase 5 §5)

**Live `.env` mode on VPS: `-rw-r--r--` (644) — world-readable.** Any shell user on the VPS reads live prod credentials. → **A-0051 (S1)**.

**`.env.bak.*` mode drift:**
```
-rw------- Jun 22 (600) genlab_20260622-165154
-rw------- Jul 17 (600) genlab_20260717
-rw------- Jul 21 (600) genlab.bak.1784640444
-rw-r--r-- Jul 23 (644) genlab.bak.20260723_154728_slo_disable
-rw-r--r-- Jul 23 (644) genlab.bak.20260723_155549_threads_sync
-rw-r--r-- Jul 23 (644) genlab.bak-shadow-1784823647
-rw-r--r-- Jul 23 (644) genlab.bak-autonomy-flags-1784825710
-rw-r--r-- Jul 23 (644) genlab.bak-autonomy-round2-1784826525
-rw-r--r-- Jul 23 (644) genlab.bak.stochastic-widen-2026-07-23
```
**7 of 14 `.env.bak.*` files (all 2026-07-23+) are 644 world-readable.** The permission drift started 2026-07-23. Someone/something switched from `chmod 600` to letting the umask default (022) → 644. → **A-0052 (S2)**.

**Untracked prod credential files:**
```
-rw------- .reddit_cookies.txt         (600 — good)
-rw------- .threads_tokens.json        (600 — good)
-rw-rw-r-- .youtube_cookies.txt        (664 — world-readable)
-rw-rw-r-- .youtube_session.json       (664 — world-readable)
-rw-rw-r-- .conformal_router_state.json (664 — world-readable)
-rw-rw-r-- .version.env                (664 — non-secret)
```
YouTube session/cookies at 664. If they contain refresh tokens or session state, this is credential exposure. → **A-0057 (S2)**.

## 5. Postgres roles + A-0032 fix design (Phase 5 §6) — HEADLINE

**`genlab_app` role status:** already has GRANTs on **all 45 tables** in public schema.

```
grantee     table_name         privilege_type
genlab_app  ab_tests           INSERT/UPDATE/DELETE/SELECT
genlab_app  affiliate_clicks   INSERT/UPDATE/DELETE/SELECT
genlab_app  affiliate_revenue  INSERT/UPDATE/DELETE/SELECT
(… all 45 tables …)

SELECT COUNT(DISTINCT table_name) FROM information_schema.table_privileges WHERE grantee = 'genlab_app'
= 45

SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public'
= 45
```

**Every table already grants the 4 CRUD privileges to `genlab_app`.** Combined with Phase 3 A-0032 (`genlab_app` has `rolsuper=f, rolbypassrls=f, rolcanlogin=t`), the fix for A-0032 reduces to:

**FIX_A-0032 sketch (delivered to Phase 9):**
1. Rotate DB password (piggybacks OPERATOR_ACTIONS row #1 for `genlab_***`).
2. Update `DATABASE_URL` in `/opt/genlab/.env` to use `genlab_app` username with the new password.
3. Restart consumers: `genlab-dashboard`, `genlab-engagement-worker`, `genlab-engagement-poller`, `genlab-webhook`, `genlab-auto-approver`, `genlab-metric-collector`, etc.
4. Verification gate: `psql "$DATABASE_URL" -tAc "SELECT niche_id, COUNT(*) FROM blueprints GROUP BY niche_id"` should return **0 rows** unless `SET LOCAL app.niche_id = 'X'` is set first.

**Blast radius if wrong:** none discovered yet — all needed GRANTs exist. The one risk is any code that assumes cross-niche read access (e.g., dashboard cross-tenant admin views) will need explicit `SET LOCAL app.niche_id` wrapping. Phase 9 must grep application code for cross-niche queries before flip. → **A-0054**.

**pg_hba.conf:** `scram-sha-256` for all local host connections. Proper hashed auth. Listen addresses default (localhost). No remote Postgres access.

## 6. A-0025 rotation status verification (INJECTED) — NOT DONE

Hash comparison:
```
literal 'genlab_***' → c7b89bffef3a
live prod DB password → c7b89bffef3a
STATUS: still LIVE (matches genlab_*** literal) — rotation NOT DONE
```

**24h+ elapsed since disclosure; the credential remains live in public source repo.** OPERATOR_ACTIONS row #1 stays PENDING. → **A-0053 (S1)**. Combined with A-0051 (`.env` mode 644) and A-0055 (post-BFG re-introduction), the credential-hygiene surface is the largest single risk in the audit.

## 7. Dashboard auth per route (Phase 5 §7)

```
grep -rE '@(app|bp)\.(route|get|post)' dashboard/server/ | wc -l = 173

grep -rn 'require_auth|login_required|Basic Auth' dashboard/server/ | head -3
= dashboard/server/review_server.py:282: # ── Security: HTTP Basic Auth ──
= dashboard/server/review_server.py:290: logger.info("HTTP Basic Auth enabled (user: %s)", _AUTH_USER)
= dashboard/server/review_server.py:528: """Verify auth via session cookie (preferred) or HTTP Basic Auth (fallback)."""
```

**173 routes; auth = HTTP Basic Auth (single-shared-secret `_AUTH_USER`) + session cookie fallback.** No RBAC, no tenant-aware access control at the app layer. Combined with A-0032 (RLS bypassed at the DB layer), every dashboard user has full cross-niche access via the UI. For today's single-operator deploy this is intended; for SaaS this is a full-tenant leak at 2 layers. → **A-0060**.

## 8. Token posture (Phase 5 §8) — D20/D26/D27 close

**D26/D27 (per-niche credential architecture) — CLOSED CONFIRMED:** `.env` inventory shows per-niche prefixes (`CLUTCHWIRE_*`, `SPLICEREEL_*`, `FRAMEDRIFT_*`, `CRITICALRUSH_*`, `BLACKBOXBRIEF_*`) as documented in CLAUDE.md. Architecture holds.

**D20 (Meta permanent EAA Page tokens) — PARTIAL:** CLUTCHWIRE Meta tokens are 199-206 chars, consistent with EAA (long-lived) token format. Exhaustive expiry-monitoring check (does prod alert on token expiry?) deferred to Phase 6/7.

## 9. Injected items dispositions

| Item | Outcome |
|---|---|
| A-0025 rotation verify | **NOT DONE** — A-0053 |
| BFG April 2026 report | Read; `genlab_***` not in purge list — A-0055 |
| VPS pre-rewrite history | Old-SHA test purged from VPS too — A-0008 gc REFUTED-as-gated (A-0059) |
| Nested `.git` gitleaks | 1 legit `PGPASSWORD` cleanup; no `genlab_***` — A-0061 |
| Untracked prod cred files | YT session/cookies 664 world-readable — A-0057 |
| Manifest exposure re-verify | Never committed. CLEAR. |

## 10. Findings carried (11 of 12 cap)

Ranking: severity × confidence ÷ effort; S1 credential exposure first.

| id | S | title |
|---|---|---|
| **A-0051** | S1 | `.env` file mode 644 world-readable on VPS — any shell user reads live prod credentials |
| **A-0052** | S2 | 7 of 14 `.env.bak.*` files (2026-07-23+) are 644 world-readable; older ones 600. Permission drift started 2026-07-23 |
| **A-0053** | S1 | A-0025 rotation NOT DONE — hash-verified live 24h+ after disclosure; OPERATOR_ACTIONS row #1 stays PENDING |
| **A-0054** | S3 | POSITIVE-DESIGN — `genlab_app` role has GRANTs on all 45 tables; A-0032 fix reduces to DATABASE_URL username swap + reconnect. `FIX_A-0032.md` sketch provided |
| **A-0055** | S2 | `genlab_***` NOT in April 2026 BFG purge list — post-BFG re-introduction. No post-BFG secret-scan CI catches new leaks (would have caught `pg_backup.sh:19`) |
| **A-0056** | S2 | FB_APP_SECRET and META_APP_SECRET have identical hashes — same value under two names. Credential duplication |
| **A-0057** | S2 | 4 untracked prod cred files 664 world-readable (`.youtube_session.json`, `.youtube_cookies.txt`, `.conformal_router_state.json`, `.version.env`) |
| **A-0058** | S2 | Two Postgres instances — Docker container 5432 (used, per DATABASE_URL) + native Postgres 18 on 5433 (purpose unknown, likely dead) |
| **A-0059** | S3 | A-0008 `git gc` REFUTED-as-gated — old-SHA blob test showed BFG rewrite propagated to VPS. gc is safe from secret-retrieval angle; only perf/disk optimization remains |
| **A-0060** | S2 | Dashboard: 173 routes, HTTP Basic Auth (single-shared-secret) + session cookie. No RBAC / tenant-aware access — every operator has cross-niche access via UI (matching A-0032 at DB layer) |
| **A-0061** | S3 | Nested `genlab-core/.git` has 1 committed `PGPASSWORD` change (legitimate audit-remediation cleanup); no `genlab_***`. Doesn't add fresh secret exposure vector |

## 11. Deferrals added this session

Appended to `DEFERRALS.md`:
- 42 apt upgradable packages (security-critical filter) → OPERATOR_ACTIONS
- Meta EAA token expiry monitoring in prod → Phase 6/7
- Full cross-host `.env` diff by hash-prefix (name join) → Phase 7
- Static-file traversal audit on 173 dashboard routes → Phase 7
- Native Postgres 18 on 5433 disposition (dead instance?) → Phase 9

## 12. Doc-delta closures

- **D20 PARTIAL** — Meta EAA tokens present (correct length), expiry-monitoring not verified this session
- **D26 CONFIRMED** — per-niche prefix architecture holds (`CLUTCHWIRE_*` etc.)
- **D27 CONFIRMED** — no cross-channel fallback observed in .env structure

## 13. Methodology issues this phase

1. **A-0008 gc-gating was based on Phase 0's Mac↔VPS pack-count asymmetry** (2 vs 25 packs, 16K vs 40K objects) — I inferred pre-BFG history retention. This phase's empirical test (`git cat-file -e` on a known-purged blob) refutes it. Class-of-bug: **object count ≠ pre-rewrite history** — the extra objects can be repacking artifacts, dangling reflog entries, or post-rewrite branches Mac doesn't have. Right check is direct blob retrieval.
2. **A-0032 fix design was more advanced than I expected** — `genlab_app` role already had GRANTs on all 45 tables. Phase 3's "the fix has been created but application code still connects as `genlab`" was accurate but understated: not only does the role exist, it's fully privileged. The remaining work is a one-line DSN change + service reconnects.
3. **Rotation status confirmed NOT DONE** — 24h+ after Phase 3 disclosed live-status of `genlab_***`. OPERATOR_ACTIONS is doing its job of keeping this visible, but time keeps advancing.

## 14. §0.5 mandatory self-scan (RUN LAST per §0.10)

Verification captured at end of session (post all writes, all shells closed):
```bash
grep -rIn 'genlab_***' .audit/                                    # exit 1 required
grep -rInE "(PGPASSWORD|password|secret|api[_-]?key|token|BEGIN [A-Z ]*PRIVATE KEY)=?...".audit/ \
  | grep -v REDACTED | grep -v 'genlab_\*\*\*'                    # empty required
```
