# QB-FIX-03 §1 — Split Finding After V1

**Date:** 2026-08-06 22:30 IST
**Recorded before W0 per §1 instruction.**

V1 confirmed TTS narration is absent from every reel. The consequence runs in two directions, and the audit has been folding them together. Splitting them now:

## Inauthentic-content exposure goes DOWN

YouTube's July 2026 buckets target AI-generated slop and AI personas. GenLab has no synthetic narration, no AI voice, no AI persona. The "TTS-only templated content" profile that made **F-QB-0708** and **C1** read as severe does not describe this pipeline. Every reel's audio is a real trailer / real anime / real gameplay / real sports moment / real creator speaking — the source video's original audio, unaltered.

**Findings this weakens** (to be verified with output measurement per W2):
- F-QB-0708 inauthentic-content template signature
- C1 severity grade
- Any Phase 9 remediation that assumed "AI narration" was in the pipeline

## Copyright exposure goes UP, materially

Section 1.3 lists the transformative patterns that hold:

1. Very short clips illustrating original analysis
2. Original VO replacing source audio
3. Data-viz overlays over borrowed footage

With TTS absent, GenLab has **none** of them. A SpliceReel reel structurally is:

- A borrowed trailer (up to 60s of a 120-180s original — ~30-50% of source)
- Its original audio dominant (source_audio_duck_db=-6, music_bed_db=-20)
- A music bed under it
- A hook overlay text

That is a re-upload with a caption. There is no transformation, no analysis layer, no original audio content. The precedent (Screen Culture, KH Studio — permanently terminated Dec 2025 for blending copyrighted footage with AI material under spam + misleading-metadata policies following the Disney → Google cease-and-desist) is directly applicable.

**Findings this strengthens:**
- F-QB-0701 SpliceReel copyright risk profile
- Section 1.3 exposure grading for movies + anime
- V3 memory-note about the audio-config-changes-before-claim-wire-existed sequencing

## Different policies, different enforcement paths

| Concern | Policy | Enforcement | Reversibility |
|---------|--------|-------------|---------------|
| Inauthentic content | YouTube spam / AI-content | Content flag → manual review → strike | High (edit + resubmit) |
| Copyright — Content ID match | Rights holder claim | Automated: mute, region-restrict, or claim revenue | Medium (dispute) |
| Copyright — DMCA takedown | Rights holder counter-notice | Automated removal + strike | Low (counter-notice → 10-day + reinstatement OR permanent) |
| Copyright — repeat-infringer | Platform TOS | Channel termination at 3 strikes / 90 days | None (permanent) |

Findings, gap-matrix rows, and compliance summary that fold these together should be split. Phase 9 remediation items should be tagged with which policy they act on.

## What this changes for QB-FIX-03

- **W0** (audio level) — the exposure this was framed to hedge doesn't respond to level. Withdraw the recommendation.
- **W1** (extent query) — orthogonal to the split; still needed to close the RLS framing.
- **W2** (reattribution) — inauthentic-content finding strength was overstated; several F-QB items become lower value once fetcher fix is live.
- **W3** (SpliceReel decision) — the copyright-exposure half of this split is exactly W3's scope. V1 makes the case stronger, not weaker.

## Explicit tag

Every finding in `.audit/QB-2026-08/phase_*_findings.md` (or equivalent) that references "inauthentic content" and "copyright exposure" in the same paragraph should be re-tagged with which of the four policies above it belongs to. Not applied in this pass — noted as a follow-up for whoever runs the next Phase 9 refresh.
