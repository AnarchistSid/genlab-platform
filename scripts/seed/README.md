# Pixabay Music Seed — manifest + regeneration

The intelligent-transformation orchestrator's `music_mood` dimension
reads from a per-niche music library at
`<niche_root>/assets/music_beds/<mood>/*.mp3`. This directory holds the
seed manifest that populates that library.

## Files

| File | Purpose |
|---|---|
| `pixabay_music_manifest.json` | Versioned mapping of niche → mood → tracks (CDN URL + title + id). Ship-committed so seeding is reproducible. |
| (this README) | How to re-generate the manifest when tracks feel stale. |

## Downloading from the manifest

```bash
# Idempotent — skips files already present on disk
uv run python3 scripts/seed_pixabay_music.py

# Dry-run first if you're unsure
uv run python3 scripts/seed_pixabay_music.py --dry-run
```

See the script's `--help` for other options (custom manifest, project
root override).

## Re-generating the manifest (when tracks feel stale)

The manifest generator is **out-of-band** because Pixabay.com is fronted
by Cloudflare — plain `curl` returns HTTP 403. The reliable
regeneration path is a Playwright browser session (via the Playwright
MCP tool from an interactive Claude Code session).

The generator flow that produced the current manifest was:

1. **Open a Pixabay tab in a Playwright browser** (bypasses Cloudflare
   via real browser session cookies).
2. **For each (niche, mood, query) pair** in the niche-tuned query map
   below, fetch `https://pixabay.com/music/search/<encoded-query>/`
   and extract track IDs via regex
   `/\/music\/[a-z]+-[a-z0-9-]+-(\d+)\//gi`.
3. **For each track ID**, fetch `https://pixabay.com/music/x-<id>/`
   (the slug-agnostic detail URL) and extract the CDN download URL via
   regex `/https:\/\/cdn\.pixabay\.com\/download\/audio\/\d{4}\/\d{2}\/\d{2}\/audio_[a-zA-Z0-9]+\.mp3/`.
4. **Dedupe within-niche** so no track appears in two mood dirs — the
   bandit needs distinct arms per mood.
5. **Throttle** — 1.5s between search fetches, 0.8s between track
   detail fetches. Cloudflare rate-limits at ~14 rapid requests.

### Niche-tuned query map (topical relevance beats mood/genre paths)

`/music/search/mood/<mood>/` returns the same 5 featured tracks for any
mood — a diversity collapse. Free-text search returns topically-relevant
tracks per (niche, mood) pair:

```python
NICHE_MOODS = {
    "ai_creators": {
        "electronic": "tech electronic",
        "ambient_tech": "cyber ambient",
        "focused": "lofi study",
        "upbeat": "upbeat corporate",
        "cinematic": "corporate cinematic",
        "contemplative": "ambient thoughtful",
        "tech_hype": "tech hype",
    },
    "gaming": {
        "energetic": "gaming energetic",
        "hype": "hype trap",
        "adrenaline": "adrenaline action",
        "intense": "intense trailer",
        "epic": "epic battle",
        "aggressive": "aggressive trap",
    },
    "sports": {
        "epic": "epic sports",
        "victorious": "victory celebration",
        "driving": "driving rock",
        "uplifting": "uplifting sports",
        "cinematic_sport": "sports cinematic",
        "hype": "sports hype",
    },
    "movies": {
        "cinematic": "cinematic orchestral",
        "dramatic": "dramatic thriller",
        "trailer": "trailer epic",
        "epic": "epic orchestral",
        "mysterious": "mysterious dark",
        "romantic": "romantic piano",
    },
}
```

See `[[pixabay-music-seed-2026-07-05]]` in Claude Code memory for the
full pattern documentation and lessons-learned (mood/genre collapse,
Cloudflare 429 behavior, sandbox sleep gotcha).

## What NOT to change without discussion

- **Never remove a mood from an activated niche** — the visuals.yaml
  bandit config lists that mood as an arm. Removing the seed dir
  silently skips the arm, distorting the bandit.
- **Never add moods without regenerating the manifest AND updating
  each activated niche's `visuals.yaml.intelligent_transform.dimensions.music_mood.moods`**
  — mismatch = silent skip.
- **FrameDrift/anime is deliberately absent** from `NICHE_ROOTS` in
  the seed script. Adding it here without flipping
  `visuals.yaml.intelligent_transform.enabled: true` on FrameDrift
  wastes disk without gaining anything.
