"""Pin the cross-niche priors artifact path resolution.

Deep-dive 2026-08-19 found the consumer wire dead due to a
producer/consumer path mismatch:
  * Producer service sets GENLAB_TMP=/opt/genlab/.tmp → writes to
    the shared artifact.
  * Pipeline (consumer) services set only GENLAB_PROJECT_ROOT — no
    GENLAB_TMP — and cwd=/opt/genlab/BlackboxBrief. Previous
    ``_priors_path()`` fell back to ``Path.cwd() / .tmp`` which
    resolved to ``/opt/genlab/BlackboxBrief/.tmp/...`` and NEVER
    matched the producer's write path.

Fix: 3-tier fallback:
  1. GENLAB_TMP env (existing)
  2. GENLAB_PROJECT_ROOT/.tmp (NEW — matches pipeline services)
  3. cwd/.tmp (dev fallback)

Pin all three so a future refactor can't silently regress the
consumer wire again.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from genlab_core.learning.cross_niche_transfer import _priors_path


class TestPathResolutionFallbackOrder:
    def test_genlab_tmp_env_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GENLAB_TMP", str(tmp_path))
        monkeypatch.setenv("GENLAB_PROJECT_ROOT", "/should/be/ignored")
        assert _priors_path() == tmp_path / "cross-niche-transfer" / "priors.json"

    def test_project_root_used_when_tmp_missing(self, monkeypatch, tmp_path):
        """The gap-fix: consumer pipeline services only set
        GENLAB_PROJECT_ROOT, not GENLAB_TMP. Path must resolve
        under the project root's .tmp — matches producer output."""
        monkeypatch.delenv("GENLAB_TMP", raising=False)
        monkeypatch.setenv("GENLAB_PROJECT_ROOT", str(tmp_path))
        assert _priors_path() == tmp_path / ".tmp" / "cross-niche-transfer" / "priors.json"

    def test_cwd_fallback_when_both_missing(self, monkeypatch, tmp_path):
        monkeypatch.delenv("GENLAB_TMP", raising=False)
        monkeypatch.delenv("GENLAB_PROJECT_ROOT", raising=False)
        monkeypatch.chdir(tmp_path)
        assert _priors_path() == tmp_path / ".tmp" / "cross-niche-transfer" / "priors.json"

    def test_project_root_beats_cwd(self, monkeypatch, tmp_path):
        """Prevent silent divergence: when both PROJECT_ROOT and cwd
        are candidates, PROJECT_ROOT wins. This is what fixed the
        producer/consumer path mismatch — pipeline services have
        PROJECT_ROOT set and DON'T want cwd (=BlackboxBrief/) used."""
        monkeypatch.delenv("GENLAB_TMP", raising=False)
        project = tmp_path / "project"
        project.mkdir()
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.setenv("GENLAB_PROJECT_ROOT", str(project))
        monkeypatch.chdir(elsewhere)
        assert _priors_path().is_relative_to(project)
        assert not _priors_path().is_relative_to(elsewhere)


class TestProducerConsumerPathParity:
    """Structural pin — the bug this test guards against:

    Producer runs with GENLAB_TMP=/opt/genlab/.tmp set explicitly in
    its systemd unit. Consumer runs with GENLAB_PROJECT_ROOT=/opt/genlab
    but no GENLAB_TMP. If ``_priors_path()`` doesn't honor
    GENLAB_PROJECT_ROOT as a fallback, the two paths diverge and the
    consumer wire is dead.
    """

    def test_producer_and_consumer_env_shapes_resolve_to_same_path(
        self, monkeypatch,
    ):
        # Producer service env
        monkeypatch.setenv("GENLAB_TMP", "/opt/genlab/.tmp")
        monkeypatch.setenv("GENLAB_PROJECT_ROOT", "/opt/genlab")
        producer_path = _priors_path()

        # Consumer service env — DROP GENLAB_TMP, keep PROJECT_ROOT
        monkeypatch.delenv("GENLAB_TMP", raising=False)
        # PROJECT_ROOT still set
        consumer_path = _priors_path()

        assert producer_path == consumer_path == Path(
            "/opt/genlab/.tmp/cross-niche-transfer/priors.json"
        ), (
            f"producer={producer_path} consumer={consumer_path} — "
            f"paths must match or the consumer wire is dead"
        )
