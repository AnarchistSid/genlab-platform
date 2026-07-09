# Audit — composite-key double-prefix sites (Task #625, 2026-07-09)

**Sequel to** `#748` (post_id double-prefix in pending_feedback, root
cause of dormant learning stack) and `#624` (consolidation of 3
sibling normalizers).

## Motivation

`#748` fixed a bug where one write site prepended `{platform}:` to
an already-`{platform}:`-prefixed `post_id`, corrupting weeks of
learning signal. `#624` consolidated the 3 sibling normalizers into
one canonical helper. This audit sweeps the rest of the codebase
for **similar composite-key patterns** — sites that concatenate a
`{something}:` prefix to a value without idempotence check.

## Scope

Grep patterns:

```
f".*{.*}:"                   # any f-string with a colon-composite
f"{niche_id}:{...}"          # niche-composite keys
f"arm:{...}", f"style:{...}" # bandit-arm-family prefixes
f"product:{...}"             # monetization product keys
```

Filter: source files only, exclude tests / caches / error messages
/ log strings.

## Findings

### High-risk site (fixed in this PR)

**`publishing/analytics_recorder.py:63`** — writes to
`Publishing_Analytics` table.

Pre-fix:
```python
"post_id": f"{platform}:{post_id}" if post_id else "",
```

Same class of bug as `#748`: no idempotence check. If `post_id` is
ever passed in already-prefixed shape (currently unlikely — the
URL-tail extraction on lines 51-58 produces bare IDs — but the
invariant should not depend on that), the composite key becomes
`platform:platform:...` and won't join with `analytics.post_id`.

**Fix:** route through `cache.post_id_norm.normalize_post_id()`.
Pin tests added; grep-style module-level pin catches future
re-inlining.

### Low-risk sites (documented, not fixed)

These grep positive but investigation shows the composition is
safe. Documented so future auditors don't re-flag them.

**`publishing/feedback_registration.py:280`** —
`f"style:{niche_id}:{hook_style}"`. `hook_style` comes from LLM
writer's structured output as a bare token like `"callback_intro"`.
Safe — atoms never contain `:`.

**`publishing/feedback_registration.py:292`** —
`f"hour:{publish_hour}:{platform}:{niche_id}"`. All atoms are
`int` or bare `str` tokens. Safe.

**`learning/cross_niche_transfer.py:250`** — `f"product:{slug}"`.
Guarded by `startswith("product__")` check on line 245; the input
is validated before composition. Safe.

**`monetization/cta_bandit.py:122,146`** —
`f"{platform}:{v.arm_id}"`. Same composition in save AND load
paths (symmetric). Bad-consistent if config supplies a
pre-prefixed `arm_id`, but that's a bad-config issue not a
divergence-across-sites issue. Low priority.

**`http/analytics_store.py:95`** — `raw = f"{candidate_id}:{platform}"`.
Used as input to a hash function, not persisted as a lookup key.
Safe.

**`learning/preference_collector.py:126`** —
`f"{r['niche_id']}:{r['platform']}"`. Used as an in-memory dict
key derived from bare stored fields. Safe.

**`http/analytics_store.py:211`** — already fixed by `#624`.

### Separate bug found (filed for follow-up)

**`publishing/analytics_recorder.py:51-58`** — URL-tail extraction:

```python
post_id = post_url.rstrip("/").split("/")[-1]
```

Produces buggy output for YouTube URLs:
- Input: `https://youtube.com/watch?v=BlZa`
- Output: `watch?v=BlZa`  ← should be `BlZa`

This is a **separate bug** from the double-prefix class — it
corrupts the `post_id` value itself, not its prefix. Impact:
`youtube:watch?v=BlZa` in Publishing_Analytics never joins with
`youtube:BlZa` shape written elsewhere. **Filed as follow-up**;
out of scope for this audit PR because it needs proper URL
parsing per-platform.

## Meta-lesson

The class-of-bug memory
`[[class-of-bug-alerts-must-reflect-current-state-not-historical-signal]]`
documents the write-side sibling of this pattern:

> **Idempotent normalize helpers** with the invariant "if the
> input is already in canonical form, return unchanged." All
> [normalizers] in this codebase share this contract. Any 4th
> write site that adds a prefix without this idempotency check
> will silently double-write.

`#625` audits the "4th write site" question. Answer: 1 real site
(`analytics_recorder.py:63`, fixed) + 6 grep-positive-but-safe
sites (documented) + 1 unrelated bug filed for later.

## For future auditors

If you're adding a new write site that composes
`f"{prefix}:{value}"` into a persisted composite key, ask:

1. Is `value` written to storage anywhere else with the same
   `{prefix}:` shape? If yes, consolidate on `normalize_post_id`
   or a sibling helper.
2. Could `value` ever arrive already-prefixed from an upstream
   normalizer? If yes, use `normalize_post_id` for idempotence.
3. Are you composing a NEW namespace (e.g. `session:`, `user:`)?
   File a task to add a matching helper in `cache/post_id_norm.py`.
