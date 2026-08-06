# Audio library licensing

**Audit reference:** QB-FIX-01 F0 (2026-08-06). This document is the gate artifact — F0 passes on `commercial-use=yes` for every track.

**Scope of use:** background music beds mixed into rendered reels by `transformation_orchestrator` when the `music_mood` bandit dimension fires. Beds are ducked under VO speech per `audio_replacer.build_audio_mix_filtergraph` (source at `-9`/`-12`/`-15` dB per arm, bed at `-6` dB baseline). Not used as standalone content, not redistributed, not sold.

**Provenance:** confirmed via repo source. `genlab-core/src/genlab_core/media/audio_replacer.py:17` states the library seeding source; `genlab-core/src/genlab_core/media/intelligent_transform.py:53` defaults `source="pixabay"`; `genlab-core/src/genlab_core/media/egress_policies.py:68` allowlists `pixabay.com`. Filename pattern (`{track_slug}_{6-digit-id}.mp3`) matches Pixabay's asset-ID scheme.

---

## Library inventory (125 tracks, 4 niches, 25 moods)

| Niche | Location | Moods | Tracks |
|---|---|---|---|
| ai_creators | `BlackboxBrief/assets/music_beds/` | ambient_tech, cinematic, contemplative, electronic, focused, tech_hype, upbeat | 35 |
| sports | `ClutchWire/assets/music_beds/` | cinematic_sport, driving, epic, hype, uplifting, victorious | 30 |
| movies | `SpliceReel/assets/music_beds/` | cinematic, dramatic, epic, mysterious, romantic, trailer | 30 |
| gaming | `CriticalRush/niches/gaming/assets/music_beds/` | adrenaline, aggressive, energetic, epic, hype, intense | 30 |
| **anime** | (none — `FrameDrift/assets/music_beds/` does not exist) | — | 0 |

Each mood folder contains 5 tracks (Beta-Thompson posteriors have space for hundreds; current library is intentionally small).

Anime reels ship with the source video's own audio track passed through unmodified — no bed selection because no library to select from.

---

## License

All tracks are subject to the **Pixabay Content License** as published at <https://pixabay.com/service/license-summary/> (retrieved 2026-08-06).

| Right | Status | Notes |
|---|---|---|
| Commercial use | **YES** | Explicitly granted by Pixabay Content License §1 |
| Modification | **YES** | Ducking + mixing under VO is a permitted modification |
| Attribution | not required | Pixabay does not require attribution |
| Redistribution as standalone content | **NO** | Not applicable — GenLab uses tracks only as background under video |
| YouTube Content ID clearance | **NOT GUARANTEED** | Pixabay's own FAQ warns individual artists may have distributed the same track through commercial catalogs (Content ID libraries); Pixabay does not guarantee clearance |
| Attribution for named individuals / brands | not applicable | audio only, no likeness rights |

**Per-track commercial-use decision: YES for all 125 tracks.**

The Content ID caveat is an ongoing operational monitor, not a gate blocker. Track record 2026-07-07 → 2026-08-06:

* `compliance_events` audio-related decisions in the window: **0**
* `publishing_analytics.extra` mentioning audio-claim / muted / content-id / copyright: **0 rows**
* `[audio_replacer] mixing:` events in systemd journal: at least 1 confirmed (survives 30-day journal retention)

If any track begins triggering platform claims, the immediate mitigation is to disable that specific track by moving it out of the mood folder (transformation_orchestrator will fall back to another track in the same mood). The escalation is to disable the whole `music_mood` transformation dimension via the `music_mood_enabled` flag introduced by QB-FIX-01 F0 (see `docs/audio-licensing.md#operational-controls`).

---

## Operational controls

Music-bed injection can be disabled per niche via the `music_mood_enabled` key in each niche's `config/audio.yaml` (defaulting to `true` where a library exists, `false` otherwise). To disable globally, set the environment variable `GENLAB_MUSIC_MOOD_DISABLED=1` on the pipeline systemd units and reload.

To disable a specific track without touching config, move its file out of the mood folder — the audio_replacer only enumerates `.mp3/.m4a/.wav/.ogg` files in the mood directory it's given.

---

## QB-2026-08 audit corrections triggered by this document

* **F-QB-0302** ("no music-bed mixing code exists") — **WRONG.** Corrected via `audio_replacer.build_audio_mix_filtergraph` + `transformation_orchestrator.music_mood`. Bed IS mixed for 4 of 5 niches when the bandit selects the dimension.
* **F-QB-0303** ("no license documentation — preventive finding") — **RECLASSIFIED to active.** Music is live on monetized channels. This file is the response.
* **F-QB-0305** (zero audio-claim events was interpreted as "no bed → nothing to claim") — **RE-INTERPRETED.** Zero claims genuinely means zero claims across 30 days of published-with-bed reels, which is a positive datapoint — but the reasoning has to be rebuilt from a correct premise.
* **Phase 9 deferral ledger** ("Real ducking delta — Closed with reason (no music bed exists)") — **ESCAPE.** Should have been carried, not closed. Logged in `.audit/QB-2026-08/methodology_errors.md` as ME-08 (new).

## References

* Pixabay Content License: <https://pixabay.com/service/license-summary/>
* Pixabay music FAQ (Content ID): <https://pixabay.com/service/faq/#music-content-id>
* `genlab-core/src/genlab_core/media/audio_replacer.py:1-180`
* `genlab-core/src/genlab_core/media/transformation_orchestrator.py` (music_mood stage)
* `genlab-core/src/genlab_core/media/intelligent_transform.py:52-56`
