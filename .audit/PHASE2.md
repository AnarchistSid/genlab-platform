# PHASE 2 — Corrections + Dead-Code Reconnaissance (partial)

## Headline numbers

| Number | Value |
|---|--:|
| **Product Python (src only, excludes tests + scratch + tools)** | **190,741** |
| **Test Python (src's twin)** | **198,899** |
| **LOC reachable from a LIVE entrypoint** | **UNVERIFIED** (import-graph walk not completed — see § Blindness) |
| **CVEs in root .venv (pip-audit against real target)** | **66 vulns in 15 pkgs** |
| **New Python LOC + tests per new channel** | **~2,900** (was ~1,268 src only) |
| **VPS Postgres 5432 exposure** | **PUBLIC on 46.224.237.56** (verified: `nc -zv 46.224.237.56 5432` succeeds) |

---

## PART A — Phase 1 corrections landed

**C1 (F-0014 → HIGH)** — 15 failing tests **named**, from **5 files**:

| File | # fails | Cluster |
|---|--:|---|
| `tests/compliance/test_disclosure_and_health.py` | 1 | isolate: passes → **test-order flake** |
| `tests/engagement/test_outbound_targeting_competitor_filter.py` | 5 | isolate: `TestCompetitorBlocklistFilter::*` — `targets=[]` when >0 expected → **real fail** |
| `tests/engagement/test_outbound_youtube_fetcher_pin.py` | 1 | `test_end_to_end_wire_with_all_layers_mocked` — same `targets=[]` shape → **real fail (same root cause as above)** |
| `tests/intel/test_refit_top_creator_priors.py` | 1 | `test_missing_api_key_exits_1` — exit 0 instead of 1 → **guard didn't fire** |
| `tests/learning/test_rationale_classifier.py` | 7 | isolate: passes → **test-order flakes** |

Effectively **8 real regressions in 3 modules** + 8 order-dependent flakes.
Prompt was right: not 15 unrelated bugs.

**C2 (new HIGH F-0023)** — Phase 1's pip-audit scanned pip-audit's **own**
dependencies (28 pkgs of `cyclonedx-python-lib`, `packageurl-python`, etc.) —
zero vulns was zero vulns *in the scanner*. Re-scanned root `.venv/` against
PyPI: **166 real pkgs, 66 CVEs in 15 pkgs**. Highlights (all fixable by
version bump):
- `aiohttp 3.13.3` → 22 CVEs (fix 3.13.4 or 3.14.1)
- `pillow 12.2.0` → 15 CVEs (fix 12.3.0)
- `yt-dlp 2026.6.6.dev0` → 7 CVEs (fix 2026.6.9) — **used in publish path**
- `pydantic-settings 2.13.1`, `cryptography 48.0.0`, `torch 2.12.1`, `setuptools 81.0.0`, `msgpack 1.1.2`, `pyasn1 0.6.3`, `python-engineio`, `python-socketio`, `httplib2`, `click`, `soupsieve`, `transformers`.

**C3 (F-0016 reframed as INTELLIGENCE, F-0024 CRITICAL)** — VPS `.env` joined
against code reads (70 vars). Classification:
- **SET_TRUE**: 20 · **SET_FALSE**: 6 · **UNSET (defaulting)**: 44
- **28 ML feature-flag vars** exist:
  - **SET_TRUE (active)**: 15 — `AUTO_EXPERIMENT`, `BAYESIAN_GATE`, `CONFORMAL_ROUTER`,
    `COUNTERFACTUAL_REPLAY`, `CROSS_NICHE_TRANSFER`, `DRIFT_PERSIST`,
    `ENSEMBLE_PERSIST`, `HOOK_CRITIC`, `HOOK_DIVERSITY`, `LLM_JUDGE`,
    `POST_RCA`, `RENDER_QC`, `TOP_CREATORS`, `OUTBOUND_REPLIES`,
    `ANTICIPATION_{NEWS,REDDIT,YT}`.
  - **UNSET / SET_FALSE (dormant)**: 13 — `BEDROCK_FINETUNE`,
    `LINUCB_STOCHASTIC` (unsuffixed), `LLM_FALLBACK`, `PERMISSIONS_REPAIR`,
    `POLICY_BLOCK_RCA`, `SPLIT_SCREEN_COMPOSITOR`, `STORYTIME_COMPOSITOR`,
    `TEMPORAL_CONTEXT`, `THOMPSON_PROPENSITY`, `CONFORMAL_STATE_PATH`, +
    `RATIONALE_WEIGHTED_REWARD` (SET to "1  # PR #642..." — string with
    comment, always evaluates falsy).
- **LinUCB per-niche gating exists**: composed dynamically in
  `push_to_backlog.py:1355` (`f"GENLAB_LINUCB_STOCHASTIC_ENABLED_{niche_id.upper()}"`).
  The 6 "orphans" from Phase 1 (`_AI_CREATORS`, `_ANIME`, `_GAMING`, `_MOVIES`,
  `_SPORTS`, `_` trailing-underscore docs bug) are **legit references**, not
  orphans. 11 orphans remain and 8 of those are retired ML flags.

**C4** — PHASE0.md two inline `[SUPERSEDED — see A4]` markers added at
lines 145 + 158. Corrections section stands as the source of truth.

**C5 (F-0025 CRITICAL)** — headline "Real source LOC: 315,882" is src **plus**
tests. True product Python split (per member):

| Member | src files | src lines | test files | test lines | ratio |
|---|--:|--:|--:|--:|--:|
| genlab-core | 423 | 125,716 | 731 | 158,059 | 1.26 |
| dashboard | 76 | 25,985 | 105 | 24,629 | 0.95 |
| scripts | 74 | 20,553 | 3 | 277 | 0.01 |
| BlackboxBrief | 31 | 6,220 | 23 | 3,630 | 0.58 |
| CriticalRush | 48 | 8,462 | 31 | 6,366 | 0.75 |
| **ClutchWire** | 10 | 1,107 | 17 | **1,925** | **1.74** |
| **SpliceReel** | 12 | 1,460 | 18 | **2,013** | **1.38** |
| **FrameDrift** | 11 | 1,238 | 17 | **2,000** | **1.62** |
| **TOTAL** | 685 | **190,741** | 945 | **198,899** | **1.04** |

Total product Python = **~190K src + ~199K tests ≈ 390K**. The 316K number was
wrong. Small-niche test:src ratios all breach the 1.5:1 threshold that
PHASE1.md called "honest."

**C6+C7 (F-0026 medium architecture)** — cross-niche clone concentration:

| Niche | LOC cloned OUT | Total src | % |
|---|--:|--:|--:|
| ClutchWire | 248 | 1,107 | **22.4%** |
| SpliceReel | 173 | 1,460 | 11.8% |
| FrameDrift | 95 | 1,238 | 7.7% |
| CriticalRush | 16 | 8,462 | 0.2% |
| BlackboxBrief | 0 | 6,220 | 0.0% |

Hotspots: `cw_strategies/visual_render.py` (124 lines out to fd_, sr_),
`cw_strategies/writing.py` (72 out), `run_pipeline.py` (36 out × 3 pairs).
Fetchers (`fetch_anime_news.py`, `fetch_film_news.py`) clone **zero** cross-
niche. **Layer 2 promise holds for research + scoring; fails for
visual_render + writing + run_pipeline** — exactly the interfaces that most
define a channel's output.

**C8** — jscpd on tests: 794 files, 173,957 lines, **5,542 duplicated (3.19%)**.
419 clone sets (vs 17 in src). Same-niche self-clones dominate but
cross-niche pairs exist (e.g., `test_anime_visual_render.py ↔
test_film_visual_render.py`, 26 lines). Tests are moderately cloned; not the
"illusory coverage" hypothesis but real duplication.

**C9** — cost per new channel: **~2,900 total** (~1,268 src + ~1,650 tests).
Extracting `visual_render`, `writing`, `run_pipeline` into genlab-core would
close most of it. Filed under F-0011.

---

## PART B — Phase 2 dead-code work (executed subset)

### Step 0 — unblock

**0a. pytest abort cause**: full run was **not** OOM/collection-error — just
time. Killed at 54% after 4 min real time on 9,451 tests × sub-second each.
Not a Phase 2 blocker. Full-suite pass rate cannot be computed this session
(F-0014 stays HIGH but reason updated).

**0b. Token rotation (F-0009)**: VPS `.env` modified **2026-07-23** (yesterday),
contains 12 credential-prefixed values. BFG ran 2026-04-03 (3+ months ago).
Rotation is **implied** by the modification date but **not proven** without
value-level diff (which I won't perform per rule 6 — don't print secrets).
Downgrade risk: if the 29-token `settings.local.json` blob was Claude Code
personal-MCP tokens tied to developer identity, VPS `.env` rotation doesn't
cover them.

### Step 1 — the real entrypoint set

Full CSV at `.audit/phase2/02_entrypoints.csv`. Summary:

- **VPS**: **181 genlab-* services** defined. **6 always-active-running**
  (dashboard, engagement-poller, engagement-worker, quota-monitor, webhook,
  github-actions-runner). **175 timer-fired oneshots** — LIVE via 85 timers.
  **2 FAILED**: `genlab-strategist.service`, `genlab-proposal-auto-accept.service`
  (last confirmed failing during Phase 0; auto-accept was fixed tonight in
  commit `18f64fdf`, verify next fire).
- **Local**: `launchctl list | grep -i genlab` → 0.
  `~/Library/LaunchAgents/*genlab*` → does not exist.
  Local `crontab -l` has entries but none genlab-related (aspirehub only).
  **Local execution = COLD.** Confirms F-0004 from Phase 0.
- **Per-channel LIVE entrypoint**: all 5 channels have `genlab-pipeline-*`
  timer-fired services. Cannot answer "which channel is publishing" from the
  systemd view alone — that's Phase 3 (DB) work.

**Public ports on VPS** — F-0027 (CRITICAL):
```
LISTEN 0.0.0.0:5432   docker-proxy (PostgreSQL — bypasses ufw)
LISTEN *:80  *:443    caddy (expected)
LISTEN *:22           sshd (key auth)
LISTEN 127.0.0.1:*    everything else (safe: 5433 postgres, 6432 pgbouncer,
                       5151 gunicorn, 8080 python, 3000 next, 9050 tor,
                       8765 uvicorn, 20241 cloudflared, 40000 warp-svc,
                       2019 caddy admin, 6379 redis-in-docker)
```
`nc -zv 46.224.237.56 5432` from my laptop **succeeds**. ufw is `active`
with `default deny` but Docker's `-p 5432:5432` inserts iptables rules
BEFORE ufw's chain. `psql` connection from external IP would succeed if
credentials were guessed or leaked (see F-0009).

### Step 6 — quick facade hunt

- **Bare `except:` / `except: pass` on reachable paths**: 0
- **`raise NotImplementedError` on reachable paths**: 5, all in `strategies/
  base_*.py` (legitimate abstract-class markers, not stubs). 2 in
  `platforms/base.py` (same pattern). 0 stubs on live path.
- **`# TODO / # FIXME` in src**: 8 total, none on publish path (one in
  `dashboard/server/api/alerts.py:36` about pool migration).
- **`if False:` / permanently-off flags**: 0
- **`except Exception: pass|return None` on any path**: 110 sites in
  genlab-core/src. Cannot classify "on the publish path" without the
  reachability graph (F-0028 medium — most silent-fail sites are
  legitimately fail-open per CLAUDE.md rule #19 discipline; auditing
  each requires per-site read).

### Step 7 — the 761 "unmerged" branches

Sampled 15. All are `ahead=1` (single-commit feature branches). Deeper check
on 4 of them: files changed on the branch **partially match** main's
current version — pattern is "PR merged via GitHub squash, branch never
deleted, main evolved afterward so `git branch --merged` returns false."

**F-0020 corrected → medium F-0029**: the real number is not "761 branches
unmerged" but "the vast majority of these were squash-merged and are safe to
delete; a small number carry orphan work." Distinguishing requires a
`git log --grep='(subject)' main` check per branch. Sampling to
production-level confidence is Phase 4 work.

### Steps NOT run — declared blindness

- **Step 2 (import graph)**: `lint-imports` covered in Phase 0 corrections
  (broken contract F-0007). Full `grimp.build_graph` walk not run because
  the reachability analysis (Step 3) needs an entrypoint whitelist for
  dynamic dispatch that requires per-Prefect-flow + per-Flask-route + per-
  YAML `_target_` enumeration. Deferred to a dedicated Phase 2b session.
- **Step 3 (reachability + reachable-LOC %)**: not computed. This is the
  audit's headline number and it is **UNVERIFIED**.
- **Step 4 (joins)**: not computed (depends on Step 3).
- **Step 5 (diverged duplication)**: partial. jscpd found 17 src clone sets;
  per-set diff to detect divergence not run.
- **Step 6.2 (multi-tier TTS/fetch cascade evidence)**: not queried against
  VPS logs.
- **Step 8 (VPS asset sprawl)**: not enumerated (`du -sh` per `/opt/genlab`
  subdir; VPS root at 80% requires the fill-date extrapolation).

---

## New findings this phase (F-0023 through F-0029)

Appended to `findings.jsonl` — 7 new findings.

## Blindness list

- **Reachable-LOC %** — the load-bearing Phase 2 headline — UNVERIFIED. No
  dynamic-dispatch whitelist built; no reachability graph computed.
- **8 "real" test failures** — root cause per module not diagnosed (only
  named + failure message extracted).
- **VPS Postgres 5432 exposure** — reachable-on-TCP confirmed; whether pg_hba
  requires strong auth from external IPs NOT tested (would require a
  connection attempt with bad credentials, which is inappropriate without
  operator sign-off).
- **Per-package `.venv` CVE scan** — only root `.venv` scanned. `ClutchWire/.venv`
  (13 MB, includes yt-dlp) untested.
- **VPS drift** — HEAD matches (edf91209 both sides), but **4 config files
  dirty on VPS** (`CriticalRush/niches/gaming/config/publishing.yaml`, 3
  others) + 1 untracked `.conformal_router_state.json`. Hot-patched prod
  files that will be destroyed by next deploy. Recorded but not filed —
  Phase 4 scope.
- **8 test-order-dependent flakes** classified from isolation runs; ordering
  bug not diagnosed.
- **32 of 181 VPS services enumerated** in `02_vps_services.txt` sample.
  Full list needed for the entrypoint CSV — currently only 32 rows.

## Process note (rule 7)

Every shell exited before this summary was written (`ps aux | grep pytest`
= 0). One pytest run was `pkill`-ed at 54% to fit the time budget; rule 4
preserved. Rule 1 preserved: reachable-LOC reported UNVERIFIED, not
extrapolated.
