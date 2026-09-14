# LLM-PRIMARY-01 §2 gate 1 — PASS. 2026-09-14 ~03:30Z.
Real caller (`write_video_content`) on prod, Anthropic UNFUNDED — reality supplied the
forced failure, so no patching was needed to prove the failover.

```
INFO llm_client: [llm-belt] served by belt:claude-sonnet-4-6 (1762 chars)
INFO video_content_writer: [ai_creators] writer response: attempt=0
     model=claude-sonnet-4-6 sys_sha=0cddf60e chars=1762
     keys=['facebook_content','hook','instagram_caption','narration_script',
           'threads_content','twitter_content','youtube_content']
hook             : "What does Claude 4 handle that GPT-4o still can't?"
narration_script : 404 chars   NON-EMPTY: True
head             : 'A million-token context window sounds like a spec-sheet boast, but the
                    architectural signal here is worth scru…'
```

**Four things proven at once, none of which had ever been observed together:**
1. Belt serves the writer as PRIMARY — zero `llm-fallback` lines; the direct path was
   never reached, and Anthropic is empty, so belt is unambiguously carrying it.
2. The §2 instrumentation (`9b5fa8f4`) fires on a real call and shows `narration_script`
   in the response keys — the boundary NARR-03 spent a day excluding is now one grep.
3. `narration_script` is NON-EMPTY, 404 chars, on-topic original commentary. **This is the
   first working narration since 2026-09-12**, and the first evidence the contract fix
   (`24d48bb5`) sits on a path that can actually produce one.
4. `attempt=0` — the model complied first time, so the retry did not fire. The retry
   recovering an omission remains unobserved; worth seeing once, but not a blocker.

## Gap found by the gate
`task=` is EMPTY in the belt log — `BeltResult.task_id` came back blank for this app, so
`belt task cost <id>` cannot be used to reconcile per-call spend. Combined with the `run`
function returning `usage=None`, per-call cost attribution currently records the CALL but
not its tokens or dollars. Spend is still visible in aggregate via `belt balance`.
Filed, not worked around.

## Still open from LLM-PRIMARY-01
* §1b Tier B — the ten raw-SDK modules (2 of 10 classified; `hook_classifier` is the
  migrate-and-elevate case, `auto_approval_gate` is keep-raw-with-reason).
* §1c exhaustion-type fix — Anthropic sends `invalid_request_error`, not `billing_error`;
  the prose markers are load-bearing and must be annotated so no refactor deletes them.
* §1d pre-flight funded check.
* §3 false-green counter (`wrote N stories (N LLM, 0 failed)`).
* §4 record updates — CLAUDE.md still says Anthropic is primary.
* §2 gates 2–5: the three Tier A siblings, a real 02:30Z fire, and whisper ≥ 0.9 after the
  publisher. Phase 0's ai_creators gate cannot close until that fire runs.
