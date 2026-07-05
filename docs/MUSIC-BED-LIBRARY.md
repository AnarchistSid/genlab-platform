# Music Bed Library — Setup Guide

The audio replacer (PR 7 of intelligent transformation sprint) mixes royalty-free music beds over source audio at render time. This document explains how to seed the local music library that the module reads from.

## Directory layout

Each niche has its own music library scoped under its niche root:

```
BlackboxBrief/
  assets/
    music_beds/
      electronic/
        track_01.mp3
        track_02.mp3
        ...
      ambient_tech/
        track_01.mp3
        ...
      focused/
        ...
      upbeat/
      cinematic/
      contemplative/
      tech_hype/

CriticalRush/niches/gaming/
  assets/
    music_beds/
      energetic/
      hype/
      adrenaline/
      intense/
      epic/
      aggressive/

ClutchWire/
  assets/
    music_beds/
      epic/
      victorious/
      driving/
      uplifting/
      cinematic_sport/
      hype/

SpliceReel/
  assets/
    music_beds/
      cinematic/
      dramatic/
      trailer/
      epic/
      mysterious/
      romantic/

FrameDrift/
  assets/
    music_beds/
      dramatic/
      orchestral/
      emotional/
      epic_battle/
      whimsical/
      ethereal/
```

The mood-tag directory names must match the `music_mood.moods` list in that niche's `config/visuals.yaml` (registered as bandit arms in PR 3 #667).

## Where to source tracks

### Pixabay Music (recommended, free commercial license, no attribution)
- URL: <https://pixabay.com/music/>
- ~30,000 tracks under CC0-style Pixabay License
- Filter by mood/genre in the UI, download tracks matching each mood tag
- **License**: free for commercial use, no attribution required

### YouTube Audio Library (free, YouTube-native)
- URL: <https://studio.youtube.com/> → Audio Library
- ~5,000 pre-cleared tracks
- Requires YouTube Studio access

### Uppbeat (freemium, 10 downloads/month on free tier)
- URL: <https://uppbeat.io/music>
- Free tier caps at 10 downloads/mo per account
- Higher-quality curation but rate-limited

## Recommended catalog size per mood

- **Minimum for viable variety**: 3-5 tracks per mood
- **Recommended**: 8-15 tracks per mood (avoids operator-noticeable repetition on daily publishes)
- **Sweet spot**: 10 tracks × 6-7 moods per niche = ~65 tracks per niche

At 3-5 minute average track length and MP3 128 kbps, that's roughly **300-500 MB per niche** on disk — fits comfortably on the 40 GB Hetzner VPS.

## Supported file extensions

`.mp3`, `.m4a`, `.wav`, `.ogg`, `.opus`

MP3 is preferred (widest browser/player support, smallest file size at acceptable quality). No transcoding needed on our side — FFmpeg reads all these directly.

## Selection behavior

Within a mood directory, the audio replacer picks one file **uniformly at random per reel**. The transformation bandit selects the mood tag (learned per niche); within-mood variety happens automatically.

If you want to weight tracks (e.g., prefer newer additions), rename files with a prefix like `01_track_a.mp3`, `02_track_b.mp3` — but note the current implementation still picks uniformly at random. A weighted-selection extension would need code changes.

## What happens if the library isn't seeded

The `replace_audio_for_reel()` function returns `False` when:
- No `assets/music_beds/` directory exists
- The picked mood tag has no subdirectory
- The mood directory is empty
- No files match the supported extensions

The render pipeline (once wired in PR 15) treats `False` as "skip audio replacement for this reel" — the reel still ships with its original audio. This means:

- **Safe rollout**: shipping PR 7 with empty libraries doesn't break rendering
- **Per-niche activation**: seed one niche's library first, verify, then expand
- **Per-mood incremental**: bandit-picked moods without library material get skipped, but other moods still work

## Verification

After seeding, verify with:

```bash
find BlackboxBrief/assets/music_beds -name "*.mp3" -o -name "*.m4a" | wc -l
# Expected: 30-100 for a reasonably seeded BB library
```

The audio replacer's file discovery is validated by 7 tests in `genlab-core/tests/media/test_audio_replacer.py::TestFindMusicBed`.

## `.gitignore`

Music files should NOT be committed to the repo — they're bulky, potentially license-tracked. Add to `.gitignore`:

```
*/assets/music_beds/*/*.mp3
*/assets/music_beds/*/*.m4a
*/assets/music_beds/*/*.wav
*/assets/music_beds/*/*.ogg
*/assets/music_beds/*/*.opus
!*/assets/music_beds/**/README.md
```

But commit an `assets/music_beds/README.md` documenting what should live in that directory tree.
