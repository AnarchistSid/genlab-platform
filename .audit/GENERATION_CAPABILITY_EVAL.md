# Content-Generation Capability Evaluation — 2026-08-05

Read-only assessment of the **machinery** (not the published output — that
was `.audit/CONTENT_BENCHMARK.md`, don't repeat). Question: **where is
quality structurally won or lost in the generation pipeline?**

**Headline verdict:** the biggest lever is **SOURCING (input context to
the writer)**, not the prompt, model, or config. The weakest niche
(`ai_creators`) has EQUAL or BETTER prompt and config quality than the
strongest (`anime`/FrameDrift), but its fetcher delivers stories with
empty `summary`/`description_snippet`/`source_url`/`channel_name` — the
writer then correctly triggers its `_has_writable_context` skip and
falls back to using `story.title` verbatim as the hook. That's the
"news-headline flat" pattern the benchmark flagged. Fix the fetcher and
BB's prompts + Sonnet upgrade + writing.yaml examples immediately do
their job.

---

## Q1 — Model tier per generation step (mismatches flagged)

Source: `genlab-core/configs/model_routing.yaml`. Router: `genlab_core.cost.model_router.get_model(task_type)`.

| Step | Task key | Model | Cost tier | Quality-sensitivity | Verdict |
|---|---|---|---|---|---|
| Hook generation | `generate_hooks` | Haiku 4.5 | CREATIVE $1/$5 per M | **Highest** — 90% of whether the post performs | **MISMATCH** |
| Hook generation (alias) | `hook_generation` | Haiku 4.5 | CREATIVE | Highest | **MISMATCH** |
| Content writing (ai_creators) | `write_ai_creators_content` | **Sonnet 4.6** | ~10-30× Haiku | High | Upgraded correctly |
| Content writing (others) | `write_post_content` | Haiku 4.5 | CREATIVE | High | On the fence |
| Narration script | `narration_script` | Haiku 4.5 | CREATIVE | Medium | OK for TTS |
| Engagement reply | `engagement_reply` | Haiku 4.5 | CREATIVE | Medium | OK |
| Hook quality score | `score_hook_quality` | Haiku 4.5 | CREATIVE | Low (classifier) | OK |
| Script generation | `generate_script` | GPT-4o-mini | BULK $0.15/$0.60 | Medium | Acceptable |
| Caption generation | `generate_captions` | GPT-4o-mini | BULK | High | **Possibly under-tiered** — captions need voice; Haiku or Sonnet would be safer |
| Platform adaptation | `adapt_for_platforms` | GPT-4o-mini | BULK | High | **Possibly under-tiered** — platform voice matters |
| Video analysis | `video_analysis` | Gemini 2.5 Flash | VIDEO | Medium | OK |
| Extraction / classification | `extract_game_name`, `classify_content_type` | GPT-4.1-nano | NANO $0.10/$0.40 | Low | OK |

**The load-bearing mismatch is `generate_hooks: Haiku`.** The
`model_routing.yaml` comment on lines 15-19 is remarkable:

> BB (ai_creators) writing upgraded to Sonnet: **Haiku defaults to flat
> "X just Y" hooks despite prompt engineering. Sonnet follows creative
> constraints much better.** Cost delta: ~$0.04/day ($1.20/month).

This comment identifies exactly the problem, then **only upgrades the
writer body (`write_ai_creators_content`) to Sonnet, not the hook
(`generate_hooks`)**. The hook is left on Haiku for ALL niches. Given
that the same file documents "Haiku defaults to flat 'X just Y' hooks,"
this is the single clearest model-tier mismatch in the config.

**Cost of fixing:** at 5 niches × 1 hook/run × 1 run/day = 5 hook calls/day.
Sonnet 4.6 vs Haiku 4.5 cost delta per hook call is roughly $0.01-0.03
depending on prompt length. Daily cost ceiling: **~$0.15**. Monthly:
~$4.50. Trivial vs the operator's <$5/day cost target.

Recommendation tag: `MODEL` (change 2-line YAML entry).

---

## Q2 — Prompt quality (BB is best-in-class in-repo)

### BlackboxBrief (ai_creators) — `BlackboxBrief/config/content_prompts.yaml`

**Read the actual system prompt**: 236 lines of tightly-engineered
guidance. Not generic. Includes:

- Voice reference: `@evolving.ai (3.9M), @chatgptricks (2.4M), @uncover.ai (621K)`
  — the operator's own aspirational competitors (also named in
  CONTENT_BENCHMARK).
- Audience spec: "18-35 year olds who follow AI creators, try new tools"
- Tone: "Write like you're texting a smart friend"
- Bans: `"LLM", "inference", "parameters", "fine-tuning", "token context"`
  → replaced with `"AI brain", "how smart it is", "teaching it new tricks"`
- **Hook rules (verbatim from prompt):**
  > - The hook MUST be 3-6 words. MAX 6 WORDS. No exceptions.
  > - ALL CAPS. Always. No mixed case hooks.
  > - Name-drop: company name, person name, or dollar amount MUST appear
  > - BAD hooks: "This just dropped:", "30 seconds on..."
  > - GOOD hooks: "GOOGLE MASS-DELETED AI ART", "OPENAI'S $100B GAMBLE"
- Includes worked JSON examples with real content
- "Would my mom understand this" test

**This is the best prompt in the repo.** If it were actually being
applied to the video pipeline, ai_creators output would be strong.

**But** — this file is the LEGACY carousel prompt for Instagram carousels
(`carousel_user_prompt`). BB's video-first pipeline lives in
`genlab-core/src/genlab_core/writing/llm_hook_generator.py` +
`video_content_writer.py` and uses the **shared** cross-niche
`base_writing._build_extra_instructions()` path that pulls from each
niche's `writing.yaml`, NOT from `content_prompts.yaml`.

`content_prompts.yaml` may or may not be in the video code path — the
newer strategy layer wires only `writing.yaml`. This is a **prompt
divergence risk**: the excellent BB prompt sits in a file that the video
pipeline may not read.

### BlackboxBrief `writing.yaml` — what the video pipeline DOES read

7 hook_examples, 5 caption_examples, 30-line tone_notes:
- Hook example: `"Sora just generated a full Fox News broadcast"` (specific tool + specific output — GOOD pattern)
- Hook example: `"The tool that made 3 million designers redundant overnight"` (specific number)
- Caption examples in sentence case with react-voice: `"Wait, so Claude is smarter than the people training it?? 😳"`
- Tone: "tech-savvy person genuinely shocked... REACT to the video... first 6 words"

**This is also strong.** The hook_examples are the format the LLM will
pattern-match against. All 5 caption_examples are on-voice. tone_notes
is specific.

### FrameDrift `writing.yaml` — what the video pipeline reads for anime

6 hook_examples, 0 caption_examples, 4-line tone_notes:
- Hook example: `"The anime nobody finished but everyone remembers the ending of"` (curiosity)
- Hook example: `"Why MAPPA animators are leaking burnout footage"` (insider news)
- Tone: "passionate otaku energy. Reference specific anime titles"

**Less detail than BB.** No caption_examples. Shorter tone_notes. Yet
FrameDrift ships stronger output per the benchmark
(`"Gege Akutami is sabotaging his own characters"` — opinion hook, unique
voice). So config detail is NOT the differentiator.

### Verdict

**Prompt quality is not the load-bearing lever.** BB has EQUAL OR MORE
prompt detail than FrameDrift (both wire via `_build_extra_instructions`
identically). Yet BB output is flatter. Something else explains the
gap — see Q4.

Recommendation tag: `PROMPT` — only useful if:
- Verify BB's `content_prompts.yaml` is or isn't consulted by the
  video pipeline (if not, consider aligning `writing.yaml` with its
  ALL-CAPS + name-drop rules).
- Add `caption_examples` to FrameDrift's `writing.yaml` (it currently
  has none — parity with BB).

---

## Q3 — Writing strategies compared (FrameDrift vs BlackboxBrief)

Both niches subclass `BaseHookStrategy` (from `strategies/base_hook.py`).
Both override `_classify_story()` for content-type-aware routing.

### `bb_strategies/hooks.py` (BlackboxBrief) — 332 lines
- 6 content-type categories: `showcase`, `tool_launch`, `creator_showcase`, `controversy`, `demo_viral`, `default`
- Keyword-based classifier: `_TOOL_KEYWORDS`, `_CREATOR_KEYWORDS`, `_MILESTONE_KEYWORDS`, `_CONTROVERSY_KEYWORDS`, `_CURIOSITY_KEYWORDS`, `_VIRAL_KEYWORDS`
- Priority routing: source-declared `content_type=showcase` beats keyword classification (Phase 2 C1 fix, 2026-06-30)
- Comment cites the issue: previously `showcase` content fell through to text-keyword classifiers and got news-style hooks that don't match the visual — routing fix ensures visual-first templates fire

### `fd_strategies/hooks.py` (FrameDrift) — 80 lines
- 4 content-type categories: `voice_actor_trigger`, `anime_premiere`, `studio_collab`, `manga_release`
- Classification via story flags (`is_creator_spotlight`, `is_new_release`, `is_collab`, `is_event_coverage`)
- Trend-cycle-aware fallback (`emerging`, `peak`, `declining`, `unknown` → category)
- Placeholder substitution: `{title}`, `{studio}`, `{voice_actor}`, `{episode}`, `{item}`, `{season}`, `{trend_name}`, `{announcement}`

### `fd_strategies/writing.py` — 46 lines
- **Empty override.** Comment: "no niche-specific overrides needed" — inherits everything from `BaseWritingStrategy`.
- Provides only `_story_to_video_dict` mapping (converts pipeline story → writer input).

### `bb_strategies/writing.py` — 30 lines
- Similar shape; niche-specific `_story_to_video_dict`.

### Structural comparison

Both have equivalent architecture. **BB's content-type classifier has MORE
categories (6 vs 4) and MORE routing signals.** So structure-wise BB is
at least as capable as FrameDrift.

**What FrameDrift has that BB doesn't:**
- **Trend-cycle-aware routing** (emerging/peak/declining/unknown) — falls
  back to cycle-appropriate template when no content-type flag fires.
  BB has no equivalent.
- **Better placeholder substitution set** — anime-specific placeholders
  like `{voice_actor}`, `{studio}`, `{episode}` that map to concrete
  entities the story dict carries.

**What BB has that FrameDrift doesn't:**
- **More `content_type` categories** (6 vs 4) with keyword-heuristic
  classification for text signals.
- **`content_type=showcase` source-declared priority** (Phase 2 C1) —
  respects fetcher's explicit content-type tag over text keywords.

**Neither differentiator explains the shipped-quality gap.** BB is
architecturally as capable or more so.

### Port opportunity — anime→ai_creators

- Trend-cycle awareness could apply to ai_creators
  (emerging AI trend / peak buzz / declining fad) — but the payoff is
  modest without richer story metadata.
- The stronger port is the OTHER direction: BB's Phase 2 C1
  `content_type=showcase` priority pattern would help FrameDrift if the
  anime fetcher started emitting content_type tags.

Recommendation tag: `CONFIG` — low priority; strategies are already
strong on both sides.

---

## Q4 — Where quality is lost (the smoking gun)

**Traced one ai_creators story: the recent Gemini Robotics 2 blueprint.**

DB row for blueprint `ba5a7cf2-afcc-4fcb-8a9b-c6593ffd85a1`
("Gemini Robotics 2 brings whole body intelligence to robots"):

```
hook:                Gemini Robotics 2 brings whole body intelligence to robots
title:               Gemini Robotics 2 brings whole body intelligence to robots
summary:             (empty)
description_snippet: (empty)
source_url:          (empty)
channel_name:        (empty)
action_taken_source: (empty)
```

**All fetcher-provided context fields are empty.** Only `title` is
populated. `hook == title` exactly.

Trace through the pipeline:

1. **Fetcher stage**: emitted a story with `title` set and everything else empty.
2. **Writer stage** (`base_writing._has_writable_context`): checked
   `summary` / `description_snippet` / `description`. All empty and below
   the 40-char floor → **set `story["_skip_llm"] = True`**. Writer never
   called Anthropic. This is by design (per the 2026-07-22 fix) to
   prevent the LLM-refusal-preamble bug.
3. **Template-fallback path**: writer used `story.title` as the hook.
   No LLM ever ran. That's why the "hook" reads like a news headline —
   it IS the news headline, verbatim.
4. **Pre-render quality gate**: rule 4 (`hook_equals_title`) SHOULD have
   fired here. `check_pre_render_quality` at
   `base_visual_render.py:177` is called with
   `title=story.get("title", "")`. If the story dict still had the
   title when it reached the gate, rule 4 would have set
   `ok=False, reason="hook_equals_title"` and the blueprint would not
   have rendered.
5. **But the blueprint IS PUBLISHED**, so either (a) the gate wasn't
   called on this render (perhaps the render happened before rule 4's
   2026-07-22 wire), (b) `story.title` was empty at gate time even
   though DB title is populated, or (c) rule 4's ordering (fires AFTER
   min_length check) let it through because the title is >=15 chars.
   Worth a follow-up but not the load-bearing finding.

### The load-bearing finding

**The generation problem is upstream of generation.** The writer + hook
generator are receiving stories with no context to write about, then
correctly declining to call the LLM (per the anti-refusal-preamble fix),
then falling back to title-verbatim.

**Prompt engineering cannot fix this.** No hook prompt can write
"OPENAI'S $100B GAMBLE" if the input has zero context beyond the title.
Sonnet-vs-Haiku doesn't matter if the LLM never runs. The
`caption_examples` don't matter if the LLM never sees them.

Recommendation tag: `SOURCING` — the ai_creators fetcher must populate
`summary` / `description_snippet` with 40+ chars of real story text per
story. The fetchers likely to be the culprit: whichever emits the
"Gemini Robotics 2" stories — probably an RSS feed or YouTube trending
fetcher that maps only `<title>` and skips `<description>` / `<summary>`.

---

## Q5 — Do guards suppress good output?

### Pre-render quality gate rules (`rendering/pre_render_quality.py`)

| Rule | Suppresses | False-positive risk |
|---|---|---|
| `llm_refusal_preamble` | Prefixes like "I need the", "I can't", "I apologize" | Low — matches specific refusal phrases; unlikely to hit real hooks |
| `hook_too_short` | <15 chars | Low — 15 is generous for a hook, no legit hook is that short |
| `hook_equals_title` | Hook = title verbatim | Low — the point IS to reject this |
| `hook_title_truncation` | Hook = title truncated at 60 with `...` | Low — same as above |
| `hook_bare_title` | No verb signal + no lowercase token | **Some risk** — a valid ALL-CAPS punchy hook like "GOOGLE MASS-DELETED AI ART" **does** pass because "MASS-DELETED" has `-ed` suffix. But a hook like "OPENAI CLAUDE DEEPMIND" would fail (no verb). Test coverage looks thorough |

Test suite (`tests/rendering/test_pre_render_quality.py`) has 52 test
cases including 8 "real hooks that shipped successfully" pinned as
must-pass. Test file: `TestLegitHooksAccepted::test_real_hooks_accepted`
parametrized with cases like:
- `"DeepSeek's New AI Speed Hack Is Amazing"` ✓ (`Is` = copula)
- `"Cinema is back and Marvel has some explaining to do"` ✓
- `"Bam Adebayo Just Dropped 83 in the Playoffs"` ✓ (`Just Dropped` has `ed`)
- `"I am SO MAD ABOUT THIS"` ✓ (`am` = copula, escapes bare-title trap)

The gate is well-designed to accept real hooks. **No evidence of
over-conservative suppression.**

### Writer thin-context skip (`base_writing._has_writable_context`)

40-char floor on `summary` / `description_snippet` / `description`. Below
threshold → `_skip_llm = True` → template-fallback path.

**This is where good output IS being suppressed** — but the fault is
**upstream**. The skip is defensive (prevents refusal preambles) and
correct given the input. It's not the guard being too conservative; it's
the fetcher not delivering context to work with.

### Writer refusal detection (`video_content_writer._is_llm_refusal`)

Detects refusal preambles in generated content and blanks the field. If
blanked, falls back to `story.title`. Same class as above — correct
behavior, but the fallback to title-verbatim is exactly the bland output
we see. **The blandness is the FALLBACK PATH working as designed** on
starved input.

### Fallback blandness — the real subtle problem

The template-fallback + title-verbatim + gate-approves pattern means
that when input is thin, output is technically "safe" (no refusal
preamble, no bare title without a verb, above min length) but
editorially flat. **Guards don't cause this — they permit it.**

If input quality is fixed (Q4 recommendation), the guards remain
appropriate. If sourcing can't be fixed, an escape-hatch template
generator that produces MORE creative fallbacks (curiosity-gap hook
from just the title) would help. But that's a `PROMPT/CONFIG` fix for
the fallback, not the guard.

Recommendation tag: (none) — guards are calibrated well.

---

## The single biggest structural quality lever

**SOURCING.** No prompt or model change moves the needle if the writer
sees empty summaries and skips the LLM entirely. Fix the fetcher's
`summary` / `description_snippet` propagation for ai_creators (and
likely gaming, sports, movies where similar patterns will exist) and
the existing prompts + Sonnet upgrade + writing.yaml examples all
finally get to do their job.

Second-biggest lever is the **`generate_hooks: Haiku` MODEL mismatch**
— even the model_routing config file's own comment acknowledges Haiku
produces flat hooks. Upgrading to Sonnet for hooks (5 niches × 1 hook
per daily run = ~$0.15/day extra) is a 2-line YAML change.

---

## Port-FrameDrift-strength-to-ai_creators opportunity

**Limited.** BB is architecturally as strong as FrameDrift. The only
port worth considering is FrameDrift's trend-cycle-aware fallback —
but the payoff is modest without richer story metadata, which loops
back to the sourcing problem.

The OTHER direction has more upside: BB's `content_type=showcase`
source-declared-priority pattern (Phase 2 C1) would help FrameDrift if
the anime fetcher started emitting content_type tags.

**Neither port would move the shipped-quality gap** the CONTENT_BENCHMARK
identified. The gap is upstream (sourcing) not in strategy structure.

---

## Ranked improvement list (highest quality-gain ÷ effort first)

| # | Improvement | Tag | Effort | Quality gain | Notes |
|---|---|---|---|---|---|
| 1 | **Fix ai_creators fetcher to populate `summary`/`description_snippet`** with ≥40 chars per story | `SOURCING` | MED | **HIGH** | The load-bearing fix. Without this, everything else is theatre. Investigate which fetcher emits stories with only `title` set. Likely the RSS or YouTube-trending fetcher for AI creators. |
| 2 | **Upgrade `generate_hooks` from Haiku to Sonnet** (5-line change to `configs/model_routing.yaml`) | `MODEL` | LOW | MED-HIGH | Config comment on lines 15-19 already acknowledges Haiku is flat. Cost: ~$0.15/day at 5 niches. |
| 3 | **Verify BB's `content_prompts.yaml` reaches the video pipeline** — if not, port its ALL-CAPS + name-drop hook rules into `BlackboxBrief/config/writing.yaml` as tone_notes | `PROMPT` | LOW | MED | The excellent BB prompt may be dead code for the video path. Grep-and-check confirms the divergence. |
| 4 | **Enable `GENLAB_HOOK_CRITIC_ENABLED=1`** — LLM-based hook-grounding critic already built in `llm_hook_generator.py:_critique_hook_grounded`, gated off by default. Would catch weak hooks post-generation. | `CONFIG` | LOW | MED | Cost: ~$0.005/day. Shadow-mode ship first. |
| 5 | **Investigate why pre-render rule 4 (`hook_equals_title`) didn't catch the Gemini blueprints** — the rule exists and is wired but the blueprints shipped with hook=title | `CONFIG` | LOW | LOW (bug fix, not quality gain) | Post-2026-07-22 rule should fire; blueprints from 2026-08-04/05 predate a possible wire regression. Grep for changes to `_compose_frame` after the fix date. |
| 6 | **Add `caption_examples` to FrameDrift's writing.yaml** — currently empty, parity with BB's 5 examples | `CONFIG` | LOW | LOW | Marginal. FrameDrift already ships strong captions per the benchmark. |
| 7 | **Add trend-cycle-aware routing to BB's `bb_strategies/hooks.py`** — port FrameDrift's `_CYCLE_CATEGORIES` fallback | `CONFIG` | MED | LOW | Modest payoff without richer story metadata (loops back to #1). |
| 8 | **Upgrade `generate_captions` and `adapt_for_platforms` from GPT-4o-mini to Haiku or Sonnet** | `MODEL` | LOW | LOW-MED | Captions carry voice. GPT-4o-mini is a bulk-tier model; may explain some caption blandness. Verify with A/B before shipping. |

**The one thing worth doing first: #1 (fix ai_creators sourcing).** All
downstream quality mechanisms are already built and correct; they just
need real input to work with.

**Second: #2 (Sonnet for hooks).** Even without the sourcing fix,
Sonnet handles the template-fallback path better than Haiku when the
LLM does run.

Everything else on the list is trim / hygiene.

---

## What was NOT changed

Zero code edits. Zero config edits. Zero deploys. This is analysis only.
Every finding above is grounded in a specific file:line reference or a
DB query result attached to a specific blueprint UUID.
