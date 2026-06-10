"""Tests for :mod:`genlab_core.platforms.meta_api`.

Audit ref: R-44 — single source of truth for the Meta Graph API version.

These tests are intentionally tiny: they pin invariants that the
constants module is supposed to provide, so a future drift (someone
inlines a literal ``v21.0`` somewhere, or the constants disappear)
fails CI immediately.
"""

from __future__ import annotations

import re

from genlab_core.platforms import meta_api


class TestMetaApiConstants:
    def test_version_has_expected_shape(self) -> None:
        # ``vNN.M`` where N is one or two digits, M is one or two.
        assert re.fullmatch(r"v\d{1,2}\.\d{1,2}", meta_api.META_GRAPH_API_VERSION), (
            f"unexpected version shape: {meta_api.META_GRAPH_API_VERSION!r}"
        )

    def test_base_url_is_graph_facebook_anchored(self) -> None:
        assert meta_api.META_GRAPH_BASE_URL.startswith("https://graph.facebook.com/")
        assert meta_api.META_GRAPH_BASE_URL.endswith(meta_api.META_GRAPH_API_VERSION)

    def test_base_url_uses_the_version_constant(self) -> None:
        # If someone replaces META_GRAPH_API_VERSION with a literal in
        # META_GRAPH_BASE_URL, this guards against drift.
        assert (
            meta_api.META_GRAPH_BASE_URL
            == f"https://graph.facebook.com/{meta_api.META_GRAPH_API_VERSION}"
        )

    def test_version_is_not_below_v22(self) -> None:
        # Floor: the audit (R-44) targeted v22.0 minimum. Don't let a
        # future edit accidentally regress to v21.0 or earlier — that
        # would re-open the metric-retirement risk the audit flagged.
        parts = meta_api.META_GRAPH_API_VERSION.lstrip("v").split(".")
        major = int(parts[0])
        assert major >= 22, f"version regressed below v22.0: {meta_api.META_GRAPH_API_VERSION}"
