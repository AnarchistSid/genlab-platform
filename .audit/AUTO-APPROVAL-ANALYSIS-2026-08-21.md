# Why nothing auto-approves — exhaustive analysis, 2026-08-21

**Question asked:** are all generated posts approved for publishing?
**Answer:** no. 24 rendered posts sit unapproved with `reviewed_at` NULL on
every one, and 4 of 5 niches are structurally incapable of auto-approving
anything.

---

## 1. The queue

```
ARCHIVED     1744
PUBLISHED     563
VISUAL_READY   56    ← 32 scheduled, 24 awaiting approval
DRAFTED        29    ← 0 scheduled, not reviewable
```

| niche | awaiting approval | oldest | ever reviewed |
|---|---:|---|---:|
| sports | 7 | 2026-08-20 | **0** |
| gaming | 6 | 2026-08-17 | **0** |
| movies | 6 | 2026-08-19 | **0** |
| anime | 4 | 2026-08-20 | **0** |
| ai_creators | 1 | 2026-08-18 | **0** |

Some backlog is by design: ~15 blueprints/day are produced against a hard cap
of 1 publish per channel per day. Surplus is expected. The problem is that
nothing triages it.

---

## 2. The auto-approver is running fine

568 gate evaluations in 24h, last at 13:00:51. All five hard checks
(`has_video`, `has_hook`, `qc_passed`, `composite_score`, `virality_score`)
pass on every gate-approved blueprint. This is not a dead worker.

## 3. It cannot act, because the threshold exceeds the achievable ceiling

Confidence is the **arithmetic mean** of the per-signal confidences
(`auto_approval_gate.py:455`). Deduped to the latest examination per blueprint,
30 days, gate-approved only:

| niche | n | p25 | p50 | p75 | max | live `min_confidence` | auto-approved 7d |
|---|---:|---:|---:|---:|---:|---:|---:|
| ai_creators | 42 | 0.893 | 0.943 | 0.946 | 1.000 | 0.846 | 6 |
| gaming | 28 | 0.787 | 0.892 | 0.900 | 1.000 | 0.890 | 2 |
| anime | 11 | 0.811 | 0.879 | 0.890 | 0.906 | **1.000** | **0** |
| movies | 25 | 0.818 | 0.864 | 0.898 | 0.925 | **1.000** | **0** |
| sports | 28 | 0.816 | 0.861 | 0.886 | 0.913 | **0.986** | **0** |

For anime, movies and sports the threshold sits **above the maximum confidence
the gate has ever produced**. In 7 days the gate approved 701 blueprints across
those three niches and auto-approved zero of them. That is not a strict filter;
it is an off switch.

Note the ratchet is invisible in code review, because these values live in the
dirty tracked YAML from #227 — nobody reading the repo would ever see them.

---

## 4. Why the ceiling is ~0.89 — the gate runs on 2 of its 4 signals

The gate is designed to aggregate four numeric signals. Two never arrive.

| signal | designed | reality |
|---|---|---|
| `composite_score` | in `extra` | ✅ present, gate reads it |
| `virality_score` | in `extra` | ✅ present, gate reads it |
| `hook_classifier_score` | in `extra` | ❌ **promoted to a column** — absent from `extra` |
| `render_qc_min_score` | in `extra` | ❌ never written at all |

`hook_classifier_score` is fully populated (18/18, 33/33, 40/40 by niche,
avg 0.38–0.47) — but it is the one **promoted column** among the gate's
inputs, so `PostgresBackend._split_fields()` routes it to the dedicated column
and out of `extra`. `auto_approval_gate` reads `extra.get(...)` → `None` → no
contribution. `composite_score` and `virality_score` are *not* promoted, stay
in `extra`, and are exactly the two that work.

There is a compensating wrapper at `auto_approver.py:889` that rebuilds `extra`
from top-level fields — but it is guarded by
`if not isinstance(blueprint.get("extra"), dict)`, and in production `extra`
**is** a dict, so it never runs. It also lists only four keys and predates both
soft signals, so even when it does fire it would not expose them. Its own
comment says "keep the two sites in sync; if the gate adds a new field, both
wrappers must expose it" — the gate added two, and neither wrapper followed.

With two signals, each mapped into [0.7, 1.0], confidence reaches 1.0 only when
both saturate. `composite_score` rarely does, which pins the ceiling near 0.89.

### Do NOT simply wire the missing signals

`hook_classifier_score` currently averages **0.38–0.47**. The gate's own
uncertainty band (`HOOK_CLF_STRONG_FLOOR = 0.4`) contributes the raw value in
[0.4, 0.5). Adding it to the mean would drag confidence from ~0.89 to ~0.74 and
make auto-approval strictly *worse*.

The gate comment records the July distribution as avg 0.167–0.293 and explains
that treating an under-trained model's 0.17 as "17% confidence" was the
structural block on the Week 1→2 ramp. The model has improved but is still
inside the band it was quarantined for. **Its absence is currently
load-bearing.** Wiring it requires re-deriving thresholds in the same change.

---

## 5. The tuner is an open loop

`calibration_tuner.suggest_min_confidence` moves the threshold by
`imbalance × 0.10`, where `imbalance = (fp - fn) / n` comes from
`compute_confusion`.

`compute_confusion` compares the gate's **5-check verdict** (`gate_approved`)
against `operator_action`. **`min_confidence` is not an input to it.** The
threshold decides whether to *act* on a gate approval; it cannot change whether
the gate approves.

So a persistent FP > FN imbalance produces the same positive delta on every
run, regardless of how high the threshold already is. The tuner integrates a
constant error against a knob that cannot move it — an open loop wearing the
costume of a closed one. The only clamp was the mathematical `[0.0, 1.0]`,
which bounds the number and nothing operational. It ratcheted to that bound and
stopped: anime 1.0, movies 1.0, sports 0.986.

This is the same family as the transform-arms lesson and the affiliate
attribution gap: a learning mechanism whose feedback signal is disconnected
from its action.

### 5b. And it is worse than that: the tuner can only ever raise

Verified in prod after shipping the ceiling. `auto_approval_calibration`:

```
gate_approved = false : 164 rows, newest 2026-06-29   ← stopped ~8 weeks ago
gate_approved = true  : 389 rows, newest 2026-08-21   ← still flowing
```

Within the tuner's 4-week lookback there are **zero** `gate_approved = false`
rows. `compute_confusion` can therefore only ever produce TP and FP; `TN` and
`FN` are structurally 0. Every dry-run confirms it:

```
ai_creators  TP=95 TN=0 FP=20 FN=0
anime        TP=13 TN=0 FP=7  FN=0
movies       TP=10 TN=0 FP=7  FN=0
sports       TP=62 TN=0 FP=29 FN=0
```

With `fn = 0`, `imbalance = (fp - fn) / n = fp / n ≥ 0` — always non-negative.
The delta is `imbalance × 0.10`, so **the delta can never be negative**. The
tuner is a one-way ratchet by construction: it is mathematically incapable of
lowering a threshold, whatever the operator does.

This is why the ceiling was necessary rather than merely prudent. Without it
the only stop was 1.0; with it the only stop is the achievable p90. Either way
the tuner will sit at its ceiling, because nothing can push it back down.

**Why the false rows stopped is worth chasing.** 2026-06-29 is the exact start
date of the `calibration_logger` incident recorded in rule #19 —
`review_server.py:1443` swallowed logger failures at DEBUG from 2026-06-29
until the fix on 2026-07-16. The `gate_approved = true` path resumed after that
fix; the `false` path never did. That looks like a partial recovery from a
known incident rather than a second, independent cause.

`calibration_logger` itself does not filter — it writes
`gate_approved = decision.approved` verbatim. So the gap is upstream: the
operator only ever acts on gate-approved blueprints. Note this is not "the
operator never rejects" — there are 65 rejections in the window (they are the
FP column). Every one of them is a rejection of a blueprint the gate had
*approved*. Nothing gate-rejected reaches the review surface at all.

**Consequence for the fix:** the ceiling makes the ratchet safe but does not
make the tuner informative. Until gate-rejected blueprints appear in the
operator's review surface, the confusion matrix has two of its four cells
permanently empty, and "agreement %" computed from it is meaningless — the
exact trap rule #22 was written for, one layer deeper.

### Shipped

* `_HARD_CEILING = 0.95` — above this a threshold is indistinguishable from
  "disabled", and disabling must be an explicit operator act
  (`auto_publish.enabled: false`), never an emergent tuner property.
* `achievable_ceiling` — per-niche, p90 of gate-approved confidence over 30d,
  deduped to the latest examination per blueprint (raw percentiles are weighted
  by how many 30-minute passes a blueprint happened to sit through). p90 not
  max, because the max is one lucky blueprint.
* Clamping is explained in the suggestion's `rationale`, so the operator sees
  why a value moved.
* A zero-rate alert: gate approvals > 0 and auto-approvals == 0 over 48h prints
  `[ALERT] ... the threshold is acting as an off switch, not a filter` —
  observing the condition directly rather than inferring it from the number.

16 tests, validated by inversion (11 fail without the ceiling). Includes a
50-round ratchet test from four starting points, so a future regression fails
in CI rather than after weeks of silent shutdown.

---

## 6. `virality_score` — the top failing check, and it is not a bug

`virality_score` is the dominant `failed_checks` entry in every niche (284
gaming, 172 ai_creators). But the failures are **exactly 0.0**, never in the
0–0.02 band below the floor. Healthy blueprints reach 0.37–0.59, well past the
0.30 soft ceiling.

The scorer is pattern-based — a weighted sum over regex matches against hook
and caption text. A score of exactly 0.0 means *no pattern matched at all*.

69 of 273 blueprints (25%) score zero, and they behave differently downstream:

| bucket | n | published | archived | avg composite |
|---|---:|---:|---:|---:|
| non-zero virality | 204 | 78 (38%) | 65 | 0.586 |
| zero virality | 69 | **7 (10%)** | 38 | 0.575 |

Composite is effectively identical across the two buckets, so this is not
"bad blueprints" — it is specifically hook language the pattern set does not
recognise. The 4× difference in publish rate suggests the signal is real, but
25% zero-coverage is high and permanently bars those blueprints from
auto-approval. Pattern breadth is worth revisiting; it is a coverage question,
not a defect.

---

## 7. What remains for the operator

The clamp prevents recurrence. It does **not** move the three runaway values
already on disk — the corrective deltas (−0.07 to −0.14) exceed
`AUTO_APPLY_MAX_DELTA = 0.05`, so the tuner will print "operator review
required" rather than self-apply. That is correct: a large downward move
increases what publishes without review, and should be a deliberate act.

Suggested reset, at each niche's achievable p50 (auto-approves the better half
of gate-approved candidates; the 1-per-channel-per-day cap bounds the blast
radius regardless):

| niche | from | to |
|---|---:|---:|
| anime | 1.000 | 0.879 |
| movies | 1.000 | 0.864 |
| sports | 0.986 | 0.861 |
| gaming | 0.890 | unchanged (already ≈ p50) |
| ai_creators | 0.846 | unchanged (working) |

Filed separately, not done here:
* wiring `hook_classifier_score` / `render_qc_min_score` — requires
  re-deriving thresholds simultaneously (§4)
* `virality_score` pattern coverage (§6)
* #227 — the tuner writing git-tracked YAML, which is why these values are
  invisible in review and why `deploy.sh` cannot run
