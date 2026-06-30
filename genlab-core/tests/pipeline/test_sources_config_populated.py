"""Pin test for ``context['sources_config']`` population in pipeline_runner.

History — 2026-06-30:
    ``context['sources_config']`` is declared in
    ``genlab_core.pipeline.stage_context.StageContext`` but was NEVER
    populated. As a result, every fetcher stage that read it
    (``FetchRedditClips``, ``FetchScorebat``, ``FetchTMDBTrailers``,
    ``FetchAnimePromos``, ``FetchTwitchClips``, ``FetchSteamTrailers``)
    early-returned on empty config — pipelines silently ran on
    YouTube-only data for weeks.  Diagnostic signature: those stages
    reported 0.0000s runtime in ``metrics.jsonl``.

    Fix landed by adding ``_load_sources_yaml(niche_root)`` helper in
    ``pipeline_runner.py`` and wiring its result into the
    ``context_dict`` constructed inside
    ``GenericPipelineRunner.run_niche``.

    These tests pin:
      1. ``pipeline_runner._load_sources_yaml`` exists and is wired
         into ``context_dict`` construction (source-string assertion).
      2. With a fake niche dir containing
         ``config/sources.yaml`` → the loader returns the parsed dict.
      3. With a fake niche dir LACKING ``config/sources.yaml`` → the
         loader returns ``{}`` (fail-OPEN).
      4. With a malformed YAML file → the loader returns ``{}``
         (fail-OPEN), it MUST NOT raise.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from genlab_core.pipeline import pipeline_runner
from genlab_core.pipeline.pipeline_runner import _load_sources_yaml


def test_load_sources_yaml_helper_exists_and_is_wired_into_context_dict() -> None:
    """Pin: the helper exists AND is referenced in context_dict construction.

    A regression that removed the call site (e.g. an accidental revert
    of the populate-sources-config commit) would cause the wire to go
    dead again. Asserting the call site appears in the source ensures
    the wire stays connected even if the rest of context_dict gets
    refactored.
    """
    # 1. Helper exists and is callable.
    assert callable(_load_sources_yaml), "_load_sources_yaml must be a callable in pipeline_runner"

    # 2. The exact wire is present in run_niche's source — proves the
    #    loader result is the value bound to the 'sources_config' key
    #    in context_dict.
    src = inspect.getsource(pipeline_runner)
    assert '"sources_config": _load_sources_yaml(niche_root)' in src, (
        "context_dict construction must populate 'sources_config' from "
        "_load_sources_yaml(niche_root). If you renamed the helper, "
        "update this pin in the same commit."
    )


def test_load_sources_yaml_reads_reddit_subreddits(tmp_path: Path) -> None:
    """With a sources.yaml present, loader parses it correctly."""
    niche_root = tmp_path / "FakeNiche"
    config_dir = niche_root / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "sources.yaml").write_text(
        "reddit:\n  enabled: true\n  subreddits:\n    - name: foo\n    - name: bar\n"
    )

    result = _load_sources_yaml(niche_root)

    assert isinstance(result, dict)
    assert "reddit" in result
    assert result["reddit"]["enabled"] is True
    assert result["reddit"]["subreddits"] == [{"name": "foo"}, {"name": "bar"}]


def test_load_sources_yaml_accepts_str_niche_root(tmp_path: Path) -> None:
    """Helper accepts ``str`` too (pipeline_runner passes a Path but
    callers elsewhere may pass strings)."""
    niche_root = tmp_path / "FakeNiche"
    config_dir = niche_root / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "sources.yaml").write_text("reddit:\n  enabled: true\n")

    result = _load_sources_yaml(str(niche_root))

    assert result == {"reddit": {"enabled": True}}


def test_load_sources_yaml_missing_file_returns_empty_dict(tmp_path: Path) -> None:
    """Fail-OPEN: a niche directory with no config/sources.yaml yields {}.

    This matches the pre-fix behaviour for niches that haven't yet
    declared a sources.yaml — the pipeline keeps running, just with
    empty source-config, so dependent fetcher stages no-op cleanly.
    """
    niche_root = tmp_path / "FakeNiche"  # no config/ subdir created
    result = _load_sources_yaml(niche_root)
    assert result == {}


def test_load_sources_yaml_empty_file_returns_empty_dict(tmp_path: Path) -> None:
    """yaml.safe_load(None) → None; helper must normalise to {}."""
    niche_root = tmp_path / "FakeNiche"
    config_dir = niche_root / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "sources.yaml").write_text("")  # empty file

    result = _load_sources_yaml(niche_root)
    assert result == {}


def test_load_sources_yaml_malformed_yaml_fails_open(tmp_path: Path) -> None:
    """Fail-OPEN: a YAML parse error MUST NOT raise — return {}.

    A malformed sources.yaml in one niche must not crash the pipeline
    runner; downstream behaviour falls back to the no-op path that
    every fetcher already handles for empty config.
    """
    niche_root = tmp_path / "FakeNiche"
    config_dir = niche_root / "config"
    config_dir.mkdir(parents=True)
    # Unbalanced bracket → yaml.YAMLError
    (config_dir / "sources.yaml").write_text("reddit: [unbalanced\n")

    result = _load_sources_yaml(niche_root)
    assert result == {}
