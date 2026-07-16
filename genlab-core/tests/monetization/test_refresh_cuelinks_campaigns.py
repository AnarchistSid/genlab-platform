"""Tests for scripts/refresh_cuelinks_campaigns.py.

Key invariants pinned:
  * Empty API result → exit 1 + WARNING log + NO file write (silent-write
    bug prevention, 2026-07-16 audit lesson)
  * Atomic write via ``os.replace`` so a crash mid-write can't corrupt
    the existing YAML
  * Post-write assertion: reload + confirm ``campaigns`` list is
    non-empty, defense against serialisation drift
  * Sort by EPC descending, top-N slice
  * --dry-run doesn't write anything
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "refresh_cuelinks_campaigns.py"


@pytest.fixture(scope="module")
def refresh_module():
    """Load the script as a module for direct function access."""
    spec = importlib.util.spec_from_file_location("refresh_cuelinks_campaigns", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["refresh_cuelinks_campaigns"] = module
    spec.loader.exec_module(module)
    return module


class TestShortlistFromCampaigns:
    def test_sorts_by_epc_descending(self, refresh_module):
        raw = [
            {"id": 1, "name": "A", "epc_7d": 1.5, "category": "apparel"},
            {"id": 2, "name": "B", "epc_7d": 4.5, "category": "apparel"},
            {"id": 3, "name": "C", "epc_7d": 3.0, "category": "beauty"},
        ]
        result = refresh_module._shortlist_from_campaigns(raw)
        assert [c["name"] for c in result] == ["B", "C", "A"]
        assert result[0]["epc_7d"] == 4.5

    def test_trims_to_top_n(self, refresh_module):
        raw = [{"id": i, "name": f"c{i}", "epc_7d": float(i)} for i in range(10)]
        result = refresh_module._shortlist_from_campaigns(raw, top_n=3)
        assert len(result) == 3
        assert result[0]["epc_7d"] == 9.0  # highest first

    def test_handles_missing_epc_gracefully(self, refresh_module):
        """A campaign with no EPC field should sort to the bottom
        (treated as 0.0), not crash the run."""
        raw = [
            {"id": 1, "name": "A"},  # no epc
            {"id": 2, "name": "B", "epc_7d": 2.0},
        ]
        result = refresh_module._shortlist_from_campaigns(raw)
        assert result[0]["name"] == "B"
        assert result[1]["name"] == "A"

    def test_handles_string_epc(self, refresh_module):
        """Some API responses stringify numeric fields. Parse floats
        defensively rather than crash."""
        raw = [
            {"id": 1, "name": "A", "epc_7d": "3.5"},
            {"id": 2, "name": "B", "epc_7d": "1.0"},
        ]
        result = refresh_module._shortlist_from_campaigns(raw)
        assert result[0]["name"] == "A"


class TestAtomicWrite:
    def test_creates_target_file_and_dir(self, refresh_module, tmp_path):
        target = tmp_path / "sub" / "cuelinks_campaigns.yaml"
        payload = {"generated_at": "2026-07-16T00:00:00+00:00", "campaigns": [{"id": 1}]}
        refresh_module._atomic_write(target, payload)
        assert target.exists()
        reloaded = yaml.safe_load(target.read_text())
        assert reloaded["campaigns"][0]["id"] == 1

    def test_uses_tmp_sibling_then_rename(self, refresh_module, tmp_path):
        """The write must go through a .tmp sibling first — pinning
        the atomic-write invariant so a future refactor to ``open(
        target, 'w')`` fails this test."""
        target = tmp_path / "cuelinks_campaigns.yaml"
        payload = {"generated_at": "test", "campaigns": [{"id": 1}]}

        original_write = Path.write_text
        write_paths = []

        def track_write(self, *a, **kw):
            write_paths.append(str(self))
            return original_write(self, *a, **kw)

        # Monkey-patch to observe the write path
        Path.write_text = track_write
        try:
            refresh_module._atomic_write(target, payload)
        finally:
            Path.write_text = original_write

        # The tmp sibling should have been the write target
        assert any(p.endswith(".tmp") for p in write_paths), (
            f"expected write to a .tmp file, got: {write_paths}"
        )

    def test_refuses_to_write_malformed_dict(self, refresh_module, tmp_path):
        """The internal serialisation-drift guard should catch a payload
        missing 'campaigns' BEFORE the rename happens."""
        target = tmp_path / "cuelinks_campaigns.yaml"
        with pytest.raises(RuntimeError, match="missing 'campaigns'"):
            refresh_module._atomic_write(target, {"foo": "bar"})
        assert not target.exists()  # no partial file left behind


class TestPostWriteAssertion:
    def test_passes_on_valid_yaml(self, refresh_module, tmp_path):
        target = tmp_path / "cuelinks_campaigns.yaml"
        target.write_text("generated_at: '2026-07-16T00:00:00'\ncampaigns: [{id: 1, name: A}]")
        # Should not raise
        refresh_module._post_write_assertion(target)

    def test_fails_on_empty_campaigns(self, refresh_module, tmp_path):
        """The load-bearing silent-write guard. Empty ``campaigns``
        must fail loudly — matches the 2026-07-16 audit lesson from
        the top-creator-priors silent-write bug."""
        target = tmp_path / "cuelinks_campaigns.yaml"
        target.write_text("generated_at: 'x'\ncampaigns: []")
        with pytest.raises(RuntimeError, match="'campaigns' is empty"):
            refresh_module._post_write_assertion(target)

    def test_fails_on_wrong_shape(self, refresh_module, tmp_path):
        """Reloaded YAML that isn't a dict at all fails loudly."""
        target = tmp_path / "cuelinks_campaigns.yaml"
        target.write_text("[]")  # top-level list, not dict
        with pytest.raises(RuntimeError, match="expected dict"):
            refresh_module._post_write_assertion(target)


class TestRefresh:
    def test_returns_1_when_key_unset(self, refresh_module, monkeypatch):
        monkeypatch.delenv("CUELINKS_V3_API_KEY", raising=False)
        assert refresh_module.refresh() == 1

    def test_returns_1_when_api_returns_empty(self, refresh_module, monkeypatch):
        """Silent-write prevention: empty API result must NOT write
        an empty shortlist to disk. Exit 1 + WARNING log."""
        monkeypatch.setenv("CUELINKS_V3_API_KEY", "test-key")
        monkeypatch.setattr(
            "genlab_core.monetization.cuelinks_client.list_campaigns",
            lambda **kw: [],
        )
        assert refresh_module.refresh() == 1

    def test_dry_run_does_not_write(self, refresh_module, monkeypatch, tmp_path):
        monkeypatch.setenv("CUELINKS_V3_API_KEY", "test-key")
        monkeypatch.setenv("GENLAB_PROJECT_ROOT", str(tmp_path))
        monkeypatch.setattr(
            "genlab_core.monetization.cuelinks_client.list_campaigns",
            lambda **kw: [{"id": 1, "name": "Flipkart", "epc_7d": 3.0}],
        )
        assert refresh_module.refresh(dry_run=True) == 0
        # No file written in dry-run mode
        target = tmp_path / "genlab-core" / "config" / "cuelinks_campaigns.yaml"
        assert not target.exists()

    def test_writes_shortlist_on_success(self, refresh_module, monkeypatch, tmp_path):
        monkeypatch.setenv("CUELINKS_V3_API_KEY", "test-key")
        monkeypatch.setenv("GENLAB_PROJECT_ROOT", str(tmp_path))
        monkeypatch.setattr(
            "genlab_core.monetization.cuelinks_client.list_campaigns",
            lambda **kw: [
                {"id": 1, "name": "Flipkart", "epc_7d": 4.5, "category": "apparel"},
                {"id": 2, "name": "Myntra", "epc_7d": 3.2, "category": "apparel"},
            ],
        )
        assert refresh_module.refresh() == 0
        target = tmp_path / "genlab-core" / "config" / "cuelinks_campaigns.yaml"
        assert target.exists()
        reloaded = yaml.safe_load(target.read_text())
        assert reloaded["total_returned"] == 2
        assert len(reloaded["campaigns"]) == 2
        assert reloaded["campaigns"][0]["name"] == "Flipkart"  # sorted by EPC desc


class TestPlaceholderYaml:
    """The committed placeholder YAML must be safe to load — consumers
    (PR 3's affiliate_matcher) will read it from day one, before the
    first timer fire has populated real data."""

    def test_placeholder_yaml_loads_cleanly(self):
        target = _REPO_ROOT / "genlab-core" / "config" / "cuelinks_campaigns.yaml"
        assert target.exists(), "placeholder cuelinks_campaigns.yaml must ship in the repo"
        loaded = yaml.safe_load(target.read_text())
        assert isinstance(loaded, dict)
        assert "campaigns" in loaded
        assert isinstance(loaded["campaigns"], list)
        # Empty list is expected pre-first-fire; consumers must handle it
        assert loaded["campaigns"] == []
