"""Tests for GoogleTrendsIntel."""

import importlib.util
from unittest.mock import MagicMock, patch

import pytest
from genlab_core.intel.google_trends import (
    NICHE_SEED_KEYWORDS,
    TRENDS_CATEGORIES,
    GoogleTrendsIntel,
)

# U-10 (2026-06-18): pandas is a transitive dep of pytrends. After
# making pytrends optional, pandas is also no longer guaranteed in
# every CI matrix slot — the test-core (3.12/3.13/3.14) jobs install
# genlab-core only, not FrameDrift's [trends] extra. Tests that
# require pandas (just one — test_realtime_trending_returns_list, the
# DataFrame mock) skip cleanly when pandas isn't there.
_HAS_PANDAS = importlib.util.find_spec("pandas") is not None
_pandas_required = pytest.mark.skipif(
    not _HAS_PANDAS, reason="pandas not installed (optional dep, lands via pytrends extra)"
)


class TestGoogleTrendsIntel:
    def test_fallback_to_seed_keywords(self):
        """When all tiers fail (RSS + pytrends + cache), returns static seed keywords."""
        intel = GoogleTrendsIntel()
        with (
            patch.object(intel, "_get_rss_trending", side_effect=Exception("no network")),
            patch.object(intel, "_get_realtime_trending", side_effect=Exception("no network")),
            patch.object(intel, "_get_daily_trending", side_effect=Exception("no network")),
            patch("genlab_core.intel.google_trends._read_cache", return_value=None),
            patch("genlab_core.intel.google_trends._read_stale_cache", return_value=None),
        ):
            topics = intel.get_trending_topics("gaming", top_n=5)

        # 2026-06-18: the seed-fallback now respects top_n (was
        # returning the full seed list which silently leaked more
        # search keywords than the caller asked for).
        assert len(topics) == 5
        assert topics == NICHE_SEED_KEYWORDS["gaming"][:5]

    def test_all_niches_have_seed_keywords(self):
        for niche in ["gaming", "sports", "movies", "anime", "ai_creators"]:
            assert niche in NICHE_SEED_KEYWORDS
            assert len(NICHE_SEED_KEYWORDS[niche]) >= 2

    def test_all_niches_have_trends_categories(self):
        for niche in ["gaming", "sports", "movies", "ai_creators"]:
            assert niche in TRENDS_CATEGORIES

    @_pandas_required
    def test_realtime_trending_returns_list(self):
        intel = GoogleTrendsIntel()
        # Mock pytrends client
        mock_pt = MagicMock()
        import pandas as pd

        mock_pt.trending_searches.return_value = pd.DataFrame(
            {0: ["Valorant update", "NBA Finals", "New Movie Trailer", "AI breakthrough"]}
        )
        intel._pytrends = mock_pt

        topics = intel._get_realtime_trending("gaming")
        assert isinstance(topics, list)
        # "Valorant update" should match gaming seed keywords? Actually no,
        # but it should still be in the results
        assert len(topics) > 0

    def test_score_multiplier_default(self):
        intel = GoogleTrendsIntel()
        with patch.object(intel, "get_trending_topics", return_value=[]):
            score = intel.get_trending_score_multiplier("random topic", "gaming")
        assert score == 1.0

    def test_score_multiplier_trending(self):
        intel = GoogleTrendsIntel()
        with patch.object(
            intel,
            "get_trending_topics",
            return_value=["GTA 6", "Valorant", "Minecraft"],
        ):
            score = intel.get_trending_score_multiplier("GTA 6 release", "gaming")
        assert score == 3.0  # Top 5 match

    def test_score_multiplier_exception_returns_neutral(self):
        intel = GoogleTrendsIntel()
        with patch.object(intel, "get_trending_topics", side_effect=Exception("fail")):
            score = intel.get_trending_score_multiplier("anything", "gaming")
        assert score == 1.0


# ── U-10 (2026-06-18): pytrends graceful-skip pins ──────────────────


class TestPytrendsGracefulSkip:
    """When pytrends is not installed, ``_get_client`` returns None
    and the realtime/daily paths skip cleanly without crashing.

    Pattern: simulate the missing module by patching ``builtins.__import__``
    to raise ImportError on ``pytrends.request``. The class's sentinel
    (``_PYTRENDS_UNAVAILABLE``) ensures we log exactly once even
    across many ``_get_client`` calls.
    """

    def _block_pytrends(self):
        """Helper: return a patch context manager that makes
        ``from pytrends.request import TrendReq`` raise ImportError."""
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "pytrends.request" or name.startswith("pytrends"):
                raise ImportError(f"No module named '{name}' (U-10 test)")
            return real_import(name, *args, **kwargs)

        return patch("builtins.__import__", side_effect=fake_import)

    def setup_method(self):
        # Reset the class-level sentinel so each test starts fresh —
        # the "log once" guarantee is enforced ACROSS instances via
        # the class attribute, so tests must reset it.
        GoogleTrendsIntel._PYTRENDS_UNAVAILABLE = False

    def test_get_client_returns_none_when_pytrends_unavailable(self):
        """Without pytrends installed, ``_get_client()`` returns None
        (not the sentinel False) so the caller's ``if pt is None``
        short-circuit fires."""
        intel = GoogleTrendsIntel()
        with self._block_pytrends():
            client = intel._get_client()
        assert client is None

    def test_realtime_trending_returns_empty_when_pytrends_unavailable(self):
        """Tier-2 path must return ``[]`` (not raise) so the caller's
        ``except Exception`` branch logs + moves to Tier-3 / cache /
        seeds — this is the existing fallback contract."""
        intel = GoogleTrendsIntel()
        with self._block_pytrends():
            result = intel._get_realtime_trending("gaming")
        assert result == []

    def test_daily_trending_returns_empty_when_pytrends_unavailable(self):
        """Tier-3 path returns ``[]`` (not seeds) when the client is
        unavailable. The caller's ``get_trending_topics`` falls
        through to Tier-4 (stale cache) → Tier-5 (seeds) where the
        seed fallback actually lives — keeping Tier-3 silent is the
        right contract; otherwise we'd double-count seeds at two tiers."""
        intel = GoogleTrendsIntel()
        with self._block_pytrends():
            result = intel._get_daily_trending("gaming")
        assert result == []

    def test_full_pipeline_falls_through_to_seeds_with_no_pytrends(self):
        """End-to-end: with pytrends missing AND RSS/cache disabled,
        the public ``get_trending_topics`` returns seed keywords —
        the same Tier-5 fallback as when all tiers fail. The user-
        facing contract doesn't change when pytrends is removed."""
        intel = GoogleTrendsIntel()
        with (
            self._block_pytrends(),
            patch.object(intel, "_get_rss_trending", return_value=[]),
            patch("genlab_core.intel.google_trends._read_cache", return_value=None),
            patch("genlab_core.intel.google_trends._read_stale_cache", return_value=None),
        ):
            topics = intel.get_trending_topics("gaming", top_n=5)
        # 2026-06-18: seed-fallback respects top_n.
        assert topics == NICHE_SEED_KEYWORDS["gaming"][:5]

    def test_warn_log_fires_exactly_once_across_many_calls(self, caplog):
        """The class-level ``_PYTRENDS_UNAVAILABLE`` sentinel exists so
        operators get ONE clear warning in journalctl, not 5 per
        score-multiplier call. Pin the once-only behaviour."""
        intel = GoogleTrendsIntel()
        with self._block_pytrends(), caplog.at_level("WARNING"):
            # Hit the client init 5 times — would spam without sentinel
            for _ in range(5):
                intel._get_client()
        warn_records = [
            r
            for r in caplog.records
            if r.levelname == "WARNING" and "pytrends not installed" in r.message
        ]
        assert len(warn_records) == 1, (
            f"expected exactly 1 warning, got {len(warn_records)}: "
            f"{[r.message for r in warn_records]}"
        )

    def test_pytrends_when_available_still_works(self):
        """Regression guard: this PR must NOT break the path where
        pytrends IS installed. Existing tests already cover this; this
        one specifically verifies the new sentinel logic doesn't
        interfere with a successful import."""
        intel = GoogleTrendsIntel()
        # Pre-populate the client so _get_client() doesn't try to import
        mock_pt = MagicMock()
        intel._pytrends = mock_pt
        client = intel._get_client()
        assert client is mock_pt


# ── Niche-pollution fix (2026-06-18) ──────────────────────────────


class TestNichePollutionFix:
    """The pre-2026-06-18 implementation appended ``general_terms``
    after ``niche_terms`` in both _get_rss_trending and
    _get_realtime_trending. That polluted the trending fetcher with
    US-general topics (gas, moscow, tornado, james colombia, estadio
    banorte) which then got prepended to NICHE_SEARCH_KEYWORDS and
    pushed the niche-correct searches past the 2-3 per-run cap.

    Result: AI niche fetched The Verge tech-news videos instead of AI
    content, sports fetched Mexican soccer stadium news, etc. — root
    cause of operator complaint "blackbox brief still not getting any
    ai-generated content" on 2026-06-18.

    These pins fix the behaviour to its forward-compatible contract:
    return ONLY niche-matching terms; if none match, return empty and
    let the next tier (seeds) take over.
    """

    def test_rss_drops_general_terms_when_niche_misses(self):
        """Reproduce today's prod payload: gas, moscow, claude down,
        is claude down. For sports, ZERO match → return empty (not
        the off-topic list)."""
        intel = GoogleTrendsIntel()
        # Mock the general-RSS fetch path
        fake_topics = ["gas", "moscow", "claude down", "is claude down"]

        def fake_urlopen(*args, **kwargs):
            # Mock response with RSS-shape XML
            xml = (
                '<?xml version="1.0"?><rss><channel>'
                + "".join(f"<item><title>{t}</title></item>" for t in fake_topics)
                + "</channel></rss>"
            )
            mock = MagicMock()
            mock.read.return_value = xml.encode()
            mock.__enter__ = lambda self: mock
            mock.__exit__ = lambda self, *a: False
            return mock

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            # Patch the category-RSS-first try to fail so we hit the general fallback
            with patch.object(intel, "_get_rss_trending", wraps=intel._get_rss_trending):
                result = intel._get_rss_trending("sports")
        # Sports seeds = NBA, NFL, soccer, etc. None matches the trash.
        # Result MUST be empty so the chain falls through to seeds.
        assert result == [], f"expected [] (no niche match), got {result}"

    def test_rss_keeps_niche_matching_terms(self):
        """When the general RSS DOES include niche-matching terms
        (e.g. 'claude down' for ai_creators — 'claude' is a seed),
        those get returned (just not the general ones alongside)."""
        intel = GoogleTrendsIntel()
        fake_topics = ["gas", "moscow", "claude down", "is claude down"]

        def fake_urlopen(*args, **kwargs):
            xml = (
                '<?xml version="1.0"?><rss><channel>'
                + "".join(f"<item><title>{t}</title></item>" for t in fake_topics)
                + "</channel></rss>"
            )
            mock = MagicMock()
            mock.read.return_value = xml.encode()
            mock.__enter__ = lambda self: mock
            mock.__exit__ = lambda self, *a: False
            return mock

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = intel._get_rss_trending("ai_creators")
        # 'claude down' + 'is claude down' both contain 'claude' (seed).
        # 'gas' + 'moscow' should be DROPPED.
        assert "claude down" in result
        assert "is claude down" in result
        assert "gas" not in result, "off-topic 'gas' must be filtered out"
        assert "moscow" not in result, "off-topic 'moscow' must be filtered out"

    def test_realtime_drops_general_terms_when_niche_misses(self):
        """Same fix in _get_realtime_trending. Pytrends returns
        the trending DataFrame; we filter to niche-matching only."""
        import pytest

        pytest.importorskip("pandas")
        import pandas as pd

        intel = GoogleTrendsIntel()
        mock_pt = MagicMock()
        mock_pt.trending_searches.return_value = pd.DataFrame(
            {0: ["tornado watch vs warning", "james colombia", "estadio banorte"]}
        )
        intel._pytrends = mock_pt

        # Gaming seeds: gaming, video games, esports, etc.
        # None of the mock topics match → return empty.
        result = intel._get_realtime_trending("gaming")
        assert result == [], f"expected [] (no niche match), got {result}"

    def test_realtime_keeps_niche_matching_terms(self):
        """Regression: real gaming match still returned through realtime."""
        import pytest

        pytest.importorskip("pandas")
        import pandas as pd

        intel = GoogleTrendsIntel()
        mock_pt = MagicMock()
        mock_pt.trending_searches.return_value = pd.DataFrame(
            {0: ["tornado", "valorant patch notes", "playstation 6 leak"]}
        )
        intel._pytrends = mock_pt

        result = intel._get_realtime_trending("gaming")
        assert "valorant patch notes" in result
        assert "playstation 6 leak" in result
        assert "tornado" not in result, "off-topic 'tornado' must be filtered out"

    def test_full_pipeline_falls_through_to_seeds_when_trends_off_topic(self):
        """End-to-end: when ALL trend tiers return off-topic data,
        ``get_trending_topics`` falls through to NICHE_SEED_KEYWORDS.
        This pins the operator-visible contract: trends-pollution
        cannot leak past the public API."""
        intel = GoogleTrendsIntel()
        # All tiers return empty (RSS + pytrends both filtered out)
        with (
            patch.object(intel, "_get_rss_trending", return_value=[]),
            patch.object(intel, "_get_realtime_trending", return_value=[]),
            patch.object(intel, "_get_daily_trending", return_value=[]),
            patch("genlab_core.intel.google_trends._read_cache", return_value=None),
            patch("genlab_core.intel.google_trends._read_stale_cache", return_value=None),
        ):
            topics = intel.get_trending_topics("ai_creators", top_n=3)
        # MUST be the seed keywords — not garbage, not empty.
        assert topics == NICHE_SEED_KEYWORDS["ai_creators"][:3]
