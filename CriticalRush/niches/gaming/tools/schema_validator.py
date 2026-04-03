"""JSON schema validation for CriticalRush pipeline outputs."""
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"


def validate_against_schema(data: dict[str, Any], schema_name: str) -> tuple[bool, str | None]:
    """Validate data against a named schema.

    Args:
        data: The data dict to validate
        schema_name: Schema filename (e.g., "written_content.schema.json")

    Returns:
        (valid, error_message) -- (True, None) if valid, (False, "reason") if not
    """
    schema_path = _SCHEMA_DIR / schema_name
    if not schema_path.exists():
        logger.warning("Schema not found: %s", schema_name)
        return True, None  # No schema = pass (graceful degradation)

    try:
        import jsonschema
    except ImportError:
        logger.debug("jsonschema not installed, skipping validation")
        return True, None

    with open(schema_path) as f:
        schema = json.load(f)

    try:
        jsonschema.validate(data, schema)
        return True, None
    except jsonschema.ValidationError as e:
        return False, str(e.message)
