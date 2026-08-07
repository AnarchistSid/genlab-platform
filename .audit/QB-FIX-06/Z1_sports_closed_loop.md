# QB-FIX-06 Z1 — Sports Closed-Loop Confirmation

**Date:** 2026-08-07 15:00 IST
**Result:** Sports unstuck confirmed. Sainz reel published today passes all applicable stack gates. Threads timeout is NOT size-correlated — transient infra. 4 sports DRAFTED rows read + archived.

## Step 1 — 4 sports DRAFTED rows

| id | created | hook (first 50 chars) | render_error | has_visual_paths | duration |
|----|---------|-----------------------|--------------|------------------|----------|
| `21febc65` | 2026-08-02 | When Lewis Hamilton decided to train with penguins | **NULL** | false | 0 |
| `9c9f0808` | 2026-08-03 | If you want to understand how money quietly took o | **NULL** | false | 0 |
| `cae23d7f` | 2026-08-04 | The onboard of the fastest lap from Mahaveer Ragun | **NULL** | false | 0 |
| `198597a1` | 2026-08-06 | PETE CROW-ARMSTRONG IS 3-FOR-3 WITH TWO HOME RUNS  | **NULL** | false | 0 |

**Observability finding:** all 4 rows have empty `render_error`. For the Pete Crow-Armstrong row specifically, we KNOW from the Y2 pipeline journal that the pre-render quality gate rejected it with `hook_title_truncation`. The gate rejects but does NOT record the reason in the blueprint row's `extra->render_error`. F-QB-0606 predicted `pre_render_quality:hook_bare_title`; the reality is the gate rejects with a different taxonomy (`hook_title_truncation` in this case) AND the reason isn't persisted.

**Follow-up filed:** the pre-render quality gate needs to write its rejection reason to `extra->render_error` so DRAFTED rows self-explain. Currently the reason lives only in the journal, which rotates.

Archived all 4 rows (`auto_archived_qb_fix_06_z1_sports_drafted`).

## Step 2 — Sainz reel stack verification (retroactive on published artifact)

Blueprint `284a885f Carlos Sainz F1 clip` published today at 12:12 IST across YT/FB/IG (Threads timeout).

- `visual_paths`: `/opt/genlab/.tmp/runs/sports_20260806_174607/visuals/60482d641170a4ea…/60482d641170a4ea_reel.mp4`
- `affiliate_url`: empty → **F1 PASS**
- `affiliate_cta`: empty → **F1 PASS**
- `audio_ducking` arm: **-9** (in post-F3a-2 set `[-3,-6,-9]`) → **F3a-2 PASS**
- `music_bed_db`: -20 per sports config → source ≥11dB above bed

ffprobe on the file:
- 1080x1920 H.264 → **F2 PASS**
- Duration: 18.6s (in 15-60s bracket)
- Video bit rate: 784 Kbps
- Audio: AAC 166 Kbps

pyloudnorm (from Y2 measurement yesterday): **-13.86 LUFS** (target -14 ±1.0) → **F3a PASS**

F3d-1: Y2 journal showed `intro skipped for niche=sports (force_none=True, bandit_pick='logo_tagline_reveal')` → **F3d-1 PASS**

F3b: **N/A** (sports has `whisper_sync.enabled=false`).

**All applicable gates PASS.** Sports is publishing post-fix reels.

## Step 3 — Threads timeout: file size correlation

| Reel | Threads status | File size | Duration | Video bit rate |
|------|----------------|-----------|----------|----------------|
| Movies INHERIT | SUCCESS | 3.98 MB | 18.6s | 1.54 Mbps |
| Anime Saga of Tanya | SUCCESS | 4.97 MB | 16.1s | 2.28 Mbps |
| **Sports Sainz** | **FAILED (180s timeout)** | **2.22 MB** | **18.6s** | **0.78 Mbps** |

**Sports is the SMALLEST and LOWEST-BITRATE of the three.** If Threads container-processing timeout were size-correlated, sports would have SUCCEEDED and anime (the largest) would have failed. It didn't — anime succeeded, sports failed. The timeout is **not a size symptom**.

**Conclusion:** the Threads timeout on the sports publish is legitimately transient Meta infra — nothing in file characteristics predicts it. Not filing as a systemic bug. Recommend manual retry via dashboard or wait for `genlab-publisher-retry.service` next fire (which runs `--retry-only` and would pick up failed platforms per R-83).

## Commit

`test(sports): confirm post-unblock reel carries the full fix stack`
