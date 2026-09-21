# Q — no relevance signal on the music bed

**Filed** 2026-09-21, ANIME-12 §5.

Both reels rendered with NO bed (`bed=None`) — narration only. The mix path
supports one (`AudioPlan.bed`, ducked by a fixed gain, `music_duck_db` in the
kit) and it is wired and tested, but nothing chooses a track.

Before wiring one: there is no relevance signal connecting a track to a
story. A romance premiere and a fight-tournament announcement should not get
the same bed, and picking at random is worse than none — a wrong bed actively
misreads the reel, where silence merely omits.

Wants a kit-level mood field per story shape, or a mood classifier over the
script, before the selector is worth building.
