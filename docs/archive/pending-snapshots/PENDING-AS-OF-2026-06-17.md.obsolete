# Pending work — comprehensive inventory as of 2026-06-17 (evening refresh)

Snapshot after the 2026-06-17 Wave-4 day: 13 PRs (#286-#299), 3 prod
migrations applied (SR-B + SR-B hotfix + SR-F), 4 bugs surfaced by
post-Wave-4 audit, 8 stale-pending-doc items closed.

## TL;DR (2026-06-17 evening)

| Audit category | Total | Done | Open | Note |
|---|---|---|---|---|
| **R-NN risk register** | 83 | 82 | 1 | R-31 sub-item a' — 4 affiliate adapters need creds (operator-blocked) |
| **U-NN upgrade register** | 25 | 19 | 6 | U-01/U-04/U-12/U-13/U-14/U-21 + most-of-U-24 verified done today |
| **W3.x / W4.x autonomy** | 8 | 5 | 3 | W3.3, W4.4 still open; W4.3 + dice fix done |
| **SR-A through SR-F (SaaS §9)** | 6 | 2 | 4 | SR-B + SR-F shipped; SR-A/C/D foundation in #299 |
| **L-NN / M-NN dashboard** | ~30 | most done | 4-5 | M-19 shipped; M-20/M-21 still open (per-niche RSS/reddit edit) |
| **Post-audit bugs (today)** | 4 | 4 in flight | 0 | PRs #296-#298 + #299 cover all |

---

## 1. Operator-blocked (engineering CAN'T unblock)

| Item | Source | What's needed |
|---|---|---|
| **PA-API credentials** | R-31 (a') | Amazon 10 sales / 30d. PR #277 (geo→US) should unblock |
| **Impact API credentials** | R-31 (a) | Impact account + campaign IDs |
| **ShareASale credentials** | R-31 (a) | ShareASale merchant relationships |
| **CJ Affiliate credentials** | R-31 (a) | CJ PID/AID |
| **ElevenLabs API key** | quick-win | sign up at elevenlabs.io |
| **Twitter API credentials** | gap-analysis | Content-policy decision + dev account |
| **AUTO #2 Day-8 calibration** | runbook §5 | Operator review ≥30/niche × ≥90% agreement |

---

## 2. Engineering-actionable

### 2a. Today's audit-surfaced bugs (PRs #296-#299 in flight / merged)

| Bug | PR | Status | Description |
|---|---|---|---|
| Alert pump case drift | **#296** | ✅ merged | `severity = 'CRITICAL'` was case-sensitive; 4 of 6 writers emit lowercase → silently dropped |
| No publish-silence check | **#296** | ✅ merged | Zero-publish outages (2026-06-14/15/16 had 1+0+1) fired no alerts |
| W4.3 random.random per-pass | **#297** | in CI | Graduated latency, not graduated rollout. Replaced with sha256-deterministic |
| M-19 concurrent-write race | **#298** | in CI | Two simultaneous POSTs lost data. fcntl.flock on sidecar |
| M-19 loose URL validator | **#298** | in CI | Accepted /watch, /results, youtu.be. Now requires channel-flavoured path |
| SR-A/C/D foundation | **#299** | in CI | ContextVar GUC shim + `pg_connect()` |

### 2b. Genuinely-open audit items (verified 2026-06-17)

Stale entries from earlier pending doc cleared. The TRUE open list:

| ID | Effort | Item | Notes |
|---|---|---|---|
| **U-10** | M | Replace archived `pytrends` | Repo archived Apr 2023; `pytrends-modern` or `trendspyg` candidates |
| **U-15** | L | TypeScript 5.9 → 6 | Do last, after U-13 (eslint 10) and U-14 (vite 8) which are done |
| **U-25** | S | Frontend dev-chain bumps | Lands with U-14 (vite major) which is done — re-evaluate now |
| **W3.3** | L | Transformer-embedding hook classifier | Currently keyword/regex; multi-day ML work |
| **W4.4** | M | Track-record dashboard view | Per-content-type × per-niche agreement over time |
| **SR-A/C/D caller migration** | L | Migrate 34 direct-psycopg sites through `pg_connect` | Foundation in #299; migration is per-site |
| **SR-E** | M | Per-tenant YouTube API key (Quota DoS) | Multi-tenant SaaS; only needed for tenant-2 |
| **U-24 (partial)** | S | starlette 0.52 → 1.0 (host-injection CVE) | Deferred — major jump + FastAPI compat |

### 2c. Dashboard gaps still open

| ID | Description |
|---|---|
| M-20 | Per-niche RSS edit UI (sources.yaml `rss_feeds_extra`) |
| M-21 | Per-niche Reddit subreddit edit UI (sources.yaml `reddit.subreddits`) |
| (frontend) M-19 | Backend done (#293) — frontend SourceQualityCard needs add/remove UI |
| (frontend) AUTO #2 calibration | Mission Control card exists; ramp-rollout UI for `rollout_pct` slider needed once W4.3 (#290+#297) is fully merged |

### 2d. ✅ Confirmed DONE — DO NOT re-touch (2026-06-17 evening audit)

These were claimed open in the morning doc but verified done. Don't waste effort re-shipping.

| Item | Evidence |
|---|---|
| **U-01** prompt caching | `writing/llm_client.py:55` `_CACHE_THRESHOLD_CHARS = 4000` |
| **U-04** Detoxify small | `engagement/toxicity_gate.py:46` `_MODEL_TYPE = "original-small"` |
| **U-12** Node baseline pin | `dashboard/frontend/package.json` engines.node + `.nvmrc=22.13.0` |
| **U-13** ESLint 9→10 | `dashboard/frontend/package.json` `"eslint": "^10.5.0"` |
| **U-14** Vite 7→8 | `"vite": "^8.0.14"` |
| **U-17** Python 3.14 CI | `.github/workflows/test.yml` test-core matrix includes 3.14 |
| **U-21** mypy CI | `mypy-check` job with `continue-on-error: true` (informational) |
| **U-24 most** | lxml/pillow/urllib3/idna/cryptography/mako/pygments/pytest all current |
| **L-9** Logout button | `dashboard/frontend/src/components/layout/shell.tsx:169` |
| **L-11** CSV export | `dashboard/frontend/src/lib/export.ts` |
| **W3.4** Per-platform bandit | `monetization/cta_bandit.py:53` `dict[str, list[CTAVariant]]` keyed by platform |
| **W4.1** confidence_score at push_to_backlog | Shipped today PR #284 |
| **W4.3** Graduated rollout policy | Shipped PR #290 + dice fix PR #297 |

---

## 3. SaaS / multi-tenancy (SR-A through SR-F) — status

| ID | Severity | Status | Description |
|---|---|---|---|
| **SR-A** | Critical | OPEN (foundation in #299) | `get/update/delete` admin-mode bypass; migration to `pg_connect` needed |
| **SR-B** | Critical | ✅ Done (#291+#294) | WITH CHECK on all 17 niche_isolation policies |
| **SR-C** | High | OPEN (foundation in #299) | `create/batch_create` no GUC; same migration as SR-A |
| **SR-D** | High | OPEN (foundation in #299) | `find(niche_id="")` fail-open default; same migration |
| **SR-E** | Medium | OPEN | Per-tenant YouTube API key (tenant-2 only) |
| **SR-F** | Critical (newly named) | ✅ Done | Closed today — affiliate_clicks_niche_policy + email_subscribers WITH CHECK |

After SR-A/C/D caller migration completes: flip `GENLAB_REQUIRE_TENANT_GUC=1` on prod → fail-closed any regression. That's the gate for tenant-2 onboarding.

---

## 4. Structural follow-ups from today's audit

| Item | Description |
|---|---|
| **Drift detection** | Build `pg_policy` introspection check (per-prod-vs-Alembic) — catches the SR-B-style policy-name drift before it bites a future migration |
| **`ruff` rule banning bare `psycopg.connect`** | Once SR-A/C/D caller migration done, codify with lint to prevent regression |
| **Pending-doc category isolation** | Separate audit findings (R-NN, U-NN) from plan items (W3.x, W4.x, SR-x). Audit findings decay fast; plan items decay slow. Mixing them caused 8 stale entries this morning. |
| **`severity` normalization** | Either ALTER pipeline_alerts to add a CHECK constraint enforcing lowercase, or normalize at write time across all 6 writers. PR #296 fixed the reader; the writers should converge too |

---

## 5. AUTO #2 rollout — what's NOT shipped

From `AUTO_2_ROLLOUT_ADDENDUM_2026-06-15-PM.md`:

| Item | Why deferred |
|---|---|
| D2.7a Strategy B+E default ON | Shipped opt-in (PR #254). Flip per-niche when calibration data warrants. |
| Day-8 flip per niche | Operator-only. Requires calibration marathon to clear 15-item readiness check. |
| Rollout slider in Mission Control | Operator-facing UI for `rollout_pct` — frontend M-21-class work |

Day-8 flip path (updated post-PR #297):
1. Operator clicks approve/reject on ≥30 blueprints per niche over ~7 days
2. Mission Control's `AutoApprovalCalibrationCard` shows niche row as "READY" (≥30 samples + ≥90% agreement)
3. Operator runs `auto2_day8_readiness.sh` (PR #257/#258) — currently fails 3/10 checks until calibration accumulates
4. Operator flips `auto_publish.enabled: true` per-niche in `publishing.yaml` via git commit (no hand-edit on prod)
5. **NEW (post-PR #297)**: Operator starts at `rollout_pct: 0.1` for 5-7 days, observes the deterministic-dice'd 10% of qualifiers auto-approve, ramps to 0.5, then 1.0

---

## 6. Process lesson (carried forward)

The **verify-before-ship** sweep saved 8+ redundant PRs across 2 audit cycles today. 30-second `grep` per claimed-open item is cheaper than shipping the work and finding it was already done.

Audit findings (R-NN, U-NN) decay fast as PRs land. Plan items (W3.x, W4.x, SR-x) decay slow. **Mixing them in one pending doc causes false positives.** Next refresh should isolate the two categories.
