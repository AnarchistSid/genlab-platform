# QB-FIX-02 V4 — `fetch_reddit_clips` Permalink-as-Summary Fix

**Date:** 2026-08-06 22:15 IST
**Status:** SHIPPED. Fetcher writes real summaries; writer gate rejects URL-dominant summaries as generalisable defense.

## Historical extent (measured pre-fix)

```sql
SELECT niche_id, COUNT(*) FROM stories
WHERE summary ~ '^https?://'
   OR LENGTH(REGEXP_REPLACE(summary, 'https?://\S+', '', 'g')) < 40
GROUP BY niche_id;
```

| niche | URL-dominant story summaries |
|-------|-----|
| sports | 404 |
| gaming | 177 |
| movies | 91 |
| ai_creators | 88 |
| anime | 76 |
| **TOTAL** | **836** |

836 stories across the DB had a summary that was either a URL or predominantly a URL. This class of defect had wide reach — confirmed the impact hypothesis behind F-QB-0606 (bare-title hooks: "Fortnite", "League of Legends" x5, "Marvel's Spider-Man 2").

**Notable:** the 3 fresh movies blueprints in F4 batch 1 (INHERIT, Primetime, Yankee) are Reddit-sourced and their stories still have permalink URL summaries in DB. The writer LLM produced quality captions anyway (it reasoned from title alone), but the F-QB-0606 pattern suggests this often produced bare-title hooks in the past.

## Fixes shipped

### Fix 1 — fetcher writes real summaries

`fetch_reddit_clips.py`:

- New helper `_build_reddit_summary(title, subreddit, flair=None, selftext="")`
- Preference order: selftext if ≥40 chars → synthesized `"Reddit clip from r/{sub}: {title}. Flair: {flair}"`
- Never emits a URL as the summary
- Applied at both write sites: `_normalise_rss_entry` (RSS path, line 254) + `_normalise_post` (JSON path, line 512)
- Permalink moved to new `reddit_permalink` field on the story dict so attribution reads still work

### Fix 2 — writer gate rejects URL-dominant summaries (generalisable)

`base_writing.py`:

- New helper `_is_url_dominant(text)`: strip `https?://\S+`, if <40 chars remain, treat as URL-dominant
- `_has_writable_context()` now applies BOTH length AND URL-dominance checks
- Comment references QB-FIX-02 V4 + calls out the historical Reddit bug shape

This is the generalisable fix per §6. Any future fetcher that regresses to URL-as-summary will be caught by the writer gate even if the fetcher itself ships broken. The anime + Reddit + potential-future cases are all one hole from a writer-contract standpoint; the writer now enforces the contract.

## Pin tests

`test_fetch_reddit_clips_summary.py` — 9 tests:

* `TestBuildRedditSummary` (5):
  * selftext used when long enough
  * synth from title + subreddit when selftext empty
  * flair appended when present
  * no URL leaks into synth even if caller confused title with permalink
  * empty inputs → empty string (writer correctly skips)

* `TestUrlDominantGate` (4):
  * bare permalink summary rejected by `_has_writable_context`
  * synth summary passes
  * summary with a URL BUT surrounding prose passes (context beats URL)
  * length floor still enforced (short non-URL rejected)

All 9 pass locally.

## Gate — measured on fresh output (deferred to next pipeline run)

Per §6 spec:

```
On the next successful pipeline run per affected niche:
- 0 stories with a URL-dominant summary reach the writer
- hooks on resulting blueprints are not bare titles
```

Deferred because it requires a fresh pipeline run to measure. Post-deploy, this becomes verifiable on any next fire.

**Query to run after next fire:**
```sql
-- 0-new-URL-summaries gate
SELECT COUNT(*) FROM stories
WHERE created_at > NOW() - INTERVAL '2 hours'
  AND (summary ~ '^https?://'
       OR LENGTH(REGEXP_REPLACE(summary, 'https?://\S+', '', 'g')) < 40);
-- expect: 0

-- bare-title-hook spot check (visual review)
SELECT LEFT(title, 30) AS title, hook_text
FROM blueprints
WHERE created_at > NOW() - INTERVAL '2 hours'
  AND source LIKE 'reddit:%';
-- expect: hooks are not bare titles
```

## Backfill (not applied)

836 historical URL-summary rows exist in `stories`. Not backfilling because:

- Writer stage reads from `context["stories"]` (in-memory fresh) not from DB — historical rows don't get reprocessed.
- Historical blueprints already published; bare-title hooks are in the past, not preventable now.
- The gate change fixes the writer path prospectively; the fetcher change fixes the fetcher path prospectively. Both cover the class-of-bug going forward.

If historical audit wants to recompute what hooks WOULD have been with real summaries, that is a separate offline analysis.

## Commit

`fix(fetchers): build real summaries from Reddit clips and reject URL-only context`
