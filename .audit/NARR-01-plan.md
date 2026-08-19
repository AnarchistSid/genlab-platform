# NARR-01 — Narration Enablement Plan

**Status**: Phase 2 (planning only, read-only). Awaiting Phase 3 authorization.
**Date**: 2026-08-18 (evening session).
**Canary scope**: BlackboxBrief (ai_creators) only. Other 4 niches must be provably unaffected.
**Blocker for confirmation (Phase 4)**: Both LLM providers at $0 credit (G1 fails). Publish evidence also blocked by BB daily-cap already consumed today → next slot tomorrow ~06:30 UTC.

---

## 1. Flag design

- **Env flag**: `GENLAB_NARRATION_ENABLED` (boolean; `1|true|yes|on` on). Master kill-switch across all niches. Off by default.
- **Per-niche YAML**: `narration.enabled: bool` in `niche.yaml`. Default `false`. BB canary flip = the ONLY niche set to `true` in this session's PR.
- **Both must be truthy**: env AND niche YAML. Env is the emergency stop; niche YAML is the canary allowlist.
- **`is_narration_enabled_for(niche_id)`** helper in `genlab_core/publishing/narration_gate.py` (new module — small, mirrors `cross_channel_footer.is_enabled_for` shape). Fail-open: any error → False (matches every other canary gate).

Rationale for two gates: matches the established pattern in `cross_channel_footer` (env flag + per-niche list) but adapts to a per-niche YAML shape because narration config is niche-specific (voice, VO ratio, wpm) and belongs closer to the audio config than an env-var list. Env-var master is the flip-off knob when operator wants a nuclear kill without editing YAML.

---

## 2. Script generation

### 2.1 New output field on the writer

`writing/video_content_writer.py` — add `narration_script` to the LLM output contract:

- **Prompt addition** (in the JSON schema block starting `video_content_writer.py:872`):
  ```
    - narration_script    ← REQUIRED when narration.enabled, 2-4 sentences
                            of original commentary/analysis/context that
                            the clip itself does not contain. Voice: the
                            channel's on-brand editorial voice. No URLs.
                            No first-person "I played/watched" claims
                            beyond what the source supports.
  ```
- **`_SENTENCE_CASE_FIELDS`** update to include `narration_script`.
- **Optional-when-flag-off**: if `is_narration_enabled_for(niche_id)` is False at call time, the prompt does NOT include this instruction — the writer produces the same 6 fields it does today (proves other 4 niches are byte-identical to pre-change output).

### 2.2 Validator (enforcement lives in code, not prompt)

New `writing/narration_validator.py`:

```python
def validate_narration_script(
    text: str,
    clip_duration_seconds: float,
    wpm: int = 150,
    tail_buffer_seconds: float = 2.0,
) -> tuple[bool, str]:
    """Returns (ok, reason). Reason is diagnostic string for logs.

    Rules (all must pass):
    1. duration_fits: len(text.split()) * 60 / wpm <= clip_duration_seconds - tail_buffer_seconds
    2. no_urls: not re.search(r'https?://', text)
    3. no_affiliate_ctas: no 'buy', 'shop', 'affiliate', 'discount', 'promo code',
       'link in bio', 'swipe up', 'grab yours' (case-insensitive)
    4. no_first_person_experience_claims: no 'i played', 'i watched', 'i tried',
       'i tested', 'i built', 'i created' (case-insensitive)
    5. not_empty: len(text.strip()) >= 20 chars
    """
```

### 2.3 Fitting rule (explicit)

`wpm = 150` (baseline TTS speech rate for Edge-TTS and OpenAI TTS at speaking_rate=1.0; ElevenLabs ~135 wpm — using 150 is conservative → any VO that fits at 150 also fits at slower rates; if operator wants ElevenLabs tighter, override wpm per-niche in YAML).

`tail_buffer_seconds = 2.0` — reels feel truncated when VO ends within 0.5s of clip end. 2s tail lets the music bed carry the reel out.

Enforcement: **writer validator rejects** any narration_script that fails the fit. If the LLM returns a too-long script AND all-other-fields pass, the writer keeps the 6 platform captions and sets `narration_script = ""` + logs WARN + adds `narration_degraded_reason: 'script_too_long'` to the blueprint's `content` dict. Consumer sees empty narration_script → skips VO mix (fail-open).

### 2.4 Content constraints (validator, not prompt)

- Duration budget: see 2.3.
- No URLs.
- No affiliate/CTA phrases (list above).
- No first-person experience claims.
- Follow-CTA is permitted if the niche's `outro_cta` bandit arm already produces a follow-style CTA — VO can include it (validator doesn't block "follow us").

---

## 3. Synthesis + mix

### 3.1 TTS synthesis

The existing `GenerateAudio` stage already runs the TTS cascade. **Change**: when `narration.enabled`, `GenerateAudio` reads `bp["content"]["narration_script"]` (new field, from writer) INSTEAD of hook+caption concatenation (`generate_audio.py:_build_script`). This decouples narration content from social-caption content — captions can still be topical/hashtag-heavy while VO is prose commentary.

Fallback: if `narration.enabled` but `narration_script` is empty (e.g., validator rejected), `GenerateAudio` skips TTS entirely, sets `bp["content"]["narration_degraded"] = True`, and logs WARN with the reason.

### 3.2 Mix routing (the load-bearing change)

Extend `media/audio_replacer.py`:

- **`AudioMixSpec`** gains 2 optional fields:
  - `narration_audio_path: Path | None = None`
  - `narration_vo_db: int = 0`  (VO gets full amplitude by default; music/source get ducked under it)
  - `vo_bed_duck_db: int = -8`  (ADDITIONAL duck applied to music bed WHEN VO segment is playing; sidechain)
  - `target_lufs: float = -14.0`  (EBU R128 final normalization; matches YouTube Shorts / Meta Reels spec)

- **`build_audio_mix_filtergraph`** — when narration path present, build a 3-input graph:

  ```
  [0:a]volume={source_duck_db}dB[src_ducked];
  [1:a]volume={music_bed_db}dB[music_base];
  [2:a]volume={narration_vo_db}dB[vo];
  [music_base][vo]sidechaincompress=threshold=0.05:ratio=8:attack=5:release=200:makeup=0dB[music_ducked_under_vo];
  [src_ducked][music_ducked_under_vo][vo]amix=inputs=3:duration=first:dropout_transition=0[premix];
  [premix]loudnorm=I={target_lufs}:TP=-1.5:LRA=11[aout]
  ```

  When narration path absent, fall back to today's 2-input graph (byte-identical to current output — pin test asserts this).

- **`build_ffmpeg_command`** — conditionally add `-i narration_audio_path` as the 3rd input when path present.

### 3.3 Loudness normalization

`loudnorm=I=-14:TP=-1.5:LRA=11` — EBU R128 broadcast standard. Applied AFTER the 3-input amix so VO+music+source are normalized as a whole rather than per-track.

**Note**: `loudnorm` is a 2-pass filter for accuracy but expensive; single-pass is fine for reels (short-form). Ship single-pass; measure LUFS in Phase 4 evidence to confirm ±1 LU spec compliance.

### 3.4 WhisperX alignment (Amendment A2 — DROPPED re-pointing)

**Amendment A2 (2026-08-18)** — the plan's original change #6 (re-point
WhisperX to the mixed audio) is DROPPED. WhisperX stays on the clean
VO track (``bp["media"]["audio_path"]``) — the TTS output, no music
or source competition. Captions produced against clean audio are
higher accuracy than captions produced against a 3-track amix.

**Insertion-offset invariant**: the 3-input mix has all three streams
aligned to t=0 by ``amix`` default (``duration=first`` doesn't shift
start times, only end times). VO starts at t=0 in the final audio.
Therefore ``narration_insertion_offset_seconds = 0.0`` by
construction — no runtime offset addition needed on caption
timestamps produced from the clean VO track.

Phase 4 verifies this by spot-checking 2 caption timestamps against
the audible VO — if VO starts at t=0 in the published audio,
WhisperX-from-clean-track timestamps align 1:1 with no offset.

If a future variant introduces a VO start delay (e.g. "let the clip
speak for 3s before narration starts"), the design must add an
explicit ``[2:a]adelay={n}ms`` filter to the ``[2:a]volume`` node
AND propagate the offset value to the caption stage. Today no such
variant exists — VO at t=0 is the invariant.

---

## 4. Fallback + degradation contract

Per prompt requirement + rule #19 memoed 2026-08-18:

**Silent degrade is forbidden**. Every fallback trigger MUST:
1. Set `bp["content"]["narration_degraded"] = True`.
2. Set `bp["content"]["narration_degraded_reason"] = "<slug>"` (see enum below).
3. Emit `logger.warning(...)` at the trigger site — not DEBUG, not INFO, not silent.
4. Persist to `blueprints.extra['narration_degraded']` (bool) + `blueprints.extra['narration_degraded_reason']` (str) via `push_to_backlog`.

**Degradation reasons enum** (namespaced strings):
- `script_generation_failed` — LLM returned no narration_script (credit exhausted, refusal, JSON parse fail)
- `script_too_long` — validator rejected on duration fit
- `script_contained_urls` — validator rejected on URL presence
- `script_contained_affiliate_cta` — validator rejected on affiliate CTA
- `script_first_person_claim` — validator rejected on unsupported first-person claim
- `tts_cascade_failed` — all 4 TTS tiers failed
- `vo_overrun` — post-synth ffprobe check: actual VO duration exceeds fit window (A4)
- `mix_failed` — 3-input ffmpeg amix returned non-zero
- `loudnorm_failed` — post-amix loudnorm failed (mix still valid, just not normalized)
- `storytime_mutex` — variant_type=storytime + STORYTIME_COMPOSITOR_ENABLED wins; NARR-01 skipped (A3)

**Aggregation rule** (per operator note 2026-08-18): `storytime_mutex` is a ROUTING OUTCOME, not a failure. Exclude it from degradation-rate aggregation. When reporting "% of blueprints degraded", filter out rows where `narration_degraded_reason = 'storytime_mutex'`. Correct denominator = blueprints that BOTH had narration enabled AND were routed through the NARR-01 path (not storytime). Example:

```sql
-- Correct degradation rate
SELECT niche_id,
       COUNT(*) FILTER (WHERE extra->>'narration_degraded' = 'true'
                          AND extra->>'narration_degraded_reason' != 'storytime_mutex') AS degraded,
       COUNT(*) FILTER (WHERE extra->>'narration_degraded_reason' != 'storytime_mutex'
                          OR extra->>'narration_degraded' IS NULL) AS narr01_eligible
FROM blueprints
WHERE created_at > NOW() - INTERVAL '1 day'
GROUP BY niche_id;
```

**Query for operator observability** (corrected NARR-03 Step 6 —
COALESCE handles the NULL-semantics case where successful
non-degraded rows have NULL reason and were being excluded from
narr01_eligible by the previous form):

```sql
SELECT niche_id,
  COUNT(*) FILTER (WHERE extra->>'narration_degraded'='true'
                     AND COALESCE(extra->>'narration_degraded_reason','')
                         != 'storytime_mutex') AS degraded,
  COUNT(*) FILTER (WHERE COALESCE(extra->>'narration_degraded_reason','')
                         != 'storytime_mutex') AS narr01_eligible
FROM blueprints
WHERE created_at > NOW() - INTERVAL '1 day'
GROUP BY niche_id;
```

`storytime_mutex` is excluded per the routing-outcome rule (§4
above). Divide `degraded` by `narr01_eligible` for the true
degradation rate.

---

## 5. Tests

All ship alongside implementation in Phase 3.

### 5.1 `tests/writing/test_narration_validator.py` (8 cases minimum)

- `test_valid_script_passes` — 3-sentence script under duration budget
- `test_too_long_rejected` — 200 words for a 5s clip
- `test_url_rejected` — script contains `https://example.com`
- `test_affiliate_cta_rejected` — script contains "grab yours at the link in bio"
- `test_first_person_experience_rejected` — "I played the game and here's what happened"
- `test_empty_rejected` — empty string
- `test_borderline_duration_passes` — exactly at budget (edge case, must pass)
- `test_borderline_over_rejected` — 1 word over budget (edge case, must fail)

### 5.2 `tests/media/test_audio_replacer_narration.py`

- `test_two_input_graph_unchanged_when_narration_absent` — asserts the filter string equals the pre-change output for the 2-input path
- `test_three_input_graph_shape` — asserts filter_complex contains `[2:a]`, `sidechaincompress`, `loudnorm=I=-14`
- `test_ffmpeg_command_adds_third_input` — asserts `-i narration.mp3` in argv when path present
- `test_loudnorm_TP_and_LRA_defaults` — asserts `TP=-1.5:LRA=11` in graph

### 5.3 `tests/pipeline/test_narration_default_disabled.py`

- `test_missing_yaml_key_no_crash` — niche.yaml has no `narration:` section → `is_narration_enabled_for` returns False, no exception
- `test_flag_off_writer_omits_narration_key` — writer produces 6-field output byte-identical to pre-change (proves other niches unaffected)
- `test_mix_path_unchanged_when_disabled` — audio_replacer command shape identical to pre-change when narration_audio_path=None

### 5.4 `tests/observability/test_flag_audit.py::TestNarrationFlagName`

- Import the reader-side constant (from `publishing/narration_gate.py`) and assert it equals `GENLAB_NARRATION_ENABLED` AND is in `_KNOWN_FLAGS`. Matches the pattern from `d6d136e3`.

### 5.5 Non-regression

- Run full test suite for `tests/writing/`, `tests/media/`, `tests/pipeline/`, `tests/observability/`. Must pass.
- Render one non-canary niche (e.g., gaming) with narration disabled and diff the ffmpeg-argv mix params against a pre-change baseline. Must be byte-identical.

---

## 6. Phase 4 evidence requirements (recap)

- **Item 1 — Local render evidence**: BB reel rendered with VO. Verify via `ffprobe` (audio stream present + duration), LUFS measurement (`ffmpeg -af loudnorm=print_format=json -f null -`), Whisper transcript of FINAL rendered audio contains commentary script text.
- **Item 2 — Publish evidence**: Post ID in `publishing_analytics`. Blocked until tomorrow's 06:30 UTC BB slot.
- **Item 3 — Caption evidence**: 2 spot-check caption timestamps against audio.
- **Item 4 — Non-regression**: Full test suite + one non-canary niche renders unchanged.
- **Item 5 — Flag audit evidence**: `[flag_audit]` log line contains `GENLAB_NARRATION_ENABLED` with correct name.

Session-end summary rule: if publish must wait for tomorrow's slot, items 1, 4, 5 done → **fix NOT confirmed**, pending-watch task with exact query.

---

## 6b. Publisher pick mechanism (NARR-04 schedule audit)

Measured from `publishing/blueprint_selector.py:37-84` (2026-08-18):

1. Query: `backlog_client.get_blueprints_by_status("VISUAL_READY", niche_id=<niche>)`
2. Gate: `PublishGatekeeper.evaluate()` — 7 gates including
   `_schedule_gate` at `platforms/gatekeeper.py:103`.
   * Schedule gate: `if not scheduled: return GateResult(allowed=True, reason="no schedule")` (line 106).
   * Scheduled-but-not-yet-due: gate blocks.
3. Sort: `(scheduled_for ASC with NULL→"9999-12-31", -priority_score)`.
   Earliest schedule wins; priority breaks ties among same-time siblings.
4. Return top-1 or None.

**Consequences**:
* Unscheduled blueprints PASS the gate but sort LAST.
* Any blueprint with a real `scheduled_for` (even 2036) will be
  picked over an unscheduled one.
* To force the publisher to pick a fresh blueprint over an old
  scheduled one: unschedule the old (`scheduled_for = NULL`) AND
  ensure the fresh one has a scheduled_for value.

**NARR-04 Pixel unschedule** (2026-08-18): Set `scheduled_for=NULL` on
`id=6fd00e50-4778-471f-aa55-10ba2404cbfb` (was `2026-08-19 06:30 UTC`).
Rollback:
```sql
UPDATE blueprints SET scheduled_for = '2026-08-19 06:30:00+00'
WHERE id = '6fd00e50-4778-471f-aa55-10ba2404cbfb';
```

## 7. Rollback

**Verified rollback command (NARR-02 + NARR-03 hygiene 2)**:

```bash
# On VPS as root or any sudo-capable user:
sudo -u genlab sed -i 's/^GENLAB_NARRATION_ENABLED=.*/GENLAB_NARRATION_ENABLED=0/' /opt/genlab/.env
# No systemctl restart needed FOR THE NARRATION FLAG SPECIFICALLY —
# `narration_gate` is imported only by 5 files consumed exclusively
# by Type=oneshot services (genlab-pipeline-*, generate_audio stage,
# transformation_orchestrator stage, base_writing stage, flag_audit).
# Each oneshot service start re-reads EnvironmentFile=/opt/genlab/.env
# → next pipeline fire produces byte-identical audio to today.
```

**Hygiene-2 amendment (2026-08-18 NARR-03)**: the "no reload needed"
claim applies to flags consumed ONLY by oneshot services. Five
`Type=exec` long-running services also consume `/opt/genlab/.env`:
`genlab-dashboard` (gunicorn), `genlab-engagement-poller`,
`genlab-engagement-worker` (dramatiq), `genlab-quota-monitor`
(quota_daemon), `genlab-webhook` (uvicorn). For any flag those
services read (verified via `grep -rlE 'GENLAB_YOUR_FLAG' /opt/genlab/`),
add `sudo systemctl restart <svc>` to the rollback. `narration_gate`
is NOT read by any of them → narration rollback is a one-liner.

**Hygiene-1 (2026-08-18)**: NARR-02 Step 2 toggled the live .env
0→1→0 in a ~5-second window at 14:23:40 UTC. journalctl scan of
14:20-14:25 UTC showed only `Deactivated` events (services had
started before the toggle, captured pre-flag env, were finishing).
Zero services read narration=1 during the toggle window. Standing
rule for future canary audits: flag-toggle tests use
`systemd-run --property=Environment=...` overrides OR a file copy —
never the live .env — to eliminate the artifact-window class.

**Alternative (BB-only, keep env flag on)**:
```yaml
# In BlackboxBrief/config/niche.yaml, change:
narration:
  enabled: false
```

Both are runtime rollbacks. No code rollback needed unless a
validator false-positive is blocking legitimate content — in which
case the writer's fail-open path (empty narration_script → skip VO,
degraded=True) still produces publishable reels.

---

## 8. Video-only mandate note

**Unchanged**: real footage is still required. Narration is ADDITIVE commentary over sourced footage, not a replacement for it. This plan does NOT touch the video-gate rules (`base_visual_render` still enforces real clip presence). VO adds an audio TRACK to the existing composite.

CLAUDE.md's "STRICT VIDEO REQUIREMENTS" section (bt709, 1080×1920, H.264, 15-60s, logo overlay) is unchanged. Only the audio track composition changes.

---

## 9. Out of scope (rejected explicitly per prompt)

- SpliceReel / FrameDrift narration (highest copyright sensitivity)
- Cadence, publish-window, approver caps
- Affiliate / CTA systems (VO validator rejects affiliate; existing affiliate injection stays disabled)
- Core base classes (no changes to BaseWritingStrategy, BasePlatformAdaptationStrategy, BaseVisualRenderStrategy)
- TikTok / X / Threads activation

---

## 10. Known blockers for Phase 3-4

1. **LLM credit**: Both Anthropic + OpenAI at $0. Script generation cannot be tested end-to-end until at least one provider tops up. Phase 3 code can still be written (validator, mix graph, tests use mocks). Phase 4 items 1 + 3 require real LLM output.
2. **BB daily-cap**: 1/1 consumed today. Phase 4 item 2 (publish evidence) waits for tomorrow's 06:30 UTC slot regardless.
3. **Rule #29 (flag flip needs code-deploy verify)**: Before flipping `GENLAB_NARRATION_ENABLED=1` on VPS, verify `git rev-parse origin/main == VPS HEAD` per the class-of-bug shipped `2026-08-18`.

---

## 11. Change summary (from Phase 1 finding F1-F8)

| File | Change | LoC estimate |
|---|---|---|
| `writing/video_content_writer.py` | + narration_script output key, prompt block, sentence-case | ~40 |
| `writing/narration_validator.py` | NEW — 5-rule validator | ~80 |
| `strategies/base_writing.py:454` | propagate narration_script to content dict | ~5 |
| `pipeline/stages/push_to_backlog.py` | persist narration_script + narration_degraded | ~15 |
| `media/audio_replacer.py` | 3-input graph + loudnorm + sidechain duck | ~60 |
| `media/transformation_orchestrator.py:365-406` | pass VO path through when enabled | ~25 |
| `pipeline/stages/render_whisper_captions.py` | re-point WhisperX to final mixed audio | ~20 |
| `pipeline/stages/generate_audio.py` | read narration_script instead of hook+caption when narration enabled | ~15 |
| `publishing/narration_gate.py` | NEW — env + YAML gate helper | ~40 |
| `observability/flag_audit.py` | add flag to _KNOWN_FLAGS | ~2 |
| `BlackboxBrief/config/niche.yaml` | canary flip narration.enabled: true | ~3 |
| `tests/*` | 4 new test files | ~250 total |
| **TOTAL** | | **~555** |

No changes to base classes, no cross-niche fanout beyond BB. Every non-canary niche continues with narration.enabled=false default → byte-identical audio to today.

---

## 12. NARR-05 (2026-08-19) — the VO never reached the mix

### 12.1 Root cause: producer scheduled after its only consumer

`GenerateAudio` ran at **stage 17**; its only consumer,
`phase4_visual_render` → `apply_post_render_transformations` →
`audio_replacer`, ran at **stage 15**. `media["audio_path"]` was therefore
always `None` at mix time and `transformation_orchestrator` fell through to
the legacy 2-input path. Every NARR-01 component was individually correct;
only the assembled order was wrong.

Confirming production output — `journalctl`, ai_creators run
`ai_creators_20260819_072015` (07:20 UTC / 12:50 IST):

```
13:02:42  [audio_replacer] mixing: source=00_trim.mp4
          music=technology__tech_technology_484304.mp3 (duck=-9 music_bed=-20)
13:03:08  [transformation_orchestrator] ai_creators complete
13:03:45  [Pipeline] Running 2 stages in parallel: ['RenderTextOverlays','GenerateAudio']
13:03:56  [GenerateAudio] 1 generated, 0 skipped, 0 errors
```

The mix ran **74 s before the VO existed**, with two inputs, and no
`narration engaged` line anywhere in the journal.

### 12.2 Why it stayed invisible

`transformation_orchestrator`'s "no VO path" branch logged **nothing**.
The storytime-mutex and already-degraded branches logged; the branch that
actually fired every single time did not. Meanwhile the run report said
`[GenerateAudio] 1 generated, 0 skipped, 0 errors`. Same shape as rule #19.

### 12.3 Changes shipped

| File | Change |
|---|---|
| `config/pipeline_template.yaml` | `GenerateAudio` hoisted above `phase4_visual_render`, after `ViralityScoring`; left the `post_render` parallel group |
| `pipeline/stages/generate_audio.py` | VO filename keyed on `story_id` (`candidate_id` is assigned at stage 21); `run_id` read from `context["run_id"]`; stamps `content["narration_expected"]` |
| `strategies/base_visual_render.py` | forwards `narration_expected` into `blueprint_context` |
| `media/transformation_orchestrator.py` | WARN on both no-VO branches, gated on `narration_expected` |
| `media/audio_replacer.py` | `-ar 48000` on the narration branch — `loudnorm` was emitting 96 kHz |
| `tests/pipeline/test_narration_stage_order.py` | order pin + VO-filename pins (7 cases) |
| `tests/media/test_narration_final_mix_integration.py` | real-ffmpeg final-mix-contains-VO + 48 kHz + control-cleanliness (3 cases) |

### 12.4 Dependency evidence for the hoist

`GenerateAudio`'s entire read-set resolves at or before stage 11:
`narration_script` + `caption` from the writer (`base_writing.py:238,526`),
`hook` from the hook strategy, clip duration from
`download_top_videos.py:720`. Stages 15–16 write only `media["render_error"]`,
`story["arm_ids_by_dimension"]`, `media["transform_reject_reason"]`,
`media["overlaid_path"]`, `media["rendered_path"]` — zero intersection.

### 12.5 The `unknown_audio.mp3` collision

`out_path` was keyed on `bp["candidate_id"]`, first assigned at
`push_to_backlog.py:2310` — four stages downstream. The key never existed at
synthesis time, so prod held exactly **one file per niche, ever**:
`/tmp/genlab_audio/{niche}_manual/unknown_audio.mp3`. `_manual` came from a
second defect: `run_id` was read from `context["run_stats"]["run_id"]`, which
no stage sets (the runner writes `context["run_id"]`,
`pipeline_runner.py:358`).

Inert while nothing consumed the path. Hoisting `GenerateAudio` above the
render makes the mix consume it, at which point a multi-story run would mix
story N's VO into story N−1's reel. Fixed as part of the hoist, not after it.

### 12.6 PRE-VERIFICATION (2026-08-19, story_0 = `03348d8f9e0e30d0`)

Real assets from the 07:20 UTC scheduled run. Not a natural fire — the VO
path was supplied explicitly to reproduce the post-fix wiring.

```
control (2-input, = what prod ships today) : /opt/genlab/.tmp/narr05_preverify/PREVERIFY_control_no_vo.mp4
narrated (3-input, = post-fix)             : /opt/genlab/.tmp/narr05_preverify/PREVERIFY_narrated.mp4
```

| speech band 1.0–3.4 kHz | control | narrated |
|---|---|---|
| mean_volume | −40.6 dB | **−27.7 dB** |
| max_volume | −20.3 dB | **−7.7 dB** |

### 12.7 OPEN RISK — VO overruns the clip by 38%

**Not fixed. Gates the value of Thursday's evidence run.**

* VO duration **29.86 s**; rendered reel **18.60 s**.
* The writer sized the script to the **30 s default** from `e1f508e9`
  (`base_writing.py:470-480`) because the story shape carried no
  `duration_seconds` — 29.86 s lands exactly on that default.
* `_check_vo_overrun` (A4) does **not** rescue this: it returns early when no
  clip duration is resolvable (`generate_audio.py:296`), which is the same
  condition that triggered the 30 s default. No `vo_overrun` marker is set.
* `amix duration=first` therefore truncates the VO at the clip length.

Measured on the real VO — the truncated tail is speech, not silence:

| VO segment | mean | max |
|---|---|---|
| 0 → 18.5 s (survives) | −35.1 dB | −12.2 dB |
| 18.5 → 29.86 s (**cut**) | −37.2 dB | −13.7 dB |

**Expected Thursday outcome without a fix**: a reel that *does* carry
narration, cut off mid-sentence ~11.4 s early, with no degradation marker
and no WARN. The A4 probe was designed to catch exactly this and cannot,
because its guard shares a root cause with the defect it guards.

Fix direction (task, not shipped): resolve the fit window from the **rendered
reel length**, not the source video / 30 s default — and make `_check_vo_overrun`
treat "no resolvable duration" as a degrade signal rather than a silent return.

### 12.8 Option C

Queued to **#222** as the eventual structural shape. Its definition is not
reproduced here — it was not available in this session and is deliberately
not paraphrased. Paste it in and it will be written up under this heading.

---

## 13. Propagator structural fix — queued, next structural cycle

Not under deadline. Written up here so the reasoning survives the session.

### 13.1 The propagator is a chain of four gates, not one

Framing this as "base_writing's propagator eats fields" understates it. A new
writer output field must be manually added at **four** sequential
explicit-assignment gates before it reaches an audience:

| # | Gate | Site | Failure if forgotten |
|---|---|---|---|
| 1 | writer result → `story["content"]` | `base_writing._write_story_llm:514-602` | field never leaves the writer |
| 2 | `content` → blueprint record | `push_to_backlog.py:2578-2581` (explicit pick, **not** a splat) | field lives in `content`, absent from the blueprint |
| 3 | blueprint dict → DB column | `PROMOTED_COLUMNS` (`storage/postgres.py:158`) | value silently lands in `extra` JSONB, every `WHERE column = X` misses it |
| 4 | consumer read | e.g. `GenerateAudio` reading `content["narration_script"]` | reads empty, degrades |

The three eaten fields died at gate 1. Rule #28's four columns
(`action_taken_source`, `hook_classifier_score`, `variant_type`,
`variant_payload`) died at gate 3. Same class, different hop.

**This is why option (a) alone is the wrong pick.** Default-propagating into
`content` fixes gate 1 and leaves 2–4 untouched — and it makes the *next*
failure harder to diagnose, because the field would now appear correctly in
`content` and vanish silently later. Fixing one gate of four buys false
confidence, which is worse than the current honest breakage.

### 13.2 Why these three fields specifically

`source_attribution`, `narration_script`, `hook_style`, `caption_segments`
share one property: they are **pass-through** — no rename, no restructure, no
truncation.

The fields that have *never* been eaten are the transformed ones:
`instagram_caption` → `content["caption"]`; `twitter_content` →
`content["x_twitter"]["tweet"][:280]`. A transformed field has a destination
someone had to write code for. A pass-through field looks like it needs no
code, which is exactly why the code gets forgotten.

That also rules out a naive `content.update(result)` for option (a) — the
propagator transforms, so a blind update would leave `instagram_caption` and
`twitter_content` sitting in `content` as raw duplicates of already-transformed
values, in a second shape, persisted.

### 13.3 Step 0 — there are already three partial declarations

Before adding any list, collapse the ones that exist. None is canonical and
they have already drifted:

* `_SENTENCE_CASE_FIELDS` — 7 keys (`video_content_writer.py:32`)
* `_REQUIRED_LLM_FIELDS` — 6 keys (`:286`)
* the prompt's `"Return JSON with keys: …"` string — 6 keys (`:988`), and it
  **omits `narration_script`**, which is appended conditionally at `:947`

None includes `hook_style`, `caption_segments`, or `source_attribution` — two
of the fields that actually died. Adding a DROPLIST (a) or a schema fixture
(b) as a *fourth* hand-maintained list is itself
`[[class-of-bug-shared-contract-n-implementers-silent-divergence]]`.

Step 0: one canonical declaration of the writer's output surface; derive the
sentence-case set, the required set, and the prompt text from it.

### 13.4 Recommendation — (b), scoped end-to-end, with (a) at gate 1 only

**(b) is the load-bearing half.** A contract test asserting a field survives
*to the consumer*, not just to `content`, is the only form that covers all
four gates and fails at authorship time. Scope it: for every key in the
canonical declaration, assert it is reachable at the blueprint record, and
that anything DB-bound appears in `PROMOTED_COLUMNS` — which also subsumes the
existing rule #28 schema pin.

**(a) is worth doing at gate 1, narrowly.** Persistence risk was checked and
is low: `push_to_backlog` picks explicitly rather than splatting `content`
(`:2578-2581`), so a new pass-through key cannot collide with a promoted
column name. Invert the default so pass-through survives, and derive the
droplist from the transformed-destination set that already exists implicitly
in `_write_story_llm` — do not hand-maintain it.

Net effect: a new writer field survives gate 1 by default, and if it is
DB-bound and the author forgot gates 2–3, CI says so before merge.

### 13.5 Option C (#224) folds in here

**Extract `audio_replacer` from the visual-render orchestrator into its own
post-`GenerateAudio` stage.**

Same root shape as the propagator problem: a producer/consumer contract held
together by something nothing enforces. NARR-05's hoist made the ordering
correct but left it **positional** — a line's index in a YAML list is the only
thing keeping it true, and `test_narration_stage_order.py` guards exactly that
one instance. Any new stage inserted between #15 and #16, or a niche that
writes its own `pipeline.stages` (gaming does), reopens it.

Extraction makes the audio mix a stage with declared inputs, so the dependency
becomes structural rather than positional — the pipeline can refuse to run a
mix stage whose VO input is unsatisfied, instead of silently mixing two tracks
and logging nothing.

Sequencing: extraction should land **after** the canonical-declaration work in
§13.3, because a stage with declared inputs needs a declaration to point at.

**Tracker ruling (operator, 2026-08-19)**: **#222** is the whole §13
structural program — Step-0 canonicalization → gate-1 inversion with derived
droplist → contract-to-consumer test → audio-stage extraction, sequenced per
§13. **#224** is the NARR-08 tactical set (§14), closing on pre-verification
round 2. The earlier "Option C from #224" attribution was operator error;
#222 is correct.

---

## 14. NARR-08 (2026-08-19) — tactical set (#224)

Four bugs, one arc, all in stage connective tissue, none visible to a
component test:

| # | Bug | Status |
|---|---|---|
| 1 | propagator drop (`narration_script` never left the writer) | fixed `ae76e975` |
| 2 | stage-order inversion (producer 4 stages after consumer) | NARR-08 |
| 3 | filename collision (`unknown_audio.mp3`, one per niche ever) | NARR-08 |
| 4 | VO truncation (38% of script cut, silently) | NARR-08 |

### 14.1 The lesson, stated once

> **Pass-through fields die; transformed fields survive, because
> transformation forces a destination to be written. When adding any field
> that "needs no code," that is the signal it needs a test.**

> **The mix ran 74 seconds before the voice-over existed.**

### 14.2 Addition A — the truncation pair

**A.1 — writer sizes to the render, not the file.** The natural fix (ffprobe
the downloaded clip) would have been *worse than the bug*: story_0's clip on
disk is **356.6 s**, so the budget would have gone 30 s → 354 s. The renderer
trims to `highlight_moment.window_seconds` (`visuals.yaml:129`, BB = 16 s)
before anything ships — 16 s + a ~2.6 s outro is the 18.60 s reel observed.

Resolution now models the renderer: trim window (clamped by a shorter clip) →
explicit metadata → ffprobe of the clip → 30 s, now a WARN and no longer
load-bearing. Sized to `window_seconds` and **not** window+outro on purpose:
the config comment at `visuals.yaml:118-123` records that `motion_compositor`
silently skips intro/outro on many renders, landing the reel at exactly
`window_seconds`. Verified: story_0 resolves **16.0 s**.

**A.2 — mix-time hard guard.** `vo_overruns_reel()` probes the trimmed reel
and the VO at the mix callsite and degrades with `vo_overrun` when the VO
exceeds the reel by more than **0.5 s**.

Tolerance is 0.5 s, not the writer's 2.0 s `tail_buffer_seconds`, because the
two answer different questions: the buffer asks "is there comfortable room
for the music to carry out?" before synthesis; this asks "will a listener
hear a sentence get cut off?" after, on measured durations. TTS routinely
carries 200–400 ms of trailing silence, and clipping that costs nothing.

It is independent of the A4 probe **by construction**: A4 returns early when
no clip duration resolves — the same condition that makes the writer fall
back to 30 s and oversize the script. A4's guard fails on exactly the inputs
that need guarding. `vo_overruns_reel` takes only file paths, and a test pins
that signature so metadata can never creep back in.

**A.3 — the missing log line.** Both no-VO fall-through branches now WARN,
gated on `narration_expected`.

### 14.3 Addition B — the hoist is positional, so verify per niche

Four niches inherit the backbone; **gaming writes its own `pipeline.stages`
and does not pick up template edits**. Resolved through the real loader for
all five:

| niche | GenerateAudio | render | before? |
|---|---|---|---|
| ai_creators | #15 | #16 | yes |
| sports | #15 | #16 | yes |
| movies | #15 | #16 | yes |
| anime | #16 | #17 | yes |
| gaming | ~~#21~~ → **#19** | #20 | fixed here |

Gaming needed care rather than the same edit. `GenerateGamingAudio` reads
`media["rendered_path"]` (`generate_gaming_audio.py:113`) and skips any story
without one — hoisting it alongside the generic stage would have produced
zero commentary, silently, with no failing test. It stays after the render;
it writes `commentary_audio_path`, a different key, so the two never collide.
Both directions are pinned.

### 14.4 Out of scope, filed

* **Gaming cannot produce narration at all** — `render_gaming_video.py:410`
  builds a `blueprint_context` omitting all four NARR-01 keys that
  `base_visual_render` passes. Order is fixed here so the wire works when it
  lands; the wire itself is a separate task. Instance of "N implementers,
  wire only in one".
* **`whisper_sync` canary dependency** — `transcribe_words` returns `None`
  without `faster_whisper`, which is absent from the VPS venv. Verify against
  the caption path's actual package per the four-step canary heuristic before
  claiming a regression. Next cycle.

---

## 15. PRE-VERIFICATION round 2 — PASS (2026-08-19, story_0)

Deployed code, VPS HEAD `a6ee8a2e` == origin/main. Evidence only: no blueprint
push, no publish. story_0 = `03348d8f9e0e30d0`, video_id `cqYLBYenBA0`,
"ChatGPT Plugins Finally Work!" (357 s source → 16 s window → 18.56 s reel).

| # | Item | Result |
|---|---|---|
| 1 | Writer | resolved_duration **16.0 s** (not 30), budget 14.0 s, script 240 chars |
| 2 | TTS | tier `infsh_inworld`; predicted 15.20 s, **actual 14.79 s**, budget 14.0 s — fits the 18.56 s reel |
| 3 | Mix | `narration engaged for niche=ai_creators vo=03348d8f…_audio.mp3 vo_bed_duck=-8dB target_lufs=-14.0`; ffprobe `aac 48000 Hz stereo`, 18.564 s |
| 4 | Loudness | **−14.33 LUFS** (target −14 ±1), TP −1.36 dBTP |
| 5 | Transcript | VO provably present in the FINAL MIXED audio — see below |
| 6 | Captions | drift +0.00 s / +0.04 s; `narration_insertion_offset_seconds = 0.00` |

`narration_degraded` = false at both TTS and mix.

### 15.1 Transcript proof

Script: *"ChatGPT plugins have a reputation problem — they shipped broken and
stayed that way. But this latest update quietly fixed the plugin ecosystem.
Chaining them together for research workflows actually works now, and the
time savings are real."*

faster-whisper on the **final mixed** file (Mac-side; no whisper packages
installed on the VPS):

```
[ 0.00 ->  2.58]  ChatGPT plugins have a reputation problem.
[ 2.64 ->  5.14]  They shouldn't broken and stay in it.
[ 5.52 ->  9.08]  But this latest update can finally fix ChatGPT's specialised workflow.
[ 9.18 -> 12.20]  Chaining them together for research workflows actually works.
[12.28 -> 15.94]  You can basically be like, hey, now you can see my Google calendar now just like my
```

Segments 1–4 are the commentary, recovered through the music bed and ducked
source. First time in the arc the complete chain is provably working.

Two honest caveats:

* Mishearings ("shouldn't broken", "specialised workflow") are whisper
  transcribing *through* a 3-track mix, not TTS defects — the clean VO track
  transcribes cleanly and ends on `'real.' @ 14.08 s`, so the script is
  complete and untruncated.
* Segment 5 is **not** commentary — it is the source video's own speech,
  ducked to −9 dB but still intelligible enough that whisper preferred it in
  that window. Whether the source sits too hot under the VO is a judgment
  call for the operator listen, not something to tune blind.

### 15.2 Ship gate

| set | count |
|---|---|
| A — full suite, with changes | 64 failures |
| B — full suite, stashed baseline | 63 failures |
| A ∖ B | 3 |

The three resolved as:

* `test_base_writing_narration_duration::test_source_tries_multiple_duration_locations`
  — pin greped for the inline chain **including the dead `video.get` branch**;
  updated to pin the resolver, plus a behavioural case.
* `test_pipeline_template_merge::test_clutchwire_real_niche_yaml_matches_pre_p4_stages`
  — pinned ClutchWire to the pre-P4 order, which is the ordering bug itself;
  expected list updated with the reason recorded inline.
* `test_backup_restore_dry_run::test_backup_with_zero_blueprints_exits_one`
  — **not mine**: passes in isolation, and a re-run of `tests/deploy` under
  xdist fails a *different* test each time. Pre-existing flakiness.

Re-run of every affected chunk after the pin updates: **zero failures outside
the baseline set**.

Commits: `5422fcc4`, `4ec634dd`, `a6ee8a2e`.
Revert set: `git revert a6ee8a2e 4ec634dd 5422fcc4`.

---

## 16. NARR-09 Phase 0 — intake (2026-08-19 19:22 IST)

### 16.1 #218 evidence record — listen verdict slots

| # | Subject | File | Verdict | Recorded |
|---|---|---|---|---|
| 1 | Round-2 pre-verification (story_0, not published) | `/opt/genlab/.tmp/narr08_preverify2/PREVERIFY2_narrated.mp4` | **PENDING** | — |
| 2 | Thursday publish candidate | (not yet produced) | **PENDING** | — |

Verdict 1 is the first human review of GenLab narration. It has not been
given. Per NARR-09 Phase 0.1 the evidence run stops here until it is.

### 16.2 Memo line (verbatim)

> **A pin test locks current behavior, bugs included. Every pin authored
> during an incident must state what correct behavior it asserts, not merely
> freeze what exists.**

Earned by `test_clutchwire_real_niche_yaml_matches_pre_p4_stages`, which
pinned ClutchWire's stage list to the pre-P4 order to prove the template
refactor was behaviour-preserving. That order placed the VO producer two
stages after its only consumer. The pin faithfully preserved the ordering bug
and would have failed anyone who fixed it — the test defended the defect.

Sibling: `test_base_writing_narration_duration` greped for
`video.get("duration_seconds")`, a branch `_story_to_video_dict` never
populated. The pin asserted the presence of dead code.

### 16.3 Task filed — `tests/deploy` xdist flake

A different test in `tests/deploy/test_backup_restore_dry_run.py` fails on each
parallel run; all pass in isolation. Classic shared test state (fixture files
or a module-level path reused across workers). It cost real time during the
NARR-08 ship gate by appearing in the with-changes failure set and not the
baseline, and it will eventually eat a ship gate for real. Next cycle.

### 16.4 Why the evidence run cannot start

1. **No listen verdict** on the round-2 file (§16.1).
2. **The fire has not happened.** It is Wednesday 2026-08-19 19:22 IST; the
   BB fire is Thursday 2026-08-20 02:30 UTC = 08:00 IST, ~12.5 h out.
   Running A.1 now returns zero rows for `created_at > 2026-08-20 02:00`, and
   the "zero blueprints → VideoGate day" branch would be a **false
   diagnosis** — the honest reading is "the run has not fired yet."

Parity at intake: VPS HEAD `5f4e9ca4` == origin/main.

---

## 17. NARR-09 Phase 0.4 / 0.5 — LUFS finding RETRACTED (2026-08-19)

### 17.1 Retraction

The −28.79 LUFS claim was a **harness artifact, not a pipeline defect**.

It was measured on exactly one file — `PREVERIFY_control_no_vo.mp4` — which I
constructed during round-1 pre-verification by calling `replace_audio_for_reel()`
directly. That bypasses `apply_post_render_transformations()`, and the loudness
pass lives in that wrapper:

```
post_render_transform.py:202   _normalize_loudness_in_place(Path(result_path), niche_id=niche_id)
```

Unconditional, outside every narration branch, added QB-FIX-01 F3a 2026-08-06.
Its docstring records the placement as deliberate — after `music_mood` +
`audio_ducking` so ducking cannot undo it. The legacy path normalizes in
production; my harness skipped the stage that does it.

### 17.2 The measurement set (stands in the record)

Production renders, 5 niches, 2026-08-11 → 08-19, 18 files:

| niche | measured LUFS |
|---|---|
| ai_creators | −14.95, −14.97, −15.01, −13.68, −13.77 |
| movies | −13.16, −13.97, −14.30, −14.90 |
| sports | −14.08, −14.10 |
| anime | −15.75, −15.78 |

Live published asset, fetched Mac-side via yt-dlp — YouTube `hZTTDpQPe2w`,
ai_creators, published 2026-08-19 06:36 UTC:

```
−13.79 LUFS   TP −0.95 dBTP   aac 48000 Hz stereo   18.65 s
```

**Target −14; live −13.79; renders −13.2 to −15.8. No gap.**

### 17.3 Consequences

* **0.5 does not apply.** Its premise ("if 0.4 confirms on live assets") is
  false. No confound note enters #219 — narrated and non-narrated reels both
  publish near −14, so the watch's treatment is narration alone and
  attribution stays clean.
* **No legacy loudnorm fix enters the next-cycle queue.** There is nothing to
  fix. The legacy path stays frozen because it works, not as a precaution.

### 17.4 A.3 / B.2 branch note — calibration baseline

Local ai_creators render **−14.95** vs same-day live YouTube asset **−13.79**:
platform transcode moved it ~1.2 dB. Thursday's narrated live asset landing
within ~1–1.5 dB of its local render is normal processing, not a pipeline
finding.

### 17.5 Filed into #222 (operator wording, verbatim)

> final-asset gate — one loudness/TP/sample-rate/duration assertion on the
> file as-published, catching the append-after-normalize class regardless of
> future stage order; audio sibling of the integration smoke test.

Origin: `03348d8f9e0e30d0_reel_with_intro.mp4` measured **TP +3.42 dBTP**,
above 0 dBFS and well over the −1.5 target, while its siblings sat at −0.39
and −1.14. The hook_thumbnail intro concat runs *after*
`_normalize_loudness_in_place`, so a prepended segment can push true peak
positive with nothing downstream to catch it. Observation, not urgent — one
sample, intro-prepended variant only, and the live asset measured −0.95.

### 17.6 #218 verdict slots — still open

| # | Subject | Verdict |
|---|---|---|
| 1 | Round-2 pre-verification (`PREVERIFY2_narrated.mp4`) | **PENDING** |
| 2 | Thursday publish candidate | **PENDING** (not yet produced) |

The final re-invocation arrived as the **unfilled template**: all three
mutually exclusive verdict lines present, `[your exact words]` and
`[what's wrong]` still literal placeholders. No verdict has been given, so
A.1 onward remains blocked per Phase 0.1.

---

## 18. NARR-10 (2026-08-20) — mix retune shipped; round 3 BLOCKED

### 18.1 Shipped

| commit | change |
|---|---|
| `6a1dc447` | source −9dB → **−20dB** on the narration path; §3.2 sidechain formally retired; spec/code/docs reconciled |
| `2a85564f` | ducking-dynamics tests — VO-above-source ≥15 dB, override pin, sidechain-absent pin |
| `818d3354` | outro music bed replaces the silent CTA card (narration path only) |

Measured on an identical fixture: source −9dB → VO **8.30 dB** above source;
source −20dB → VO **17.10 dB** above source. The new test was validated by
inversion — reverting the constant reproduces `8.3 >= 15.0` failure.

Deviation recorded: the directive specified the outro bed "at −28dB". A raw
offset does not work because `loudnorm` runs upstream, so −28 dB is measured
against the bed asset's arbitrary level — it landed at −52.1 dB against a
−24.1 dB programme, still scored as silence. Implemented as a **LUFS target
(−24.0)** instead, which places the bed a predictable ~10 LU under a −14 LUFS
programme regardless of asset.

### 18.2 Round 3 could not produce a narrated artifact

Two consecutive full-chain runs on story_0 both degraded:

```
run A: script 265 chars · predicted 16.0s · actual VO 16.98s · reel 16.07s → vo_overrun
run B: script 271 chars · predicted 16.0s · actual VO 16.88s · reel 16.07s → vo_overrun
```

Both correctly degraded to the legacy 2-input mix. **This is the first live
firing of the NARR-08 mix-time guard** — the confirming output that
`4ec634dd` said it was waiting for. The guard works.

### 18.3 ROOT CAUSE — the NARR-01 script validator was never wired

`writing/narration_validator.py` implements the 5-rule validator, including:

```python
projected_seconds = project_tts_duration_seconds(stripped, wpm)
fit_budget = clip_duration_seconds - tail_buffer_seconds
if projected_seconds > fit_budget:
    return False, "script_too_long"
```

**`validate_narration_script` has zero production callers.** Repo-wide grep
returns only its own test file (20 pin tests) and two comment references in
`video_content_writer.py`. It is never invoked by `base_writing`, the writer,
or any stage.

Consequences:

* No narration script is length-checked before synthesis. The writer produced
  **16.0s projected against a 14.0s budget** in both runs and nothing objected.
* Four documented degradation reasons are **unreachable**: `script_too_long`,
  `script_contained_urls`, `script_contained_affiliate_cta`,
  `script_first_person_claim`.
* URLs, affiliate CTAs and unsupported first-person claims in narration are
  never rejected — a compliance surface, not just a fit one.
* TTS spend is incurred producing oversized VO that the mix then discards.
* The NARR-08 mix-time guard is doing work the validator should have done
  earlier and cheaper — it is the only thing standing between an oversized
  script and a truncated reel.

Same class as `inference_utilities` (built, zero callers) and the propagator
gates: **built, tested, never wired.**

Secondary: TTS runs ~6% slower than the `wpm=150` model (predicted 16.0s →
actual 16.88–16.98s), so even a correctly-validated script needs headroom.

### 18.4 Status

* NARR-10 Steps 1–3: **complete, shipped, deployed** (VPS parity `818d3354`).
* Step 4 round-3 pre-verification: **BLOCKED** — no narrated artifact can be
  produced until the validator is wired and the writer respects its budget.
* Step 5 operator listen: **not reached**.
* #218 round-2 verdict recorded: **FAIL on mix — "too much overlapped audio."**

Out of NARR-10 scope (mix graph / docs / tests / outro bed) → filed, not fixed.

---

## 19. NARR-11 (2026-08-20) — validator wired; round 3 rendered; one spec miss

### 19.1 Shipped

| commit | change |
|---|---|
| `90963a0f` | `validate_narration_script` wired into `base_writing` with retry-then-degrade; per-tier TTS rates (Inworld 141); reachability test |
| `fb364def` | prompt word cap fed from the validator's rate — a divergence `90963a0f` itself created |

The fifth built-never-wired bug is closed. All four documented degradation
reasons are reachable for the first time, three of which are compliance rules
(spoken URLs, spoken affiliate CTAs, unsupported first-person claims).

**Self-inflicted regression, caught and fixed in-session**: calibrating the
validator to 141 wpm while `_build_narration_hint` still computed the prompt's
word cap at 150 guaranteed rejection of every compliant script — cap 35 words
projects 14.89s against a 14.0s budget. Two implementers of one contract, now
fed from one value and pinned.

### 19.2 Round-3 pre-verification — 5 of 6 pass

| # | item | result |
|---|---|---|
| 1 | Writer | 221 chars / 32 words, projected **13.62s** @141wpm, budget 14.0s — **validator PASSED first try**, no retry needed |
| 2 | TTS | `infsh_inworld`; predicted 13.62s, **actual 14.52s**, fits the 16.07s reel |
| 3 | Mix | `narration engaged` + `outro bed engaged` (first live firing); 48 kHz stereo; **audio 18.562s == video 18.562s**; **zero silence gaps anywhere** |
| 4 | Loudness | LRA **5.70** (up from 2.6 — dynamics restored) · **LUFS −15.51 ✗ outside −14 ±1** |
| 5 | Intelligibility probe | **PASS** — commentary present verbatim, **0 six-word source runs** |
| 6 | Ducking spot check | not cleanly measurable post-mix without stems — see note |

### 19.3 The intelligibility probe, first live run

Final-mix transcript, complete:

```
[ 0.00-> 3.68] ChatGPT plugins launched as a feature nobody used,
[ 3.68-> 7.52] but the latest batch actually changed together for real workflows,
[ 7.52->10.32] research, writing, data synthesis.
[10.32->14.16] We're seeing the plugin ecosystem finally deliver on what it promised.
```

Raw source, for comparison: *"ChatGPT just looked at my schedule, my calendar,
found the best opening and added a 30 minute break…"* — **none of it appears.**

Round 2's transcript ended with a fifth segment that was source speech, not
commentary. That segment is gone. The automated ear confirms the retune.

### 19.4 The miss — integrated loudness −15.51 LUFS

Outside the −14 ±1 spec by 0.51 LU. Per the NARR-11 rule (any step failing →
STOP, no fix-forward), **not fixed**.

Hypothesis, INFERRED not measured: the outro bed now carries audio where there
was digital silence, and `loudnorm` runs downstream of the compositor, so the
2.5s bed segment pulls the integrated measurement down. Round 2, with a silent
outro, measured −14.33. Testable by rendering the same assets with the bed
disabled; not run.

### 19.5 Notes for follow-up

* **TTS rate still under-predicts.** Actual 14.52s vs predicted 13.62s = +6.6%.
  Effective rate on this script is ~132 wpm, not 141 — and 141 came from a
  different script measuring ~141. Rate varies with content, which is the
  argument for the logged-triples auto-fit task rather than another hand-tune.
* **Ducking spot check** could not isolate source from VO in the same band
  post-mix without stems. The fixture test (17.1 dB VO-above-source, validated
  by inversion) and the intelligibility probe are the real evidence.
* **Standing pattern recorded**: bed levels are specified in LUFS relative to
  programme, never raw dB offsets.
