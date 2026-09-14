# NARR-03 §1b — ROOT CAUSE FOUND. Read-only; no reproduction needed.
2026-09-13 ~18:50Z. All Measured from deployed source + prod DB.

## THE DEFECT (one line of code, one missing element)

`genlab-core/src/genlab_core/writing/video_content_writer.py:286-295`
```python
_REQUIRED_LLM_FIELDS: frozenset[str] = frozenset({
    "hook", "instagram_caption", "twitter_content",
    "youtube_content", "facebook_content", "threads_content",
})
```
**`narration_script` is NOT in this set.**

Meanwhile `:1003` appends to the prompt, whenever `narration_target_seconds` is set:
`"  - narration_script  ← REQUIRED for narration-enabled niches"` + a 9-line spec.

**The prompt's REQUIRED list and the code's REQUIRED set are two implementations of one
contract, and they disagree on exactly one field — the field that is failing.**

## The mechanism, end to end [Measured]
`_complete_and_parse_json` (`:371-390`) computes
`missing = [k for k in _REQUIRED_LLM_FIELDS if not str(parsed.get(k,"")).strip()]`, and:
* `missing` on attempt 0 -> logs INFO, **retries**
* `missing` after retry  -> logs **WARNING** "LLM omitted required fields … downstream
  fallback will fill from related fields"
* otherwise -> `return parsed`

Because `narration_script` is absent from the set, when the model omits it:
1. `missing` stays empty -> **no retry** (the one mechanism that would have recovered it)
2. **no INFO, no WARNING** -> the omission is invisible; nothing logs it, ever
3. `parsed` returns without the key
4. `result.get("narration_script", "") or ""` -> `""`
5. gate 1 (`base_writing:905`) faithfully assigns `""` — innocent, as established
6. `GenerateAudio` sees empty content -> `narration_degraded=true`,
   `reason=script_generation_failed`
7. TTS falls to the legacy path -> speaks the CAPTION (the 06:20Z hashtags-and-URL finding)

## This explains every observation in the arc
| observation | explanation |
|---|---|
| intermittent success (09-10, 09-11, 09-12) | the model emits `narration_script` **voluntarily** some runs; nothing enforces it |
| total failure (09-09, 09-13) | the model omitted it; no retry exists to correct that |
| `script_too_long` on 09-11/09-12 | on those runs the field DID arrive and was rejected on **length** — a different, healthy path |
| no log naming the cause | the field is not checked, so its absence is not loggable |
| deploy correlation | **coincidence**, now excluded twice (§2: no commit in the 32-commit window touched `video_content_writer.py` or `base_writing.py`) |
| gates 1–4 clean | correct — the field never existed to propagate |

## Supporting exclusions completed this pass
* **§3 variant_type:** all 14 ai_creators blueprints since 09-09 are `single_clip`. No
  `storytime`, so the mutex is excluded and no documented conflict was in play.
* **§2 deploy diff:** 32 commits between the 09-12 fire (`1b66168e`) and the 09-13 fire
  (`e0143ec6`). Production LLM files touched: **`llm/fallback.py` only**. Neither
  `video_content_writer.py` nor `base_writing.py` was modified. The writer takes a generic
  `llm_client.complete(...)` and never references `fallback.py`. **The deploy cannot have
  changed the parse.**
* **§4 cost lead:** `32b06809` added `record_provider_usage()` to `cost_accumulator.py`, but
  it is **additive** — it records *fallback-provider* spend under a prefixed key
  (`belt:claude-haiku-4-5`) and does not touch primary-path recording. It does **not**
  explain the missing Anthropic spend on 09-13. §4 stays open.

## THE FIX — and a design constraint the obvious fix gets wrong
Adding `narration_script` to the module-level `frozenset` is **wrong**: the set is global,
but the field is required only when `narration_target_seconds is not None`. The four
non-narration niches would then retry twice and emit a WARNING on **every** story, forever.

Required shape: make the required-set **per-call**, e.g.
`required = _REQUIRED_LLM_FIELDS | ({"narration_script"} if narration_target_seconds is not None else frozenset())`
threaded into `_complete_and_parse_json`. The prompt already branches on exactly that
condition at `:1003` — the checker must branch on the same one, from the same variable, so
the two cannot drift again.

Stronger option (§4's branch, T-63): move the contract into
`response_format: json_schema` with `required`, so omission is an API-level error rather
than an empty string. Belt-and-braces: do both — schema makes omission impossible, the
per-call required-set keeps the retry path honest if the schema is ever bypassed.

## §1 instrumentation: still worth shipping, no longer needed to diagnose THIS
Logging response keys + model + template version remains valuable for permanent
readability, but it is not required to find this defect and should not gate the fix.

## Class
`[[class-of-bug-shared-contract-n-implementers-silent-divergence]]` — the prompt and the
validator are two implementers of "which fields are required", diverging on one element.
Detection heuristic for the sibling sweep: **any field named REQUIRED in a prompt string
must appear in the code-side required set, and vice versa.** That comparison is greppable
and should become a pin test.
