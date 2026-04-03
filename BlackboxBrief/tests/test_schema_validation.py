"""Tests for JSON schema validation."""

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
SCHEMAS_DIR = PROJECT_ROOT / "schemas"
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures" / "news_samples"
GOLDEN_DIR = PROJECT_ROOT / "tests" / "golden"


def validate_json(schema_path: Path, data: dict) -> bool:
    """Validate data against a JSON schema."""
    from jsonschema import ValidationError, validate
    schema = json.loads(schema_path.read_text())
    try:
        validate(instance=data, schema=schema)
        return True
    except ValidationError:
        return False


class TestBacklogRowSchema:
    def test_valid_backlog_row(self):
        data = json.loads(
            (FIXTURES_DIR / "sample_backlog_row.json").read_text()
        )
        assert validate_json(SCHEMAS_DIR / "backlog_row.schema.json", data)

    def test_missing_required_field(self):
        data = {"id": "test", "type": "story"}  # Missing status, title, created_at
        assert not validate_json(SCHEMAS_DIR / "backlog_row.schema.json", data)

    def test_invalid_status(self):
        data = {
            "id": "test",
            "type": "story",
            "status": "INVALID_STATUS",
            "title": "Test",
            "created_at": "2026-02-16T00:00:00Z"
        }
        assert not validate_json(SCHEMAS_DIR / "backlog_row.schema.json", data)


class TestTrendPackSchema:
    def test_golden_trend_pack_valid(self):
        data = json.loads(
            (GOLDEN_DIR / "trend_pack.json").read_text()
        )
        assert validate_json(SCHEMAS_DIR / "trend_pack.schema.json", data)


class TestBlueprintPackSchema:
    def test_golden_blueprint_pack_valid(self):
        data = json.loads(
            (GOLDEN_DIR / "blueprint_pack.json").read_text()
        )
        assert validate_json(SCHEMAS_DIR / "blueprint_pack.schema.json", data)
