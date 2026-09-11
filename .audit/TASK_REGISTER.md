# GenLab task register — filed 2026-09-11 (OPS-06 D)
Filed, not started. M=measured, I=inferred, D=documented.

| id | title | one line | class |
|---|---|---|---|
| **T-06** | Characterise the prod test baseline | **201 failing node IDs, identical in both trees** (measured 09-11 via `baseline_compare`); 189 failed + 12 errors each side. Classify each by cause (env/credential, fixture rot, real defect, flaky). No repairs. | M |
| **T-07** | Cascade docs stale; voice gate must key on config | Live cascade head is `infsh_inworld`, not the documented ElevenLabs→OpenAI→Edge→gTTS. Correct the docs; specify the hero-format voice gate as `audio_provider ∈ config-declared hero tiers AND degraded == false`, never a hardcoded provider name. Blueprint now carries both fields. | M |
| **T-08** | Proportional retry haircut cannot recover an overshoot | The 85% retry cuts budget and script proportionally, preserving the overshoot ratio (story B: +0.29s → +0.42s *after* retry). Every `script_too_long` degrades by construction. Derive the retry cap from measured overshoot; gate on a previously-degrading blueprint passing on retry. | M |
| **T-09** | Clean-baseline testing needs mirrored machine state | A git worktree reconstructs the repo, not the machine. Collection depends on untracked models/runtime/caches **and two gitignored operator configs**. Superseded in practice by `scripts/baseline_compare.sh`; keep as the rationale record. | M |
| **T-10** | `pytest-baseline-diff` app parser defect | Its `_OUTCOME` regex matches ERROR-level log lines at column 0; needs the `::` node-id requirement. Shipped app, requires redeploy. | M |
| **T-12** | Suite provisioning spec | Collection depends on gitignored state. Spec = B.12's 19 prod-path files + T-11's two gitignored configs (`affiliate_catalog.yaml`, BB `persona.yaml`) + T-14c's divergent render path. Explains local ~516 vs prod 11,225 collection. | M |
| **T-13** | Waiter helper, PID-only | Two self-matching incidents: `pgrep -f` matched the waiter's own command line (3 loops spun 30 min, one silently blocked a launch); `pkill -f` then killed the ssh session. Encoded in `baseline_compare.sh`; extract as a shared helper. | M |
| **T-14a** | `caption_animator` filtergraph defect | `No such filter: '0.000'` (gaming), `'23.700'`/`'21.000'` (ai_creators) — a **time value lands where a filter name belongs**. ffmpeg exit 8. Not style-specific: movies ran identical style+count and succeeded. Discriminator is segment timing. | M |
| **T-14b** | Exception swallowed to WARNING, stage reports success | ffmpeg exit 8 logged at WARNING; **zero ERROR/CRITICAL lines** in the run; `RenderGamingVideo` and `ValidateVideos` both reported completion; a `< SPEC.min_duration → return base composite` guard then shipped the untransformed clip. **A class, not an instance** — enumerate every swallowed-to-WARNING fall-through and every return-base-composite guard. Rule #19. | M |
| **T-14c** | Gaming render output path diverges | Gaming writes `CriticalRush/.tmp/rendered/<run_id>/`; the other four use `/opt/genlab/.tmp/runs/`. Single-path audits report a false zero — INV-01b did. Converge, or register both paths in audit tooling. | M |
| **T-15** | Approval path — corrected wording | **The publisher requires approval; scheduling is not approval.** Gate: `action_taken == "approved"` (`platforms/gatekeeper.py:88`, hardened by R-08). **No direct writes to any of** `action_taken`, `reviewed_at`, `scheduled_for`, `action_taken_source`, `auto_approval_confidence` **outside the auto-approver's `update_fields`** — the sanctioned path stamps all five together by construction. | M |
| **T-16** | Zero-cut reels in three niches | anime, movies, sports ship single-clip 16–19s renders with **0 scene cuts**; only ai_creators cuts (9, 0.294/s). The one-borrowed-clip ceiling measured; the playbook's pattern-interrupt requirement fails by construction on the footage path. | M |
| **T-18** | Harness adoption | Every future fix prompt's "existing tests pass" gate is replaced by **"set (b) empty via `scripts/baseline_compare.sh`"**. The 201 baseline is the reference until T-06 shrinks it. | — |
| **T-19** | INV-01d — deferred render rows | Ducking, transitions, attribution slate for the four rendered niches from `.audit-retention/2026-09-11/`. Not time-pressured; opens after the 09-12 observation, each probe validated against a control first. | — |
| **T-20** | Probe-validation rule (standing methodology) | Any new measurement probe reports against a **positive control** before its first real reading. Case: three false zeros in one day — the retention `find` matching no MP4s, the scene-cut probe returning 0 where `showinfo` finds 9, and `find -maxdepth 1` reading a symlinked `.tmp` as empty. Not a code task. | M |

**T-17 — CLOSED, not filed.** Stale-slot rows are handled: `_SCHEDULE_STALE_AFTER = 18h` blocks them (`gatekeeper.py:19`) and two archiver timers sweep them (`archive-stale-visuals` 21:30Z, `archive-stale-drafted` 04:15Z). (M)

**T-11 — CLOSED 2026-09-11.** The 13 skip-delta tests gate on two gitignored operator configs; both now mirrored by the harness. (M)

**T-14 — SUPERSEDED** by T-14a/b/c. The "gaming rendered zero MP4s" reading was an artifact of scanning only `/opt/genlab/.tmp/runs/`.

---

## Added 2026-09-11 (OPS-16b)

| id | title | one line | class |
|---|---|---|---|
| **T-26** | Remove the retry-only unit | Gate: 7 consecutive days of full-run journal lines showing `_run_retry_pass` re-attempting prior failures under the 06:35/12:05/18:35 fires. Code read already confirms it is called on the full path (`publish_all_platforms.py:380,710`); this is the observed half. Observation 1 due from the 18:35Z fire on the 12:15Z movies/threads failure. | M |
| **T-29** | Approved-queue ordering is FIFO by approval time | Degraded, script-less blueprints publish ahead of narrated ones: `ca19f6a7` (degraded, 0 chars) holds 09-12 while `3c904e01` (356-char script, not degraded) waits for 09-13. Proposed: when >1 approved candidate waits, order by `degraded = false`, then confidence, then approval time. Config-level. | M |
| **T-27** | TN = 0 in the tuner's confusion matrix | The operator has never confirmed a rejection, so agreement measures half the decision (rule #22's shape). A rejection path — even sampled — is needed before the tuner's calibration means anything. | M |
| **T-28** | Tuner alerts unread | `[ALERT] … the threshold is acting as an off switch, not a filter` was correct for three weeks and reached only the journal. Same class as T-24. | M |

### #227 state as of 2026-09-11

Parity revert **done**: all five `publishing.yaml` at committed values
(0.85 / 0.732 / 0.85 / 0.85 / 0.85), tree clean, both tuner timers
**disabled** (`genlab-gate-tuner` 01:00Z, `genlab-calibration-tuner` 06:15Z;
restoration record in `.audit-retention/2026-09-11/tuner/`).

Mechanism, from the tuner's own log: **ratchet-only-up plus a self-locking
safety rail.** The `.bak` chain shows `0.9 → 0.95 → 0.99 → 1.0` over four days.
Escaping 1.0 needs a delta of −0.075 to −0.090, and `|delta| > 0.05` triggers
`[SKIP APPLY] operator review required` — it can climb in auto-appliable steps
and can only descend in blocked ones. The tuner had already diagnosed it
correctly: *"the threshold is acting as an off switch, not a filter."*

**Still owed:** §C's recomputed bimodal table (low-cluster max, main-cluster
min, proposed midpoint per niche) for the operator's value decision, and
A.3/A.5 (does the tuner ever commit; what "gate approved" counts in its
confusion matrix — 21-in-48h vs 1-in-14d cannot both be `action_taken`).

| **T-30** | Tuner "achievable ceiling" uses the wrong denominator | It is p90 of the `gate_approved` population (rows in `auto_approval_calibration`), which skews high by construction, not of the rendered set. Its suggestions admit 0/24 (movies, 0.925) and 0/11 (sports, 0.896) of rendered blueprints — it would "correct" an unreachable 1.0 to an unreachable 0.925 and log it as a fix. | M |
| **T-31** | Config reads must assert on the ACTIVE line | Any read or edit of a YAML value must count **non-comment** matches and assert exactly one; verification must read that same active line. Never `count=1` on a regex, never a `grep -o` that can match a comment. Case: the 0.65 edit changed a *comment* in `BlackboxBrief/config/publishing.yaml`, the verify step re-matched the same comment and reported success, and it would have shipped green. The assertion `expected 1 ACTIVE min_confidence line, found N` is what caught it — and repairing it revealed BB's real value was 0.715, invalidating three earlier readings. Same family as T-20: a green check that measured the wrong thing. | M |
