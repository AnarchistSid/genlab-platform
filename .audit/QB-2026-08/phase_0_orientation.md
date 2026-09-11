# Phase 0 — Orientation & Measurement Capability Inventory

**Audit ID:** QB-2026-08
**Date:** 2026-08-06
**Scope:** Read-only. Repo mapping, per-channel config inventory, measurement tooling (Mac + VPS), artifact store, DB schema.

---

## Environment summary

| Item | Value |
|---|---|
| Local (Mac) | Darwin 25.3.0, ffmpeg 8.1 **with libvmaf**, ffprobe 8.1, tesseract 5.5.2, Python 3.14.3 (system, no measurement libs), uv workspace has librosa/opencv-headless/torch/transformers/pytrends/detoxify |
| VPS | Linux 6.8, Ubuntu 24.04, ffmpeg 6.1.1 (has `scdet` + `vmafmotion` but **no libvmaf** full VMAF filter), tesseract 5.3.4, Python 3.12.3, `.venv` is a symlink to system python — no scenedetect / pyloudnorm / whisper / pytesseract in it |
| Audit venv (Mac, isolated at `.audit/QB-2026-08/.venv/`) | scenedetect 0.7.1, pyloudnorm (installed), librosa (installed), opencv-headless 4.13.0, pytesseract, openai-whisper, torch 2.13.0, numpy, scipy, PIL. **All measurement libs available on Mac; VMAF possible via ffmpeg-full.** |
| Prod DB | Docker container `genlab-postgres` on VPS, bound `127.0.0.1:5432` → container 5432. Native PG 17 on VPS 5433 hosts `aspirehub_saas` (unrelated). Confirms A-0058 5432 vs 5433 ambiguity. |
| DB access from Mac | Public port firewalled (46.224.237.56:5432 times out). Access via `ssh genlab-prod` + `psql "$DATABASE_URL"` from `/opt/genlab`. |
| App role | `genlab_app` (rolbypassrls=**f**, rolsuper=f). Rule #33 `genlab` role does exist and IS rolbypassrls=t, rolsuper=t — used by migrations only. Data plane respects RLS. |

---

## Verified pipeline stage locations (all read-only verified, files exist)

| Stage | Path | Notes |
|---|---|---|
| Render / compose | `genlab-core/src/genlab_core/strategies/base_visual_render.py` + `media/frame_compositor.py` (`compose()`) | Reads `PLATFORM_SPECS` from `media/ffmpeg.py` |
| Platform encode specs | `genlab-core/src/genlab_core/media/ffmpeg.py` (PLATFORM_SPECS dict) | libx264 CRF 20-22, preset=fast for all 5 platforms (VPS 4 GB RAM constraint) |
| TTS cascade | `genlab-core/src/genlab_core/tts/cascade.py` (`TTSCascade.synthesize()`) | ElevenLabs → OpenAI → Edge-TTS → gTTS. No music bed mixing found. |
| Whisper captions | `genlab-core/src/genlab_core/pipeline/stages/render_whisper_captions.py` | Per CLAUDE.md was disabled 2026-06-13. But `_captioned.mp4` variants observed on VPS for ai_creators since 2026-08-01 — status has changed since CLAUDE.md written; verify in Phase 4. |
| Overlay compositor | `genlab-core/src/genlab_core/rendering/overlay_compositor.py` | — |
| Pre-render quality gate | `genlab-core/src/genlab_core/rendering/pre_render_quality.py` | Called by `base_visual_render._compose_frame` before compositor |
| Video quality gate | `genlab-core/src/genlab_core/pipeline/stages/video_gate.py` | **Not at path `rendering/video_gate.py` from CLAUDE.md** — moved to `pipeline/stages/` |
| Cover / thumbnail | (not found) | No deliberate cover-frame generation code exists. Verify in Phase 6 by checking published metadata. |
| Publish (parallel) | `genlab-core/src/genlab_core/publishing/parallel_publish.py` (`execute_parallel_publish()`) | ThreadPoolExecutor across platforms |
| Platform clients | `genlab-core/src/genlab_core/platforms/{facebook,instagram,youtube,threads,x_twitter,tiktok}.py` | Rule #23: X/TikTok are out-of-scope; still in tree |
| MetricCollector | `genlab-core/src/genlab_core/learning/metric_collector.py` | — |
| Per-platform fetchers | `genlab-core/src/genlab_core/learning/metrics/{youtube,instagram,facebook,threads,x_twitter,tiktok}.py` | — |
| LinUCB / Thompson | `genlab-core/src/genlab_core/learning/linucb.py` | — |
| RewardShaper | `genlab-core/src/genlab_core/learning/reward_shaper.py` | Formula details deferred to Phase 8 |
| Retention derivations | `genlab-core/src/genlab_core/learning/retention_derivations.py` | Verify Phase 8 whether `sends_per_reach = (total_interactions − likes − comments − saves) / reach` derivation runs and where results land |
| Auto-approval gate | `genlab-core/src/genlab_core/scheduling/auto_approval_gate.py` (`evaluate()`) | — |
| Auto-approver worker | `genlab-core/src/genlab_core/scheduling/auto_approver.py` (`run_pass()`) | Gate examinations show it may not be running for all niches — see F-QB-0004 |
| PROMOTED_COLUMNS | `genlab-core/src/genlab_core/storage/postgres.py` | Governs dedicated column vs. `extra` JSONB routing |
| Disk quota | `genlab-core/src/genlab_core/storage/disk_quota.py` | Protects published runs + N most-recent |

---

## Per-channel config inventory (verified, all YAMLs present)

All 5 channel dirs have `config/niche.yaml`, `config/sources.yaml`, `config/scoring_weights.yaml`, `config/visuals.yaml`, `config/publishing.yaml`, `config/schedule.yaml`, `config/writing.yaml`, `config/persona.yaml`, `config/media.yaml`, `config/platform_algorithms.yaml`, `config/audio.yaml`. Only BlackboxBrief additionally has `virality_scoring.yaml`, `hook_formulas.yaml`, `image_gen_prompts.yaml`, `mid_reel_hooks.yaml`, `content_mix.yaml`, `ugc.yaml`, `topic_weights.yaml`, `ab_tests.yaml`. CriticalRush has `game_registry.yaml`, `compilation_rules.yaml`, `platform_bandits.yaml`, `error_budgets.yaml`, `platform_specs.yaml`, `overlay_styles.yaml`, `captions.yaml`, `risk_rules.yaml`, `crispy_models.yaml`, `rate_limits.yaml`. Non-AI niches are missing per-niche `virality_scoring.yaml` — matches memory note (2026-07-21 backfill added inline sections; verify current state in Phase 5/6). Full YAML inventory in `phase_0_config_inventory.txt`.

---

## Artifact store (VPS)

| Location | Retention | Layout |
|---|---|---|
| `/opt/genlab/.tmp/runs/{niche_id}_{YYYYMMDD}_{HHMMSS}/` | Live runs — 31 dirs total, disk-quota protected once "published" marker present | `visuals/{blueprint-hash}/*.mp4`, `clips/*.mp4` |
| `/opt/genlab/.backups/visuals/{YYYY-MM-DD}/opt/genlab/.tmp/runs/…` | ~14 days rolling (2026-07-23 → 2026-08-06 present) | Mirrors run tree under dated dirs |
| `/opt/genlab/.media/cdn/` | Only 8 files — appears to be a select CDN staging area, not the primary corpus | — |
| Per-niche MP4 counts across both stores (last 14d) | ai_creators 519, sports 191, movies 62, anime 144, **gaming 0** | Gaming render path is broken — see F-QB-0002 |

Filename conventions observed:
* `{hash16}_reel.mp4` — master render, no platform variant
* `{hash16}_reel_captioned.mp4` — captioned variant (whisper-style word-level, ai_creators only, first observed 2026-08-01)
* `{hash16}_reel_captioned_{platform}.mp4` — platform-transcoded captioned variant
* `{hash16}_reel_{platform}.mp4` — platform-transcoded uncaptioned (other niches)

---

## DB schema — analytics tables (verified live query)

**`publishing_analytics`** — dedicated columns: `id, niche_id, post_id, platform, published_at, status, views, likes, comments, shares, saves, metrics_fetched, created_at, updated_at, blueprint_id, error_message`. `extra` JSONB observed keys (last 7d, ai_creators): `candidate_id, host_id, post_url, Title`, and for facebook only `fb_survival_checked`. **No per-metric time-series lives here** — only latest snapshot of 5 canonical counters.

**`analytics`** — dedicated columns: `id, niche_id, post_id, platform, metric_type, value, collected_at, window`. Rows in last 14d: **every single row has `metric_type='composite'`** (72 rows total across 4 platforms × 3 windows {24h, 48h, 168h}). No per-metric rows (no `saves`, `dm_send_rate`, `avg_view_duration`, `subscriber_gained`, `reply_chain_rate`, `discovery_share` etc.). Individual metrics are fetched by MetricCollector, folded into a composite score, then discarded. This is a **major measurement gap** for the reward loop and for this audit — see F-QB-0007.

Other tables present (verified via `\dt`): `bandit_arms`, `bandit_validation`, `post_decision_trace`, `auto_approval_calibration`, `gate_examinations`, `compliance_events`, `ensemble_votes`, `pending_feedback`, `strategist_reports`, `late_reward_deltas`, `learning_findings`, `content_pool`, `product_embeddings`, `preference_data`, `affiliate_clicks`, `affiliate_revenue`, `audience_snapshots`, `outbound_reply_history`, `content_memory`, plus 25 others (full list in `phase_0_tables.txt`).

---

## Capability matrix — can each dimension be measured?

Legend: ✅ measurable now, 🟡 measurable via Mac audit venv, 🔴 not measurable (with reason).

| # | Dimension | Measurement approach | Local Mac | VPS | Comment |
|---|---|---|---|---|---|
| 1 | Thumbnail / cover | ffprobe first-frame + OCR + CLIP aesthetic score | 🟡 (CLIP separately) | 🔴 (no python libs) | Pipeline emits no deliberate cover — first frame only. Confirm in Phase 6. |
| 2 | Editing quality | PySceneDetect + optical flow via cv2 | 🟡 | 🔴 | Cross-check with ffmpeg-native `scdet` filter as backup |
| 3 | Audio / music / ducking | pyloudnorm + ffmpeg ebur128 + librosa onsets + WhisperX for VAD | 🟡 (except WhisperX requires GPU or slow CPU) | 🔴 | Also license audit is a code+config read |
| 4 | Captions / on-screen text | pytesseract on sampled frames + WhisperX for ground-truth timestamps | 🟡 | 🔴 (tesseract binary present but no python wrapper) | Whisper karaoke path is re-enabled for ai_creators — verify accuracy |
| 5 | Hook / branding shot / duration | ffprobe duration + scenedetect + OCR on first 3s | 🟡 | 🔴 | Own-completion-curve reality gap = per-metric data is `composite` only |
| 6 | Video quality (encode) | ffprobe + libvmaf | ✅ (Mac has full VMAF) | 🟡 (only vmafmotion) | Best done on Mac |
| 7 | Affiliate disclosure (caption) | DB query for published captions + regex | ✅ | ✅ | Both sides |
| 8 | Affiliate disclosure (on-screen) | pytesseract OCR of first 3s + regex | 🟡 | 🔴 | Same pipeline as caption OCR |
| 9 | Topic freshness / novelty | DB query for source pub-time vs GenLab publish, embeddings vs prior N | ✅ (embed via API) | ✅ | Need to check whether prod stores source_pub_ts |
| 10 | Outcome metrics (retention, sends/reach, etc.) | Direct DB read | 🔴 partial | 🔴 partial | **Only `composite` stored in analytics; publishing_analytics only has 5 counters. Per-metric data not persisted after rollup.** |

---

## Findings (12/12)

### F-QB-0001 — HIGH — VPS ffmpeg lacks libvmaf; full VMAF must be computed on Mac against pulled artifacts

* **Measured value:** `ffmpeg -filters | grep libvmaf` returns empty on VPS (only `vmafmotion` present); Mac returns `libvmaf VV->V`.
* **Command:** `ssh genlab-prod 'ffmpeg -filters | grep -i vmaf'` vs. local `ffmpeg -filters | grep -i vmaf`.
* **Impact:** Any VMAF measurement (Phase 1) must run on Mac against MP4s pulled from VPS. VPS-side quality gating cannot use full VMAF.
* **Confidence:** HIGH.
* **Verification gate:** rerun the same `ffmpeg -filters` after any VPS ffmpeg upgrade.

### F-QB-0002 — BLOCKER — Gaming (CriticalRush) has zero MP4 artifacts across both `.tmp/runs` and `.backups/visuals` for the entire 14-day retention window, despite 6 recent run dirs and 11 blueprints marked `VISUAL_READY`

* **Measured value:**
  ```
  find /opt/genlab/.backups /opt/genlab/.tmp -path '*/gaming_*' -name '*.mp4' | wc -l
  → 0
  ```
  vs. counts of 519 / 191 / 62 / 144 for the other four niches.
* **Impact:** (a) Gaming cannot be audited in Phases 1–6 for any dimension that requires the rendered artifact. (b) The `VISUAL_READY` status is a lie — the DB says "media is ready" but there is no media on disk. (c) The gate has approved 71 blueprints on 2026-07-24 (in a single day) then examined nothing for gaming since — indicates auto_approver or examiner stalled specifically for gaming from that date.
* **Confidence:** HIGH.
* **Verification gate:** rerun `find … gaming_* -name '*.mp4' | wc -l` and expect > 0; separately, count gaming rows in `gate_examinations` for the trailing 7 days — expect > 0.

### F-QB-0003 — BLOCKER — Publishing has collapsed to ai_creators only; four channels have not published in 9–14 days despite live render activity on 3 of them

* **Measured value (publishing_analytics, last 14 days):**
  * ai_creators: 48 rows across 4 platforms, most recent 2026-08-06 ✅
  * sports: 20 rows across 4 platforms, most recent **2026-07-28** (9 days silent)
  * anime: 8 rows across 4 platforms, most recent **2026-07-26** (11 days silent)
  * gaming: 8 rows, most recent **2026-07-25** (12 days silent, no MP4s on disk)
  * movies: **0 rows in 14 days** despite 17 fresh MP4s rendered today (2026-08-06)
* **Blueprint status distribution (14d):** anime = 10 ARCHIVED + 6 DRAFTED (no VISUAL_READY at all); movies = 9 ARCHIVED + 6 VISUAL_READY + 5 DRAFTED; sports = 7 ARCHIVED + 4 DRAFTED (no VISUAL_READY at all).
* **Impact:** ai_creators is the only channel where an audit against ~real recent output produces relevant findings. Other channels' findings will be against a corpus that pre-dates the pipeline's current behavior.
* **Confidence:** HIGH.
* **Verification gate:** `SELECT niche_id, MAX(published_at) FROM publishing_analytics GROUP BY 1;` — expect all 5 niches within trailing 24-48 h.

### F-QB-0004 — HIGH — Auto-approver gate examinations only running for ai_creators; sports/anime/movies have zero examinations in 14 days, gaming has 78 (all on one day)

* **Measured value:**
  ```
  SELECT niche_id, approved, COUNT(*) FROM gate_examinations WHERE examined_at >= NOW() - INTERVAL '14 days' GROUP BY 1,2;
  → ai_creators/true=374, ai_creators/false=447, gaming/true=71 (all 2026-07-24), gaming/false=7, sports/false=4
  → anime, movies: 0 rows
  ```
* **Impact:** The AUTO #1 calibration surface that is supposed to build a per-niche operator-agreement dataset is silently dark on 3 of 5 niches. CLAUDE.md's rollout ladder (Week 1 → Week 4) cannot progress. The rule-#19 sibling of "silent-fail observability write" is likely at play.
* **Confidence:** HIGH.
* **Verification gate:** trailing-7d count of `gate_examinations` per niche — expect non-zero for all 5.

### F-QB-0005 — HIGH — The `analytics` table stores only `metric_type='composite'`; no per-metric time series is persisted

* **Measured value:**
  ```
  SELECT DISTINCT metric_type FROM analytics WHERE collected_at >= NOW() - INTERVAL '14 days';
  → composite (only value)
  ```
* **Impact:** The audit cannot compute realized sends-per-reach, avg watch time, saves rate, retention curves, subscriber_gained from DB alone — the raw counters land in `publishing_analytics` (only 5 canonical fields — views/likes/comments/shares/saves), then MetricCollector rolls them into a single composite score for the reward loop and discards the rest. This is also the reward-loop's own bandit-signal quality ceiling; deep Phase-8 finding.
* **Confidence:** HIGH.
* **Verification gate:** `SELECT metric_type, COUNT(*) FROM analytics GROUP BY 1;` — expect at least {views, saves, shares, avg_watch_time, follower_gained, sends_per_reach} to appear as distinct metric_type values before Phase 8 can validate the reward loop against realized outcomes.

### F-QB-0006 — MEDIUM — `_reel_captioned.mp4` variant is appearing for ai_creators from 2026-08-01 onward, contradicting CLAUDE.md's claim that `whisper_sync.enabled = false` across all niches since 2026-06-13

* **Measured value:** 12+ files matching `ai_creators_*_reel_captioned*.mp4` observed on VPS with mtimes between 2026-08-01 and 2026-08-06 (see `artifact_manifest_raw.txt`). No `_captioned` files observed for other niches.
* **Impact:** CLAUDE.md rule (line ~"whisper_sync.enabled = false across all 5 niches as of 2026-06-13") is stale; audit's Phase 4 must first verify the current state of whisper captions for ai_creators, then measure timing accuracy against WhisperX ground truth.
* **Confidence:** HIGH (file evidence unambiguous).
* **Verification gate:** grep `whisper_sync` in each niche's `visuals.yaml` or `captions.yaml` and record the setting.

### F-QB-0007 — HIGH — DB access from Mac requires SSH to VPS; the .env DATABASE_URL points to firewalled `46.224.237.56:5432` which times out

* **Measured value:** `psql "postgresql://…@46.224.237.56:5432/genlab" -c 'SELECT 1'` times out after `PGCONNECT_TIMEOUT=8s`. VPS-local Postgres is on Docker container `genlab-postgres` at `127.0.0.1:5432`.
* **Impact:** All DB reads must go through `ssh genlab-prod 'psql "$DATABASE_URL" …'`. Rules out any Mac-local psycopg-driven analysis without a tunnel. Also confirms A-0058 5432 vs 5433 ambiguity: 5432 (Docker) is genlab; 5433 (native PG 17) hosts unrelated aspirehub DBs.
* **Confidence:** HIGH.
* **Verification gate:** — (this is a persistent infra fact, not a fix candidate).

### F-QB-0008 — MEDIUM — Rule #33 (`genlab` role has BYPASSRLS defeating tenant isolation) confirmed present but mitigated by app connecting as `genlab_app` (rolbypassrls=f)

* **Measured value:** `SELECT rolname, rolbypassrls, rolsuper FROM pg_roles WHERE rolname LIKE 'genlab%'` returns `genlab | t | t`, `genlab_app | f | f`. Current session user via app is `genlab_app`.
* **Impact:** RLS `niche_isolation` policy DOES gate rows for the data plane. The audit's cross-niche joins in later phases can trust `SET LOCAL app.niche_id`. However, any migration script or manual `psql` connection using the `genlab` role bypasses RLS. Also worth verifying that PROMOTED_COLUMNS + `AND niche_id = %s` belt-and-suspenders (per rule #27) is present in `PostgresBackend.find/update/delete` — that check is deferred to Phase 8.
* **Confidence:** HIGH.
* **Verification gate:** confirm `session_user` is `genlab_app` in the app process by scanning the connection string source.

### F-QB-0009 — MEDIUM — No cover-frame / thumbnail generation code exists; platforms use their default (first frame). Applies to all 5 niches.

* **Measured value:** grep for `thumbnail|cover_frame|first_frame` in `genlab-core/src/genlab_core/media/`, `rendering/`, `publishing/` returns no deliberate generation code. First-agent report confirms; needs one more verification against published post metadata in Phase 6.
* **Impact:** Per Section 1.1 row 1, thumbnails have MEDIUM importance for Reels grid and Facebook and LOW for Shorts. Missing thumbnail generation is a Tier-3 dimension gap for Shorts distribution, but Tier-2 for FB/IG grid discoverability. Phase 6 will confirm the pipeline delivers no cover metadata to FB/IG upload calls.
* **Confidence:** HIGH.
* **Verification gate:** grep the IG/FB payload builders in `publishing/payload_builder.py` for `cover|thumbnail`; expect no such field.

### F-QB-0010 — MEDIUM — Non-AI niches (gaming, sports, movies, anime) are missing per-niche `virality_scoring.yaml`; only BlackboxBrief has one

* **Measured value:** `ls */config/virality_scoring.yaml` shows only `BlackboxBrief/config/virality_scoring.yaml` present. Memory note `[[session-2026-07-21-auto-approver-throughput-shipping]]` says a per-niche pattern section was added inline to each niche's `scoring_weights.yaml` on 2026-07-21 — must verify per niche in Phase 5.
* **Impact:** If the inline `virality_scoring` sections are also missing for these niches today, all their content will score 0 on virality and the auto-approver's virality_score gate (>=0.05) will silently reject everything. This lines up with F-QB-0003's four-channel publish collapse.
* **Confidence:** MEDIUM (pending verification of inline sections in `scoring_weights.yaml`).
* **Verification gate:** grep `virality_scoring:` in each `*/config/scoring_weights.yaml`; expect a populated section for all 5 niches.

### F-QB-0011 — LOW — VPS Postgres has 45 tables in `public` schema. This audit only needs ~15; enumerated for orientation.

* **Measured value:** `\dt` returned 45 rows (see `phase_0_tables.txt`).
* **Impact:** No action; ambient context so Phase 8 doesn't miss e.g. `late_reward_deltas`, `learning_findings`, `ensemble_votes`, `strategist_reports` (all present, all needed for later phases).
* **Confidence:** HIGH.
* **Verification gate:** — (informational).

### F-QB-0012 — LOW — Audit measurement venv is isolated at `.audit/QB-2026-08/.venv/` — no modification to repo `pyproject.toml` or workspace lockfile

* **Installed:** scenedetect 0.7.1, pyloudnorm, librosa 0.11 (via workspace share), opencv-headless 4.13.0.92, pytesseract, openai-whisper, torch 2.13.0, numpy, scipy, PIL. Tesseract binary is `/opt/homebrew/bin/tesseract 5.5.2`.
* **Impact:** Later phases can measure directly on Mac without touching the repo's Python environment.
* **Confidence:** HIGH.
* **Verification gate:** — (audit tooling; will be deleted at end of audit).

---

## Deferral ledger (Phase 0)

| Item | Reason deferred | Owner |
|---|---|---|
| Detailed reward formula from `reward_shaper.py` | Deep read belongs to Phase 8 | Phase 8 |
| Confirm auto_approver stall root cause (per-niche journalctl) | Blocker-adjacent; not read-only-safe if requires touching prod | Phase 8 |
| Movies-render-yet-no-publish diagnostic | Belongs to Phase 8 (publish path) + Phase 7 (compliance) | Phase 7/8 |
| Whether `sends_per_reach` derivation actually runs in prod | Belongs to Phase 8 (reward loop) | Phase 8 |
| Detailed per-niche `scoring_weights.yaml` sections | Belongs to Phase 5 (virality gate + hook) | Phase 5 |

---

## Methodology errors logged

* **Assumed VPS `.venv` was uv-managed and had measurement libs.** Reality: `.venv/bin/python` is a symlink to system `/usr/bin/python3`. Reversed after direct `ls -la /opt/genlab/.venv/bin/python*`. Logged in `methodology_errors.md`.
* **Second Explore agent used incorrect username in file paths (`/Users/anthropistsid/…` — a typo).** Verified all reported paths against `[ -f ]` before accepting. One path (`rendering/video_gate.py`) was off — file is at `pipeline/stages/video_gate.py`. Logged.
* **First Explore agent's IG-metric report contradicted the second's** (agent 1: sends "NO — explicitly omitted"; agent 2: sends_per_reach derived from total_interactions). Not resolved in Phase 0 — deferred to Phase 8 with pinned verification against `metrics/instagram.py` source.

Full log at `methodology_errors.md`.

---

## What was not measured in Phase 0

* Actual render code paths for whisper captions (see F-QB-0006 — deferred to Phase 4).
* Actual publish-path failure modes for the 4 collapsed channels (Phase 7/8).
* Config value of `virality_scoring` per niche (Phase 5).
* VMAF baseline of any rendered MP4 (Phase 1 — pending artifact pull to Mac).

## Sample size available for Phases 1–6

* ai_creators: 10+ fresh MP4s available (multiple platform variants + `_captioned` variants since 2026-08-01)
* movies: 6+ fresh MP4s (2026-08-06 renders + older ones back to 2026-07-22)
* anime: 6+ MP4s (mix of fresh and older)
* sports: only 07-28-or-older MP4s (5 blueprints × 4 platform variants), no rendering since
* **gaming: 0** — Phase 1–6 findings for gaming will be "cannot verify"

Full pull manifest: `artifact_manifest_raw.txt`. Selected 10-per-niche list at `artifacts/<niche>/pull_list.txt`. Actual MP4s downloaded to `artifacts/<niche>/`.
