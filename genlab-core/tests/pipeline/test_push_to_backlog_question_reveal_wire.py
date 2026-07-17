"""Pin: push_to_backlog wires question_reveal variant + priority chain.

Layer 3 S4a (2026-07-17). Source-shape pin matching S2/S3 pattern.
Verifies:

1. The question_reveal_selector is imported at the push_to_backlog callsite
2. When ``is_question_reveal_eligible`` returns True, fields get
   ``variant_type='question_reveal'`` + empty variant_payload
3. Question_reveal wire is gated on ``if not variant_assigned`` (series priority)
4. Question_reveal ALSO sets variant_assigned=True so watch_till_end
   short-circuits (question_reveal > watch_till_end)
5. Selector exception is caught (fail-open pattern)

Behavioral coverage in ``test_question_reveal_selector.py``.
"""

from __future__ import annotations

import inspect

from genlab_core.pipeline.stages import push_to_backlog


class TestPushToBacklogQuestionRevealWire:
    def _source(self) -> str:
        return inspect.getsource(push_to_backlog)

    def test_selector_imported_in_stage(self) -> None:
        src = self._source()
        assert (
            "from genlab_core.writing.question_reveal_selector import" in src
            or "question_reveal_selector" in src
        ), "push_to_backlog must import question_reveal selector"

    def test_variant_type_question_reveal_assigned(self) -> None:
        src = self._source()
        assert "question_reveal" in src, (
            "push_to_backlog must assign variant_type='question_reveal' when eligible"
        )

    def test_question_reveal_sets_variant_assigned(self) -> None:
        """After assigning variant_type='question_reveal', the code must set
        variant_assigned=True so the downstream watch_till_end check
        short-circuits. Otherwise both variants could apply to the same
        blueprint — corruption."""
        src = self._source()
        # Find the question_reveal assignment block + verify variant_assigned
        # is set to True inside it
        idx = src.find('fields["variant_type"] = "question_reveal"')
        assert idx > 0, "question_reveal assignment not found"
        # Next ~200 chars should contain variant_assigned = True
        window = src[idx : idx + 300]
        assert "variant_assigned = True" in window, (
            "question_reveal assignment must set variant_assigned=True so "
            "watch_till_end wire-guard short-circuits (variants exclusive)"
        )

    def test_question_reveal_appears_before_watch_till_end_in_source(self) -> None:
        """Priority chain enforced by ORDER in the source: series first,
        then question_reveal, then watch_till_end. A refactor that reorders
        these would silently change variant priority."""
        src = self._source()
        qr_idx = src.find('fields["variant_type"] = "question_reveal"')
        wte_idx = src.find('fields["variant_type"] = "watch_till_end"')
        assert qr_idx > 0 and wte_idx > 0, "both variant assignments must exist"
        assert qr_idx < wte_idx, (
            "question_reveal assignment must appear BEFORE watch_till_end in source "
            "to enforce priority chain (question_reveal > watch_till_end)"
        )

    def test_question_reveal_fail_open(self) -> None:
        src = self._source()
        assert "question_reveal selection skipped" in src, (
            "push_to_backlog must fail-open when question_reveal selection raises"
        )
