# Phase 6 — Cost and performance

**Audit run:** A (2026-07-29)
**Session:** 7 (Pre-Session-7 gate + Phase 6)
**Status:** COMPLETE — 10 findings carried (A-0062…A-0071) under 12-cap
**§0.5 self-scan:** PASSED (run LAST per §0.10)
**§0.10 shell quiescence:** all SSH sessions closed before summary write
**Related artifacts:** `PHASE_4_runtime.md` (A-0034 measured), `PHASE_1_findings.yaml` (A-0015 pre-verify), `PHASE_2_findings.yaml` (A-0042), `OPERATOR_ACTIONS.md` (rotation still PENDING), `DEFERRALS.md`

**Prior-artifact posture (§0.8):** Two prior findings materially corrected:
- **A-0015 REFUTED-as-safe** (Pre-Session-3 downgrade was wrong) — this phase's fire-verify shows the 14-day prune is NOT firing (oldest visual backup 33 days old, 421 files would be pruned). Same class-of-bug as A-0026: line exists ≠ line fires. → **A-0062** headline.
- **A-0042 refined** — grep for `INSERT INTO affiliate_clicks` in genlab-core/src/ returned empty. Sample-only evidence points to A-0042's situation 1 ("tracking never wired") but full trace deferred. → A-0068.

---

## 0. Pre-Session-7 gate

- **Scrub held:** `grep -rIn 'genlab_***' .audit/` → exit 1 (no matches).
- **Rotation status re-check:** `printf '%s' "$DATABASE_URL" | ... | sha256sum` still returns `c7b89bffef3a` (old-literal hash). **Rotation NOT DONE** — runbook not yet executed; OPERATOR_ACTIONS Row #1 remains PENDING.
- Shell quiescence: no lingering background jobs at session start.

## 1. VPS right-sizing (Phase 6 §7) — measured baseline

**Memory state (2026-07-29):**
```
Mem:  total 3.7Gi  used 1.6Gi  free 345Mi  buff/cache 2.2Gi  available 2.1Gi
Swap: total 1.0Gi  used 207Mi  free 816Mi
```
Improvement from Phase 0 (151 MiB → 345 MiB free) — swap essentially unchanged. Swap-usage baseline of ~200 MiB persists across all sessions.

**OOM kill history (last 30 days):** empty. No `oom-killer` events. VPS is under pressure but has not tipped.

**Top RSS consumers (this snapshot):**
```
aspireh+   265 MB  /opt/aspirehub/venv/bin/python
aspireh+   175 MB  /opt/aspirehub/venv/bin/python
aspireh+   135 MB  next-server
root       127 MB  /bin/warp-svc
gh-runn+   114 MB  actions-runner
genlab      82 MB  python3
postgres    64 MB  postgres:
genlab      48 MB  python
root        47 MB  dockerd
postgres    40 MB  postgres:
```

**Class-of-bug discovery:** VPS is SHARED between **at least 3 projects**: `genlab` (~130 MB from its main processes), `aspirehub` (~575 MB across 3 Python + next-server), and `gh-runn+` (114 MB — GitHub Actions runner). Total from named processes ~950 MB; add Postgres (~104 MB), Redis + Docker (~50 MB), Caddy + Cloudflared + WARP + Tor (~200 MB) — matches 1.6 GB used.

**GenLab is not the biggest consumer on its own VPS.** → **A-0067** (info).

**Service-level memory (systemd `MaxMB` / peak):**
```
genlab-dashboard.service         Memory: 65.8M  peak: 123.1M  swap: 34.6M  (max: 256M)
genlab-engagement-worker.service Memory: 15.4M  peak: 44.8M   swap: 31.0M  (max: 384M)
```
Both use swap even at low RSS — indicates memory pressure from other consumers, not GenLab's fault.

## 2. A-0015 fire verification (Phase 6 §8) — HEADLINE

**Test (§8 spec: "verify it FIRES, not that the line exists"):**
```
find /opt/genlab/.backups/visuals -type f -printf "%T@ %p\n" | sort -n | head -1
= 1782441263.9342... /opt/genlab/.backups/visuals/2026-07-15/…/8be45c35c5019699_reel.mp4

age of oldest file: 33 days  (14-day retention SHOULD delete anything > 14)

find /opt/genlab/.backups/visuals -type f -mtime +14 | wc -l
= 421 files that -mtime +14 -delete WOULD remove
```

**A-0015 REFUTED-as-safe.** The 14-day retention comment in `backup_visual_assets.sh` line 121 was true about intent, false about behavior. The prune is not executing. **421 files (33 days worth) would be reclaimed on next successful fire.** Same class-of-bug as A-0026 (`pg_backup.sh` retention wired-not-firing).

**Exhaustion date recomputation:**
```
du -sh /opt/genlab/.backups/visuals = 3.5G
last 24h accretion:                    12.3 MB (BB dark, low volume)
observed average over 33 days:         3500 MB / 33 = ~106 MB/day
```

v1.4 spec estimated 230-250 MB/day exhaustion → early-mid September 2026. My recomputation with actual accretion rate:
- 12 GB free / 0.106 GB/day = **~113 days = November 2026** exhaustion under CURRENT load.

Not the ~6-week emergency v1.4 posited. But **when the BB and other pipelines resume normal render volume** (post OPERATOR_ACTIONS Row #2 fix), accretion returns to ~250 MB/day and the exhaustion date collapses back to the v1.4 estimate. Fix path: same as A-0026 — investigate why the `find -delete` isn't executing.

→ **A-0062** (S1 headline).

## 3. Media pipeline (Phase 6 §4 — D21 close)

**PLATFORM_SPECS verified:**
```
/opt/genlab/genlab-core/src/genlab_core/media/ffmpeg.py:64: codec: str = "libx264"
/opt/genlab/genlab-core/src/genlab_core/media/ffmpeg.py:71: crf: int | None = Field(default=18, ge=0, le=63)
/opt/genlab/genlab-core/src/genlab_core/media/ffmpeg.py:72: preset: str | None = "medium"
/opt/genlab/genlab-core/src/genlab_core/media/ffmpeg.py:120:  if self.codec == "libx265":
```

**D21 DELTA:**
- Codec: libx264 ✓ (matches CLAUDE.md)
- **CRF: 18** vs CLAUDE.md's documented `CRF 20` — 30-50% larger output files
- **preset: medium** vs CLAUDE.md's `preset=fast` — slower encoding, marginally better compression
- **libx265 branch still exists as dead code** (line 120) — CLAUDE.md rule "4 GB Hetzner VPS OOMs on libx265" but the code path is retained

The CRF drift is the main cost multiplier — it directly feeds A-0015 accretion. If PLATFORM_SPECS drifted from CLAUDE.md silently, other spec-vs-code drifts likely exist. → **A-0063**.

## 4. Caches (Phase 6 §6)

**Redis (`docker exec genlab-redis redis-cli info`):**
```
used_memory_human:1.93M
maxmemory_human:0B          ← NO CEILING
maxmemory_policy:noeviction ← writes FAIL when memory exhausts
evicted_keys:0
keyspace_hits:48822
keyspace_misses:9354         (hit rate: 83.9%)
```

**Redis has no memory ceiling.** On a 4 GB VPS shared with 2 other projects (§1), an unbounded Redis is a class-of-bug: at first sign of abnormal traffic (dramatiq queue backup, aggressive engagement poll), Redis grows to whatever RAM is available, then writes start failing (noeviction). Fix: `maxmemory 256mb` + `maxmemory-policy allkeys-lru`.

**Hit rate 83.9%** is healthy for a dramatiq + rate-limit workload.

In-memory caches behind restarting services: not surveyed exhaustively (context tight). Deferred → Phase 7 or Phase 9.

→ **A-0064**.

## 5. LLM spend + credit exhaustion behaviour (Phase 6 §1+2)

**§1 — Query cost analysis:** BLOCKED. Per A-0035, `pg_stat_statements` is not installed. **No LLM spend attribution per stage/channel available without either the extension OR a code-side telemetry pass** (grep for cost-tracking calls in genlab-core). Deferred to OPERATOR_ACTIONS (extension install) + Phase 7 (code telemetry). → **A-0070**.

**§2 — Credit-exhaustion behaviour, consuming A-0034 measured incident:**

```
systemctl status genlab-anthropic-credit-monitor.timer:
  Active: active (waiting) since Wed 2026-07-15 18:10:29 IST; 2 weeks 0 days ago
  Trigger: Wed 2026-07-29 21:15:00 IST; 8min left (every 15 min)

grep in monitoring code:
  llm_cost.py:6: ``anthropic_credit_exhausted`` alert fires only AFTER the balance hits (…)
  anthropic_credit_monitor.py:1: Detect Anthropic credit-balance exhaustion and surface as a CRITICAL
  anthropic_credit_monitor.py:9: "message":"Your credit balance is too low to access the Anthropic
  anthropic_credit_monitor.py:12: The key was valid; only the credit balance was exhausted.
```

**The monitor is REACTIVE**: it scans journalctl every 15 min for the post-exhaustion error signal ("Your credit balance is too low"). It does NOT proactively query Anthropic's `/v1/organizations/me/usage` (or equivalent) for the current balance. This matches CLAUDE.md rule #16's anti-pattern ("Never treat env var timestamp as evidence of token healthy — always live-probe the API").

**Consumer of A-0034 measured incident:** the BB 32-attempts/0-published outage started 2026-07-22. Was this credit exhaustion? Cannot determine from journal (rotated per A-0050) but the 15-min-detection-latency monitor would have caught it after ~15 min. Detection latency is **15 minutes reactive**, versus a proactive check that could catch imminent exhaustion 24-48h ahead. → **A-0065**.

## 6. API quota utilisation (Phase 6 §5)

**YouTube API quota state:**
```
{
  "used": 217,
  "upload_count": 0,
  "reset_date": "2026-07-29",
  "per_niche": {
    "all": {"used": 217, "upload_count": 0}
  }
}
```

Quota is 10,000/day. Currently at 2.2%. Massive headroom.

**Class-of-bug:** `per_niche` schema exists but all usage lumped under `"all"` bucket. No per-niche attribution. Feeds Phase 8 SaaS-observability (per-tenant quota tracking is a multi-tenancy prerequisite). → **A-0066**.

**Meta API quota:** not enumerated this session (context tight). Deferred.

## 7. A-0042 affiliate-network cross-check (Phase 6 §9) — DISPOSITION

Phase 3 A-0042: `affiliate_clicks` = 0 rows, `affiliate_revenue` = 0 rows. Three situations:
1. Tracking never wired
2. Wired but zero conversions
3. Clicks/revenue write elsewhere

**Wire-check partial:**
```
grep -rln 'INSERT INTO affiliate_clicks\|affiliate_clicks.*INSERT' /opt/genlab/genlab-core/src/
= (empty)

grep -rn 'def.*record.*affiliate\|def.*track.*affiliate' /opt/genlab/genlab-core/src/genlab_core/monetisation/
= (empty)
```

No writer sites found in genlab-core/src/. **Points at situation 1 (tracking never wired)** — but this is a sample-only search; a fuller ORM/pipeline trace could find hidden writers. → **A-0068**.

**Network-dashboard cross-check** (Amazon IN/US, Cuelinks, Admitad, EarnKaro): **requires operator credentials to those third-party services** — cannot be done from within the audit. Moved to `OPERATOR_ACTIONS.md` as new Row #10. → **A-0071**.

## 8. Fallback-router tiers (Phase 6 §3)

Not exhaustively verified this phase (would need historical journal grep across LLM providers). Sampled: TTS cascade documented in CLAUDE.md as ElevenLabs → OpenAI TTS → Edge-TTS → gTTS. Verification deferred → Phase 7.

## 9. Findings carried (10 of 12 cap)

Ranking: severity × confidence ÷ effort; S1 disk-exhaustion first.

| id | S | title |
|---|---|---|
| **A-0062** | S1 | A-0015 REFUTED-as-safe — visual-backup prune NOT FIRING (oldest 33 days vs 14-day retention, 421 files pruneable). Same class as A-0026 |
| **A-0063** | S2 | D21 DELTA — PLATFORM_SPECS `CRF 18 preset=medium` vs CLAUDE.md `CRF 20 preset=fast`. 30-50% bigger output; feeds A-0062 accretion. libx265 branch is dead code |
| **A-0064** | S2 | Redis `maxmemory=0B` (no ceiling), `maxmemory-policy=noeviction` — writes fail on OOM. On shared 4 GB VPS with `aspirehub` (§1) this is a class-of-bug risk |
| **A-0065** | S2 | Anthropic credit monitor is REACTIVE (scans journal after exhaustion, 15-min latency). Matches CLAUDE.md rule #16 anti-pattern. Should be proactive balance-poll |
| **A-0066** | S2 | YouTube quota tracker has `per_niche` schema but all usage lumped under "all" bucket. No per-tenant quota attribution (SaaS-blocker) |
| **A-0067** | S3 | VPS shared with `aspirehub` project (~575 MB RSS) + gh-runner (~114 MB). GenLab (~130 MB) is not the biggest consumer on its own VPS |
| **A-0068** | S2 | A-0042 refinement — grep for `INSERT INTO affiliate_clicks` in genlab-core/src/ returned empty. Points at situation 1 (tracking never wired); full trace → Phase 7 |
| **A-0069** | S3 | A-0015 exhaustion date recomputation — 12 GB free / 106 MB actual daily accretion = **~113 days (Nov 2026)** under current low-render load. v1.4 spec's 6-week estimate assumed 250 MB/day; collapses back to that when BB and other pipelines resume |
| **A-0070** | S3 | Phase 6 §1 BLOCKED — `pg_stat_statements` not installed (A-0035). No LLM spend or query-cost attribution possible without extension install (OPERATOR_ACTIONS) or code telemetry (Phase 7) |
| **A-0071** | S3 | A-0042 network-dashboard cross-check needs Amazon/Cuelinks/Admitad/EarnKaro operator credentials — out of audit scope. Add OPERATOR_ACTIONS Row #10 |

## 10. Doc-delta closures

- **D21 → A-0063** (DELTA — CRF and preset drift from docs)

## 11. Deferrals added this session

- Full fallback-router historical verification → Phase 7
- Meta API quota + rate-limit posture → Phase 7
- Full-repo affiliate-clicks writer trace (A-0068) → Phase 7
- Redis maxmemory config change → OPERATOR_ACTIONS
- Anthropic proactive balance-poll wiring → Phase 9 (code fix)
- Per-niche YT quota attribution wiring → Phase 8
- pg_stat_statements install → OPERATOR_ACTIONS (per A-0035 disposition)
- A-0042 network-dashboard cross-check → OPERATOR_ACTIONS Row #10

## 12. Methodology issues this phase

1. **A-0015 downgrade in Pre-Session-3 was wrong** — I read line 121 of `backup_visual_assets.sh` and concluded "prune is wired" without testing whether it fires. A-0026's evidence (sibling `pg_backup.sh` with identical `-mtime +14 -delete` NOT firing) was already in scope. Same class-of-bug hit twice in same run: **"line exists" ≠ "line fires"** in this codebase. v1.4 §Phase-6-§8's explicit "verify fires" instruction was load-bearing — without it I would not have re-tested.
2. **A-0042 refinement is sample-only** — grep-based writer trace is not exhaustive. Full ORM/pipeline trace requires Phase 7 test-coverage analysis.
3. **VPS-shared-tenant discovery** (aspirehub, gh-runner) was surfaced only by `ps aux --sort=-rss`. Prior phases treated the VPS as GenLab's dedicated box. Real memory pressure is not from GenLab.
4. **Anthropic credit monitor's reactive design** is the class of thing that would have made A-0034 (BB outage) resolvable in 15 min had credits been the cause, but leaves no chance for pre-emptive intervention. Same shape as A-0050 (journal rotation destroyed evidence): the observability instruments exist but their design constrains what can be learned.
