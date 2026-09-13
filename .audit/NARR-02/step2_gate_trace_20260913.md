# NARR-03 §1 — four-gate trace. READ-ONLY, static + prod config probe. 2026-09-13 ~18:45Z.

## Result: gates 1–4 are NOT the defect. The drop is UPSTREAM of all of them.

### Gate 1 (writer result -> content dict): INTACT [Measured]
`ae76e975` IS deployed (prod HEAD `e0143ec6`; commit present in prod history) and the
NARR-06 comment block survives at `base_writing.py:866-885`. Critically the assignment
itself is **unconditional**:
```
_narr_script = result.get("narration_script", "") or ""
if narration_target_seconds is not None and _narr_script.strip():
    _narr_script, _narr_reason = self._validate_narration_with_retry(...)   # may rewrite
    ...
content["narration_script"] = _narr_script        # :905 — OUTSIDE the if
```
So gate 1 faithfully copies whatever `result` holds, including `""`. It did not regress.
**Therefore the field was already absent from `result` — gate 1 had nothing to drop.**

### The prompt DOES ask for it [Measured]
`video_content_writer.py:1003` appends, only when `narration_target_seconds is not None`:
`"  - narration_script  ← REQUIRED for narration-enabled niches"` plus a 9-line spec.
`narration_script` is also in `_SENTENCE_CASE_FIELDS` (:39). The request is well-formed.

### The writer DID ask [Inferred, strongly]
`narration_target_seconds` stays `None` only if (a) `is_narration_enabled_for()` is False,
or (b) the gate raises — which logs `"narration gate check raised … falling back to legacy
writer path (no narration_script)"`. A third path logs `"could not resolve the render
duration … falling back to the 30s baseline"`. **None of those three strings appears in the
09-13 ai window** (control: 626 lines). So the gate returned True and a target was computed.

### Conclusion: the drop is at the **LLM response -> `result` dict** boundary
Everything downstream of that is confirmed sound. This is the one boundary §1 cannot read
statically, and is exactly what NARR-06 needed a standalone reproduction to settle. Two
candidates remain, indistinguishable without logging the raw response:
  * the model omitted `narration_script` despite the REQUIRED line (compliance variance), or
  * the JSON parse in `write_video_content` dropped it.
**Recommend §1's reproduction target the response parse specifically** — log the response
JSON *keys* (not content) at DEBUG plus the prompt-template version. That is a one-line
change and makes this boundary readable forever.

## §6 REFRAME — the four-niche "absence" is CONFIG, not a defect [Measured on prod]
```
env master flag : True
  ai_creators   yaml_on=True   ENABLED=True   wpm=141
  gaming        yaml_on=False  ENABLED=False  wpm=141
  sports        yaml_on=False  ENABLED=False  wpm=141
  movies        yaml_on=False  ENABLED=False  wpm=141
  anime         yaml_on=False  ENABLED=False  wpm=141
```
The four non-canary niches have never produced a `narration_script` because **narration is
switched off in their YAML**, not because anything drops it. `is_narration_enabled_for`
requires env AND yaml; env is on, their yaml gate is off.

**This changes NARR-02 §3 from "build narration on four niches" to "enable it on four
niches and verify".** The writer, prompt, validator and propagator are already niche-generic
— the four simply never enter the branch. It also explains their `degraded=false` with an
empty reason: not attempted, exactly as the flag says.

**It does NOT change the sequencing.** §3 must still wait: enabling four niches while
ai_creators' response-boundary drop is unresolved would reproduce the same silent failure
five times — the brief's own argument, now with a measured reason.

## Open, not determined this pass
* Which of the two response-boundary candidates is the cause. Needs the reproduction.
* What differed between the 09-12 fire (script produced, rejected `script_too_long`) and
  09-13 (`script_generation_failed`). Both had narration enabled and a target; on 09-12 the
  field arrived and on 09-13 it did not. `variant_type` was NOT checked this pass —
  the storytime-mutex hypothesis from the brief remains untested.
* Deploy diff between the two fires: prod HEAD moved to `e0143ec6` at 09-12 14:22Z, i.e.
  BETWEEN them. The commits in that window are FIX-LLMFB's chain. Since the LLM call
  demonstrably still ran and succeeded, a wrapper that altered the *response handling*
  (rather than the call) is the one FIX-LLMFB-adjacent hypothesis NOT yet excluded.
  **Do not treat this as confirmed — the same deploy was already refuted once as a cause.**
