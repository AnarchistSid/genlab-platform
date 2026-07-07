"""Pin cross-niche transfer for product arms (L3 PR 6).

Mirrors ``test_cross_niche_transformation_arms.py`` — the same testing
pattern applied to the new product routing. Confirms:

  1. ``extract_product_suffix`` returns the ``product:<slug>``
     namespaced key from ``product__<slug>`` arm_ids, None for
     everything else.
  2. ``extract_prior_key`` routes product arms to
     ``extract_product_suffix`` after checking style + transformation.
  3. ``compute_transferred_priors`` produces a Beta prior for a
     product when ≥ 2 niches have ≥ ``_MIN_OBS_PER_NICHE`` observations.
  4. ``get_transferred_prior`` returns the persisted prior for
     product arms when the flag is on.

The extension exists because 113 product arms × 5 niches means every
new (niche, product) arm starts at Beta(1,1). Same product across
niches shares the physical event (a PS5 click is a PS5 click); the
cross-niche prior warms up a cold arm from what other niches learned
about the same product.
"""

from __future__ import annotations

from genlab_core.learning.cross_niche_transfer import (
    compute_transferred_priors,
    extract_prior_key,
    extract_product_suffix,
    extract_style_suffix,
    get_transferred_prior,
    persist_priors,
)


class TestExtractProductSuffix:
    """The extractor MUST namespace with ``product:`` (colon prefix) so
    product priors never collide with style names."""

    def test_basic_slug(self):
        assert extract_product_suffix("product__ps5-console") == "product:ps5-console"

    def test_multi_word_slug(self):
        assert (
            extract_product_suffix("product__xbox-game-pass-ultimate")
            == "product:xbox-game-pass-ultimate"
        )

    def test_slug_with_numbers(self):
        assert extract_product_suffix("product__nvidia-rtx-4090") == "product:nvidia-rtx-4090"

    def test_seasonal_slug(self):
        assert (
            extract_product_suffix("product__prime-day-ps5-console")
            == "product:prime-day-ps5-console"
        )

    def test_style_arm_returns_none(self):
        assert extract_product_suffix("style:gaming:revelation") is None
        assert extract_product_suffix("style:gaming:ps5-console") is None

    def test_transformation_arm_returns_none(self):
        assert extract_product_suffix("transform__music_mood__cinematic") is None

    def test_source_arm_returns_none(self):
        assert extract_product_suffix("source:youtube_trending") is None

    def test_hour_arm_returns_none(self):
        assert extract_product_suffix("hour:12:instagram:gaming") is None

    def test_empty_slug_returns_none(self):
        """``product__`` with no slug is malformed — don't emit a key."""
        assert extract_product_suffix("product__") is None

    def test_not_product_class_returns_none(self):
        assert extract_product_suffix("randomstring") is None
        assert extract_product_suffix("") is None


class TestExtractPriorKeyRouting:
    """The unified extractor must route to product AFTER style and
    transformation checks."""

    def test_style_arm_still_routes_to_style(self):
        """Style arms take precedence — no accidental routing to product."""
        assert extract_prior_key("style:gaming:revelation") == "revelation"

    def test_transformation_arm_still_routes_to_transformation(self):
        assert extract_prior_key("transform__music_mood__cinematic") == "music_mood__cinematic"

    def test_product_arm_routes_to_product_extractor(self):
        assert extract_prior_key("product__nvidia-rtx-4090") == "product:nvidia-rtx-4090"

    def test_unknown_arm_returns_none(self):
        assert extract_prior_key("source:youtube_trending") is None
        assert extract_prior_key("hour:12:instagram:gaming") is None


class TestNamespaceIsolation:
    """The product namespace prefix prevents collision with style
    names. A style named after a product slug must NOT share the
    product's cross-niche prior (different reward semantics)."""

    def test_product_and_hypothetical_style_dont_collide(self):
        """If a style ever gets named ``ps5-console`` (unlikely but
        possible), its prior key would be ``ps5-console`` from
        extract_style_suffix. The product's prior key is
        ``product:ps5-console`` — different string, different prior slot."""
        style_key = extract_style_suffix("style:gaming:ps5-console")
        product_key = extract_product_suffix("product__ps5-console")
        assert style_key != product_key
        assert style_key == "ps5-console"
        assert product_key == "product:ps5-console"


class TestComputeTransferredPriorsForProducts:
    """The compute step must recognize product arms and moment-match
    when ≥ 2 niches have enough observations."""

    def test_product_arm_gets_prior_when_2_niches_have_obs(self):
        """gaming and movies both surface PS5 with 10 observations each
        at different reward rates. Cross-niche variance is non-zero,
        moment matching produces a valid prior."""
        bandit_state = {
            "gaming": {
                "product__ps5-console": (7.0, 5.0),  # obs=10, rate=0.583
            },
            "movies": {
                "product__ps5-console": (4.0, 8.0),  # obs=10, rate=0.333
            },
        }
        priors = compute_transferred_priors(bandit_state)
        assert "product:ps5-console" in priors
        prior = priors["product:ps5-console"]
        # Mean of (0.583, 0.333) = 0.458 — prior mean must be near this
        assert 0.3 < prior.observed_rate_mean < 0.6
        assert prior.n_niches == 2

    def test_single_niche_product_skipped(self):
        """Only gaming has observations for PS5 → no cross-niche
        variance to fit; falls through to Beta(1,1) at consumption time."""
        bandit_state = {
            "gaming": {"product__ps5-console": (10.0, 5.0)},
            # movies has no arm
        }
        priors = compute_transferred_priors(bandit_state)
        assert "product:ps5-console" not in priors

    def test_mixed_style_transformation_product_all_work(self):
        """All three arm classes coexist in the same bandit_state and
        each produces its own prior."""
        bandit_state = {
            "gaming": {
                "style:gaming:revelation": (8.0, 4.0),
                "transform__music_mood__cinematic": (7.0, 5.0),
                "product__ps5-console": (6.0, 6.0),
            },
            "movies": {
                "style:movies:revelation": (5.0, 7.0),
                "transform__music_mood__cinematic": (4.0, 8.0),
                "product__ps5-console": (9.0, 3.0),
            },
        }
        priors = compute_transferred_priors(bandit_state)
        # All three prior classes present
        keys = set(priors.keys())
        assert "revelation" in keys
        assert "music_mood__cinematic" in keys
        assert "product:ps5-console" in keys

    def test_insufficient_obs_per_niche_skipped(self):
        """A niche with only 2 total observations for a product is too
        noisy — its rate doesn't count toward cross-niche mean."""
        bandit_state = {
            "gaming": {"product__ps5-console": (2.0, 1.0)},  # obs=1
            "movies": {"product__ps5-console": (1.0, 1.0)},  # obs=0 (uniform)
        }
        priors = compute_transferred_priors(bandit_state)
        assert "product:ps5-console" not in priors


class TestGetTransferredPriorForProducts:
    """The consumer read side must return the persisted prior when
    called with a product arm_id + flag on."""

    def test_returns_prior_when_persisted(self, tmp_path, monkeypatch):
        # Compute + persist
        bandit_state = {
            "gaming": {"product__ps5-console": (7.0, 5.0)},
            "movies": {"product__ps5-console": (4.0, 8.0)},
        }
        priors = compute_transferred_priors(bandit_state)
        priors_path = tmp_path / "priors.json"
        persist_priors(priors, output_path=priors_path)

        # Consumer read
        monkeypatch.setenv("GENLAB_CROSS_NICHE_TRANSFER_ENABLED", "true")
        result = get_transferred_prior(
            "sports",  # niche not in bandit_state — cold-start
            "product__ps5-console",
            input_path=priors_path,
        )
        assert result is not None
        alpha, beta = result
        assert alpha > 0
        assert beta > 0

    def test_returns_none_when_flag_off(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GENLAB_CROSS_NICHE_TRANSFER_ENABLED", raising=False)
        result = get_transferred_prior(
            "sports",
            "product__ps5-console",
            input_path=tmp_path / "nonexistent.json",
        )
        assert result is None

    def test_returns_none_when_no_persisted_prior(self, monkeypatch):
        monkeypatch.setenv("GENLAB_CROSS_NICHE_TRANSFER_ENABLED", "true")
        result = get_transferred_prior(
            "sports",
            "product__brand-new-untracked-product",
        )
        assert result is None

    def test_non_product_arm_id_returns_none(self, monkeypatch):
        """Passing a style arm to get_transferred_prior for a nonexistent
        style must return None (not crash on the routing extension)."""
        monkeypatch.setenv("GENLAB_CROSS_NICHE_TRANSFER_ENABLED", "true")
        result = get_transferred_prior(
            "sports",
            "source:youtube_trending",  # not a style, transformation, OR product
        )
        assert result is None
