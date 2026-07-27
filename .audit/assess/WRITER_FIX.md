# GenLab — Writer Fix (2026-07-26 IST)

**Findings:** 82 (+3: F-0080 CRITICAL, F-0081 HIGH, F-0082 MEDIUM). Guards prepared as uncommitted local edits (29 lines); deploy pending operator authorization.

## 1. Daily-binary mechanism: `llm_hook_generator.py:1385`

**Not two runs, not a resource trip, not alternating sources.** One writer function has a silent-except that turns an entire pipeline run into title-only hooks.

`generate_platform_hooks(base_hook, story, niche_id)` at `llm_hook_generator.py:1273` — called ONCE per blueprint per pipeline run. If any exception fires (rate limit, credit exhaustion, timeout, network, malformed response), the except at line 1385 catches with `logger.debug` and returns `{p: base_hook for p in ("instagram","youtube","twitter","facebook")}`. The caller passes `story["title"]` as `base_hook`. **Result: whole run's hooks = source title verbatim.**

Timers show 1 run per niche per day (nightly-schedule 22:00 IST; per-niche pipelines 08:00–11:30 IST). One run = one API call state. Some days that call succeeds → CLEAN. Some days it doesn't → FALLEN, uniformly across all posts.

**Evidence:** 60/61 day-tagged rows over 21d are pure FALLEN or pure CLEAN; only 1 MIXED (ai_c 07-12, 4/6). If the mechanism were per-post scatter, most days would be MIXED. It is per-run.

**Not F-0054 (Phase 7's gaming-specific writer defect).** F-0054 was one manifestation of the same silent-except at a different site. F-0080 (this finding) generalises it to every niche via one code path.

## 2. Refusal-leak and passthrough are TWO bugs

- Passthrough FALLEN days: scattered across 21d (e.g. ai_c 07-11, 07-22, 07-24, 07-26).
- Refusal-shape hooks: 14 in 21d, all concentrated 07-07 to 07-12 (movies 9, ai_c 5 on 07-12 only).

Different date ranges. Different mechanisms. Refusal-leak fires when the LLM returns valid text that IS a refusal ("I need the Story Summary to write a hook..."); the writer's candidate loop (`llm_hook_generator.py:638-670`) checked length + banned phrases + banned regex patterns but had no refusal-shape check. Refusal text passes all three, gets accepted.

**F-0082 filed** with a 16-line refusal-shape rejection added at line 650 area — prepared and staged.

## 3. Guards prepared (NOT DEPLOYED)

29 lines in `llm_hook_generator.py`; `git diff` clean, `git checkout` reverts:

- **Refusal rejection** (line ~654): 12-pattern prefix + first-60-char match; continues to next candidate; WARN with truncated raw.
- **Silent-except elevation** (line 1385): `debug` → `warning` with `classify_llm_error` attribution. Does NOT remove the base_hook fallback — that needs a run-level retry harness that doesn't exist. Interim: converts invisible quality bug into WARN pager.

**FALLEN-day verification pending deploy.** Deploy path per `OPERATOR_TASKS.md` task 3.

## 4. Anime freshness

30d avg fetch-to-publish: **anime 7.3d (max 13)**, movies 6.1d, ai_c 4.6d, gaming 3.5d, sports 3.2d. Anime is 2× slower than the two closest niches. Confirms the rejection split: half of anime rejects are decent writing on stale trends. **F-0081 filed HIGH.**

Options (do not decide): raise anime cadence to 2/day so trending moments ship within 24h; tighten source scoring to reject items older than 48h at fetch; or accept staleness and cut mandate from 7/wk. Not a writer fix; a source-selection/scheduling problem specific to anime's trend-decay curve.

## 5. Anthropic + reframed re-measurement

**Anthropic still exhausted** at 14:00 IST 2026-07-26 (session 8). Now parallel to writer-fix deploy, not prerequisite.

**2026-08-02 measures RESIDUAL passthrough after outage noise clears — the floor, not zero.** Passthrough only reaches zero after F-0080 guard ships AND a FALLEN-pattern day passes clean. Expecting Phase 7's 0% ai_c passthrough after top-up alone would be wrong — the writer fix takes it there.

All shells exited. `.audit/` writes only. Guards uncommitted pending operator authorization.
