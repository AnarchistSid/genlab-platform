# #227 — threshold table for the operator's value decision
**Read-only. No values applied.** 14-day rendered set, n=101, every blueprint
carries an `auto_approval_confidence`.

## Method

Primary: **largest adjacent gap** in each niche's sorted scores.
Cross-check: **1-D k-means, k=2**, initialised at min/max.
**Both methods produce the identical split on all five niches** — not just one,
as asked. Bimodality threshold: widest gap ≥ 0.10; all five qualify
(0.181–0.346), so no niche falls back to p25.

## The table

| niche | n | low-cluster max | main-cluster min | gap | midpoint | **proposed (clamped)** | clears @ proposed | low excluded | clears @ committed | clears @ tuner |
|---|---|---|---|---|---|---|---|---|---|---|
| ai_creators | 30 | 0.472 | 0.791 | 0.319 | 0.632 | **0.650** | 24/30 | 6 | 14/30 (0.85) | — |
| anime | 13 | 0.431 | 0.612 | 0.181 | 0.522 | **0.650** | 7/13 | 5 | 2/13 (0.85) | — |
| gaming | 23 | 0.385 | 0.731 | 0.346 | 0.558 | **0.650** | 15/23 | 8 | 3/23 (0.85) | — |
| movies | 24 | 0.391 | 0.682 | 0.291 | 0.537 | **0.650** | 18/24 | 6 | 3/24 (0.85) | **0/24** (0.925) |
| sports | 11 | 0.457 | 0.789 | 0.332 | 0.623 | **0.650** | 9/11 | 2 | 9/11 (0.732) | **0/11** (0.896) |

## Two things the table shows that the rule did not anticipate

**1. The [0.65, 0.80] clamp is binding on every niche.** Every midpoint falls
*below* 0.65 (0.522–0.632), so all five clamp to the floor and the rule returns
**the same value for all five niches**. The per-niche tailoring it intends does
not occur. Either the floor is wrong for this data, or the midpoint is the wrong
statistic.

**2. The tuner's own suggested values clear nothing.** movies 0.925 → **0/24**
of the rendered set; sports 0.896 → **0/11**. Its "achievable ceiling" is p90 of
*gate-approved* confidence, which is evidently a different and higher-scoring
population than the rendered set — see A.5. Its suggestions are still above
every score the pipeline actually produced, so they would remain off switches.

## An alternative statistic, for comparison only

If the intent is "admit the main cluster, exclude the low cluster," the tighter
statistic is **just below main-cluster min**, not the midpoint:

| niche | main-cluster min | alt proposal (min − 0.01) | clears | low excluded |
|---|---|---|---|---|
| ai_creators | 0.791 | 0.781 | 24/30 | 6/6 |
| anime | 0.612 | 0.602 | 7/13 | 5/5 |
| gaming | 0.731 | 0.721 | 15/23 | 8/8 |
| movies | 0.682 | 0.672 | 18/24 | 6/6 |
| sports | 0.789 | 0.779 | 9/11 | 2/2 |

Identical admit counts to the clamped midpoint, identical exclusions, but
**per-niche** and derived from the data rather than from a floor. Offered as a
comparison, not a recommendation — the choice is the operator's.

## Not touched

R-08 approval hardening, the safety gate, the strict video policy. Only the
confidence-prediction threshold is in scope. Nothing applied; both tuner timers
remain disabled.

---

## Correction, 2026-09-11 (OPS-19 §1)

**BlackboxBrief's active `min_confidence` was `0.715` (line 131), not `0.85`.**
The 0.85 cited for ai_creators throughout OPS-14 §5's drift table, OPS-15 §B's
revert verification, and this file's "clears @ committed" column came from a
**commented-out line at line 22**. Both sides of the "live == committed, no
drift" comparison read the same comment.

Corrected admit count for ai_creators at its true pre-change threshold:
**0.715 → 24/30 clear** (not 14/30 as tabulated at 0.85).

MEASURED, not interpolated. The first draft of this correction said 22/30 by
eyeballing between the 0.65 and 0.85 columns; that was an inference and it was
wrong. The table's own cluster bounds settle it: ai_creators' scores are bimodal
with low-cluster max **0.472** and main-cluster min **0.791**, and *nothing lies
between them*. 0.715 falls inside that empty gap, so it admits exactly the
main cluster — the same 24 that 0.65 admits.

### Consequence: 1b66168e is a no-op for two niches

Both thresholds sit in the same gap, so for ai_creators the change moves the
admit set not at all. The same holds for sports: committed 0.732 also lies
inside its gap (0.457 → 0.789), clearing 9/11 either way — as the table's own
"clears @ committed" column already recorded.

| niche | pre-change (ACTIVE, verified at 1b66168e^) | clears before | clears at 0.65 | moved? |
|---|---|---|---|---|
| ai_creators | 0.715 (line 131) | 24/30 | 24/30 | **no — same gap** |
| sports | 0.732 (line 98) | 9/11 | 9/11 | **no — same gap** |
| gaming | 0.85 (line 122) | 3/23 | 15/23 | yes, +12 |
| movies | 0.85 (line 88) | 3/24 | 18/24 | yes, +15 |
| anime | 0.85 (line 86) | 2/13 | 7/13 | yes, +5 |

Verified by reading the ACTIVE (non-comment) line at `1b66168e^` for all five
files: **only BlackboxBrief carried the comment defect**; gaming, movies and
anime were genuinely 0.85 and sports genuinely 0.732.

### Correction to this correction (OPS-20): two legs, not one

The paragraph originally here said sports approving 0 would not be evidence of
failure. That was over-broad — it generalised a Q2-only exclusion into a blanket
exemption, and it is wrong for Q1.

Each niche moved in **two legs**, and they answer different questions:

| niche | leg 1 — parity revert | leg 2 — 1b66168e | in Q1? | in Q2? |
|---|---|---|---|---|
| sports | **0.986 → 0.732** (large) | 0.732 → 0.65 (no-op, same gap) | **yes** | no |
| movies | **1.0 → 0.85** (large) | 0.85 → 0.65 (+15) | **yes** | **yes** |
| anime | **1.0 → 0.85** (large) | 0.85 → 0.65 (+5) | **yes** | **yes** |
| gaming | 0.85 (unchanged) | 0.85 → 0.65 (+12) | **yes** | **yes** |
| ai_creators | 0.715 (unchanged) | 0.715 → 0.65 (no-op, same gap) | **yes** | no |

**Q1 — is approval unblocked at all?** Scope: **all five niches**. The relevant
comparison is against the 14-day state, in which the tuner had driven sports to
0.986 and movies/anime to 1.0 — thresholds above every score the pipeline
produced, i.e. off switches. Sports approving 0 while candidates clear 0.65 is a
**failure of something**, and the log must name what.

**Q2 — did 0.65 specifically admit more than 0.85 would have?** Scope: **gaming,
movies, anime only**. ai_creators and sports are excluded by construction,
because 0.715 and 0.732 sit inside their own empty gaps and admit the identical
set at 0.65.

The no-op finding above is sound; what was wrong was treating it as an exemption
from Q1. A niche can be excluded from Q2 and still be a hard failure under Q1.

History is not rewritten; this note is the correction of record.

### P3 reinterpretation

At 0.715, roughly three-quarters of ai_creators' 30 rendered blueprints cleared
the threshold, yet only 8 were approved. **ai_creators' narrowing was the
one-reel-per-niche-per-day cap and the approval queue — not the threshold.**

The threshold explanation stands unchanged for the other three: sports (0.986)
and movies (1.0) against maxima of 0.870 and 0.891, and anime (1.0) against
0.862 — all unreachable by construction.

---

## OPS-21 — scheduled-job label audit, 2026-09-11 23:20 IST

**Scheduler TZ: Asia/Kolkata (+0530), determined by discriminating test.**
Host `/etc/localtime` → `Asia/Kolkata`, `TZ` env unset, and no DST shift on
09-11/12/13 (all +0530). The discriminating evidence is `9a4b93a2`: its cron
names day-of-month **12** while its label says **09-11** 18:55 UTC. Only local
IST interpretation produces that rollover (00:25 IST Sep 12 = 18:55Z Sep 11);
under UTC interpretation the cron would fire 00:25Z Sep 12 and could not carry a
Sep 11 label. Consistency across the other three is corroboration, not proof.

| job | cron (raw) | tz evaluated in | resolved UTC | label claims | match? |
|---|---|---|---|---|---|
| 9a4b93a2 | `25 0 12 9 *` | Asia/Kolkata | **2026-09-11 18:55Z** | 09-11 ~18:55 UTC | ✅ |
| 4157f9c6 | `2 10 12 9 *` | Asia/Kolkata | **2026-09-12 04:32Z** | 09-12 04:32Z | ✅ |
| 58a4b47d | `27 12 12 9 *` | Asia/Kolkata | **2026-09-12 06:57Z** | 09-12 06:57Z | ✅ |
| 6092026c | `25 12 13 9 *` | Asia/Kolkata | **2026-09-13 06:55Z** | 09-13 ~06:55 UTC | ✅ |

**No mismatches. No job replaced.** The only mislabel in the set was `b674da45`,
already retired under OPS-20 §1 and now filed as T-32.

### Order-dependent pairs — MEASURED, not assumed

Upstream durations from the live journal (UTC-forced):

* **auto-approver**: starts :00/:30, exits in **3–24 s** (six consecutive runs
  15:00–17:30 all ≤ 24 s).
* **publisher**: 09-11 06:35Z fire finished **06:39:53** (4m53s); 09-11 12:05Z
  fire finished **12:17:01** (**12m00s**). Duration is variable, 5–12 min.

| pair | requirement | margin | verdict |
|---|---|---|---|
| 4157f9c6 (04:32Z) after ~04:30Z approver pass | approver exits ≤ 04:30:25 | **~95 s** | ✅ |
| 4157f9c6 (04:32Z) before 06:35Z publisher | — | **2h03m** | ✅ |
| 58a4b47d (06:57Z) after 06:35Z publisher | worst observed exit 06:47Z | **10 min** | ✅ |
| 6092026c (06:55Z) after 06:35Z publisher | worst observed exit 06:47Z | **8 min** | ✅ |

**Contention: none.** `58a4b47d` fires 09-**12**, `6092026c` fires 09-**13** —
different days, not two minutes apart. No fold-in required; `6092026c` stands.

**Residual risk, named:** the publisher's 5→12 min spread is two samples. A
06:35Z fire running >20 min would put both 06:55Z and 06:57Z reads mid-run.
Mitigated, not eliminated: both jobs gate on `ExecMainExitTimestamp` at STEP 0,
so that case reports "not yet run" rather than a false zero — which is the whole
point of the three-meanings gate.

---

## FIX-T23 gate 2 (18:35Z fire) + T-26 obs 1 — 2026-09-11/12

**STEP 0 (M):** publisher 18:35:01 → 18:45:08 UTC, 10m07s, Result=success.
Third duration sample (prior: 4m53s @06:35Z, 12m00s @12:05Z).

### Gate 2 — PASSED
4 post IDs, anime blueprint `aeff4e14`, slot 18:00Z, published 18:44–18:45Z.

| niche | post_id | status | pub | scheduled_for | stale_at | in window? |
|---|---|---|---|---|---|---|
| anime | instagram:17925963714184715 | SUCCESS | 18:44 | 18:00Z | 09-12 12:00Z | ✅ |
| anime | youtube:FSNjcxnOyj4 | SUCCESS | 18:44 | 18:00Z | 09-12 12:00Z | ✅ |
| anime | facebook:1585787566657906 | SUCCESS | 18:44 | 18:00Z | 09-12 12:00Z | ✅ |
| anime | threads:18036935789831548 | SUCCESS | 18:45 | 18:00Z | 09-12 12:00Z | ✅ |

**Precision on the gain (I):** the 18:00Z slot would NOT have been lost without
FIX-T23 — it was future at 12:05Z but still inside its 18h window at 06:35Z on
09-12. What the 18:35Z fire bought is **same-day publication instead of
next-day**. Timeliness, not rescue. `audio_provider` is NULL on this blueprint
(`tier = -`) though `degraded=false` — FIX-T01's field is not populated on the
anime path.

### T-26 observation 1 of 7 — 2026-09-11, MECHANISM CONFIRMED
The FULL run **does** execute the retry pass. Measured:
```
18:40:01  retry_pass: [publish] Retrying 1 failed platform(s) for blueprint 9c9927e3: ['threads']
18:40:02  retry_pass: [publish] Retry FAILED: movies/threads (TRANSIENT):
          Threads: video container creation failed (VIDEO):
          Param text must be at most 500 characters long.
```
Code read (`publish_all_platforms.py:380,710`) is now confirmed by observation:
`_run_retry_pass` fires on the full path. **1 of 7 observations toward removing
`genlab-publisher-retry.timer`.**

**Two NEW defects surfaced by that line (out of scope — filed, not fixed):**
* **T-34 — Threads captions exceed the 500-char API limit.** movies/threads fails
  at container creation on length. CLAUDE.md documents Twitter ≤280 but carries
  no Threads 500 rule, and the caption builder does not enforce one.
* **T-35 — a permanent failure is classified TRANSIENT.** A 500-char overflow
  fails identically on every retry; tagging it TRANSIENT guarantees repeat
  attempts that cannot succeed. Retry classification must distinguish
  length/validation errors from network transients.

### ai_creators was PAUSED (M) — a confound on every ai_creators reading
`[publish] niche=ai_creators — niche is paused (PR #577 emergency-stop),
skipping fresh publish + retry pass`. Auto-approver logged
`[ai_creators] examined=0 ... paused=True` from ≤13:00Z through 18:35Z on 09-11 —
**it was never evaluated**, so its zeros in that window are not gate results.
`niche_pauses` is now empty; the sweeper deleted 2 expired rows at 02:00:02Z on
09-12; all five niches read `paused=False`. **#218 is not blocked going forward.**

### STEP 4 — 06:35Z 09-12 prediction
Due and in-window at the next fire: gaming `76d4d9de` (slot 09-12 06:30Z,
"Grand Theft Auto V" — note bare title), sports `2f663eb9` (slot 09-12 06:30Z),
gaming `2cb13852` (slot 09-11 19:00Z, stale_at 09-12 13:00Z).
Future-blocked: `f89d7b52` 09-13 · `566bd8be` 09-14 · `a7a3d9f8` 09-15 ·
`7c3a4028` 09-16 · `c5bdbb0d` 09-17 · `44fb8c90` 09-18.
Gate tally for the run: 23 × schedule_gate, 14 × Stale.

---

## FOUR INSTRUMENT DEFECTS found while running this check (all M)

Each would have produced a confident wrong reading. All four are now corrected
in the 06:32Z and 06:57Z jobs.

1. **`genlab-pipeline@<niche>.service` does not exist.** Real units are
   `genlab-pipeline-{ai,gaming,sports,movies,anime}`. `genlab-pipeline-ai-creators`
   is also a phantom.
2. **`systemctl show` returns `Result=success` for a unit that does not exist**
   (`LoadState=not-found`). My STEP 0 gate in both scheduled jobs would have
   read a green light on five phantom units and proceeded. **Always print
   LoadState beside Result.** → **T-36**.
3. **The publisher declares `SuccessExitStatus=0 1 3 4`.** The 18:35Z fire
   exited **1** and systemd recorded **success**. `Result=success` is therefore
   NOT a valid completion gate for this unit — print `ExecMainStatus`. This is
   rule #26's remedy applied so widely it now masks genuine failures. → **T-37**.
4. **`psql -U genlab` fails** ("role genlab does not exist") and defaults to port
   **5433**. Working DSN is `DATABASE_URL` in `/opt/genlab/.env` → **127.0.0.1:5432,
   user `genlab_app`**. This is the 5432/5433 ambiguity from A-0058, still live.

## Schedule facts that invalidated the 04:32Z job (M)
* Pipelines are **staggered**, not a single 02:30Z fire:
  **ai 02:30Z · movies 03:30Z · gaming 04:00Z · sports 05:00Z · anime 06:00Z.**
* Auto-approver `OnCalendar=*-*-* 06..22:00,30:00 UTC` — **no runs 22:30→06:00.**
* Therefore at 04:32Z zero approvals could exist from any overnight fire, and
  only 3 of 5 niches had fired. The prediction was structurally impossible.
  Job moved to **06:32Z** (after both 06:00/06:30 approver passes, before the
  06:35Z publisher) — the only window where it can be made.
* Anime fires 06:00Z and the approver runs 06:00/06:30, so anime is structurally
  one cycle behind and may legitimately publish at 12:05Z rather than 06:35Z.

---

## OPS-23 §2 — ai_creators pause provenance, 2026-09-11

### Verdict: HUMAN action via the dashboard. Not an automated trip.

`grep` for callers of `niche_pause.pause()` across `genlab-core/src/` and
`scripts/` returns **zero**. Nothing in the pipeline, publisher, approver or any
runner can set a pause. The only write surface is
`POST /api/v1/scheduling/pauses` (dashboard, PR #577), reachable by a person.
**§2's "if automated, name the rule" does not apply — there is no rule to name.**

### Dated timeline (M unless noted)

| when (UTC) | event | source |
|---|---|---|
| 2026-09-11 ~09:00 | ai_creators examined normally, 18 rows/hr | `gate_examinations` |
| 2026-09-11 10:00–11:00 | examinations drop 18 → **6** → 0. **Pause begins here.** | `gate_examinations` |
| 2026-09-11 13:00–22:30 | approver logs `[ai_creators] examined=0 … paused=True` on every run (15 observations) | journal |
| 2026-09-11 18:35 | publisher: `niche is paused (PR #577 emergency-stop), skipping fresh publish + retry pass` | journal |
| 2026-09-11 22:30:04 | last `paused=True`; approver then stops for the overnight window | journal |
| 2026-09-12 02:00:02 | `sweep_expired_pauses deleted 2 row(s)` — expiry had passed | journal |
| 2026-09-12 02:35 | all five niches read `paused=False` | live probe |

**Correction to the earlier reading:** I previously dated the pause "≤13:00Z"
from the journal. `gate_examinations` puts the true start at **~10:xx Z**, three
hours earlier. The journal could not show it because of T-44.

### UNRECOVERABLE, and stated as such rather than guessed
**Who set it, the reason string, the exact `paused_until`, and who the second
swept row belonged to are all gone.** Three independent reasons:
* the row was DELETEd by the sweeper and there is no archive (**T-43**);
* the journal reaches back only ~14h (**T-44**);
* the dashboard logs no request line for the POST.

Note the sweeper deleted **2** rows — so a second niche also carried an expired
pause. Which one is not recoverable.

### "Any other niche paused in the last 30 days?" — CANNOT BE ANSWERED
`gate_examinations` is durable and survives journal rotation, but it **cannot
distinguish "paused" from "no candidates"** — a paused niche and an idle one both
produce zero rows. Proof from the data itself: on 2026-09-11 gaming shows only
2 examined-hours and sports 5, and neither was paused. Reporting the low-hour
table as a pause history would be a false positive of exactly the T-20 shape,
so it is not reported as one.

**Answering this properly needs T-43 fixed first.** Until a pause is archived on
expiry, pause history is only knowable for the ~14h the journal retains.

### Why this is the fourth confound on ai_creators this week
1. BB's live `min_confidence` was 0.715, not the 0.85 read from a comment (T-31);
2. 0.715 and 0.65 sit in the same empty score gap, so `1b66168e` is a no-op there;
3. the one-per-niche-per-day cap and approval queue, not the threshold;
4. **and it was paused with `examined=0` for ~12h of 09-11** — those zeros are not
   gate results at all.

A silent emergency-stop that expires on its own and then erases itself is the
T-14b shape lifted to the niche level: the state is invisible while active and
unprovable afterwards.

---

# OPS-20 §1/§2 — 09-12 cycle. Run at 08:31Z (both windows had passed)

**The 06:32Z prediction was never issued** — this session resumed at 08:31Z, after
both scheduled windows. What follows is the confirmation scope only; the
prediction/confirmation split did not happen and is not claimed.

## STEP 0 — GATE (OPS-23 §3): all three satisfied

| fact | value |
|---|---|
| (a) exited | publisher 06:35:01 → **06:48:09Z** ✅ |
| (b) exit code **verbatim** | **`ExecMainStatus=1`** (`Result=success` — reported, not gated) |
| (c) post count, 06:35–07:00Z window | **8** ✅ |

**Exit 1 for the second consecutive fire.** T-37 is recurring, not a one-off: the
publisher exits 1 and `SuccessExitStatus=0 1 3 4` renders `Result` meaningless.

All five pipelines `LoadState=loaded`, `ExecMainStatus=0`. **Stagger confirmed
exactly**: ai 02:30:08→03:02:55 · movies 03:30:11→03:47:39 · gaming
04:00:11→04:12:19 · sports 05:00:10→05:21:04 · anime 06:00:11→06:08:41.

## Publishes — 8 posts, 2 niches

| niche | bp | slot | published | stale_at | tier |
|---|---|---|---|---|---|
| gaming | `2cb13852` | 09-11 19:00Z | 06:39 | 09-12 13:00Z | – |
| sports | `2f663eb9` | 09-12 06:30Z | 06:44 | 09-13 00:30Z | `infsh_inworld` |

**Prediction result: 2 of 3 held, and the third was correctly anticipated.**
`2cb13852` ✅, `2f663eb9` ✅. `76d4d9de` did NOT publish — the daily cap admitted
**one** gaming post and the older slot (09-11 19:00Z) won. Reported as *which and
why*, not as "gaming published".

`sports` carries `audio_provider=infsh_inworld`; gaming `2cb13852` is NULL
(rendered before FIX-T01 reached that path).

---

# THE HEADLINE FINDING — ai_creators is in a self-sustaining auto-pause loop

## I was wrong in OPS-23 §2. The pause IS automated.

```
reason:       auto_paused_health_critical: Reach dropped ∞x: 48h avg 0 vs 14d baseline 5. Probable shadowban.
paused_by:    system:account_health_check
created_at:   2026-09-12 06:00:10Z     paused_until: 2026-09-12 10:00:21Z
```

I reported "human dashboard action, zero automated callers" from a grep for
`niche_pause.pause|pause_niche|\.pause(`. The real caller imports the symbol
directly and calls a bare `pause(` — `compliance/account_health.py:384`,
`maybe_auto_pause_on_critical`. **Textbook grep-doesn't-match-codebase**, and I
filed a confident wrong conclusion on it.

## The rule, now named (what §2 asked for)

* Flag `GENLAB_AUTO_PAUSE_ON_HEALTH_CRITICAL=1` (live in prod).
* Trips when 48h avg reach ÷ 14d baseline reach < `CRITICAL_RATIO_THRESHOLD` (**0.1**).
* Pause window **NOW + 4h**, `paused_by='system:account_health_check'`.
* **UPSERT semantics: each new critical signal re-pauses and extends the window.**

## The loop

1. ai_creators' recent reach reads **0**;
2. → critical → **auto-pause 4h**;
3. → publisher skips the niche entirely (confirmed at 06:35:02Z, and `ca19f6a7`
   sat approved with an in-window 06:30Z slot and did not publish);
4. → no new posts → reach stays 0;
5. → next check re-pauses. **It fired 8 times in a single run**, once per
   blueprint examined, each an UPSERT extending the window.

**A niche paused for low reach cannot earn reach.** The remedy and the disease
are the same action.

## The detector has no absolute floor — this is the root defect

The baseline is **5**. Not 5,000 — five. Going 5 → 0 is mathematically an
infinite drop and practically meaningless; both numbers are noise.
`MIN_BASELINE_SAMPLES=5` gates the *sample count*, never the *magnitude*. So the
shadowban detector fires hardest on exactly the low-reach channels that most need
to keep publishing, and then prevents them from publishing.

ai_creators DID publish 4 posts on 09-11 at 12:07Z (status since advanced to
`INSIGHTS_6H`). The zero is a **reach** zero, not a publish zero.

*(Method note: my first 48h query filtered `status='SUCCESS'` and returned a
false zero for anime and ai_creators, because rows advance SUCCESS →
INSIGHTS_6H. T-20 in my own SQL — caught by the anime row I knew existed.)*

---

# `is_paused()` FAIL-OPENS SILENTLY — and I shipped the broken probe

Measured, both directions, same host and user:

```
without env:   _connect() -> None          is_paused('ai_creators') -> False   # FAIL-OPEN
with .env:     _connect() -> Connection    is_paused('ai_creators') -> True  + full row
```

`_connect()` returns None when `DATABASE_URL` is absent from the environment, and
`is_paused` fail-opens to False. **My 02:35Z reading of "all five niches read
paused=False" was a false negative**, and the OPS-23 §2 claim that the pause "had
cleared" rests on it. I then wrote that same env-less probe into all three
scheduled jobs, where it would have reported `paused=False` regardless of truth.

Correct form: `set -a && . /opt/genlab/.env && set +a` before invoking python.

---

# Q1 — IS APPROVAL UNBLOCKED? (all five niches)

| niche | candidates | cleared 0.65 | approved | cleared-not-approved | verdict |
|---|---|---|---|---|---|
| ai_creators | 4 | 4 | **0** | 4 | **blocked — queue full** |
| anime | 1 | 1 | 1 | 0 | ✅ |
| gaming | 2 | 2 | 1 | 1 | ✅ (1 rejected by LLM judge) |
| movies | 2 | 2 | 1 | 1 | ✅ |
| sports | 5 | 5 | **0** | 5 | **see below** |

**ai_creators — the threshold is not the constraint. Quoted:**
> `[auto_approver] niche=ai_creators bp=0d0182d2… — no cap-available slot in next 7 days, skipping`

All four cleared 0.65 and all four were refused for **slots**, not confidence.
ai_creators has **7 queued out to 09-19**. This is the P3 reinterpretation
confirmed verbatim: the cap and queue are the narrowing, not the threshold.

**sports — 5 cleared, 0 approved, and it is NOT a threshold failure either.**
Only 1 of the 5 was examinable (4 are `DRAFTED`, not `VISUAL_READY`). The one
examined was rejected by the LLM judge:
> `[gate] LLM judge fired for niche=sports rule_decision=False rule_conf=0.40 llm_decision=False reason=Virality score of 0.0 indicates minimal engagement potential; hook is too niche/specific for broad sports audience`

**Q1 verdict: approval IS unblocked.** Sports published `2f663eb9` today — its
first publish in 14 days — and movies and anime each approved one. No niche is
blocked by the threshold. The remaining blockers are the slot queue
(ai_creators), the daily cap (gaming), render state (sports `DRAFTED`), and the
LLM judge — all downstream of the threshold, and all now named.

# Q2 — DID 0.65 MOVE ANYTHING? (gaming / movies / anime only)

Marginal admits this cycle — cleared 0.65 but **not** 0.85:

| niche | marginal | of candidates |
|---|---|---|
| gaming | 0 | 2 (both ≥ 0.85) |
| movies | **2** | 2 |
| anime | **1** | 1 |

**3 marginal admits in one cycle.** Both of movies' candidates (0.784, 0.729) and
anime's single approved one (0.836) would have been refused at 0.85 — anime's is
the blueprint now holding the 09-13 06:30Z slot. A one-cycle slice of the 14-day
prior (+12/+15/+5), as expected.

**ai_creators and sports: N/A**, not zero — 0.715 and 0.732 sit inside their empty
score gaps.

# Queue depth (T-29 evidence)

| niche | queued | next slot | last slot |
|---|---|---|---|
| ai_creators | **7** | 09-13 06:30Z | 09-19 06:30Z |
| gaming | **7** | 09-13 06:30Z | 09-19 06:30Z |
| anime | 1 | 09-13 | 09-13 |
| movies | 1 | 09-13 | 09-13 |
| sports | **0** | — | — |

16 queued, out to 09-19. ai_creators and gaming are saturated a full week ahead —
which is precisely why their new candidates get "no cap-available slot in next 7
days". Sports at 0 explains why its 5 candidates matter and why 4 being `DRAFTED`
is the live constraint there.
