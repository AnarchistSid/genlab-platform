# GenLab — Diagnostic (2026-07-26 IST)

## 1. Passthrough shape: STANDING DEFECT, not outage-caused

Daily per-niche passthrough is **binary and alternating** — clean 0% days interleaved with 100% days:

- **ai_creators**: 07-19 0% / 07-22 100% / 07-23 0% / 07-24 100% / 07-25 0% / 07-26 100%
- **anime**: mostly 0% with 100% batches on 07-13, 07-17, 07-22, 07-23
- **gaming**: 100% on 07-13/14/15/19/24; 0% on 07-16/18/22/23/25
- **sports**: mostly 0% with 100% spikes on 07-22 and 07-26
- **movies**: 3 clean days + 1 100% day (07-23)

**Not a switch that flipped once — a per-pipeline-run bug.** Some runs produce human-written hooks, some fall through to source-title verbatim. Anthropic exhaustion would produce uniform post-transition 100% days; it doesn't.

**First `anthropic_credit_exhausted` alert: 2026-06-28 08:01Z** (28 days ago); recurring since. `publishing_analytics` 402 rows since 2026-07-05 are all `twitter` (X/Grok, different account). No temporal alignment between Anthropic bursts and passthrough-100% batches.

**Verdict: STANDING DEFECT + outage noise.** Writer fallback fires on some fraction of runs regardless of Anthropic balance — likely a per-run condition (thin source, LLM timeout, silent except). Topping up will reduce noise but NOT fix passthrough. **F-0054 stands, generalised beyond gaming to every niche.**

## 2. Anime rejection reasons (8 rejected hooks, read by hand)

Four writer-side bugs: passthrough (`FGO - Ordeal Call III: Bellum Novae...`); **LLM-refusal-text leaked as hook** (`I need the Story Summary to write a hook...` — writer failed and its refusal string was persisted); hashtag flood (`#Anime #animeedit #edit #shorts deserves way more attention`); generic clickbait template (`Why this cosplay bit broke the internet in 60 seconds`).

Four operator-side rejections of decent writing: Rengoku's mom, Tanjiro/wuxia, Nami's staff (all read well but rejected — freshness or repeat), plus one policy-adjacent (`Why do anime fans keep failing this sexuality test?`).

**Anime is doubly-penalised**: writer bugs like every niche PLUS a content-source freshness gap (5+ day queue = half the rejects are stale trending moments). **File as a source-side finding, distinct from the writer defect.** The LLM-refusal-text-as-hook is a new bug class worth its own finding — the writer needs to hard-fail on refusal-shape output, not persist it.

## 3. MIN_OBS=15: benign cold-start guard, currently OFF

`cross_platform_gate.py` — Beta-posterior gate that would skip (niche, platform) combinations with `posterior_mean < 0.02` once `n_obs >= 15`. **Flag-gated by `GENLAB_CROSS_PLATFORM_GATE_ENABLED`, off by default; module docstring explicitly says "one week of shadow observation before flipping."** Called only from `publish_all_platforms.py:403`. Fail-open on any error.

**Not a live throttle.** Not contributing to the 41.4% mandate shortfall. Close as benign.

## 4. Anthropic + re-measurement

**Anthropic still `exhausted`** at 14:00 IST 2026-07-26 — `matches_found: 2`. Session 8 continuing. **Diagnostic is now captured — the outage-era data has been read.** Safe to top up.

**Post-top-up plan:** Do not re-run A.3/A.4 immediately. **Re-measure content quality on 2026-08-02** (7 days post-restore) against a clean week. A.1 (intelligence) stays trustworthy now — 30-day arm history predates the outage. **A.3 + A.4 findings from Part A are unreadable during the outage; hold them.**
