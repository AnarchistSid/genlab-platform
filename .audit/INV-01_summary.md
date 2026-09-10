# INV-01 — Capability inventory summary
**Read-only. Window: 14 days ending 2026-09-10 unless stated. Evidence class on every line: M=measured, I=inferred, D=documented.**

## Phase 0 — manifest

| item | value | class |
|---|---|---|
| repo HEAD == origin == prod | `ca37a028` — **all three match** | M |
| uv workspace members | 7: genlab-core, dashboard, BlackboxBrief, CriticalRush, ClutchWire, SpliceReel, FrameDrift | M |
| genlab systemd units | 329 total (110 active, 215 inactive, 2 not-found); 106 timers | M |
| failed units | 5 + 1 orphan: `genlab-post-deploy-verify`, `genlab-strategist`, `run-u555874` (orphan transient), + 3 aspirehub | M |
| prod-only units (no repo file) | 6: archive-stale-drafted, attribution-health-monitor, retro-credit (.service+.timer each) | M |
| GENLAB_* flags in prod .env | 105 set, 86 enabled, 0 explicitly disabled; `flag_audit` tracks 59 (`active=55/59`) | M |
| credentials present | ElevenLabs, OpenAI, Anthropic, YouTube = yes; INFERENCE_SH/BELT = **absent** | M |

### Journal retention — the binding constraint on this audit

| unit | oldest available entry | class |
|---|---|---|
| genlab-pipeline-ai | **-- no entries --** | M |
| genlab-publisher | 2026-09-10T06:35:01 | M |
| genlab-auto-approver | 2026-09-10T06:00:01 | M |

Configured `MaxRetentionSec=30d`, `SystemMaxUse=2G` (with a **conflicting 500M**
in a second config file); actual disk in use **47.8M**. Effective retention is
**hours**. This is not disk pressure.

**Consequence, declared rather than papered over:** "last successful/failed
execution per stage per niche" (Phase 1) is **UNMEASURABLE from journal** for
every stage. I read `genlab-pipeline-ai` successfully at 02:30–02:47 UTC today;
by 10:00 UTC it had rotated. Phase 1 execution history is therefore reported
from DB artifacts only, and stages with no DB footprint are `UNMEASURABLE`.

## Status buckets

### FUNCTIONING-VERIFIED (measured artifact in window)
| capability | evidence | class |
|---|---|---|
| Publish to 4 platforms × 5 niches | 240 live posts enumerated via Graph/YouTube/Threads APIs since 2026-08-22; per-platform counts identical within each niche | M |
| Bandit learning loop | 265 of 502 arms updated in window | M |
| TTS synthesis (producer) | `167a830c…_audio.mp3` 136,690 B; Whisper transcript matches script verbatim | M |
| 3-input narration mix | `narration_bisect` L3.8: re-executed production argv → 94% script overlap, 15/16 words | M |
| Final-asset loudness gate | v3 measured −14.06 LUFS / −1.40 dBTP, gate PASS | M |
| Compliance event capture | 173 events across 5 niches in window | M |

### DEGRADED
| capability | evidence | class |
|---|---|---|
| Publish cadence | 60 reels vs 90 expected (5 niches × 18d) = 33% miss; anime 3 of 18 days | M |
| anime end-to-end | 1 reel in 14d; 0 rewards; writer emits source title as hook on 60% of DRAFTED | M |

### BROKEN
| capability | evidence | class |
|---|---|---|
| `genlab-post-deploy-verify` | failed state; every roadmap phase ends in "deploy and verify" | M |
| `genlab-strategist` | failed state; `strategist_llm_call_failed` ×15 (2026-08-23→09-06) | M |
| Engagement comment ingestion | `pending_engagement` = **0 rows** in window | M |

### BUILT-NEVER-WIRED
| capability | evidence | class |
|---|---|---|
| `narration_audio_path` consumer (pre-`ca37a028`) | BB + gaming built 4-key `blueprint_context`; orchestrator read a key that could not exist. Fixed, not yet exercised by a pipeline fire | M |
| 5 of 6 `variant_types` | `single_clip` only; `split_screen`/`series_part`/`watch_till_end`/`question_reveal`/`storytime` defined, zero production callers | M |

### HUMAN-BLOCKED
See `INV-01_deferral_ledger.md` H-01…H-08.

### DRIFTED
| item | evidence | class |
|---|---|---|
| 6 prod-only units absent from repo | enumerated above | M |
| Calibration tuner writes uncommitted prod YAML | 5 modified `publishing.yaml` + **65 untracked `.bak`/`.lock` files** on the box | M |
| 46 `GENLAB_*` flags in .env untracked by `flag_audit` | 105 set vs 59 known | M |

### MISSING
| capability | class |
|---|---|
| Multi-clip / compilation renderer | M (no concat over N sources; intro/outro only) |
| Trending-audio attachment | M (no mechanism) |
| VO tier persistence | M (`audio_provider` written in memory, never stored) |

## Triage — the 12 findings that most change the two decisions

| # | finding | class | artifact |
|---|---|---|---|
| 1 | **VO tier unrecoverable for every reel** — `audio_provider` never persisted; blocks the playbook's DEGRADED test outright | M | 158 blueprints, 0 with the key |
| 2 | **Journal retention is hours, not 30d**, with 47.8M of a 2G cap used — makes stage-level history unmeasurable and every future incident un-forensicable | M | `journalctl` oldest entry per unit |
| 3 | **anime is the outlier on every consolidation axis** — 1 reel/14d, 0 rewards, 0 audience Δ; its low warn count is a consequence of not publishing | M | consolidation_inputs |
| 4 | **sports is the best-performing channel measured** — reward mean 0.1871 (3.5× gaming) and the only subscriber movement (+7 YT) | M | consolidation_inputs |
| 5 | **Total follower movement +11 across 10 cells in 14d** — audience acquisition remains the binding constraint on every content decision below it | M | audience_snapshots |
| 6 | **Engagement ingestion produces nothing** — 0 rows in 14d; cannot distinguish polled-and-empty from not-polled without journal (rotated) | M / UNMEASURABLE cause | pending_engagement |
| 7 | **post-deploy-verify is in failed state** while deploys are bare pulls — the verify step for the whole roadmap is itself down | M | `systemctl --failed` |
| 8 | **65 untracked tuner files + 5 modified YAML on prod** — #227; both of today's deploys needed manual intervention | M | `git status` on box |
| 9 | **Two FB pages carry 10,022 / 8,653 fans** against 19–56 elsewhere — legacy provenance unverified (H-07); excluding them, measured reach is near zero | M | audience_snapshots |
| 10 | **5 of 6 variant types unshipped** — the compilation renderer that the playbook's top-10s/best-of formats all depend on does not exist | M | `variant_types.py` |
| 11 | **46 flags set in prod are invisible to `flag_audit`** — canary verification covers 59 of 105 | M | .env vs flag_audit |
| 12 | **#218 verdict given (PASS)**; `1b3e0a5c` assigned to 2026-09-11T07:00Z — first narrated publish pending, not yet closed | M | blueprints row |
