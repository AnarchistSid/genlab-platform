# INV-01b — Render-artifact audit, 2026-09-11 fire
**Read-only. Window: the 02:30Z pipeline fire → 06:35Z publisher. M=measured, I=inferred, D=documented.**

Retention directory: `/opt/genlab/.audit-retention/2026-09-11/`
Manifest: `manifest.txt` — **86 entries, 281 MB**, sha256 per file. Everything below reads from the copy.

## Step 0 — publisher
`success | 06:35:01 → 06:39:53 UTC` (4m52s; yesterday 15m20s). (M)

## Step 3 — 1b3e0a5c
`audio_provider = (absent)`, as predicted — synthesised before `12e7d7f7`. Still
`VISUAL_READY` at 07:30 against a 07:00 slot. **It did not publish.** (M)

## The 12 findings, ranked by effect on the build order

| # | finding | class | artifact |
|---|---|---|---|
| 1 | **#218's reel was blocked by the approval gate, not the schedule gate.** `[publish] Blueprint 1b3e0a5c blocked by approval_gate: Not approved`. I asserted on 2026-09-09 that the publisher does not require `reviewed_at`; that was wrong, and setting `scheduled_for` directly bypassed the approval path that stamps it. #218 cannot close until the reel is approved through the dashboard path. | M | publisher journal 06:35:03 |
| 2 | **gaming rendered ZERO MP4s today** despite 6 blueprints created and 6 TTS syntheses logged. Every other niche produced renders. | M | retention `gaming/` — 0 mp4, 4 json |
| 3 | **Three of four rendered niches have ZERO scene cuts.** anime 0, movies 0, sports 0 over 16–19s; only ai_creators cuts (9 cuts, 0.294/s). This is the "one borrowed clip, trimmed" ceiling measured directly, and it is the gap MULTICLIP-01 exists to close. | M | showinfo scene>0.3 on retention copies |
| 4 | **FIX-T01 verified at scale: 13/13 blueprints across all 5 niches carry `audio_provider`**, all `infsh_inworld`, zero fallbacks, 17 tier lines in journal. | M | `tts tier attempted=… used=…` ×17 |
| 5 | **movies final asset measures −0.19 dBTP**, failing the ≤ −1.0 gate. The probed file is `_reel_with_intro.mp4` — intro appended after normalisation, the same class the ValidateVideos gate was built for. | M | loudnorm print_format=json |
| 6 | **Per-platform reward is wildly asymmetric and Facebook-dominated**: movies/FB 0.2922, sports/FB 0.2225, gaming/FB 0.1920, ai_creators/FB 0.1854 — against YouTube ≈0 everywhere **except sports/YT 0.3828 (n=8), the single highest cell measured.** | M | `pending_feedback`, 14d, n≥3 only |
| 7 | **anime has n<3 on every platform** — no reward observations at all, consistent with 1 reel in 14 days. | M | per-platform table |
| 8 | **Only ai_creators produces `_reel_captioned.mp4`.** The other four niches have no captioned artifact, matching whisper_sync being canary-only. Caption presence for those four is therefore structurally absent, not merely unmeasured. | M | retention file inventory |
| 9 | **No caption sidecar files (SRT/VTT/ASS) exist in any niche** — 0 across all five retention subdirectories. Sidecar-to-YouTube is therefore not happening. | M | retention inventory |
| 10 | **Compliance warns are one class, not three.** Only `ai_disclosure_added` (gaming 152, ai_creators 46, movies 36, sports 32, anime 4, "all" 30) plus gaming `pre_publish_check` 25. The prompt's "top three classes" cannot be produced — there are at most two. | M | `compliance_events`, 14d |
| 11 | **A niche_id of `all` exists in compliance_events** (30 rows), which no niche uses elsewhere. Tenant-attribution gap in the compliance writer. | M | `compliance_events` group-by |
| 12 | **Audio spec is clean on every rendered niche**: 2 streams, h264/aac, LUFS −14.02 to −14.65 (target −14 ±1). Only the movies true-peak fails. | M | ffprobe + loudnorm |

## Section 5 — FrameDrift/anime

Anime **did fire today** (run `anime_20260911_060014`, 2 blueprints, 4 MP4s, TTS logged,
`degraded=false`). So "scheduler not firing" and "pipeline failing at a stage" are
**ruled OUT** for today. (M)

Given finding #1 — the approval gate blocks unapproved blueprints regardless of
schedule — **approver-rejection is the leading candidate** for the 14-day
stoppage, consistent with CADENCE-01's measurement that scheduling is 100%
downstream of approval and anime carried 10 DRAFTED with title-as-hook on 60%.
Publish-failure is **not ruled out**; distinguishing requires the publisher
journal for prior days, which has rotated. **UNMEASURABLE for the 14-day window.**

---

## Scope 1b addendum (OPS-03b) — caption_animator sweep

| niche | segments / style | ffmpeg exit | `No such filter:` | min-duration guard | fired |
|---|---|---|---|---|---|
| ai_creators | 4 / karaoke | **8** (×2) | **`'23.700'`, `'21.000'`** | absent | yes |
| anime | — | absent | absent | absent | yes (transform not reached) |
| movies | 4 / karaoke | absent | absent | absent | yes — **succeeded** |
| sports | 4 / word_by_word | absent | absent | absent | yes — **succeeded** |
| gaming | 4 / minimal | **8** | **`'0.000'`** | **8.52s < 15.0s** | yes |

**Statement: the defect is neither gaming-only nor style-specific.** It hit
ai_creators (twice) and gaming (once); movies ran the *same* style (karaoke) and
the *same* segment count (4) and succeeded. All three failing literals —
`0.000`, `23.700`, `21.000` — are **time values appearing where a filter name
belongs**, so the discriminator is segment timing, not style or count. (M)

**Caption consequence differs per niche, and the distinction matters:**
- **gaming — DEGRADED (M).** `caption_animator` failed and gaming has no
  whisper stage, so today's reels shipped with **no captions at all**.
- **ai_creators — not degraded on captions.** `caption_animator` failed, but
  `RenderWhisperCaptions` is a *separate stage* and succeeded on 3 stories.
  This supersedes the HUMAN-PENDING caption row, and it also explains the 0/5
  pixel-heuristic result differently than a caption absence would.

## Retraction — INV-01b finding #2

"gaming rendered ZERO MP4s" is **withdrawn**. Gaming rendered four MP4s to
`CriticalRush/.tmp/rendered/gaming_20260911_040019/` (2.4–7.5 MB). My retention
scan read only `/opt/genlab/.tmp/runs/`, which the other four niches use. Files
are now in retention; manifest 102 entries. The real findings underneath are
T-14a (filtergraph defect) and T-14c (divergent output path).
