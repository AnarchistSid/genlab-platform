# PHASE 2.7 — Last correction pass

## Four leading lines

1. **5432 = still open — accepted risk this session.** Operator chose not to
   close (auto-mode classifier blocked prior repair attempts; explicit prompt
   this session answered "record accepted risk"). BFG-blob follow-up: current
   DB password NOT in `c463c6ed` (Phase 2.6 hash-only compare). Historical
   password rotation trail: no `git log -S POSTGRES_PASSWORD=` commits
   suggest actual rotation events — search returned only doc/plan commits.
   Cannot hash a historical value that was never in git. **Owner: operator.
   Date: next-time-in-Settings**.
2. **v2 detector: STATIC 109 / DYNAMIC 2, local == VPS, diff IN SYNC.**
   Corrected from v1's 125/7 (v1 counted 16 static + 5 dynamic log/exit
   strings as reads). Symmetric-methodology conclusion from Phase 2.6 still
   holds — Python source in sync.
3. **ML dormant flag list = 7**: `GENLAB_BEDROCK_FINETUNE_ENABLED`,
   `GENLAB_LINUCB_STOCHASTIC_ENABLED` (unsuffixed — per-niche variants ARE
   set on VPS, so this is "cross-niche default off"),
   `GENLAB_POLICY_BLOCK_RCA_ENABLED`,
   `GENLAB_SPLIT_SCREEN_COMPOSITOR_ENABLED`,
   `GENLAB_STORYTIME_COMPOSITOR_ENABLED`,
   `GENLAB_TEMPORAL_CONTEXT_ENABLED`,
   `GENLAB_THOMPSON_PROPENSITY_ENABLED`. Phase 6 INTELLIGENCE input.
4. **Phase 3 gate: CLEAR.** 3 pipeline-config YAMLs drifted, but only 1
   semantic change (CriticalRush `rollout_pct 0.0→1.0`, gated by
   `enabled: false`); other 2 are cosmetic `null → <empty>`. Cuelinks
   `+918 lines` regenerable via weekly `genlab-cuelinks-campaign-refresh.timer`.

## v2 partition (assertion PASSES, total=union=119)

| Bucket | Count |
|---|--:|
| SET_AND_READ | **55** |
| SET_UNREAD | **0** (was 2 in Phase 2.6 — see Section 3) |
| UNSET_AND_READ | **62** (14 `_ENABLED`, 7 ML dormant defaults-off) |
| UNSET_UNREAD | 0 |

## Phantoms resolved (Section 3)

Both Phase 2.6 "phantoms" are read via module-level constant + variable-key
`os.environ.get()`:

```
scripts/auto_remediate_content_gap.py:63    _ENABLE_ENV_VAR = "GENLAB_CONTENT_GAP_REMEDIATOR_ENABLED"
scripts/auto_remediate_content_gap.py:85    return os.environ.get(_ENABLE_ENV_VAR, "") in ("true", ...)
scripts/drain_engagement_review_queue.py:51 _ENABLE_ENV_VAR = "GENLAB_ENGAGEMENT_DRAINER_ENABLED"
scripts/drain_engagement_review_queue.py:69 return os.environ.get(_ENABLE_ENV_VAR, "") in ("true", ...)
```

**Real phantom count = 0.** Detector STATIC=109 measures Python literal reads
only. Reads via module constant / shell / systemd ExecCondition / variable
key remain invisible. Other invisible reads may exist. **F-0033 SUPERSEDED**
by F-0042; F-0041 rewritten with corrected bucket counts.

## VPS drift classification (Section 4)

22 files (4 tracked-modified, 18 untracked):
- **PIPELINE_CFG (6)**: 3 publishing.yaml + 3 publishing.yaml.lock — see gate note.
- **AFFILIATE_DATA (1)**: cuelinks_campaigns.yaml — 918 lines added on prod,
  regenerable via weekly timer. F-0043.
- **SCRIPT (1)**: `scripts/verify_policy_block_l1.sh` — hot-patched operator
  tool, not in git. Would be destroyed by re-clone. F-0044.
- **RUNTIME_ARTIFACT (6)**, **BINARY_ASSET (4)**, **OTHER (4)** = cookies,
  tokens, state files — expected.

## Blindness list

- **STATIC 109 counts Python literal reads only.** Blind to: module-constant
  keys (Section 3), shell/systemd `ExecCondition`, `os.environ.get(k)` with
  variable `k`, pydantic-settings alias fields.
- **DB password strength beyond length-10** — untested (would require online
  bruteforce).
- **Historical DB password rotation trail** — not knowable from git; passwords
  don't get committed by any commit search hit.
- **cuelinks_campaigns.yaml regenerable IF timer succeeds** — timer status
  active + next Mon 09:30 IST. Not proven that content is fully regenerated
  vs incrementally appended.

**No summary written until every shell exited** (`ps aux | grep pytest = 0`).
No secret values in `.audit/`. Ready for Phase 3.
