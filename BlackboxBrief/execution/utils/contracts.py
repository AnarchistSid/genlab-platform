"""Typed boundary contracts for backlog and artifact payloads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


class ContractValidationError(ValueError):
    """Raised when a boundary payload does not satisfy contract."""


@dataclass(frozen=True)
class BlueprintContract:
    """Typed contract for blueprint record payloads."""

    record_id: str
    candidate_id: str
    status: str
    format: str
    fields: Dict[str, Any]

    @classmethod
    def from_record(cls, record: Dict[str, Any]) -> "BlueprintContract":
        if not isinstance(record, dict):
            raise ContractValidationError("record_not_dict")
        record_id = str(record.get("id", "") or "").strip()
        if not record_id:
            raise ContractValidationError("record_id_missing")
        fields = record.get("fields", {})
        if not isinstance(fields, dict):
            raise ContractValidationError("fields_not_dict")
        candidate_id = str(fields.get("candidate_id", "") or "").strip()
        if not candidate_id:
            raise ContractValidationError("candidate_id_missing")
        status = str(fields.get("status", "") or "").strip().upper()
        fmt = str(fields.get("format", "") or "").strip().lower()
        return cls(
            record_id=record_id,
            candidate_id=candidate_id,
            status=status,
            format=fmt,
            fields=fields,
        )


@dataclass(frozen=True)
class OverlayCandidateContract:
    """Typed contract for render_text_overlays candidate payloads."""

    candidate_id: str
    story_id: str
    format: str
    status: str
    payload: Dict[str, Any]

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "OverlayCandidateContract":
        if not isinstance(payload, dict):
            raise ContractValidationError("overlay_candidate_not_dict")
        candidate_id = str(payload.get("candidate_id", "") or "").strip()
        if not candidate_id:
            raise ContractValidationError("overlay_candidate_id_missing")
        story_id = str(payload.get("story_id", "") or "").strip()
        fmt = str(payload.get("format", "") or "").strip().lower()
        status = str(payload.get("status", "") or "").strip().upper()
        return cls(
            candidate_id=candidate_id,
            story_id=story_id,
            format=fmt,
            status=status,
            payload=payload,
        )
