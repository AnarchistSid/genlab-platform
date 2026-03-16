"""Tests for niche config validation."""
import tempfile
from pathlib import Path

from genlab_core.tools.validate_configs import validate_niche_dir, ValidationResult


class TestValidateConfigs:
    def test_valid_config_passes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "config").mkdir()
            (d / "config" / "niche.yaml").write_text(
                "niche_id: test\nbrand_name: Test\naccent_color: '#FF0000'\n"
                "video_gate: require\nfallback_to_text_render: false\n"
            )
            result = validate_niche_dir(d)
            assert result.niche_yaml_valid

    def test_missing_niche_yaml_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "config").mkdir()
            result = validate_niche_dir(d)
            assert not result.niche_yaml_valid

    def test_bad_accent_color_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "config").mkdir()
            (d / "config" / "niche.yaml").write_text(
                "niche_id: test\nbrand_name: Test\naccent_color: red\n"
                "video_gate: require\nfallback_to_text_render: false\n"
            )
            result = validate_niche_dir(d)
            assert len(result.errors) > 0

    def test_fallback_true_fails(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "config").mkdir()
            (d / "config" / "niche.yaml").write_text(
                "niche_id: test\nbrand_name: Test\naccent_color: '#FF0000'\n"
                "video_gate: require\nfallback_to_text_render: true\n"
            )
            result = validate_niche_dir(d)
            assert len(result.errors) > 0

    def test_missing_config_dir_reports_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            result = validate_niche_dir(d)
            assert not result.niche_yaml_valid
            assert not result.ok

    def test_warns_on_missing_optional_configs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            (d / "config").mkdir()
            (d / "config" / "niche.yaml").write_text(
                "niche_id: test\nbrand_name: Test\naccent_color: '#FF0000'\n"
                "video_gate: require\nfallback_to_text_render: false\n"
            )
            result = validate_niche_dir(d)
            assert result.ok  # should pass — errors list is empty
            assert len(result.warnings) > 0  # but warns about optional files

    def test_ok_property(self):
        result = ValidationResult()
        assert result.ok  # no errors => ok
        result.errors.append("something broke")
        assert not result.ok
