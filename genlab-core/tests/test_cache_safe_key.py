"""A cache key is a filename, and a rejected key is a cache that never caches.

`yt_search_anime_anime fight scene new` was refused by the key validator on
every anime fire. The rejection logged a warning nobody read, the 6-hour TTL
never applied, and each keyword search re-paid its 100 quota units every run.
At `_max_searches=6` that is 600 units a day for one niche.
"""

from __future__ import annotations

import re

from genlab_core.cache.disk_cache import _SAFE_KEY, Cache


def test_a_query_with_spaces_produces_a_valid_key():
    k = Cache.safe_key("yt_search", "anime", "anime fight scene new")
    assert _SAFE_KEY.match(k), k


def test_the_slug_stays_greppable():
    """The log line has to remain useful — a bare hash is not."""
    k = Cache.safe_key("yt_search", "anime", "anime fight scene new")
    assert k.startswith("yt_search_anime_anime_fight_scene_new")


def test_queries_that_slugify_alike_stay_distinct():
    """Double space, punctuation, case — all collapse under a slug alone."""
    a = Cache.safe_key("yt_search", "anime", "anime fight scene new")
    b = Cache.safe_key("yt_search", "anime", "anime fight scene  new")
    c = Cache.safe_key("yt_search", "anime", "anime/fight/scene/new")
    assert len({a, b, c}) == 3, (a, b, c)


def test_awkward_inputs_still_yield_a_valid_key():
    for q in ("", "   ", "///", "アニメ 戦闘", "a" * 400, "..", "../../etc/passwd"):
        k = Cache.safe_key("yt_search", "anime", q)
        assert _SAFE_KEY.match(k), (q, k)
        assert ".." not in k and "/" not in k


def test_it_is_stable_across_calls():
    """An unstable key is a permanent miss, which is the bug in another form."""
    q = "anime pv 2026"
    assert Cache.safe_key("yt_search", "anime", q) == Cache.safe_key("yt_search", "anime", q)


def test_the_fetcher_uses_it():
    import inspect

    from genlab_core.media import trending_video_fetcher as T

    src = inspect.getsource(T)
    assert "safe_key(" in src
    assert not re.search(r'cache_key = f"yt_search_\{niche_id\}_\{query\}"', src), (
        "the raw-text key is back"
    )
