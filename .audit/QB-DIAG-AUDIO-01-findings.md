# QB-DIAG-AUDIO-01 — Findings (READ-ONLY; nothing repaired)

Subject: `/opt/genlab/.tmp/narr08_preverify2/PREVERIFY2_narrated.mp4`
Date: 2026-08-20. All probes read-only. No file, config, or render changed.

## VERDICT — Hypothesis B, with a correction to the brief's premise

**B (intentional variant), confirmed by code + config + commits + artifacts.**
The narrated mix is a deliberate, flag-gated, operator-directed feature. It is
not a leak of a caption-timing artifact.

The brief's stated posture — *"published reels contain no TTS narration; the
TTS cascade exists solely to generate WhisperX caption timestamps"* — was
accurate until 2026-08-18 and is now stale. NARR-01 changed it deliberately.

Evidence (measured):

| artifact | value |
|---|---|
| prod `/opt/genlab/.env` | `GENLAB_NARRATION_ENABLED=1` |
| `BlackboxBrief/config/niche.yaml` | `narration:` / `enabled: true` |
| `b838b5d6` | NARR-01 primitives — env+YAML gate + script validator |
| `e69128d5` | NARR-01 wire — narration_script through writer + **3-input mix** |
| `a91baef8` | NARR-01 flag_audit pin + **BB canary YAML flip** |
| producing harness | `/tmp/preverify2.py`, supplying `narration_audio_path` to `apply_post_render_transformations` |
| plan record | `.audit/NARR-01-plan.md` §3.2, §12–§17 |

The brief's substantive complaint is nonetheless **correct and reproduced**:
the narrated variant's mix graph has no source attenuation relative to VO and
no dynamic bed ducking.

---

## F1 — Root cause of double-speech: source audio is never ducked under VO

`media/audio_replacer.py:231-237`, the shipped 3-input graph:

```
[0:a]volume={source_duck_db}dB[src];
[1:a]volume={total_music_duck_db}dB[music];
[2:a]volume={narration_vo_db}dB[vo];
[src][music][vo]amix=inputs=3:duration=first:dropout_transition=0[premix];
[premix]{loudnorm}[aout]
```

For this render (values from the prod log line, measured):
`source_duck_db=-9`, `music_bed_db=-20`, `vo_bed_duck_db=-8`
→ `total_music_duck_db=-28`, `narration_vo_db=0`.

Source dialogue sits **9 dB under VO, statically, wall-to-wall**. Roughly 35%
of VO amplitude — plainly intelligible. Nothing attenuates it when VO speaks.
This is the measured "two simultaneous speech layers".

## F2 — `sidechaincompress` was specified, documented, and never shipped

The plan (`NARR-01-plan.md` §3.2) specifies:

```
[music_base][vo]sidechaincompress=threshold=0.05:ratio=8:attack=5:release=200:makeup=0dB[...]
```

`audio_replacer.py` docstring (`:176-184`) still describes step 3 as
*"Sidechain-compress music bed by vo_bed_duck_db when VO is playing"*, and an
inline comment (`:207-217`) discusses `ratio=8 → aggressive duck`.

**No `sidechaincompress` node exists in the returned graph.** It was replaced
by a static pre-duck: `total_music_duck_db = music_bed_db + vo_bed_duck_db`.
The comment even describes a hybrid — *"pre-duck … AND rely on sidechain to
keep the duck active"* — of which only the first half exists.

Consequence: **no dynamic ducking anywhere in the pipeline**, which is exactly
the externally measured LRA 2.6 / zero-gaps symptom. Spec-to-implementation
divergence, with the docs left describing the spec.

## F3 — Trailing dead air: the outro asset is digital silence

Measured on the artifact: exactly one silence region, **16.047 → 18.564 s
(2.517 s)**. Audio stream is full-length (18.564 s vs video 18.500 s) — this is
not a truncation or an `apad` artifact.

Cause, measured:

```
/opt/genlab/BlackboxBrief/assets/motion/outros/comment.mp4
  duration 2.500 s · aac stream present · mean_volume -91.0 dB (digital silence)
```

`motion_compositor` concatenates the outro **after** the Stage-1 audio mix
(prod log: `compositing 2 segments (intro=- outro=comment.mp4)`), so the outro
contributes its own silent track. 2.500 s asset ≈ 2.517 s measured tail.

## F4 — Blast radius: the silent tail is PRODUCTION-WIDE and predates narration

Probes on the most recent rendered reel per niche (read-only):

| niche | duration | gaps | last gap | LRA |
|---|---|---|---|---|
| ai_creators | 19.41 s | 2 | **2.517 s** | 3.4 |
| movies | 18.60 s | 1 | **2.517 s** | 2.7 |
| sports | 18.60 s | 1 | **2.517 s** | 4.6 |
| anime | 16.07 s | 0 | none | 1.8 |
| gaming | — | — | — | pipeline failed 08-19 |

**Three of four measurable niches ship reels ending in 2.517 s of digital
silence** — ~13% of an 18.6 s reel. Anime shows none (no outro applied).
This has nothing to do with narration and affects published reels today.

Low LRA (1.8–4.6) across all production reels independently corroborates F2:
there is no dynamic ducking anywhere, narrated or not.

## F5 — No narrated reel has ever published; double-speech is preverify-only

Measured:

```
blueprints with non-empty narration_script : 0
published with narration                   : 0
```

Narration shipped 2026-08-19; the first narrated blueprint is expected from the
2026-08-20 02:30 UTC fire. **No double-speech reel has reached an audience.**
The F1/F2 defect is confined to the pre-verification artifact — which is
precisely what the listen window existed to catch.

## F6 — The integration test cannot catch this class

`tests/media/test_narration_final_mix_integration.py` (authored 2026-08-19,
this session) asserts VO **presence** via a 1800 Hz band delta. It would pass
unchanged on a graph with no ducking at all — and did. It verifies that the VO
arrives, never that anything gets out of its way. Test gap, self-inflicted.

---

## Proposed fix scope for the NEXT prompt (implement nothing here)

1. **Duck source under VO.** Add sidechain on the source path, keyed by VO —
   the graph currently sidechains nothing. Decide static-vs-sidechain
   deliberately; the spec wanted sidechain, the code chose static, and the
   docstring never followed.
2. **Restore or formally retire the music sidechain** so code and docs agree.
3. **Trim or mute-aware outro.** Either give the outro a real audio bed, or
   have the mix extend under it, or drop the silent tail. Production-wide, so
   it is not narration-scoped and should be sequenced on its own merits —
   2.5 s of dead air at the end of a short-form reel is a completion-rate cost.
4. **Test upgrade**: assert ducking dynamics (LRA floor, or source-band
   attenuation during VO), not just VO presence.

Sequencing note: item 3 touches every niche's render and must not land
between the round-2 proof and the NARR-09 evidence fire.
