# FIX-LLMFB-2 — §1 reconnaissance + §5 corrections. READ-ONLY. 2026-09-14 ~03:10Z.
Nothing shipped. §1–§4 ship next session per standing note, gated as §2.

## THE OUTAGE, measured verbatim (tonight's ai fire, 08:09 IST = 02:39Z)
```
[llm-fallback] Anthropic BadRequestError → falling back to OpenAI gpt-4o-mini:
    400 invalid_request_error 'Your credit balance is too low to access the Anthropic API'
[llm-fallback] OpenAI fallback ALSO failed (429 insufficient_quota:
    'You have no credits remaining') — re-raising original Anthropic error
[circuit-breaker] ANTHROPIC CIRCUIT OPEN: 3 consecutive exhaustion errors
```
Detection was CORRECT: `should_fallback()` returns True for this exact error via the
`"credit balance is too low"` marker (verified against a reconstructed exception on prod).
Both flags on; `OPENAI_API_KEY` set. **The chain worked and had nowhere to go.**
Belt credit available and unreachable: **$102.15**.

## §1 — CALLER MAP: who is covered, who is not

### Tier A — routes through `AnthropicLLMClient` (has the OpenAI fallback today,
### would gain the belt tier from the §1 fix)
| module | line |
|---|---|
| `strategies/base_writing.py` | 1271 — **the writer**; this is the path that failed tonight |
| `media/chart_data_extract.py` | 107 |
| `media/music_mood_llm_fit.py` | 209 |
| `monetization/dynamic_matcher.py` | 97 |

### Tier B — uses the RAW `anthropic` SDK directly: **NO fallback at all, not even OpenAI**
`scheduling/auto_approval_gate.py` · `learning/hook_classifier.py` ·
`learning/rationale_classifier.py` · `scheduling/shadow_reviewer.py` ·
`scheduling/auto_experiment_parser.py` · `intelligence/anthropic_client.py` ·
`publishing/first_comment_question.py` · `llm/router.py` · `llm/batch.py` ·
`llm/errors.py` (definitions)

**This is the finding §1's grep was for, and it is larger than the writer.** Ten modules
bypass the client that owns the failover. Several are consequential: the auto-approval
LLM ensemble, the hook classifier, the shadow reviewer, the strategist's client. On a
credit outage they do not degrade — they fail, each in its own way, and several are
fail-open, so they will have been returning defaults tonight without saying so.

**Scope decision for next session:** the §1 fix (belt tier inside `AnthropicLLMClient`)
covers Tier A only. Tier B needs either migration onto the shared client or an explicit
per-module reason. Do not report FIX-LLMFB-2 as "the chain is three-deep" until Tier B is
resolved — that is the same overclaim being corrected in §5 below.

## §5 — CORRECTIONS TO THE RECORD

1. **`1272cb25`'s causal claim is WITHDRAWN.** The `_REQUIRED_LLM_FIELDS` divergence did
   NOT cause the narration failures. No LLM call succeeded on the failure days, so there
   was no response whose fields could be checked. The 09-10/11/12 successes were days with
   credit, not days when the model happened to comply.
   **The defect and its fix stay**: `narration_script` genuinely was unenforced, `reveal`
   still is, and `24d48bb5` + the contract pin are correct and worth keeping. They are
   necessary and were not sufficient, and tonight proved they were not the cause.

2. **The cost-attribution finding is WITHDRAWN.** `llm_usd = $0.0000` on 09-09 and 09-13
   was accurate, not a recording failure: no call succeeded, so no spend existed. My
   "pipeline_run_costs did not record writer LLM spend" claim (4e3a0223) is wrong.

3. **My statements on the 12th and 14th were wrong.** I said the fallback made a top-up
   unnecessary, and that the writer's chain was three-deep. Neither was true: the belt tier
   was never reachable from `llm_client.complete()`.

4. **The `_skip_llm` root cause (1c55d981) remains withdrawn** — already retracted in
   4e3a0223; noted here so the chain of corrections is readable in one place.

**Corrections this week: four.** RSS sourcing, the FIX-LLMFB deploy, the `_skip_llm` skip,
and the contract divergence as cause. Each was refuted by one query. The pattern worth
naming is not the individual errors — it is that every one of them was a *downstream*
explanation for an *upstream* precondition nobody checked.

## §3 — THE FALSE GREEN (for next session)
`[ai_creators] WritingStrategy: wrote 4 stories (4 LLM, 0 failed)` was logged in the fire
where all four LLM calls returned 400. The counter tracks stories *processed*, not
*written*. This line made the writer look healthy across three separate investigations
(NARR-02, NARR-03, NARR-04). Fix per brief: `failed` counts LLM exceptions and empty
responses; `failed > 0` → WARNING; `failed == total` → ERROR to the Slack outage webhook.

## §4 — THE UNCHECKED PRECONDITION (for next session)
No diagnostic this week asked whether the account was funded. Pre-flight per fire, one
minimal call per provider, `providers_funded: {anthropic, openai, belt}` in the run report,
T-233's runway guard reading that field (it currently reports healthy at zero), Slack alert
on the first unfunded fire. Belt balance is directly readable via `belt balance` (minus the
$5 reservation hold per running task); Anthropic's is not, so the probe call is the
instrument.

# =====================================================================
# §0 + §3 partial inventory — 2026-09-14 ~03:00Z. READ-ONLY.
# =====================================================================

## §0 — top-up had NOT landed at 02:56Z [Measured]
Live probe as genlab with the prod key:
`ANTHROPIC: FAILED BadRequestError error.type=invalid_request_error :: 'Your credit balance…'`
The ai fire was still running (ExecMainExitTimestamp empty at 02:55Z).

### NEW HAZARD found by that probe — the type check never matches [Measured]
Anthropic returns **`error.type = "invalid_request_error"`** for credit exhaustion, NOT
`"billing_error"`. `ad6b4d18` ("detect Anthropic exhaustion by error.type, not by prose")
keys on `billing_error`, whose docstring calls it "the authoritative signal [that] does not
depend on prose". **It never fires in production.** The thing actually detecting exhaustion
is the prose fallback immediately below it:
```
if _error_type_of(exc) == "billing_error":   # never true for real exhaustion
    return True
msg = str(exc).lower()
return any(marker in msg for marker in _ANTHROPIC_EXHAUSTION_MARKERS)   # <- this fires
```
**Latent hazard:** the prose markers look redundant next to a typed check and are exactly
what a tidying refactor would delete. Deleting them silently disables all failover.
The type check should keep `billing_error` AND add `invalid_request_error` gated on the
credit prose, or the markers need a comment saying they are load-bearing. Filed.

## §3 — Tier B inventory, TWO of ten classified properly

| module | LLM path | on failure | log level | consumer | decision |
|---|---|---|---|---|---|
| `scheduling/auto_approval_gate` | **shadow-mode**; gate calls `ensemble_decide(..., enable_llm_judge=False)`; judge itself is opt-in via `GENLAB_LLM_JUDGE_ENABLED` | returns; gate decision unaffected | **WARNING + exc_info** | none — rule decision is authoritative | **KEEP RAW, with reason.** Not load-bearing, already loud. The brief's "look hardest" case does not apply — this one is built correctly. |
| `learning/hook_classifier` | load-bearing scorer | **returns `None`** | **DEBUG** (rule #19) | `conformal_router.py:220,459` reads `hook_classifier_score` with **`default=0.5`** | **MIGRATE + elevate to WARNING.** This is the real "silent default" case: on an outage the router makes decisions on a neutral 0.5 that is indistinguishable from a measured score, and nothing above DEBUG says so. |

**Remaining eight NOT yet classified** (`rationale_classifier`, `shadow_reviewer`,
`intelligence/anthropic_client`, `first_comment_question`, `llm/router`, `llm/batch`,
`auto_experiment_parser`, plus `llm/errors` which is definitions only). Crude counts were
taken and are not sound enough to decide on — a per-module read of the actual failure path
is needed, as the two above demonstrate: the counts ranked `auto_approval_gate` as the
highest risk and the real read reversed it.

**Method note for the rest:** classify by (i) what the failure path RETURNS, (ii) at what
LOG LEVEL, (iii) whether a downstream consumer substitutes a default for the absent value.
(iii) is the one that matters — `hook_classifier` is dangerous not because it returns None
but because `conformal_router` turns that None into 0.5.
