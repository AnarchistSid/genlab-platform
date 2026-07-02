"""Intervention 5 consumer-wire pins (2026-07-02).

The one-line adopter in ``TrendingVideoFetcher``'s stage function
calls ``reorder_by_anticipation(extra_keywords, niche_id)`` after
Google Trends returns the keyword list. Pins:

* Flag off → input order preserved (identity function)
* Flag on with valid artifact → anticipated topics surface first

Both properties are already tested at the module level in
``test_trend_anticipation.py``; this file pins the WIRE — that
``reorder_by_anticipation`` is actually called at the pipeline
call site with the right args.
"""

from __future__ import annotations

import inspect


def test_wire_call_present_in_fetcher_source():
    """Source pin — grep the trending_video_fetcher for the wire
    string. Losing this line is losing the entire Intervention 5
    steering path; catching it at test-time is cheaper than
    catching it at deploy-time."""
    from genlab_core.media import trending_video_fetcher

    src = inspect.getsource(trending_video_fetcher)
    assert "reorder_by_anticipation" in src, (
        "trending_video_fetcher must call reorder_by_anticipation on "
        "extra_keywords — dropping this line reverts to the pre-"
        "Intervention-5 behaviour where topics are used in Google-"
        "Trends order regardless of anticipation ranking."
    )
    assert "extra_keywords, niche_id" in src, (
        "The call must pass (extra_keywords, niche_id) so the "
        "anticipation module can look up the right day's artifact "
        "per niche."
    )
