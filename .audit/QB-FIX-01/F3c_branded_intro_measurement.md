# F3c — Branded cold-open measurement (2026-08-06)

## Findings (measure-only per spec)

| Niche       | Intro asset             | Intro present | Time-to-first-non-logo-frame |
|-------------|-------------------------|:-------------:|:----------------------------:|
| ai_creators | pattern_break_intro.mp4 | YES           | 2.52s (frames 0-60 sim=1.000, frame 75 sim=0.322) |
| ai_creators | logo_tagline_reveal.mp4 | YES           | 2.50s (frames 0-60 sim=1.000, frame 75 sim=0.127) |
| movies      | logo_tagline_reveal.mp4 | YES           | 2.51s (frames 0-60 sim=1.000, frame 75 sim=0.014) |
| anime       | none                    | NO            | 0.00s |
| sports      | (assets exist; not measured today — reels aged) | | |
| gaming      | (assets exist; F-QB-0002 pipeline produces no reels) | | |

Intro asset durations verified via ffprobe: all 3 assets (logo_zoom / logo_tagline_reveal / pattern_break_intro) are **2.5000s** exactly across all 4 niches with libraries.

## Phase 9 gap matrix correction

`phase_9_synthesis.md` §3 row for Dimension 4 Branding intro on ai_creators was originally graded:
> `4 Branding intro | ai_creators | text-in-first-1s=100% | MATCH | LOW | Tier 3`

That original grading measured "text presence in first second" (which the intro's animated text satisfies) as if it were a proxy for substantive content. F3c reveals that the entire first 2.5s of every measured reel on ai_creators, movies (and by implication sports + gaming when they render) is a brand-only intro asset — no substantive content until frame 75.

Per Section 1.1 row 4 benchmark (MEDIUM confidence): "no logo cold open. Time-to-first-content should be ≈0s. Branded intros are a retention liability in short-form." The measured 2.5s value is 2.5× the entire "swipe decision" window on short-form (typically decided within 1s).

Corrected row:
> `4 Branding intro | ai_creators | 2.5s intro | WORSE (target ≈0s) | HIGH | Tier 1`

Same correction for movies + sports + gaming (their reels ship an equivalent 2.5s intro when intro_animation bandit arm fires).

## Follow-up (not in F3c scope — separate work item)

Removing the 2.5s intro is a bandit-arm-level change:
1. Set `intro_animation` dimension's default arm to `no_intro` (add such an arm if it doesn't exist), OR
2. Disable the `intro_animation` dimension entirely via transformation config, OR
3. Compress the intro to ≤0.5s (retention-safe range) — requires new short-format intro assets, not just config change.

Option 1 is least invasive but requires the bandit to have a "no_intro" arm. Option 2 is a config-only kill switch. Option 3 changes brand-identity balance and needs design input.

Not scoped for QB-FIX-01. This measurement is the input to that decision.
