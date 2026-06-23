# Gen Lab Operator Playbook

Operator-facing handoff documentation for the 2026-06-22→23 sprint
(38 PRs, lifetime PR #114). Reading this in order gives you the
post-deploy activation checklist + daily workflow + workflow
patterns that future contributors should keep using.

If you're a contributor reading this for the first time after
clone: jump to **Pre-commit setup** below. If you're the operator
running prod: jump to **Post-deploy activation**.

---

## Pre-commit setup (do this once after clone)

`.pre-commit-config.yaml` is committed and pins the EXACT same
ruff version CI runs (`v0.15.14` as of 2026-06-23). Activate
the hooks once:

```bash
pre-commit install                # arms the git pre-commit hook
pre-commit run -a                 # one-time check against entire repo
```

After this, every `git commit` runs:
- `ruff check --fix` (fixes I001 import-order + safe rules in place)
- `ruff format` (matches CI's `ruff format --check` exactly)
- whitespace + EOF + YAML + private-key + gitleaks checks

**Why this is non-negotiable**: the 2026-06-22→23 sprint hit the
recurring `lint: fail` post-push pattern 3-4 times. Every recurrence
caused by an `Edit` into an existing import block breaking I001
order. The hooks above are the durable fix that survives across
contributors and sessions.

When `Edit`ing imports in any Python file without pre-commit
installed: run `ruff check --fix <file>` manually on every modified
file before push.

---

## Post-deploy activation (2026-06-23 sprint PRs)

### 1. Deploy the sprint bundle

PRs #477-#490 are queued, zero migrations required. Single command
on prod:

```bash
sudo -u genlab ./scripts/deploy.sh --apply
```

All shipped features fail-OPEN — if any single feature breaks on
deploy, the rest still work.

### 2. Verify Mission Control surfaces render

Open the dashboard's Mission Control page. Three learning-status
cards should now appear:

| Card | What it shows | Polls |
|---|---|---|
| `AutoApprovalCalibrationCard` (AUTO #1c, prior) | Gate accuracy per niche | 60s |
| `SponsorshipReadinessCard` (PR #481) | Per-niche tier + Copy/Kit buttons + portfolio footer | 60s |
| `BanditHourHeatmap` (PR #489) | Per-niche 24-hour color strips for the optimal-time bandit | 60s |

All three share the same visual language (5 niche rows, 60s
polling, ready-badge at 30-obs threshold). Operator's mental
model is consistent across them.

### 3. Per-niche cross-platform synergy (default off)

PRs #480 + #486 ship the wires for YouTube→X and Facebook→X
auto-teasers but ship default-off. To activate per niche, add to
that niche's `publishing.yaml`:

```yaml
cross_post:
  youtube_to_x:
    enabled: true
  facebook_to_x:
    enabled: true
```

When enabled and a publish succeeds on YT or FB, an X teaser fires
automatically — main tweet = hook text only (no URL per CLAUDE.md
X rule), first reply = source URL with `utm_source=x_teaser` tag.

Recommended first niche: one with strong X follower base + active
YouTube channel.

### 4. Optimal-time bandit activation (~2 weeks after deploy)

PR #487 ships **producer + env-gated consumer**. Producer writes
`hour:{H}:{platform}:{niche}` arms on every publish immediately
after deploy. Consumer is gated behind `GENLAB_OPTIMAL_TIME_BANDIT=1`.

After ~1-2 weeks (operator watches `BanditHourHeatmap` for
per-niche READY badges at 30+ observations), flip the env flag:

```bash
echo "GENLAB_OPTIMAL_TIME_BANDIT=1" >> /opt/genlab/.env
sudo systemctl restart genlab-publisher.service
```

The consumer falls through to the legacy Bayesian-shrinkage
heuristic when bandit arms are cold-start empty for any
(niche, platform). Safe to flip even before all niches are
ready — non-ready niches keep using the heuristic.

---

## Daily operator workflow (post-activation)

```
Open Mission Control
  ↓
See SponsorshipReadinessCard tier badges per niche
  ↓
For any "eligible_now" or near-eligible niche:
  [Copy]  → outreach pitch in clipboard
              paste into email/Slack/LinkedIn
              edit [BRAND] + [NAME], hit Send
  [Kit]   → preview /media-kit/<niche>, Cmd+P → Save as PDF
              attach to email if brand prefers PDF over link
  ↓
For cross-channel pitches (5-niche portfolio deal):
  [Copy portfolio]    → cross-channel pitch in clipboard
  [View portfolio →]  → preview /media-kit/all, Cmd+P → Save as PDF
  ↓
After brand opens kit, /media-kit/<niche> renders:
  - Per-niche audience numbers (PR #481/#482)
  - Sponsorship-tier badge
  - Top 3 recent posts as CLICKABLE LINKS (PR #490) — brand
    judges actual content quality before deciding
```

---

## Sponsorship-loop architecture (composers ≥ 2)

| Primitive | Origin PR | Used by |
|---|---|---|
| `_compute_tier` | #481 | #482, #483, #484, #488 |
| `_build_audience_summary` | #482 | #483, #484, #488 |
| `_build_niche_kit` | #483 (refactor of #482) | #482, #483, #490 |
| `_SOURCE_ROUTES` (cross-post dispatch) | #486 (refactor of #480) | YT + FB routes |
| `extra_arms` hour pattern | #487 producer | #489 + future bandits |
| `_derive_post_url` | #490 | future kit thumbnails |

All sponsorship surfaces share **one** tier-computation primitive
(`_compute_tier`) so they can never disagree on what a niche's
tier is. Cross-platform synergy will be similar once a 3rd route
ships — `_SOURCE_ROUTES` extracted at the 2nd implementation per
the "rule of two."

---

## Test patterns for lazy-imported dependencies

Endpoints using lazy imports (`from X import Y` inside a function)
for cold-start safety need two test-pattern conventions:

### 1. Patch the source module, not the importing module

Lazy imports inside a function don't create attributes on the
dashboard/server module — they look up bindings in the source
module's namespace at call time. Patch at the source:

```python
# ✓ Works
patch("genlab_core.http.backlog_client.BacklogClient", ...)
patch("genlab_core.learning.arm_loader.load_all_arms", ...)

# ✗ AttributeError: module does not have the attribute 'BacklogClient'
patch("server.api.bandit_hour_posteriors.BacklogClient", ...)
```

PR #489 (bandit hour-heatmap) initially shipped with wrong-target
patches → 13/15 tests failed. Lesson learned: when mocking lazy
imports, patch the source module.

### 2. Autouse-fixture-stub expensive lazy-loaded DB calls

Without a default stub, every test pays the 5s `connect_timeout`
per invocation. A 22-test suite jumps from ~1s to ~85s. Pattern:

```python
@pytest.fixture(autouse=True)
def _stub_top_posts(monkeypatch):
    monkeypatch.setattr(
        "server.core.top_posts_pg.fetch_top_posts",
        lambda niche_id, **kw: [],
    )
    yield
```

Individual tests that assert the behavior re-patch inside the
test body. PR #490 (top-post embed) restored 22-test runtime
from 85s back to 0.9s with this fixture.

---

## Workflow patterns to keep using

These compounded through 14 PRs in one session — durable across
future work:

### 1. Pre-format-then-push

Run `ruff check --fix + ruff format` on **all modified files**
locally before push, not just the new ones. The 2nd-time-ruff-bites
pattern from PRs #477/#478/#482 eliminated when this became habit.
Pre-commit hook above automates this.

### 2. Refactor at the 2nd implementation

Don't extract abstractions on the 1st implementation (premature).
Extract when a 2nd similar implementation appears — the
duplication makes the right extraction point visible. PR #486
extracted `_SOURCE_ROUTES` dispatch when FB → X joined YT → X;
PR #483 extracted `_build_niche_kit` when portfolio joined
per-niche kit.

### 3. Symmetry walk after each feature ship

The SECOND PR after a feature ships usually reveals symmetry holes
the first PR missed. PR #485 was the kit-link discoverability
symmetry (PRs #482/#483 shipped routes without nav entries);
PR #488 was the portfolio Copy symmetry (PR #484 shipped per-niche
Copy but missed the portfolio version). Walk the user journey
end-to-end after each feature ships — symmetry holes pop out in
30 seconds.

### 4. Producer-before-consumer for learning systems

When shipping a new learning loop, ship the PRODUCER side
(captures data) immediately + the CONSUMER side env-gated
(reads data, but default-off). Avoids cold-start regressions
where the new consumer is worse than the legacy heuristic until
enough data accumulates. PR #487 (optimal-time bandit) is the
canonical example — producer writes arms immediately, consumer
flips on after 1-2 weeks of arm accumulation surfaced by PR #489's
heatmap card.

### 5. Fail-OPEN for kit/dashboard augmentations

Anything that decorates an existing surface (top-posts embed in
kit, scratchpad in LLM judges, hour-arm in PendingFeedbackTask)
MUST fail silently — empty list / no contribution / debug log —
rather than blocking the surface it decorates. The decorated
surface's contract is the load-bearing one; the augmentation is
gravy.

---

## What's NOT yet shipped (deferred work)

| Capability | Why deferred | Estimated effort |
|---|---|---|
| Embedding-based memory (semantic similarity for dedup + scratchpad chunk selection) | LARGE, requires embeddings infra choice + storage migration | ~1-2 weeks |
| Hourly trend-hopping express lane | MEDIUM-LARGE, touches scheduling cap semantics | ~1 week |
| DPO fine-tune pipeline | Month-3 work, needs eval harness first | ~4 weeks |
| IG / Threads / TikTok URLs in top-posts kit | v2 — needs shortcode lookup or handle inference | ~2-3 days each |
| Auto-fire outreach on tier-transition | needs historical-tier tracking + transition detection | MEDIUM |
| Day-of-week bandit extension (`dayhour:{D}:{H}:{platform}:{niche}` arms) | Premature until hour-only bandit validates in prod (~1 month) | SMALL |

---

## Session reference

Full session memory: see project memory at
`memory/session_2026_06_22_full_autonomy_sprint.md` for PR-by-PR
notes including the architectural primitives table and the
recurring workflow patterns documented above.
