"""Regression guards for ``execution.utils.config_loader`` after the
symlink-to-shared-config refactor.

History
-------
``BlackboxBrief/config/`` used to contain seven symlinks pointing at
``genlab-core/config/*.yaml``. They were the last "BB-as-special-case"
architectural debt: every reader had to know about the link hop, ``git
status`` reported shared files as BB-owned, and the symlinks broke on
cross-platform checkouts. The refactor replaced them with a declarative
:data:`SHARED_CONFIG_NAMES` frozenset inside the loader.

These tests guard the boundary:

* Shared names resolve to ``genlab-core/config/`` (not ``BB/config/``).
* Non-shared names still resolve to ``BB/config/`` (no silent re-routing).
* The seven shared yamls are actually present on disk where the loader
  expects them — a cheap end-to-end sanity check that catches an
  accidental ``rm`` of the genlab-core copy too.
* ``BlackboxBrief/config/`` contains no symlinks — the whole point of
  the refactor.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from execution.utils.config_loader import (
    _GENLAB_CORE_CONFIG_DIR,
    PROJECT_ROOT,
    SHARED_CONFIG_NAMES,
    _resolve_config_path,
    clear_cache,
    load_config,
)


@pytest.fixture(autouse=True)
def _isolate_cache():
    """Each test gets a clean process-wide cache so resolution paths are
    actually exercised (not served from a sibling test's load)."""
    clear_cache()
    yield
    clear_cache()


# ``lists_config.yaml`` is gitignored — it carries deployment-local SharePoint
# list IDs and is provisioned per environment, not checked in. The previous
# symlink also pointed at a gitignored file, so absence-in-CI is the historical
# baseline, not a regression. Exclude it from on-disk presence assertions; the
# routing-path assertion below still covers it.
_DISK_PRESENT_SHARED: frozenset[str] = SHARED_CONFIG_NAMES - {"lists_config.yaml"}


class TestSharedConfigRouting:
    """The seven shared names route to genlab-core, not BB."""

    @pytest.mark.parametrize("name", sorted(SHARED_CONFIG_NAMES))
    def test_shared_name_resolves_under_genlab_core(self, name: str) -> None:
        resolved = _resolve_config_path(name)
        assert resolved == _GENLAB_CORE_CONFIG_DIR / name, (
            f"{name} must resolve under genlab-core/config/, not BB/config/. Got: {resolved}"
        )

    @pytest.mark.parametrize("name", sorted(_DISK_PRESENT_SHARED))
    def test_shared_name_file_exists(self, name: str) -> None:
        """The shared yaml must exist where the loader expects it. Catches
        an accidental delete of the genlab-core source — the original BB
        symlink can no longer paper over it.

        ``lists_config.yaml`` is excluded (gitignored, env-local)."""
        resolved = _resolve_config_path(name)
        assert resolved.exists(), (
            f"Shared config {name} not found at {resolved}. genlab-core/config/ source must exist for BB to read it."
        )

    @pytest.mark.parametrize("name", sorted(_DISK_PRESENT_SHARED))
    def test_load_config_returns_non_empty_for_shared(self, name: str) -> None:
        data = load_config(name, required=False)
        assert isinstance(data, dict)
        assert data, f"{name} should parse to a non-empty dict (sanity check)"


class TestNicheConfigRouting:
    """Non-shared (BB-owned) names still resolve under BB/config/."""

    @pytest.mark.parametrize(
        "name",
        ["sources.yaml", "persona.yaml", "templates.yaml", "scoring_weights.yaml"],
    )
    def test_bb_owned_name_stays_in_bb(self, name: str) -> None:
        resolved = _resolve_config_path(name)
        assert resolved == PROJECT_ROOT / "config" / name, (
            f"BB-owned config {name} must NOT be re-routed to genlab-core. Got: {resolved}"
        )

    def test_unknown_name_resolves_against_bb_not_genlab_core(self) -> None:
        """A typo'd name fails loud against BB's directory, not silently
        re-routed to genlab-core (which would surface a less useful
        FileNotFoundError pointing at the wrong directory)."""
        typo = "publishng.yaml"  # intentional misspelling
        assert typo not in SHARED_CONFIG_NAMES
        resolved = _resolve_config_path(typo)
        assert resolved == PROJECT_ROOT / "config" / typo


class TestAbsolutePath:
    def test_absolute_path_passes_through(self, tmp_path: Path) -> None:
        custom = tmp_path / "weird.yaml"
        custom.write_text("hello: world\n")
        resolved = _resolve_config_path(str(custom))
        assert resolved == custom
        assert load_config(str(custom)) == {"hello": "world"}


class TestNoSymlinksRemain:
    """BB/config/ must contain ZERO symlinks. This guards against someone
    re-introducing the legacy ``ln -s ../../genlab-core/config/...`` pattern."""

    def test_bb_config_has_no_symlinks(self) -> None:
        bb_config = PROJECT_ROOT / "config"
        links = [p for p in bb_config.iterdir() if p.is_symlink()]
        assert not links, (
            f"BB/config/ should contain no symlinks (those were dropped in "
            f"the symlink-to-includer refactor). Found: {[str(p) for p in links]}"
        )
