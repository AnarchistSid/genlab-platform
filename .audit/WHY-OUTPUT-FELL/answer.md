# Why output fell on all five channels — measured 2026-09-14 ~08:30Z

## 1. The size of it
Reels published per day (distinct blueprints, no status filter):
Apr 5.1 · May 4.6 · Jun 2.9 · Jul 3.5 · Aug 3.8 · **Sep 2.6** — against a 5/day cap
(1 per niche per day). Roughly half.

Blueprint CREATION is nearly flat (466 in May -> ~375/month rate in Sep). The loss is
after generation, not in it.

## 2. Where it stops — three niches have nothing rendered
Blueprint status, last 14 days:
| niche | VISUAL_READY | DRAFTED | ARCHIVED | PUBLISHED |
|---|---|---|---|---|
| ai_creators | **30** | 1 | 0 | 4 |
| gaming | 13 | 8 | 12 | 8 |
| anime | **0** | 9 | 11 | 3 |
| movies | **0** | 16 | 26 | 2 |
| sports | **0** | 15 | 15 | 2 |

ai_creators is healthy with a 30-deep rendered queue — it is throttled only by the
1/day cap. **anime, movies and sports have ZERO rendered blueprints**: everything they
generate either stalls at DRAFTED or is ARCHIVED.

## 3. The dominant cause — the pre-render quality gate rejecting title-hooks
Failure reasons, 14 days:
```
anime   hook_equals_title 9  + hook_title_truncated 2   = 11 of 11 archived
movies  hook_title_truncated 14 + hook_equals_title 12  = 26 of 26 archived
sports  hook_title_truncated 8  + hook_equals_title 6   = 15 of 15 archived
gaming  hook_bare_title 9                               =  9 of 12 archived
sports  (DRAFTED) hook_equals_title 5 + hook_title_truncated 5 = 10 of 15
```
The writer is emitting hooks that ARE the story title, or a truncated one. The gate is
working exactly as designed (CLAUDE.md: "hook_bare_title — bare titles … with no verb
signal") and correctly refusing to render them.

## 4. Why the writer emits title-hooks — the LLM has not been running
Hook rejections vs successful renders per day:
```
08-28  5 rejects / 0 rendered      09-09   9 / 3
08-29  8 / 0                       09-10   1 / 2     <- LLM funded
08-30  4 / 0                       09-11   3 / 3
08-31  8 / 0                       09-12   1 / 6     <- LLM funded
09-05 11 / 0                       09-13   9 / 5     <- llm_usd $0.0000
09-06 11 / 4                       09-14   6 / 6
```
Rejections run 5-11/day and **spike on exactly the days LLM spend is $0.0000**
(09-09 and 09-13 measured: `by_model` carried only `{"tts": …}`), and fall to 1-3 on
days the LLM ran (09-10, 09-12). A ~10x difference tracking provider availability.

Tonight's fire showed the mechanism verbatim:
```
Content generation failed: 400 invalid_request_error
  'Your credit balance is too low to access the Anthropic API'
[llm-fallback] OpenAI fallback ALSO failed (429 'You have no credits remaining')
```
**Both LLM providers have been empty, intermittently, for weeks.** When the LLM call
fails the writer falls back to template/title-derived hooks, and the quality gate then
archives them. Output halves without a single error surfacing as an incident, because
every stage "succeeded".

## 5. Secondary cause — loudness validation
`render:validation_failed:loudness_off:-15.12 … -16.07 LUFS` on ~8 blueprints (movies,
sports). Target is -14 LUFS ±1, so these sit just outside tolerance. Small next to the
hook rejections but real.

## 6. What is already fixed, today
`e2ad877b` + `9a65ceef` (deployed ~03:00Z) make **inference.sh the primary LLM provider**,
with Anthropic/OpenAI as optional fallbacks. Belt has ~$100 of credit. Verified through
the real caller with Anthropic unfunded: the writer produced a 404-char narration script
served by `belt:claude-sonnet-4-6`, and all four Tier A call sites were observed on belt.

So the writer has a funded provider again as of this morning. **The prediction is that
hook rejections fall to the 1-3/day seen on funded days, and the three empty niches start
accumulating VISUAL_READY again.** That is measurable on tomorrow's fires — it is the
single number worth watching.

## 7. What is NOT yet fixed
* Ten Tier B modules still call Anthropic directly with no fallback (hook_classifier,
  shadow_reviewer, auto_approval_gate's judge, and seven more). They still fail when
  direct credit is out.
* The loudness drift.
* Sports also has the source-mix problem fixed hours ago (highlight-first selection), which
  is a separate defect about WHICH clips get picked, not how many publish.
