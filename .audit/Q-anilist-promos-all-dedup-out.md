# Q — AniList promo candidates: 20 in, 0 surviving dedup

**Filed** 2026-09-21, ANIME-12 §5.

The AniList path can supply upcoming-premiere candidates for anime, and the
data is good — reel B's card is built entirely from it (studio, season,
premiere date, genres, following), and every field on screen is exact
because the card is drawn locally from those strings.

But of 20 AniList promo candidates fetched, 0 survived dedup into blueprints.
Worth knowing WHICH dedup layer rejects them before adding AniList as a
source: video_id dedup cannot apply (there is no video), so it is either the
title dedup or the PUBLISHED-skip. If it is title dedup against previously
seen show names, then a show can never be covered twice — which is wrong for
a premiere (announcement, then premiere day).

Cheap to answer: log the rejecting layer per candidate for one run.
