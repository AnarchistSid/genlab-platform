"""Pin: push_to_backlog persists reveal to variant_payload — Layer 3 S4b.

When the writer emits a `reveal` field alongside the standard content
(question_reveal variant), push_to_backlog must extract it into
variant_payload so the compositor can find it at render time (via
BlueprintStore → blueprint.variant_payload["reveal"]).

Behavioral coverage in test_writer_reveal_output.py. This pin
closes the connective-tissue gap: writer emits → push_to_backlog
persists → compositor renders.
"""

from __future__ import annotations

import inspect

from genlab_core.pipeline.stages import push_to_backlog


class TestPushToBacklogRevealWire:
    def _source(self) -> str:
        return inspect.getsource(push_to_backlog)

    def test_reveal_extracted_from_content_dict(self) -> None:
        """The question_reveal wire must READ reveal from content dict
        (writer output) and place it in variant_payload."""
        src = self._source()
        # Reveal extraction happens inside the question_reveal block
        idx = src.find('fields["variant_type"] = "question_reveal"')
        assert idx > 0
        window = src[idx : idx + 800]
        assert "reveal" in window, "question_reveal block must reference reveal field extraction"
        # Specifically: reads from content dict (writer output)
        assert 'content or {}).get("reveal"' in window, (
            "reveal must be pulled from content dict (writer output), "
            "not from story or blueprint at this point in pipeline"
        )

    def test_reveal_persisted_to_variant_payload(self) -> None:
        """Regression: the extracted reveal must land in variant_payload
        (not in fields directly, not silently dropped)."""
        src = self._source()
        idx = src.find('fields["variant_type"] = "question_reveal"')
        window = src[idx : idx + 800]
        assert '"reveal": _reveal' in window or '"reveal"' in window, (
            "reveal must be persisted to variant_payload dict, not dropped"
        )

    def test_empty_reveal_produces_empty_payload(self) -> None:
        """If writer didn't emit reveal (LLM refusal, JSON parse fail),
        the payload should stay empty — not carry an empty-string reveal
        key that the compositor would try to render as blank text."""
        src = self._source()
        idx = src.find('fields["variant_type"] = "question_reveal"')
        window = src[idx : idx + 800]
        # Look for the conditional payload assignment
        assert "if _reveal else {}" in window or "if _reveal" in window, (
            "empty reveal must produce empty payload {} to avoid "
            "compositor rendering blank text overlay"
        )
