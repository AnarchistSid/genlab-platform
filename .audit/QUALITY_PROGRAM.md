# GenLab — Content Quality Program: From "Runs" to "Wins"

This is a program, not a session — weeks of work, staged so each phase gates the next. The ordering is deliberate and the audit earned it: **do not build new capacity before measuring whether existing capacity is competitive, and do not generate more before you can judge what you have.**

Prepend the standard STATE + RULES preamble. Production DB is 5432.

**Two hard truths this plan is built around:**
1. The competitive benchmark (Part B of the assessment) was never run. "Match the top channels" is a target with unknown distance. Phase 0 fixes that.
2. Most of this is a taste problem, not an engineering one. You reject 40–58% of current output by eye — that rejection signal is the most valuable asset here, and the build must capture it as training signal, not bury it.

**And one existential constraint:** movie / sports / anime compilations are the highest-takedown-risk content on the platform (YouTube Jan-2026 precedent). A "Top 10" compilation is *more* exposed than a single transformed clip. The build order below is copyright-ascending on purpose — safest content types first.

---

# PHASE 0 — Benchmark one niche before building anything (1 session)

You cannot improve toward a target you have not measured. Pick the niche you would consolidate toward — on audit evidence, ai_creators (lowest copyright risk, cleanest generation) or gaming (best closure).

Reuse the existing `fetch_*` clients — this is retrieval the system already does:
- Top ~10 competing short-form channels in that niche. Per channel: followers, median views on last 20 shorts, upload cadence, and the hook + first 3 seconds of their 5 most-viewed recent posts.
- Put GenLab's last 20 reels in that niche beside them on four axes: hook pattern, pacing (length + cuts), **engagement-per-follower** (the only true cross-size comparison), and freshness (source-to-publish lag).

**Output the gap per axis: matches / close / behind.** Do not average. If engagement-per-follower is within range, the content competes and the problem is distribution — most of this program is unnecessary and you should raise cadence instead. If it is an order of magnitude below, the content does not compete and this program is justified. **This single result decides whether to build the rest.**

---

# PHASE 1 — Fix the writer before improving it (prerequisite)

You cannot raise quality on a generator that silently ships source titles. The `llm_hook_generator.py:1385` fallback (F-0080) must be fixed first — and fixed as **hard-fail, not fallback-plus-log**. On the two auto-approved niches there is no operator to catch a passthrough reel, so the prepared "debug→warning" interim changes nothing a viewer sees. A missing reel beats a source-title reel:

```python
# at the hook-generation failure point: raise, do not return {p: base_hook}
# the publisher already tolerates a niche producing no reel that day (mandate is 41%)
```
Plus the refusal-shape guard (F-0082) already prepared. Verify a FALLEN-pattern day passes clean under the fix before moving on. **Phase 2+ is wasted effort until this ships.**

---

# PHASE 2 — The taste loop: capture your rejections as training signal

This is the highest-leverage quality work and it is nearly free — the signal already exists, it is just being thrown away. You reject 40–58% of blueprints. Right now that judgement evaporates.

1. **Log the reason.** When you archive a blueprint, capture *why* in one tap: `wrong_fit / bad_hook / stale / off_brand / low_quality`. A five-value enum on the review UI. This is the training data the whole program runs on.
2. **Feed it back.** Once ~100 labelled rejections exist per niche, the reasons become a critique rubric — the exact failure modes to check for at generation time (Phase 3), and the scoring features that predict your rejection.
3. **Measure the loop.** Rejection rate should fall over weeks as the generator learns what you reject. That falling number is the quality metric this whole program is judged by — not a subjective "it looks better."

Without this, every downstream improvement is guesswork. With it, your taste becomes the system's objective function. Build this second, right after the writer fix.

---

# PHASE 3 — Self-critique generation (per-niche quality, not new capability)

This improves the content type you already have. A generate → critique → regenerate loop, with the critique rubric drawn from Phase 2's rejection reasons:

- Generate N hook candidates (the loop already exists — it just lacks a good judge).
- **Critique pass**: a separate LLM call scores each against the niche's rubric — the specific things you reject for. Business logic stays in code (rule 7); the prompt only does the creative judging.
- Regenerate the lowest-scoring, or hard-fail if none clear the bar (never publish the best of a bad batch — that is how you got 26% rejection).
- **Per-niche voice**: the rubric and exemplars differ per niche. Gaming hooks, anime hooks, and AI-news hooks are not the same craft. Load top-performing past hooks (by your own reward signal) as few-shot exemplars per niche.

Verify against Phase 0's benchmark, not against itself: does the hook pattern move toward the top channels' patterns?

---

# PHASE 4 — Music and audio (real, and mostly a mixing problem)

"Add music without obscuring the message" is 80% an audio-engineering problem and 20% a selection problem. Both are solvable and neither needs AI.

1. **Ducking is the "without obscuring" fix** — sidechain compression: when voiceover or on-screen-emphasis plays, the music auto-drops 6–12 dB, then returns. This is a standard FFmpeg `sidechaincompress` filter, deterministic, no model. It is the single highest-impact audio change and it is a filter graph, not a feature.
2. **Loudness normalisation** to platform targets (−14 LUFS YouTube, −14 IG) so audio is never too quiet or clipping — FFmpeg `loudnorm`.
3. **Better tracks, licensed** — this is the real constraint, and it is legal not technical. Sourcing: a licensed library (Epidemic, Artlist, Uppbeat) or properly-licensed royalty-free. **Do not scrape trending audio** — that is the same copyright exposure as the footage, on the audio track. Match track energy to niche (gaming ≠ anime ≠ AI-news) via a per-niche music config, not a model.
4. **Beat-aware cuts** (advanced, optional): align clip transitions to the music beat. Detectable with `librosa`; it is what makes top-channel edits feel intentional. Do this last, only if Phase 0 showed pacing as a gap.

Music is a Layer-2 `AudioStrategy` + config, not a rewrite. Ship ducking first; it alone closes most of the "obscures the message" gap.

---

# PHASE 5 — Compilations: a NEW content type, built copyright-ascending

Be clear-eyed: a compilation is not an improved reel, it is a different pipeline — multi-clip retrieval, ranking, sequencing, a narrative through-line, inter-clip transitions. It is the largest build here and a new Layer-2 strategy you do not have. **Build it once, on the safest niche, prove it, then port.**

Order is by copyright exposure, ascending — this is not negotiable:

1. **AI-generated compilations FIRST** (your own footage, zero takedown risk). "This week in AI, visualised" from generated clips. This is where you build and debug the compilation engine — ranking, sequencing, transitions, through-line — with no legal exposure while you get it wrong.
2. **AI-news compilations** (ai_creators) — talking-head/news clips, transformed, lower risk. Reuses the engine from step 1.
3. **THEN, and only with a documented transformation defense**, the high-risk three — gaming, sports, anime. A "Top 10" of third-party footage is your most legally fragile product. Before building these, answer in writing: what transformation (commentary, re-edit, added analysis) makes this defensible, and does it survive YouTube's automated matching? If you cannot answer that, do not build them — the audit flagged these three as existential four times.

The compilation engine itself (retrieval → rank-by-reward → sequence → transition → through-line narration) is one build. The niches differ only in source and risk. Build the engine on niche 1, port by config.

---

# THE HONEST SEQUENCE

- **Phase 0 gates everything.** If the benchmark says you already compete, raise cadence and skip most of this.
- **Phase 1 before any quality work** — no improving a broken generator.
- **Phase 2 is the cheapest high-leverage phase** — capture the taste signal you already produce.
- **Phases 3–4 improve the content type you have.** Achievable, incremental.
- **Phase 5 is a new content type and the biggest build** — safest niches first, high-risk three only with a written copyright defense.

Do not build Phase 5 for gaming/sports/anime before Phase 0 proves the content competes and before a transformation defense exists. Building your most fragile product on unproven content is two audit lessons ignored at once.

**First action:** Phase 0, one niche, one session. Everything else waits on what it says.
