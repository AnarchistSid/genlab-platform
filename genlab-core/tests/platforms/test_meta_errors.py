"""Tests for genlab_core.platforms.meta_errors.format_meta_error.

Preserves attribution across all Meta clients (IG/FB/Threads). See
[[class-of-bug-signal-loss-through-merged-failure-paths]] — 3 prod
rows in the last 7d wrote "media_publish failed: An unknown error has
occurred." with zero attribution.
"""

from __future__ import annotations

from genlab_core.platforms.meta_errors import format_meta_error


class TestFormatMetaError:
    def test_full_envelope_appends_all_fields(self):
        result = format_meta_error(
            {
                "error": {
                    "message": "Unknown error",
                    "code": 1,
                    "error_subcode": 2207032,
                    "fbtrace_id": "Abc123",
                }
            }
        )
        assert result == "Unknown error [code=1, subcode=2207032, fbtrace_id=Abc123]"

    def test_message_only_no_suffix(self):
        """When only ``message`` is present, no suffix is appended —
        no empty brackets."""
        result = format_meta_error({"error": {"message": "Just a message"}})
        assert result == "Just a message"
        assert "[]" not in result

    def test_missing_message_falls_back_to_str_payload(self):
        """Older Meta responses might return ``{"error": {...}}`` with
        no message — fall back to the payload repr so nothing is lost."""
        result = format_meta_error({"error": {"code": 32}})
        # Message defaults to str(payload) — check code still shows
        assert "code=32" in result

    def test_no_error_key_returns_str_payload(self):
        """When the whole response has no ``error`` key at all, return
        str(payload) — matches pre-2026-07-23 behavior."""
        result = format_meta_error({"unexpected": "shape"})
        assert result == "{'unexpected': 'shape'}"

    def test_non_dict_payload_returns_str_payload(self):
        """Defensive against non-dict payloads (network fail, garbage)."""
        assert format_meta_error(None) == "None"
        assert format_meta_error("plain string") == "plain string"
        assert format_meta_error(42) == "42"

    def test_code_zero_is_preserved(self):
        """Meta sometimes returns code=0 — must not be dropped by
        truthy check. `is not None` is the correct guard."""
        result = format_meta_error({"error": {"message": "m", "code": 0}})
        assert "code=0" in result

    def test_subcode_zero_is_preserved(self):
        """Same for subcode=0."""
        result = format_meta_error(
            {"error": {"message": "m", "error_subcode": 0}}
        )
        assert "subcode=0" in result

    def test_partial_fields_ordered(self):
        """When only code + fbtrace_id present (no subcode), suffix is
        well-formed and ordered."""
        result = format_meta_error(
            {"error": {"message": "m", "code": 190, "fbtrace_id": "XYZ"}}
        )
        assert result == "m [code=190, fbtrace_id=XYZ]"

    def test_empty_fbtrace_id_dropped(self):
        """Empty string fbtrace_id is dropped (truthy check is correct
        here since empty ID has no lookup value)."""
        result = format_meta_error(
            {"error": {"message": "m", "code": 1, "fbtrace_id": ""}}
        )
        assert result == "m [code=1]"
        assert "fbtrace_id" not in result

    def test_docstring_examples(self):
        """Pin the exact examples in the module docstring so the docs
        stay accurate as the function evolves."""
        # Example 1
        assert (
            format_meta_error(
                {
                    "error": {
                        "message": "Unknown error",
                        "code": 1,
                        "error_subcode": 2207032,
                        "fbtrace_id": "Abc123",
                    }
                }
            )
            == "Unknown error [code=1, subcode=2207032, fbtrace_id=Abc123]"
        )
        # Example 2
        assert (
            format_meta_error({"error": {"message": "Just a message"}})
            == "Just a message"
        )
        # Example 3
        assert (
            format_meta_error({"unexpected": "shape"})
            == "{'unexpected': 'shape'}"
        )
