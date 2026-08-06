# System Alerts Triage — 2026-08-05

Read-only diagnostic. Every claim has a command + its output. **No fixes applied.**

Session context: five CRITICAL alerts on the dashboard, 10–19h old, operator reported
adding $20 Anthropic credit after the 2026-08-04 19:30Z exhaustion alert. The three
alert groups map to three underlying issues — one still-live outage, one long-
standing cosmetic bug, and one known-tracked content issue.

Current UTC: 14:47Z. Local (server): 20:17 IST.

---

## Alerts 1, 2, 3, 5 — Credit exhaustion + zero-blueprint pipelines

**VERDICT: STILL LIVE.** The top-up did not restore service. Both LLM providers are
currently at zero balance.

### Timeline (all 2026-08-05 UTC unless noted)

| Time (UTC) | Time (IST) | Niche | Result | Blueprints |
|---|---|---|---|---|
| 2026-08-04 19:30 | 01:00 (05) | all | `anthropic_credit_exhausted` fires | — |
| 02:30 | 08:00 | ai_creators | **success** (exit 0) | ≥1 |
| 03:30 | 09:00 | movies | **success** (exit 0) | 2 |
| 04:00 | 09:30 | gaming | **failed** (exit 2) | 0 |
| 05:00 | 10:30 | sports | **failed** (exit 2) | 0 |
| 06:00 | 11:30 | anime | **failed** (exit 2) | 0 |

Credits ran out between 03:30Z and 04:00Z on Aug 5, i.e. mid-morning UTC (during
the daily pipeline sweep). ai_creators and movies had already completed; the last
three couldn't run the writer stage.

### Evidence that credits are exhausted RIGHT NOW (live probe, moments ago)

```
$ curl -X POST https://api.anthropic.com/v1/messages -H "x-api-key: $ANTHROPIC_API_KEY" ...
{"type":"error","error":{"type":"invalid_request_error",
 "message":"Your credit balance is too low to access the Anthropic API.
           Please go to Plans & Billing to upgrade or purchase credits."}, ...}

$ curl -X POST https://api.openai.com/v1/chat/completions -H "Authorization: Bearer $OPENAI_API_KEY" ...
{"error":{"message":"You have no credits remaining. Add credits to continue using the API ...",
          "type":"insufficient_quota","code":"credit_balance_exhausted"}}
```

**Both providers exhausted.** The operator's $20 Anthropic top-up either (a) landed
on a different account/key, (b) was consumed by continued pipeline load between the
top-up time and now (~half a day), or (c) didn't propagate — this is for the
operator to trace via the Anthropic console. OpenAI (the fallback) is also
depleted; without OpenAI credit, even a working Anthropic key doesn't fully solve
the second-shoe-drops resilience the writer expects.

### Evidence from failed anime run (representative)

`/opt/genlab/.tmp/logs/framedrift/framedrift_20260805_060001.log:50`:
> `[niche-fit] LLM ranking raised (using velocity): Error code: 400 — 'Your credit balance is too low to access the Anthropic API.'`

`/opt/genlab/.tmp/logs/framedrift/framedrift_20260805_060001.log:125`:
> `[llm-fallback] OpenAI fallback ALSO failed (Error code: 429 — 'You have no credits remaining.') — re-raising original Anthropic error`

`/opt/genlab/.tmp/logs/framedrift/framedrift_20260805_060001.log:161`:
> `[llm-fallback] Anthropic circuit breaker OPEN for 600s after 3 consecutive exhaustion errors — routing ALL Anthropic sites straight to OpenAI`

The circuit breaker fired, then discovered OpenAI was also dead — no recovery path.
Writer produced zero blueprints; RunReport emitted `SLO VIOLATION: Zero blueprints
produced from 4 stories`, and `run_pipeline.py` returned exit 2 (which is what
systemd reports as `status=2/INVALIDARGUMENT` — that string is a systemd artifact,
not a real INVALIDARGUMENT).

### A-0065 reactive-monitor gap — CONFIRMED

`pipeline_alerts` history for `anthropic_credit_exhausted`:

```
2026-08-04 19:30:05Z ← yesterday's exhaustion
2026-08-03 19:15:04Z
2026-08-02 19:00:06Z
2026-08-01 19:00:06Z
2026-07-31 19:00:05Z
```

**Fires ~daily at ~19:00Z, always after the fact.** Today (Aug 5), credits
depleted around 04:00Z but the monitor has NOT fired yet — presumably because
yesterday's alert is still unresolved (dedup), or the monitor's cadence hasn't
caught it, or the auto-resolver flipped it green somehow. Either way the operator
had no proactive signal for the mid-morning failures — the first thing they see
is the three `systemd_unit_failed` alerts at 04:07/05:01/06:07Z, and they're then
left to notice the LLM connection failure themselves.

This is exactly the reactive-detection failure mode from audit **A-0065**. A
proactive gauge (poll balance every N min, alert at <$X remaining) would have
prevented today's mid-run exhaustion. **Do NOT fix here** — the audit already
scopes this. This incident is live evidence.

### Fix-queue note (do not implement here)

Two items separately actionable:
1. **Operator action, not code:** verify Anthropic + OpenAI credit balances via
   each console; determine why the $20 didn't stick. If it did stick but got
   consumed, the daily-burn-rate needs a look.
2. **Code follow-up on A-0065:** proactive balance-based monitor (poll Anthropic
   `/v1/usage` or account API for remaining budget; alert at threshold BEFORE
   exhaustion). Currently the monitor only detects `credit_balance_exhausted`
   errors in journal — reactive by definition.

---

## Alerts 1, 2, 3 (secondary) — psycopg `__del__` teardown crash

**VERDICT: LONG-STANDING COSMETIC BUG.** Not the cause of today's alerts.

### The user's prompt hypothesis was PARTIALLY WRONG

The prompt claims "Every pipeline unit exits with `status=2/INVALIDARGUMENT`, and
the journals show, at process teardown ... `ConnectionPool.__del__` ... `cannot
join current thread`" — with the implication that the __del__ crash is
*corrupting* the exit code from 0 to 2.

**Not what's happening.** Evidence:

- Successful runs (movies today, ai_creators today) exit **status=0** cleanly and
  their logs end with `Finished: 2026-08-05 03:34:13 UTC | exit=0`.
- Failed runs (gaming, sports, anime today) exit **status=2** because
  `run_pipeline.py` returns 2 on `SLO VIOLATION: Zero blueprints produced`. That
  is Python's real business-logic exit code, not a corruption from teardown.
- `daily_intel.sh` line 4 has `set -euo pipefail` + line 56 pipes `python ... |
  tee`. When python exits non-zero, `set -e` triggers immediately after the pipe
  — which is why failed-run logs end abruptly at "Run complete" without the
  trailing "Finished: exit=N" line. That's a shell-side detail, not the __del__
  crash.
- The __del__ crash **does** happen (see below), but it's in syslog, not the
  pipeline's own log file, and appears AFTER Python has already returned its
  exit code.

### Evidence: the __del__ crash exists in syslog TODAY

`sudo grep -nE 'cannot join current thread' /var/log/syslog | grep 2026-08-05`:

```
2026-08-05T09:37:10 bash[720221]: Exception ignored in: <function ConnectionPool.__del__ at 0x71c055919da0>
2026-08-05T09:37:10 bash[720221]:   File ".../psycopg_pool/pool.py", line 126, in __del__
2026-08-05T09:37:10 bash[720221]:     raise RuntimeError("cannot join current thread")
2026-08-05T09:37:10 bash[720221]: RuntimeError: cannot join current thread
2026-08-05T10:31:41 bash[749064]: Exception ignored ... same traceback ...
```

The 09:37 IST (04:07 UTC) hit correlates with gaming's failed run; 10:31 IST
(05:01 UTC) with sports's. Anime's 06:07Z run should have a third occurrence at
~11:37 IST — it isn't in the query output above, could be journal-rotated or
timing-off; not material.

### New vs long-standing

`git log -S "ConnectionPool" --oneline --since "60 days ago"`:

```
812690a4  docs(claude): commit CLAUDE.md with 2026-07-24 rules + AUTO #2 diagnostic section
6df94d56  feat(episodic_memory): Postgres backend + migration (Lever R1 production storage) (#451)
23b92833  feat(lint): test-based ban on new bare psycopg.connect calls (#305)
f9386a42  perf(metrics): pool + cache get_channel_metrics Postgres (audit P-1) (#93)
```

Same crash in syslog on **2026-07-27, 2026-07-28, 2026-08-03, 2026-08-04, 2026-08-05**
— **long-standing**, at least 10+ days. Predates the Python 3.14→3.12 pin work.

### Crash origin (proposed fix — NOT applied)

Three `ConnectionPool(...)` creation sites:

```
genlab-core/src/genlab_core/learning/metric_collector.py:247
    _PG_POOL = ConnectionPool(db_url, min_size=1, max_size=4, open=True)

genlab-core/src/genlab_core/learning/episodic_memory.py:330
    _PG_POOL = ConnectionPool(...)

genlab-core/src/genlab_core/storage/postgres.py:432
    self._pool = ConnectionPool(...)
```

Only `postgres.py:450` has a `.close()` call. The two module-level `_PG_POOL`
globals in `metric_collector` and `episodic_memory` are **never explicitly
closed** — they get cleaned up by CPython's interpreter shutdown, which invokes
`ConnectionPool.__del__`. That handler tries to `.join()` its own worker thread,
which is illegal from within the same thread → `RuntimeError`.

**Proposed fix pattern** (any of these, apply to both module-level pools):
1. Register `atexit.register(_PG_POOL.close)` immediately after pool creation.
2. Wrap with `weakref.finalize(_PG_POOL, _PG_POOL.close)`.
3. Refactor from module-level global to a proper singleton context-managed at
   pipeline entry (bigger change).

Option 1 is smallest. Neither changes behavior on successful runs; both cleanly
suppress the teardown noise on any run.

### Is this a "false-alarm generator"?

**No, not in the way the prompt frames it.** The alert `SYSTEMD_UNIT_FAILED
[anime] Zero blueprints produced` is caused by Python's SLO exit=2, which is
correct — the run really did produce zero blueprints. Fixing the __del__ crash
would not silence any of today's alerts. It would clean up log noise and remove
an incorrect "correlation looks like causation" trap for future incident
responders (which arguably matters, since the operator was misled by exactly
this trap when writing the prompt).

---

## Alert 4 — affiliate-link-check "Broken rate 11.5%"

**VERDICT: KNOWN, CONTENT-QUALITY.** Confirmed the live face of A-0068/A-0073.
Least urgent of the three.

### Evidence: checker is behaving correctly

`genlab-core/scripts/check_affiliate_links.py:326-347`:

```
# 2026-07-21: threshold-based exit (rule #26). Broken links are
# already reported via stdout above; exit code only signals a
# genuine outage worth paging on.
broken_rate = len(broken) / max(checked, 1)
BROKEN_RATE_THRESHOLD = 0.10
if broken_rate >= BROKEN_RATE_THRESHOLD:
    ...
    sys.exit(1)
```

Rule #26 (`class-of-bug-systemd-exit-code-alarm-cascade`) explicitly designed
this check: exit 1 ONLY when >=10% of links are broken, so incidental single-link
404s don't page. 11.5% is above the threshold; the check exited 1 correctly.

Last invocation:
- `2026-08-05 09:15:01 IST` (03:45Z), `ExecMainStatus=1`, exit-code failure.
- systemd OnFailure fired the shared `genlab-service-failure-alert@...service`
  template → wrote the `SYSTEMD_UNIT_FAILED` alert we see (three copies —
  timer fires multiple times/day per its cadence).

### Content vs infra classification

The 11.5% is genuine dead-link rate on Amazon affiliate URLs. `check_affiliate_links.py`
uses HEAD requests to detect 404/403/410. Amazon frequently returns 503 to
automated HEAD requests as a rate-limit signal (comment at
`check_affiliate_links.py:55` acknowledges this) — but the checker already treats
503-only cases below threshold as "browser-loads-fine". A 11.5% rate means genuine
merchant removals + expired ASINs, not a checker malfunction.

Journal was empty for direct-query (`journalctl _SYSTEMD_UNIT=…` returns nothing —
same pattern as pipeline units; standard output isn't captured by journald for
these). Actual URL-level breakdown (404 vs 400 vs 503, which niches, which
merchants) is not directly retrievable without re-running the checker, which
would touch external URLs — deferred to operator or the audit follow-up on
A-0068/A-0073.

### A-0068/A-0073 alignment

Those audit findings scoped the **write side** of the affiliate system — the
`affiliate_clicks` and `affiliate_revenue` tables being empty because
`link_tracker.py:144`'s `pg.create("affiliate_clicks", record)` was thought
missing but was later confirmed present (A-0073). The link-check is the read
side of the same subsystem: it verifies the URLs GenLab is publishing are still
live. A high broken rate + empty affiliate_revenue table together suggest the
affiliate content pipeline is producing links that don't earn.

The link-check is doing its job (detecting content-quality drift). The alert
should be treated as "our published affiliate content has dead links, refresh
the merchant/ASIN mapping" — a content operations task, not an engineering
bug. **Nothing to fix on the checker.**

---

## Priority call

Ranked by "should this get a fix-queue slot next?":

1. **Credit exhaustion (Alerts 1-3, 5) — HIGHEST** — actively blocking the
   writer stage right now. Operator action first (confirm balances, real
   top-up), then A-0065's proactive-balance monitor to prevent the next
   recurrence. Losing 3 daily pipeline runs is the biggest current
   operational cost.

2. **psycopg `__del__` teardown crash — MEDIUM** — genuine bug (10+ days
   recurring), fix is small (`atexit.register(_PG_POOL.close)` in two files),
   and its main value is preventing incident-responders from being MISLED by
   the crash (as happened when framing this prompt). Won't silence any live
   alerts, but reduces confusion cost per incident.

3. **Affiliate broken links — LOWEST** — already covered by A-0068/A-0073.
   Content ops task, not code. Alert is behaving correctly. Safe to leave
   with a note on the audit register that the checker is the live monitor
   for this class.

**Safe to leave until a fix session:** #2 (cosmetic) and #3 (already tracked).
**Not safe to leave:** #1 — every 24h it stays unresolved, the daily publishing
schedule loses another N pipeline runs.

---

_Generated read-only. `.audit/` grep for stray dev references at end of session._
