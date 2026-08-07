# QB-FIX-08 B1 — Auto-Approver / Publisher Selection Order

**Date:** 2026-08-07 15:45 IST
**Result:** Selection is `(scheduled_for ASC, priority_score DESC)`. Deterministic. NOT oldest-created-first — but interacts with `auto_approver_v1`'s slot-assignment behavior in ways that scatter fresh content across a 5-day future queue. Backlog trajectory: **every non-movies niche produces faster than it publishes** (2.7×–∞), guaranteeing freshness degradation over time regardless of ordering.

## Step 1 — selection logic (verbatim)

`genlab-core/src/genlab_core/publishing/blueprint_selector.py:29-84`:

```python
def select_blueprint(niche_id, backlog_client) -> dict | None:
    all_blueprints = backlog_client.get_blueprints_by_status("VISUAL_READY", niche_id=niche_id)
    if not all_blueprints:
        return None
    eligible = _filter_by_gatekeeper(all_blueprints, niche_id)
    if not eligible:
        return None

    # 2026-06-15: sort by (scheduled_for ASC, priority_score DESC).
    # Earliest-scheduled wins; priority_score is the tiebreaker among
    # same-time siblings only.
    def _sort_key(b):
        f = b.get("fields", b)
        sched = f.get("scheduled_for") or "9999-12-31T23:59:59+00:00"
        score = -float(f.get("priority_score", 0) or 0)
        return (sched, score)

    eligible.sort(key=_sort_key)
    return eligible[0]
```

**Comment from PR #220 fix (2026-06-15):** publisher used to pick by `priority_score` alone. That threw away the scheduler's "11:30 first, then 12:00" signal — 11:30 stranded, cap fired on 12:00 sibling. Anime + sports on 2026-06-15 verified prod. The `scheduled_for ASC` ordering was added to honor the scheduler's slot intent.

## Step 2 — deterministic?

**Yes.** Python's `list.sort()` is stable, keys are tuples of (ISO string, float). Identical inputs produce identical outputs. Two runs would pick the same row.

**Not equivalent to "oldest-created wins."** The order depends on whatever `scheduled_for` values the auto-approver and manual approvers assigned. The interaction of `auto_approver_v1` + `blueprint_selector` produces the queue behavior — not the selector alone.

## Step 3 — median age at publish (14d)

Proxy for "median age of selected blueprint at publish time" — measures `updated_at - created_at` on rows currently in `PUBLISHED` state (the `updated_at` transitions on VISUAL_READY→PUBLISHED).

| niche | n published 14d | median hours | median days |
|-------|-----------------|--------------|-------------|
| ai_creators | 12 | **87.8** | 3.7 |
| anime | 2 | **152.5** | 6.4 |
| gaming | 1 | **194.6** | 8.1 |
| movies | 2 | **23.6** | 1.0 |
| sports | 5 | **169.3** | 7.1 |

**Compare to F-QB-0602 baseline** (event-to-publish lag): ai_creators 4.6d, sports 7.6d, anime 11.7d. These are similar magnitudes on a different axis (my measurement is create-to-publish, F-QB-0602 was event-to-publish). Freshness is degraded end-to-end.

**Movies is anomalous** at 1 day — driven by F4 batch's manual approval with same-day-plus-1 scheduled_for. That's an artifact of QB-FIX-01 F4 discipline, not the default behavior. When F4 winds down (Aug 8 is batch 2's fire), movies' median will regress toward the multi-day pattern.

## Step 4 — backlog trajectory (create vs publish, 14d)

| niche | created 14d | reels published 14d | net delta 14d | ratio (create/publish) |
|-------|-------------|---------------------|---------------|------------------------|
| ai_creators | 32 | 12 | **+20** | 2.7× |
| anime | 19 | 2 | **+17** | 9.5× |
| gaming | 18 | 0 | **+18** | ∞ |
| movies | 23 | 2 | **+21** | 11.5× |
| sports | 16 | 5 | **+11** | 3.2× |

**Every niche is producing faster than it publishes.** With daily 1-reel-per-niche cap, publish rate is fixed at ~14/14d = 1/day. Anything created above that rate goes to the queue. In 14 days:
- Publish capacity per niche: 14 reels
- Create rate exceeds capacity on all 5 niches
- Net queue growth per niche: 11–21 rows in 14 days
- Combined queue growth: ~87 rows in 14 days

Most of that gets archived (auto_archived_render_never_completed, F-QB-* sweeps, ordinary rejections) — not all rows reach VISUAL_READY-approved state. The measurable queue impact is visible in the 26 rows currently in VISUAL_READY/DRAFTED (per QB-FIX-07 A2, updated to 17 post-Z0 archive, currently ~17-20 depending on tonight's pipeline output).

## Current queue at selection order (per niche)

**ai_creators (5 approved) — order under `(scheduled_for ASC, priority_score DESC)`:**

| position | title | created (IST) | scheduled (IST) | priority_score |
|----------|-------|---------------|-----------------|----------------|
| 1 (fires tomorrow) | DeepMind Just Changed How AI Sees | 08-07 15:29 | 08-08 12:00 | 0.824 |
| 2 (Aug 9) | Introducing Agent Plugins | 08-06 22:52 | 08-09 12:00 | 0.849 |
| 3 (Aug 10) | How to Turn a Forecast Spreadsheet | 08-07 15:29 | 08-10 12:00 | 0.805 |
| 4 (Aug 11) | ChatGPT Created Ticket | 08-07 08:12 | 08-11 12:00 | 0.855 |
| 5 (Aug 12) | How to Schedule a Weekly Metrics Report | 08-07 15:29 | 08-12 12:00 | 0.824 |

**Observation:** priority_score has almost no signal here (0.805–0.855 range, tiebreaker-only since scheduled_for values are all distinct). The `auto_approver_v1` slot-assignment logic determines the order, and it's NOT strictly created_at-based:
- Newest (DeepMind, 15:29 today) got EARLIEST slot (Aug 8)
- Oldest of the batch (Agent Plugins, 22:52 last night) got 2nd slot (Aug 9)
- Highest-score (ChatGPT Created Ticket, 0.855) got 4th slot (Aug 11)

**The auto-approver appears to assign slots based on when it evaluates, not by content priority.** Tonight's 15:29 IST batch of 3 got the next 3 free slots (Aug 8/10/12) because Agent Plugins (Aug 9) and ChatGPT Created Ticket (Aug 11) were already scheduled by prior fires. This creates a rotation-like pattern that doesn't optimize for freshness OR score.

**sports (4 approved) — same behavior:**

| position | title | created (IST) | scheduled (IST) | priority_score |
|----------|-------|---------------|-----------------|----------------|
| 1 (fires tomorrow) | Ronald Acuña Jr. hi | 08-07 10:50 | 08-08 12:00 | 0.616 |
| 2 (Aug 9) | Ian Machado Garry vs Carlos Prates | 08-07 10:50 | 08-09 12:00 | 0.621 |
| 3 (Aug 10) | Jan Blachowicz | 08-07 10:50 | 08-10 12:00 | 0.621 |
| 4 (Aug 11) | Jordantaylor Ocon | 08-07 10:50 | 08-11 12:00 | 0.624 |

Sports 4 candidates all created at 10:50 IST today (from Y2's pipeline) but auto-approver split them across Aug 8/9/10/11. Priority_score again is nearly flat (0.616–0.624).

## Consequences

1. **Freshness inversion:** the sort key `scheduled_for ASC` picks earliest-scheduled — which USUALLY correlates with "first approved" — which USUALLY correlates with "older content." Movies' 1-day median vs anime's 6.4-day median tells the story: when F4's manual scheduling injected same-day slots, freshness was preserved. Under normal auto-approver flow, content ages 3-8 days.

2. **priority_score is dead weight** in current data. The tiebreaker only fires when scheduled_for values match, and they almost never do (auto_approver_v1 assigns one row per slot). If two rows had `scheduled_for=NULL` they'd compare by score — but rows without scheduled_for typically don't have `action_taken='approved'` and thus don't reach the selector.

3. **5-day pre-scheduled backlog on ai_creators** means new content published Aug 7-11 has to wait until Aug 12+ to fire. A trending story fetched today would compete only against the tail of the queue, not the head. The freshness-versus-throughput trade-off is being resolved implicitly toward "always publish something even if stale" rather than "publish only fresh."

4. **Movies + anime discipline is different because F4 was manual.** Once auto-approver takes over on those niches (post-Aug 10 rollout_pct decision), they'll trend toward ai_creators' pattern.

## What COULD change (design decision, not shipped)

Per prompt: "the fix is a design decision — newest-first maximises freshness but strands older content permanently; a scoring approach balances them; a hard age cap discards rather than strands."

Options for the operator to consider (do NOT ship in this pass):

- **A. Sort by created_at DESC (newest-first):** publishes most-recent content daily; old approved rows never fire → they age out and get archived by staleness sweeps. Maximum freshness, throws away older investment.
- **B. Sort by (max_age_score, priority_score):** weight `priority_score` by an age-decay function (e.g., `score / (1 + hours_since_created / 24)`). Newer content boosted, older content still eligible if very high-scoring.
- **C. Hard age cap:** any row older than N days at scheduled_for gets auto-archived instead of publishing. Prevents stale-content leaks; wastes some render cost.
- **D. Auto-approver assigns scheduled_for=today for newer content:** if a fresh candidate scores higher than the head of the queue, push the queue back a day. Reshuffling logic in auto_approver_v1's slot assignment, not the selector.
- **E. Keep current behavior:** publish oldest-approved daily; accept ~4-8 day median freshness lag; rely on the fact that most trending content stays trend-relevant for a week.

Interacts with the Aug 10 `rollout_pct` decision because flipping movies/anime to 0.1 means auto_approver_v1 starts assigning their slots too, and freshness degrades from F4's 1-day pattern toward the multi-day pattern seen on other niches.

## Gate

```
Selection ordering: (scheduled_for ASC, priority_score DESC) — deterministic
Median age at publish per niche (14d hours):
  ai_creators=87.8  anime=152.5  gaming=194.6  movies=23.6  sports=169.3
Create vs publish 14d net delta (rows created > rows published):
  ai_creators=+20  anime=+17  gaming=+18  movies=+21  sports=+11
Every non-capped niche produces faster than 1/day publish rate.
```

## Commit

`test(publishing): measure auto-approver selection ordering and backlog trajectory`
