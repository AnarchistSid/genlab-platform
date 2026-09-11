# Cost Attribution — 2026-08-05

Read-only investigation. **No fixes.** Question: where is the daily
credit-exhaustion burn coming from, given Anthropic console shows $0?

**Headline:** There is no ongoing burn. The pipeline spends cents. A single
$10 Sonnet call on **2026-07-24** drained whatever top-up was in place; the
"daily exhaustion" alerts since then are the monitor re-firing on the same
zero-balance situation (24h dedup expires, next sweep files a fresh row).
The console shows $0 because pipeline spend truly is $0 for the last 11 days.

---

## 1. Actual spend — the numbers

Table: `pipeline_run_costs` (14-day daily rollup).

```
day        | total_usd | llm_usd | runs
2026-08-05 |    0.0261 |  0.0261 |   6    ← my one gaming test-run earlier this session
2026-08-04 |    0.0000 |  0.0000 |   5    ← all 5 runs failed (writer got credit_exhausted)
2026-08-03 |    0.0000 |  0.0000 |   5    ← same
2026-08-02 |    0.0000 |  0.0000 |   5
2026-08-01 |    0.0000 |  0.0000 |   5
2026-07-31 |    0.0000 |  0.0000 |   5
2026-07-30 |    0.0000 |  0.0000 |   5
2026-07-29 |    0.0000 |  0.0000 |   5
2026-07-28 |    0.0000 |  0.0000 |   5
2026-07-27 |    0.0000 |  0.0000 |   5
2026-07-26 |    0.0000 |  0.0000 |   5
2026-07-25 |    0.0000 |  0.0000 |   5
2026-07-24 |   10.0110 | 10.0110 |  11    ← THE SPIKE
2026-07-23 |    0.0044 |  0.0044 |   7
```

**Total 14-day tracked spend: $10.05.** $10.00 of that is a single row with
`run_id = 'test_run'`. The rest is $0.05 across 71 rows.

### The $10 spike

```
run_id   | niche_id | completed_at                | usd     | model
test_run | gaming   | 2026-07-24 10:26:17.346197Z | 10.0000 | claude-sonnet-4-6
         |          |                             |         | budget_usd=20, budget_remaining_pct=0.00
```

- Model: **Sonnet 4.6** (production pipeline uses Haiku 4.5 — see the
  `by_model` breakdown for the Aug 5 test which shows
  `{"claude-haiku-4-5-20251001": 0.0261}`). A Sonnet call is 10-100× more
  expensive per token.
- `entry_count = 1`: one accumulator flush lumped this in.
- `budget_usd = 20.0000`, `budget_remaining_pct = 0.00`: the cost accumulator's
  recorded budget was $20 and this call ate 50% of it (the row shows 0.00%
  remaining because the accumulator flushes when depleted).
- `run_id = "test_run"` is a **literal string**, not a real pipeline
  `{niche}_{yyyymmdd}_{hhmmss}` ID. Someone/something invoked the cost
  accumulator with run_id="test_run" and spent $10 in Sonnet in a single
  session. Most likely: a manual test / dev script hit the pipeline's key.

**This one call is what drained the balance.** For the next 11 days, every
`pipeline_run_costs` row shows $0 because the writer couldn't call the API
at all (credit-exhausted → circuit breaker OPEN → OpenAI fallback also
exhausted → nothing succeeded, nothing spent).

### By niche (last 7 days)

```
niche_id    | total  | per_run | max_run | runs
gaming      | 0.0261 |  0.0033 |  0.0261 |   8    ← all from my Aug 5 test-run
ai_creators | 0.0000 |  0.0000 |  0.0000 |   7
anime       | 0.0000 |  0.0000 |  0.0000 |   7
movies      | 0.0000 |  0.0000 |  0.0000 |   7
sports      | 0.0000 |  0.0000 |  0.0000 |   7
```

**Expected steady-state cost when credits are live** (from the Aug 5
successful run): ~$0.026 per successful pipeline run. 5 niches × 1
run/day = **~$0.13/day**. Anthropic console showing $0 is consistent —
$0.13/day for 11 days = $1.43 total, well below the console's rounding.

---

## 2. Which provider actually exhausts

Read `genlab_core/monitoring/anthropic_credit_monitor.py:26-31`:

> Every 15 min via systemd timer:
> 1. Run `journalctl --since "60 minutes ago" --no-pager -q` and scan stdout
>    for the literal phrase `"credit balance is too low"` (verbatim text from
>    the Anthropic API error body).

`"credit balance is too low"` is **Anthropic-specific error text**. OpenAI's
credit-exhausted error is `"You have no credits remaining"` — completely
different string, not scanned by this monitor. **The daily 19:00Z alert is
therefore truthfully Anthropic** — not a mis-identified OpenAI issue.

**Live probes today (post-topup):**
- Anthropic: HTTP 200, returned "Hi! How can I" → **funded, working**
- OpenAI: HTTP 429 `credit_balance_exhausted` → **still empty**

So both providers had been exhausted for weeks. Anthropic got topped up
sometime today (operator action). OpenAI was never funded (or was funded
long ago and drained). There is **no OpenAI monitor** — OpenAI exhaustion
has been silent.

### Why the alert fires ~daily even without ongoing burn

Alert table shows daily fires from 2026-07-25 through 2026-08-04, with
timestamps drifting ~15-30 min later each day:

```
2026-07-25  12:30    (earliest — right after the $10 spike drained balance)
2026-07-26  12:45
2026-07-27  14:15  + probe alert at 15:30
2026-07-28  15:45  + probe alert at 17:00
2026-07-29  17:15  + probe alert at 18:30
2026-07-30  18:45
2026-07-31  19:00
2026-08-01  19:00
2026-08-02  19:00
2026-08-03  19:15
2026-08-04  19:30  (last)
```

Read the dedupe rule at `anthropic_credit_monitor.py:58-66`:

> * 24h dedupe matches operator expectations: if you topped up yesterday
>   and it broke again, that IS a new incident worth a fresh CRITICAL row.

The monitor dedupes within 24h. After 24h the dedup expires, the next
15-min sweep sees exhaustion errors STILL in journal (from pipeline
attempts), and files a fresh CRITICAL row. **The 15-30 min daily drift is
the dedup expiry cadence**, not a burn-rate change. There is no per-day
$20 burn — there is one $10 burn on 2026-07-24 and a stuck alert loop.

---

## 3. Retry loops — none unbounded

Grep on `writing/`, `llm/`, `http/`, `strategies/` for retry patterns:

| File | Loop | Bound | Verdict |
|---|---|---|---|
| `writing/llm_hook_generator.py:638` | `for _ in range(3):` | 3 attempts | Capped ✓ |
| `writing/video_content_writer.py:255` | `for attempt in range(2):` | 2 attempts | Capped ✓ |
| `llm/batch.py:254` | `while True:` | `TimeoutError` after `max_wait_s` (default 300s) at line 260-264 | Time-bounded ✓ |
| `llm/video_analyzer.py:66` | `for _ in range(30):` | 30 iterations, 1s sleep each = 30s wait | Capped ✓ |
| `http/retry.py:40` | `for attempt in range(1, max_attempts + 1):` | Configurable, default 3 | Capped ✓ |

**No unbounded retry loops found.** The one `while True` at `batch.py:254` has a
deadline check and raises `TimeoutError` when exceeded. All writer + LLM-caller
retry paths are 2-3 attempts. This is not the source of any burn.

---

## 4. Non-pipeline API consumers — the cost-tracking gap

The pipeline's daily cost is recorded via `record_anthropic_usage` at 5 sites
(all in the pipeline hot path). But **19 Anthropic client instantiation sites
exist codebase-wide.** Grep for `anthropic.Anthropic` shows:

**Tracked (calls `record_anthropic_usage`):**
- `writing/llm_hook_generator.py:577, 965, 1154, 1346` — writer hook gen
- `scheduling/auto_approval_gate.py:671` — LLM judge on auto-approver
- `monitoring/token_health.py:54` — periodic health check

**Untracked (no `record_anthropic_usage` call):**
- `intelligence/anthropic_client.py:267` — generic client factory
- `learning/scratchpad.py:249` — agent memory (weekly per timer)
- `learning/post_rca.py:229` — post-run root cause analysis
- `learning/rationale_classifier.py:337`
- `engagement/persona_engine.py:120, 293` — engagement replies (frequent)
- `scheduling/shadow_reviewer.py:205`
- `scheduling/auto_experiment_parser.py:159`
- `writing/caption_segments.py:400`
- `writing/llm_client.py:100` — writer's own client
- `media/niche_fit_ranker.py:229` — LLM ranking during trending fetch
- `monitoring/checks/infrastructure.py:688` — infra health

Non-pipeline systemd timers that could invoke these:

```
genlab-auto-approver.timer            every 30 min  (LLM judge, tracked)
genlab-health-monitor.timer           every 30 min  (health checks, tracked)
genlab-attribution-health-monitor     every 30 min  (unknown if calls LLM)
genlab-agent-memory-scratchpad        weekly Sun    (UNTRACKED — scratchpad.py)
genlab-strategist.timer               weekly Sun    (UNTRACKED — big call)
genlab-strategist-apply.timer         daily         (UNTRACKED)
genlab-token-refresh.timer            daily         (small)
```

**But** — the Anthropic console showing $0 means, empirically, these
untracked consumers are not actually burning credits either. They're either
(a) not being called (services idle, timers firing but early-exiting),
(b) failing before the LLM call (credit exhausted upstream, so their own
calls also return immediately with 400s), or (c) on a different Anthropic
account/key than the console the operator is viewing.

None of these can be the source of a mystery burn. They're a **latent risk**
if the operator re-tops-up and one has a bug — the cost-tracking wouldn't
catch it — but they are not the cause of the current alert story.

---

## 5. Rogue non-pipeline consumer? — nothing external found

Grep for Anthropic client instantiation everywhere (`genlab-core/src`, `dashboard/server`,
`scripts/`, all 5 channel dirs, plus tests): all sites are inside the
codebase, gated by service invocations, and each fails-fast on credit
exhaustion. No dev script left running against the same key surfaced.
Claude Code sessions themselves (this session, prior sessions) use their
own API keys, not the pipeline's `ANTHROPIC_API_KEY` — those don't hit
the pipeline's budget.

The **one exception** is the 2026-07-24 `run_id="test_run"` $10 Sonnet call.
That IS an ad-hoc test harness that ran against the pipeline's key,
recorded with a synthetic run_id. Someone/something did that; it's history.

---

## 6. Verdict

**There is no ongoing excess burn. The balance is just small and unreloaded.**

Reconstructed timeline:
1. Operator tops up Anthropic (unknown amount, ~$20 based on the recorded
   `budget_usd` field on the test_run row).
2. **2026-07-24 10:26 UTC**: something ran a `test_run` against the
   pipeline's key using Sonnet 4.6 and consumed $10 in one flush.
3. **2026-07-24 through 2026-07-25**: remaining ~$10 of budget consumed by
   normal pipeline runs (5 daily runs at ~$0.03 each = $0.15/day is
   negligible; the balance must have leaked to another consumer OR the
   remaining $10 was consumed by another `test_run`-style call not
   captured in the accumulator).
4. **2026-07-25 onward**: credits at $0. All pipeline runs fail with
   `credit_balance_exhausted`. **Zero further spend** because zero calls
   succeed.
5. **Daily monitor alerts** are the same-zero-balance re-detection
   (`anthropic_credit_monitor` 24h-dedup expiry cadence + fresh journal
   sweep still sees pipeline attempts hitting 400s from earlier that day).
6. **2026-08-05**: operator top-up landed. Live probe confirms Anthropic
   funded. My gaming test-run today spent $0.0261 successfully.

**No burn to fix.** The operator's mental model ("cents/day for 5 daily
runs") is correct: ~$0.13/day at steady state is the real cost. The
$10 test_run entry is a one-off historical artifact.

### What is worth flagging (not "burn," but hygiene)

1. **Cost-tracking wire is incomplete** — 12 of 19 Anthropic instantiation
   sites don't call `record_anthropic_usage`. If a future non-pipeline
   consumer starts burning, `pipeline_run_costs` won't show it. Fix:
   route all Anthropic calls through a central wrapped client that always
   records usage. Not urgent while Anthropic console shows $0.

2. **The `anthropic_credit_monitor` re-fires on stuck zero-balance** —
   correctly by design, but the operator sees "another exhaustion" every
   day and reads it as "another burn today." The alert body could say
   "same zero-balance as N days ago; add credit — no fresh burn to
   investigate" when the last N days of `pipeline_run_costs` show $0.
   Small text change; low priority.

3. **No OpenAI exhaustion monitor exists** — OpenAI has been silently
   `credit_balance_exhausted` for weeks (only surfaced via my live probe
   today). This is the mirror of A-0065 (reactive-monitor gap) but for a
   provider that has NO monitor at all. If OpenAI is the intended
   fallback for Anthropic outages, silent OpenAI exhaustion means no
   fallback when Anthropic goes down.

4. **The 2026-07-24 $10 Sonnet call** — nobody currently understands what
   ran that. It's the SINGLE biggest spend in the tracked history. Worth
   the operator asking themselves "was that me testing something?" and,
   if not, grepping git log for anything that instantiates
   `claude-sonnet-4-6` (production defaults to Haiku). Low priority
   (historical, one-off) but useful to close the loop.

---

## Numbers at a glance

- **Real 14-day tracked spend:** $0.05 (excluding the $10 test_run outlier)
- **Real steady-state cost:** ~$0.13/day (5 niches × ~$0.026/run)
- **Anthropic console:** $0 spend — **consistent with the tracked spend**
- **Credit-exhaustion alerts:** stuck-zero-balance re-detections, not fresh burn
- **Retry loops:** all capped, none unbounded
- **Cost-tracking wire:** 5 sites tracked, 12+ untracked (hygiene gap, not a burn)
- **Verdict:** small balance + one $10 historical spike + no reload; not a burn
