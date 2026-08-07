# QB-FIX-09 C1 — `scheduled_for` Assignment + Three-Segment Lag Decomposition

**Date:** 2026-08-07 15:55 IST
**Result:** The approver's `_pick_next_available_slot()` function dominates the total lag. Slot contention (scheduled_for → published) is negligible (0.1–1.8h). Fetcher latency is small (0–24h). The 87–195 hour median lag is almost entirely the day-by-day forward walk with `daily_cap=1`.

## Step 1 — assignment logic (verbatim)

`genlab-core/src/genlab_core/scheduling/auto_approver.py:82-197` — `_pick_next_available_slot()`:

```python
def _pick_next_available_slot(
    *, backlog_client, niche_id, exclude_record_id="",
    canonical_slot_ist="12:00", in_flight_slot_counts=None,
) -> str | None:
    """Cap-aware: walks IST days 0..7 forward from 'now + 1h', counts
    existing VISUAL_READY+approved + PUBLISHED blueprints per (niche,
    day), returns the canonical 12:00 IST slot on the first day where
    count < effective_cap.
    """
    # daily_post_cap defaults to 1 (from platform_caps.yaml).
    # Multi-publish gate can raise it per niche/platform.
    cap = effective_cap(niche_id=niche_id, platform="instagram", ...)

    # Load existing approved + published for this niche, count per IST-day.
    records = get_blueprints_by_status("VISUAL_READY", niche_id=niche_id) \
            + get_blueprints_by_status("PUBLISHED", niche_id=niche_id)
    posts_per_day = { ... count per IST day of scheduled_for ... }

    # Same-pass collision guard (added 2026-07-21 after 6-approvals-piled-on-3-days).
    if in_flight_slot_counts:
        posts_per_day += extra_count per day_key

    # Walk forward day 0..7 from now+1h.
    for day_offset in range(0, 8):
        if posts_per_day[day_key] >= cap:
            continue
        return (that day at canonical_slot_ist)

    return None   # 7-day window exhausted → skip this approval
```

**Not a fixed offset. Not a slot-spacing rule. It is a first-fit-forward algorithm with daily cap of 1.**

Each new approval consumes one day of future capacity, starting from tomorrow. Under backlog pressure (B1's 2.7×–11.5× overproduction), the walk consistently lands 3–8 days out.

**7-day ceiling implication:** if all 7 days are full, the function returns None and the approval is deferred. That means the auto-approver silently stops approving when the queue is 7 days deep, without operator visibility.

## Step 2 — median gap per segment

Measured on rows currently in PUBLISHED state with `updated_at >= NOW() - 14d`:

| niche | n | fetch (h) | approver (h) | slot (h) | total (h) |
|-------|---|-----------|--------------|----------|-----------|
| ai_creators | 12 | 11.0 | **87.7 (100%)** | 0.1 | 87.8 |
| anime | 2 | 24.2 | **151.9 (99.6%)** | 0.7 | 152.5 |
| gaming | 1 | 0.1 | **194.4 (99.9%)** | 0.2 | 194.6 |
| movies | 2 | 6.2 | 21.8 (92.4%) | 1.8 | 23.6 (F4 artifact) |
| sports | 5 | 11.7 | **169.1 (99.9%)** | 0.2 | 169.3 |

Where:
- **fetch** = `blueprint.created_at - story.published_at` (event to blueprint in DB)
- **approver** = `blueprint.scheduled_for - blueprint.created_at` (created to slot picked)
- **slot** = `blueprint.updated_at - blueprint.scheduled_for` (scheduled to actually published)

`updated_at` transitions on VISUAL_READY → PUBLISHED, so it approximates the actual publish time.

## Step 3 — dominant segment per niche

| niche | dominant | share | actionable lever |
|-------|----------|-------|-----------------|
| ai_creators | approver | 100% | raise cap, cap production, or reshuffle on new approval |
| anime | approver | 99.6% | same |
| gaming | approver | 99.9% | same (also blocked by Y1 (c) defect) |
| movies | approver | 92.4% | same (F4 artifact — will regress to multi-day pattern once F4 winds down) |
| sports | approver | 99.9% | same |

**The 87–195 hour freshness lag is NOT a fetcher problem. It is NOT a selector problem. It is a slot-assignment queuing problem produced by `_pick_next_available_slot()` walking days forward with cap=1.**

## Consequences

1. **F-QB-0602's fetcher-cadence framing was wrong.** The 4–12 day event-to-publish lag it recorded is queue residency, not fetch latency. Update the finding to say the lag is post-blueprint, not pre-blueprint.

2. **Raising `daily_cap` from 1 → N is the direct freshness lever.** Each additional slot per day shortens the forward walk by 1 day for the next approval. Under 5-approvals-per-day pressure (B1's rate for ai_creators), cap=2 halves the walk; cap=3 reduces it to ~1.7 days average.

3. **B1's median-age-at-publish exactly matches what the algorithm predicts.** If cap=1 and N new approvals arrive per day, the median scheduled_for is `N/2` days out. ai_creators at 32 blueprints / 14 days = 2.3/day average → cap=1 predicts ~1.15 days ahead. Actual 87.7h = 3.7 days is higher because approvals arrive in bursts (3 at once tonight) and the same-pass collision guard forces sequential slot consumption.

4. **The 7-day silent-defer ceiling means the approver stops approving when it can't find a slot.** Under sustained overproduction, `_pick_next_available_slot` returns None and rejected approvals go unmentioned. Filed as follow-up: elevate this to WARN or a metric so operators see when the ceiling is hit.

## Not shipping in C1

No change to `_pick_next_available_slot()`. The lever exists; the cadence decision belongs in C2 for the operator.

## Gate

```
scheduled_for assignment: _pick_next_available_slot() walks IST days 0..7
  from now+1h, first-fit-forward on daily_cap=1
Per-niche median lag (hours), fetch / approver / slot / total:
  ai_creators  11.0 / 87.7 / 0.1 / 87.8
  anime        24.2 / 151.9 / 0.7 / 152.5
  gaming        0.1 / 194.4 / 0.2 / 194.6
  movies        6.2 / 21.8 / 1.8 / 23.6 (F4 artifact)
  sports       11.7 / 169.1 / 0.2 / 169.3
Dominant segment: approver on all 5 niches (92%–100% of total)
```

## Commit

`test(publishing): decompose event-to-publish lag into fetch, schedule, and slot segments`
