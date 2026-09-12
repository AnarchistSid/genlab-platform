# VOICE-03 — TTS naturalness bake-off
**2026-09-12 · one niche (ai_creators) · one script · six providers · all at −14 LUFS**

Aditya's verdict on the Inworld design set was **too robotic**. This tests whether
that is the provider's ceiling or the way it was driven — so the same Inworld
voice appears twice, once as shipped and once with explicit natural-language
steering the design step never used.

**Pick a PROVIDER, not a voice.** Per-niche voices come after.

## Play them
```bash
cd ~/GenLab/.audit/retention/voice-bakeoff-2026-09-12/norm
for f in *.m4a; do echo "== $f"; afplay "$f"; done
```

## The script — identical for every sample

> GPT-Live-1 in the API isn't just a feature drop — it's a structural shift in what voice agents can do. Full-duplex audio means the model listens and speaks simultaneously, which eliminates the awkward turn-taking that made previous voice APIs feel robotic. We're watching the gap between ChatGPT's consumer experience and developer tooling

(339 chars, 52 words — a real `narration_script`
from an ai_creators blueprint, not a tongue-twister.)

## Before you listen — two biases to discount

1. **`grok_tts` normalised to −15.70, >1 LU short of −14.** It will sound quieter.
   That is the normalisation hitting the −1 dBTP ceiling on a wide-dynamic source
   (the FIX-LOUD mechanism), not the voice. Every other sample is on target.
2. **`dramabox` is a different kind of thing.** It is driven by a scene prompt
   describing the speaker, not by a voice ID, so it cannot be pinned to a stable
   per-niche voice today. Judge it on delivery, but note the constraint.

## The sheet

| # | provider | file | dur | LUFS | on target | custom voices | price | terms | verdict (yours) |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Inworld (current production voice) | `control_inworld_default.m4a` | 21.68s | -14.47 | yes | design + publish | $5/M chars | CLEARED owned channels |  |
| 2 | Inworld + natural-language steering | `inworld_steered.m4a` | 28.03s | -14.42 | yes | design + publish | $5/M chars | CLEARED owned channels |  |
| 3 | MiniMax Speech 2.8 HD | `minimax_speech_2_8_hd.m4a` | 23.95s | -14.9 | yes | custom voice_id supported | $0.0001/char = $100/M | eval_only — TERMS NOT READ |  |
| 4 | DramaBox (scene-prompt driven) | `dramabox.m4a` | 24.01s | -14.05 | yes | prompt-described speaker, no stable ID | no published pricing | eval_only — TERMS NOT READ |  |
| 5 | xAI Grok TTS | `grok_tts.m4a` | 23.16s | -15.7 | **no — quieter** | stock voices only (eve/ara/rex/…) | $15/M chars | eval_only — TERMS NOT READ |  |
| 6 | Dia TTS (fal.ai) | `dia_tts.m4a` | 30.0s | -14.92 | yes | voice clone via ref_audio | $0.04/K chars = $40/M | eval_only — TERMS NOT READ |  |

## Not in the sheet, and why

| provider | what happened |
|---|---|
| `infsh/higgs-audio` | **Provider-side failure**: `setup failed: HiggsAudioTokenizer.__init__() got an unexpected keyword argument`. Not our input — the app could not start. |
| `heygen/text-to-speech` | **Could not run**: `voice_id` is a REQUIRED field and the app's `voices` function returned nothing, so there was no valid ID to pass. Not skipped silently; it needs a voice ID sourced another way. |

Reported as gaps rather than omitted. Either can be added if none of the six clears the bar.

## Terms status — important

**Only Inworld is cleared.** The four alternatives are `eval_only`: their terms have
not been read, so nothing from them may ship until they are. That read is cheap and
comes *after* a pick — no point reading four sets of terms to adopt one provider.

And the ElevenLabs set Aditya liked earlier is **reference-only**: it was generated
on a free account, and the ElevenLabs ToS restricts Free Users to non-commercial
use. Those files cannot ship and cannot be cloned onto another provider. The
prompts survive — regenerating on a **$6/month Starter plan** is clean and needs no
re-listen.

## If nothing here clears the bar

The honest options are: **$6/month ElevenLabs Starter** (the voices he already
endorsed, regenerated under a commercial licence), or accept the best available and
revisit. No provider gets adopted without his endorsement.
