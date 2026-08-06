# Pipeline Stabilization Diagnostic — 2026-08-05

Read-only diagnosis + one live test-run to confirm. **No code changes made.**

Current UTC: ~15:30Z. Local (server): 21:00 IST.

---

## One-line diagnosis

**The automation code is NOT broken. The system was STARVED (Anthropic credits
exhausted overnight); the operator's Anthropic top-up landed and the pipeline
now produces blueprints. A separate long-standing problem — 4 of 5 channels
haven't published anything in 7+ days because the auto-approver is enabled
only for `ai_creators` and the operator hasn't been manually approving the
others — is the actual "everything is broken" experience.**

Two orthogonal fixes needed:
1. **Fund OpenAI** (currently exhausted; used as writer fallback and for TTS
   — fallback works but degraded).
2. **Either enable AUTO #2 for the other 4 niches OR resume manual review**
   — the pipeline can produce blueprints but they sit unapproved in the review
   queue.

Neither requires code changes today. The "producing horrible content" complaint
has a real editorial signal (ai_creators hooks are news-headline style, and
two consecutive days repeated the same topic) but is not a code-emergency; it
is scoped for a separate content-quality session.

---

## Step 0 — Live API probe (the gate)

Ran the exact SDK probes the writer uses, from the `genlab` user on the VPS
with `.env` sourced via `set -a; source .env; set +a` (same pattern as
`daily_intel.sh`).

### Anthropic — LIVE ✓

```
$ ./.venv/bin/python -c "import anthropic; print(anthropic.Anthropic().messages.create(...))"
Hi! How can I
```

Key prefix (safe to share, non-secret): `sk-ant-api03...`

### OpenAI — STILL EXHAUSTED ✗

```
$ ./.venv/bin/python -c "import openai; print(openai.OpenAI()...)"
OPENAI: RateLimitError Error code: 429 - {'error': {'message': 'You have no
credits remaining. Add credits to continue using the API at
https://platform.openai.com/settings/organization/billing/.', 'type':
'insufficient_quota', 'param': None, 'code': 'credit_balance_exhausted'}}
```

Key prefix: `sk-proj-uFuU...`

### Interpretation

Anthropic (the writer's primary) is funded. OpenAI (the writer's fallback,
and the TTS second-tier) is still exhausted. Since Anthropic works, the
`llm-fallback` chain in `writing/llm_client.py` returns from Anthropic on
the first try and never needs OpenAI for content — the writer works. TTS
retries OpenAI on every clip (see below) but the TTS cascade
(ElevenLabs → OpenAI → Edge-TTS → gTTS) still lands on Edge-TTS/gTTS. Runs
complete successfully; log noise is elevated.

**Verdict: Branch A applies.** Proceeding to live test run.

---

## Step 0 (continued) — Live test-run: gaming pipeline

Same entrypoint the systemd timer uses:

```
/bin/bash /opt/genlab/CriticalRush/runbooks/daily_intel.sh
```

Invoked as `genlab` user, foreground on VPS. Test-run ID:
`gaming_20260805_152014`. Duration: 514 seconds (~8:30). This morning's
scheduled gaming run at 04:00Z failed with SLO violation `Zero blueprints
produced` due to credit exhaustion.

### Result

**SUCCESS.** Last 10 lines of `/tmp/pipeline_gaming_test.out`:

```
[PUSH] Created blueprint 'League of Legends' (status=VISUAL_READY)
[PUSH] 3 stories, 2 blueprints pushed to backlog (0 video-dedup skipped, 0 errors)
[Pipeline] Stage PushToBacklog completed in 2.3s
[FetchInsights] No backlog_client — skipping
[Pipeline] Stage FetchInsights completed in 0.0s
[PerformanceLearner] gaming snapshot: 74 arms, 0 linucb_obs, mean_spread=0.4742
[RunReport] gaming | partial | 514s | stories=3 blueprints=2 | QC: 100.0% | violations=0
[cost_persist] persisted run_id=gaming_20260805_152014 niche=gaming total_usd=0.0261
[Pipeline] Released niche lock 'gaming'
Finished: 2026-08-05 15:28:40 UTC | exit=0
```

Compared to the failed 04:00Z run:
- Blueprints: 2 vs 0
- SLO violations: 0 vs 1
- LLM cost: **$0.0261 vs $0.0000** (the 0-cost result of the failed run is what
  a credit-exhausted writer produces — no successful call, no cost)
- Exit: 0 vs 2

**Automation is not broken.** The 04:07 / 05:01 / 06:07Z failures were unfunded-
writer failures. Now that Anthropic is funded, the pipeline runs green.

Bonus finding from the healthy run: `[LLM judge] fired for niche=unknown
llm_decision=False reason=Hook is generic template` — the AUTO #1 quality gate
is actively judging content (independent evidence that Anthropic works, since
the judge is an Anthropic call). One blueprint reached `VISUAL_READY`
(rendered end-to-end), one stayed at `DRAFTED`, one was near-dupe-hook rejected
by dedup.

**Noise-that-is-not-failure observed during the run:**
- OpenAI TTS returned 429 on every clip attempt. Cascade fell through to
  Edge-TTS/gTTS. Not a failure, but log-noisy. Once OpenAI is funded, this
  quiets.
- `transformed output 14.63s < SPEC.min_duration 15.0s — returning base
  composite` — known issue per memory rule
  `[[transformation-attribution-min-duration-guard-trap-2026-07-09]]`. Not
  new. Not a blocker.

---

## Step 2 — "Horrible content": no output or bad output?

**Both, but each with a different cause.**

### 2a — Publishing history (`publishing_analytics`, last 7 days)

```
    day     |  niche_id   | platform  | count
------------+-------------+-----------+-------
 2026-08-05 | ai_creators | fb/ig/th/yt | 4
 2026-08-04 | ai_creators | fb/ig/th/yt | 4
 2026-08-03 | ai_creators | fb/ig/th/yt | 4
 2026-08-02 | ai_creators | fb/ig/th/yt | 4
 2026-08-01 | ai_creators | fb/ig/th/yt | 4
 2026-07-31 | ai_creators | fb/ig/th/yt | 4
(24 rows — ai_creators only)
```

**Only `ai_creators` has published anything in the last 7 days.** Gaming,
sports, movies, anime = zero publishes for the entire window. This is the
"nothing is publishing" face of the operator's complaint, and it is NOT the
credit exhaustion (which was overnight last night, not 7 days).

**Root cause (structural, not new):** per `CLAUDE.md` rule #22 and the AUTO #2
section, the auto-approver is enabled only for `ai_creators`
(`min_confidence=0.80, rollout_pct=0.1`). The other 4 niches require operator
manual approval via the dashboard. If manual approvals stop, publishing on
those channels stops. Meanwhile the pipelines keep generating blueprints
into the review queue that never get approved. That's the operator's "nothing
publishes" experience on 4 of 5 channels.

### 2b — Sample of content that DID publish (ai_creators, last 7 days)

Joining `publishing_analytics` × `blueprints`, one hook per day:

```
    day     |  niche_id   |                     hook
------------+-------------+---------------------------------------------------------
 2026-08-05 | ai_creators | Gemini Robotics 2 brings whole body intelligence to robots
 2026-08-04 | ai_creators | Intelligent whole-body control with Gemini Robotics 2
 2026-08-03 | ai_creators | Did Anthropic just kill the indie hacker...?
 2026-08-02 | ai_creators | We're giving 100,000 academic researchers free access to ...
 2026-08-01 | ai_creators | Using Voice in ChatGPT Work
 2026-07-31 | ai_creators | Apple's launches new 'Upgrade' program #Vergecast
```

**Editorial signal (real, but not a code emergency):**

- **Aug 3 hook is good** ("Did Anthropic just kill the indie hacker...?") —
  curiosity gap + provocative framing. This is what a hook should read like.
- **Aug 1, Jul 31 hooks are news-headline flat**. No curiosity, no
  personality. The Jul 31 one embeds a hashtag mid-hook (`#Vergecast`), which
  is a formatting bug for a hook — hashtags belong in caption.
- **Aug 4 → Aug 5 repeats the same topic** ("Gemini Robotics 2 …" twice, hooks
  90% overlapping). Either the trending fetcher is fixated on that story,
  the video_id dedup catches individual clips but not topic-level repetition,
  or the writer generated near-identical hooks from different videos. Two
  consecutive days of the same story is a content-freshness problem the
  operator would notice.

**This is a real editorial issue, but it is NOT the same problem as "the
automation is broken."** It is a hook-quality + topic-dedup issue on
ai_creators — one channel out of five, and only visible because ai_creators
is the only channel actually publishing. Fix scope belongs in a separate
content-quality session, not this stabilization pass. The prompt explicitly
scopes this out ("Do NOT rewrite the content pipeline in this session").

---

## Step 3 — Cosmetic alerts (confirmed non-urgent)

### 3a — psycopg `__del__` teardown crash

Confirmed cosmetic. Evidence:

- Movies pipeline today (03:30Z): **exit=0, has `Finished:` line** in log,
  produced blueprints. No __del__ crash observed for this run in syslog.
- Successful runs (ai_creators + movies) exited cleanly with `Finished: exit=0`
  lines in their logs. The `__del__` crash appears in syslog **only** at times
  that correlate with failed runs (2026-08-05T09:37 = gaming 04:07Z failure;
  2026-08-05T10:31 = sports 05:01Z failure). It does NOT appear at 08:00 IST
  (ai success) or 09:00 IST (movies success).

This matches the interpretation from `.audit/SYSTEM_ALERTS_TRIAGE.md`:
`__del__` fires at interpreter shutdown when a failed run bypasses normal
cleanup, leaving module-level `_PG_POOL` objects to be GC'd by the interpreter.
The crash appears AFTER the exit code is already set. It doesn't cause the
alert; it accompanies it.

**Optional fix — proposed, not applied:** at the two module-level pool
creation sites, register an `atexit` cleanup:

```python
# genlab-core/src/genlab_core/learning/metric_collector.py:247
_PG_POOL = ConnectionPool(db_url, min_size=1, max_size=4, open=True)
import atexit; atexit.register(_PG_POOL.close)   # <-- add

# genlab-core/src/genlab_core/learning/episodic_memory.py:330
_PG_POOL = ConnectionPool(...)
import atexit; atexit.register(_PG_POOL.close)   # <-- add
```

Cost: 2 lines. Benefit: silences the `RuntimeError: cannot join current thread`
noise that misleads incident triage (as it did in the operator's initial
framing of this session — "the __del__ crash corrupts exit codes" — which is
incorrect). Verification: watch syslog after next failed run; the crash line
should be gone. Not urgent.

### 3b — Affiliate broken rate 11.5%

Confirmed as the live face of audit findings A-0068 / A-0073. The checker
(`check_affiliate_links.py`) is behaving correctly per rule #26 (threshold-
based exit at 10%). 11.5% represents genuine dead affiliate URLs — a content
ops task (refresh ASIN/merchant mapping), not an infrastructure bug. Do not
fix here; already tracked.

---

## Operator actions (only you can do these)

1. **Fund OpenAI.** Anthropic top-up landed; OpenAI still at zero. Fallback
   works but degraded: TTS retries OpenAI on every clip (log noise, +2s
   latency per clip), and if Anthropic exhausts again, the writer has no
   fallback. Add credit at
   `https://platform.openai.com/settings/organization/billing/`. Verify with
   the same live-probe pattern used above.

2. **Wallet-match Anthropic (confirm the top-up went to the right key).** The
   key the VPS uses starts with `sk-ant-api03...`. When you top up, verify
   the account console shows a positive balance on THAT key's project/account,
   not on a Claude.ai subscription. This has been mis-routed before per the
   session context.

3. **Enable auto-reload / auto-recharge** on both Anthropic and OpenAI to
   prevent the ~19:00Z daily exhaustion pattern (Anthropic monitor fired
   every day 2026-07-31 → 2026-08-04 at ~19:00Z; daily burn > any one-shot
   top-up). This addresses the A-0065 reactive-monitor gap at the source.

4. **Decide the auto-approver strategy for the other 4 niches.** Options:
   (a) enable AUTO #2 for gaming/sports/movies/anime with a lower rollout_pct
   for observation; (b) resume manual review in the dashboard; (c) accept
   that those channels don't publish. If none of a/b/c happens, the 7-day
   publishing gap on 4 channels continues. This is not a code fix.

5. **(Optional, low-priority)** decide on the 2-line psycopg atexit fix
   above.

6. **(Content-quality, scoped separately)** editorial audit of the recent
   ai_creators hooks — the topic-dedup + hook-style issues are real but out
   of scope for this stabilization session.

---

## Ranked fix list — what actually needs a code change

| # | Item | Kind | Urgency | Notes |
|---|---|---|---|---|
| 1 | Fund OpenAI account | **Operator, not code** | High | Enables writer fallback + quiets TTS retries |
| 2 | Fund Anthropic (auto-recharge) | **Operator, not code** | High | Prevents recurrence of overnight exhaustion |
| 3 | Approval workflow decision for 4 niches | **Operator, not code** | High | 7-day publishing outage on 4/5 channels |
| 4 | `atexit.register(pool.close)` × 2 sites | Code, 2 lines | Low | Cosmetic; reduces triage confusion |
| 5 | Editorial hook quality on ai_creators | Content work, separate session | Medium | Not this session's scope |
| 6 | Affiliate ASIN refresh | Content ops | Low | Already tracked as A-0068/A-0073 |
| 7 | Proactive credit-balance monitor (A-0065) | Code, follow-up | Medium | Prevents next incident before it happens |

**Truth of the matter:** items 1, 2, 3 are the actual load-bearing fixes and
none of them are code. Items 4, 5, 6, 7 are real work but not what causes the
"everything is broken" experience today. **The automation isn't broken; it's
partially starved (OpenAI) and largely un-approved (4 niches). Fund + approve
and the system runs.**

---

## What was NOT changed in this session

- Zero code edits
- Zero systemd restarts / daemon-reloads
- Zero config drops-in (`/etc/systemd/system/*.service.d/` untouched)
- Zero deploys or merges
- One test-run of `daily_intel.sh` for gaming — this is a normal invocation
  the timer runs daily; the resulting 2 blueprints (`Escape from Tarkov`
  status=DRAFTED, `League of Legends` status=VISUAL_READY) join the review
  queue exactly as today's scheduled run would have if credits had been live
  at 04:00Z

Cost of the diagnostic: **$0.03** (the successful gaming test-run's LLM
spend).
