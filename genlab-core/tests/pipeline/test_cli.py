"""Tests for genlab_core.pipeline.cli — unified pipeline CLI.

Tests cover:
- Niche argument parsing (single, comma-separated, "all")
- GENLAB_ROOT resolution
- Niche root directory building
- Run report writing
- CLI main() with mocked GenericPipelineRunner
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from genlab_core.pipeline.cli import (
    NICHE_DIR_NAMES,
    VALID_NICHE_IDS,
    _build_niche_roots,
    _parse_niche_arg,
    _resolve_genlab_root,
    _write_run_report,
    build_parser,
    main,
    run_pipeline,
    run_multi,
)


# ── _parse_niche_arg tests ──────────────────────────────────────────────────


class TestParseNicheArg:
    def test_single_niche(self) -> None:
        assert _parse_niche_arg("gaming") == ["gaming"]

    def test_comma_separated(self) -> None:
        result = _parse_niche_arg("sports,movies,anime")
        assert result == ["sports", "movies", "anime"]

    def test_all_niches(self) -> None:
        result = _parse_niche_arg("all")
        assert len(result) == 5
        assert set(result) == VALID_NICHE_IDS

    def test_all_case_insensitive(self) -> None:
        result = _parse_niche_arg("ALL")
        assert len(result) == 5

    def test_invalid_niche_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid niche"):
            _parse_niche_arg("bogus")

    def test_mixed_valid_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid niche"):
            _parse_niche_arg("gaming,bogus")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="No niche IDs"):
            _parse_niche_arg("")

    def test_whitespace_trimming(self) -> None:
        result = _parse_niche_arg(" sports , movies ")
        assert result == ["sports", "movies"]

    def test_deduplication(self) -> None:
        result = _parse_niche_arg("gaming,gaming,gaming")
        assert result == ["gaming"]

    def test_dedup_preserves_order(self) -> None:
        result = _parse_niche_arg("anime,sports,anime")
        assert result == ["anime", "sports"]


# ── _build_niche_roots tests ────────────────────────────────────────────────


class TestBuildNicheRoots:
    def test_builds_correct_paths(self, tmp_path: Path) -> None:
        # Create the expected directories
        (tmp_path / "CriticalRush").mkdir()
        (tmp_path / "ClutchWire").mkdir()

        roots = _build_niche_roots(tmp_path, ["gaming", "sports"])
        assert roots["gaming"] == tmp_path / "CriticalRush"
        assert roots["sports"] == tmp_path / "ClutchWire"

    def test_unknown_niche_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="Unknown niche_id"):
            _build_niche_roots(tmp_path, ["nonexistent"])

    def test_missing_directory_raises(self, tmp_path: Path) -> None:
        # Don't create the directory — should raise
        with pytest.raises(FileNotFoundError, match="Channel directory not found"):
            _build_niche_roots(tmp_path, ["gaming"])


# ── _resolve_genlab_root tests ──────────────────────────────────────────────


class TestResolveGenlabRoot:
    def test_env_var_override(self, tmp_path: Path) -> None:
        with patch.dict("os.environ", {"GENLAB_ROOT": str(tmp_path)}):
            assert _resolve_genlab_root() == tmp_path

    def test_inferred_from_package_location(self) -> None:
        # Should work without env var since we're running from within GenLab
        with patch.dict("os.environ", {}, clear=False):
            # Remove GENLAB_ROOT if set
            import os
            old = os.environ.pop("GENLAB_ROOT", None)
            try:
                root = _resolve_genlab_root()
                assert (root / "genlab-core").is_dir()
            finally:
                if old is not None:
                    os.environ["GENLAB_ROOT"] = old


# ── _write_run_report tests ────────────────────────────────────────────────


class TestWriteRunReport:
    def test_writes_json(self, tmp_path: Path) -> None:
        results = {
            "gaming": {"stories": 5, "errors": 0, "is_aborted": False},
            "sports": {"stories": 3, "errors": 1, "is_aborted": False},
        }
        report_path = _write_run_report(tmp_path, "test_run_123", results)

        assert report_path.exists()
        data = json.loads(report_path.read_text())
        assert data["run_id"] == "test_run_123"
        assert "gaming" in data["niches"]
        assert "sports" in data["niches"]
        assert data["niches"]["gaming"]["stories"] == 5

    def test_creates_run_dir(self, tmp_path: Path) -> None:
        _write_run_report(tmp_path, "new_run", {})
        assert (tmp_path / ".tmp" / "runs" / "new_run").is_dir()


# ── NICHE_DIR_NAMES correctness ────────────────────────────────────────────


class TestNicheDirNames:
    def test_all_valid_niches_mapped(self) -> None:
        for niche_id in VALID_NICHE_IDS:
            assert niche_id in NICHE_DIR_NAMES, f"{niche_id} missing from NICHE_DIR_NAMES"

    def test_known_mappings(self) -> None:
        assert NICHE_DIR_NAMES["ai_creators"] == "BlackboxBrief"
        assert NICHE_DIR_NAMES["gaming"] == "CriticalRush"
        assert NICHE_DIR_NAMES["sports"] == "ClutchWire"
        assert NICHE_DIR_NAMES["movies"] == "SpliceReel"
        assert NICHE_DIR_NAMES["anime"] == "FrameDrift"


# ── build_parser tests ──────────────────────────────────────────────────────


class TestBuildParser:
    def test_niche_required(self) -> None:
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_niche_accepted(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--niche", "gaming"])
        assert args.niche == "gaming"

    def test_all_flags(self) -> None:
        parser = build_parser()
        args = parser.parse_args([
            "--niche", "all",
            "--dry-run",
            "--verbose",
            "--force-publish",
            "--stages", "Fetch,Score",
        ])
        assert args.niche == "all"
        assert args.dry_run is True
        assert args.verbose is True
        assert args.force_publish is True
        assert args.stages == "Fetch,Score"


# ── run_pipeline / run_multi with mocked runner ────────────────────────────


class TestRunPipeline:
    @patch("genlab_core.pipeline.cli.GenericPipelineRunner")
    @patch("genlab_core.pipeline.cli.settings")
    def test_single_niche_delegates(
        self, mock_settings: MagicMock, mock_runner_cls: MagicMock,
    ) -> None:
        mock_settings.validate_for_niche.return_value = []

        mock_ctx = MagicMock()
        mock_ctx.stories = [{"title": "test"}]
        mock_ctx.errors = []
        mock_ctx.is_aborted = False
        mock_runner_cls.return_value.run.return_value = mock_ctx

        ctx = run_pipeline("gaming", dry_run=True)

        mock_runner_cls.assert_called_once()
        call_kwargs = mock_runner_cls.call_args
        assert "gaming" in call_kwargs.kwargs.get("niche_roots", call_kwargs[1].get("niche_roots", {}))
        mock_runner_cls.return_value.run.assert_called_once_with(
            "gaming", dry_run=True, verbose=False,
        )
        assert ctx is mock_ctx

    @patch("genlab_core.pipeline.cli.GenericPipelineRunner")
    @patch("genlab_core.pipeline.cli.settings")
    def test_multi_niche_runs_each(
        self, mock_settings: MagicMock, mock_runner_cls: MagicMock,
    ) -> None:
        mock_settings.validate_for_niche.return_value = []

        mock_ctx = MagicMock()
        mock_ctx.stories = []
        mock_ctx.errors = []
        mock_ctx.is_aborted = False
        mock_ctx.run_id = "test_123"
        mock_runner_cls.return_value.run.return_value = mock_ctx

        results = run_multi(["gaming", "sports"], dry_run=True)

        assert "gaming" in results
        assert "sports" in results
        # Two separate GenericPipelineRunner instantiations (one per niche)
        assert mock_runner_cls.call_count == 2


# ── main() with mocked runner ──────────────────────────────────────────────


class TestMain:
    @patch("genlab_core.pipeline.cli.GenericPipelineRunner")
    @patch("genlab_core.pipeline.cli.settings")
    def test_single_niche_exit_0(
        self, mock_settings: MagicMock, mock_runner_cls: MagicMock,
    ) -> None:
        mock_settings.validate_for_niche.return_value = []

        mock_ctx = MagicMock()
        mock_ctx.stories = []
        mock_ctx.errors = []
        mock_ctx.is_aborted = False
        mock_ctx.run_id = "gaming_test"
        mock_runner_cls.return_value.run.return_value = mock_ctx

        exit_code = main(["--niche", "gaming", "--dry-run"])
        assert exit_code == 0

    @patch("genlab_core.pipeline.cli.GenericPipelineRunner")
    @patch("genlab_core.pipeline.cli.settings")
    def test_aborted_returns_1(
        self, mock_settings: MagicMock, mock_runner_cls: MagicMock,
    ) -> None:
        mock_settings.validate_for_niche.return_value = []

        mock_ctx = MagicMock()
        mock_ctx.stories = []
        mock_ctx.errors = [{"stage": "fetch", "message": "boom"}]
        mock_ctx.is_aborted = True
        mock_ctx.run_id = "gaming_fail"
        mock_runner_cls.return_value.run.return_value = mock_ctx

        exit_code = main(["--niche", "gaming"])
        assert exit_code == 1

    def test_invalid_niche_exits_nonzero(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main(["--niche", "bogus_niche"])
        assert exc_info.value.code != 0
