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
