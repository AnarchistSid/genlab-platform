"""Pin the 2026-07-22 content_type_showcase niche-based fallback.

History: `build_content_context` at `linucb.py:766` calls
`_extract_content_type(story)` which looks in 3 places on the story dict:
top-level `content_type`, `gallery_metadata.content_type`, and
`source_config.content_type`. Agent 3's comprehensive audit found that
NO fetcher stage in the codebase actually WRITES any of those fields —
so `_extract_content_type` returned `""` on every story, and the LinUCB
`content_type_showcase` binary feature (line 784: `1.0 if content_type
== "showcase" else 0.0`) was a CONSTANT 0.0 across all training data.

A constant feature is a dead feature — LinUCB can't learn from it, but
the dimension still takes up compute in every arm's covariance-matrix
update. Silent training-data quality bug affecting every bandit fire
since the 13-D bump.

Fix: when no source-level content_type is set AND the niche is
ai_creators, default to "showcase". Justification: BlackboxBrief
`_fetch.py:252` filters sources.yaml for `content_type: showcase` and
routes every ai_creators story through the creator_only_mode gate. So
every surviving ai_creators story is definitionally showcase content —
we can backfill the signal at the LinUCB boundary without touching 5+
fetcher `to_story()` shapes.

Other 4 niches (movies/sports/gaming/anime) are NOT showcase content;
they're highlight/trailer/clip/gameplay. Feature correctly stays 0.0
for them.
"""

from __future__ import annotations

import numpy as np

from genlab_core.learning.linucb import build_content_context


# Content-type-showcase feature lives at index 12 in the v1 13-D vector.
# See linucb.py:703 for the layout comment.
_CONTENT_TYPE_SHOWCASE_INDEX = 12


def _feat(vec: np.ndarray) -> float:
    return float(vec[_CONTENT_TYPE_SHOWCASE_INDEX])


class TestContentTypeShowcaseFallback:
    def test_ai_creators_default_is_showcase(self) -> None:
        """When ai_creators story has no content_type set, the LinUCB
        feature MUST be 1.0 (showcase). Without this fallback the feature
        would be 0.0 → dead dimension for the entire ai_creators niche."""
        story = {"title": "Some AI creator story"}
        vec = build_content_context(story, niche_id="ai_creators")
        assert _feat(vec) == 1.0, (
            f"ai_creators default MUST be showcase (feature=1.0). "
            f"Got {_feat(vec)}. Constant-zero feature = dead LinUCB dimension."
        )

    def test_ai_creators_explicit_content_type_wins(self) -> None:
        """Explicit `content_type` on the story dict overrides the fallback.
        If a future fetcher stage properly sets content_type, it must not
        be silently overwritten by the niche default."""
        story = {"title": "x", "content_type": "explainer"}
        vec = build_content_context(story, niche_id="ai_creators")
        # "explainer" != "showcase" → feature stays 0.0
        assert _feat(vec) == 0.0

    def test_gaming_default_is_not_showcase(self) -> None:
        """Gaming is highlight/gameplay, not showcase. Feature MUST stay
        0.0 for gaming (and any non-ai_creators niche) when no explicit
        content_type is set."""
        story = {"title": "trending gameplay"}
        vec = build_content_context(story, niche_id="gaming")
        assert _feat(vec) == 0.0

    def test_movies_default_is_not_showcase(self) -> None:
        """Movies is trailer content. Feature MUST stay 0.0."""
        story = {"title": "avengers trailer"}
        vec = build_content_context(story, niche_id="movies")
        assert _feat(vec) == 0.0

    def test_sports_default_is_not_showcase(self) -> None:
        """Sports is highlight content. Feature MUST stay 0.0."""
        story = {"title": "top play of the day"}
        vec = build_content_context(story, niche_id="sports")
        assert _feat(vec) == 0.0

    def test_anime_default_is_not_showcase(self) -> None:
        """Anime is clip content. Feature MUST stay 0.0."""
        story = {"title": "top anime moments"}
        vec = build_content_context(story, niche_id="anime")
        assert _feat(vec) == 0.0

    def test_gaming_with_explicit_showcase_wins(self) -> None:
        """Symmetric to ai_creators — if a story genuinely IS marked
        showcase in the source config, the feature MUST reflect it
        regardless of niche."""
        story = {"title": "x", "content_type": "showcase"}
        vec = build_content_context(story, niche_id="gaming")
        assert _feat(vec) == 1.0

    def test_source_config_showcase_propagates(self) -> None:
        """The 3-field extraction chain includes source_config.content_type.
        Test that path still works for any niche."""
        story = {
            "title": "x",
            "source_config": {"content_type": "showcase"},
        }
        vec = build_content_context(story, niche_id="movies")
        assert _feat(vec) == 1.0
