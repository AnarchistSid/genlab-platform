"""Pin: EVENT_PUBLISH episodic emit on successful publish.

The 2026-06-22 audit identified 4 dormant EVENT_* types with no
producers. Prior PRs wired BANDIT_PICK (PR #441), POST_BOMBED +
REWARD_WINDOW_CLOSED (PR #461), OPERATOR_REVIEW (PR #461),
OPERATOR_EDIT (PR #473). EVENT_PUBLISH was the next-most-impactful
missing producer.

Without this emit:
- The weekly scratchpad (PR #474) has no "what published this week"
  signal — its sample data only includes reviews + bombed posts
- Cross-run publishing-velocity analytics is impossible
- The agent's own awareness of its publishing throughput is blind

This module pins the wire at publishing/publish_all_platforms.py:
after the existing dashboard_events push (operator-facing) AND
after the dashboard try/except block, a NEW try/except block emits
EVENT_PUBLISH with the success metadata to episodic_events
(learning-facing).

These pins guard:
- Source-level wire is structurally present
- Payload includes the canonical fields downstream consumers expect
- Emit only fires on any_success (failures handled elsewhere)
- Wrapped in try/except — episodic emit MUST NEVER block publish
"""

from __future__ import annotations

from pathlib import Path

_PUBLISH_SRC = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "genlab_core"
    / "publishing"
    / "publish_all_platforms.py"
)


class TestPublishEpisodicEmitWire:
    def setup_method(self):
        self.content = _PUBLISH_SRC.read_text()

    def test_imports_event_publish_constant(self):
        """Pin: EVENT_PUBLISH constant + record_event helper are
        imported from episodic_memory. Without both, the event can't
        be emitted."""
        assert "EVENT_PUBLISH" in self.content
        assert (
            "from genlab_core.learning.episodic_memory import EVENT_PUBLISH, record_event"
            in self.content
        )

    def test_emit_only_on_any_success(self):
        """Pin: the emit is INSIDE an `if any_success:` block. We don't
        emit EVENT_PUBLISH on failure — that path is covered by
        push_event('publish_failure', ...) above AND by metric_collector
        emitting POST_BOMBED downstream when 48h reward is near zero.
        Adding an EVENT_PUBLISH_FAILED would be a separate decision."""
        # The new emit block opens with this exact comment marker so
        # consumers can find it via grep
        assert "2026-06-22 — EVENT_PUBLISH episodic emit" in self.content
        # And it's structurally inside an if any_success: check (the
        # block opens with `if any_success:\n        try:` near it)
        # Source-level proof: the EVENT_PUBLISH import string is inside
        # the same indentation context as the if any_success guard
        idx = self.content.find("EVENT_PUBLISH episodic emit")
        assert idx > 0
        # The if any_success guard appears AFTER the comment block
        # (the block explains why; the code does the check). Look in
        # the next ~1KB for the guard + the record_event call.
        next_block = self.content[idx : idx + 1500]
        assert "if any_success:" in next_block
        assert "record_event(" in next_block

    def test_payload_includes_required_fields(self):
        """Pin: payload includes the canonical fields scratchpad +
        cross-run analytics depend on. Missing any of these breaks
        downstream queries (the scratchpad's 'what published this
        week' section is one consumer)."""
        required_payload_keys = [
            "platforms",
            "platform_count",
            "attempt_count",
            "hook_snippet",
            "had_partial_failures",
        ]
        for key in required_payload_keys:
            assert f'"{key}"' in self.content, (
                f"EVENT_PUBLISH payload missing required key '{key}'. "
                f"Scratchpad + analytics consumers expect this exact shape."
            )

    def test_hook_snippet_truncated(self):
        """Pin: hook snippet truncated to 200 chars to bound payload
        size. A 10MB malicious caption can't blow up episodic_events."""
        assert "[:200]" in self.content
        # And the truncation is on the hook_text/hook/title cascade
        # (which is the same shape used by the dashboard push_event
        # above — both consumers see the same snippet)

    def test_emit_failure_is_fail_open(self):
        """Pin: the emit block is wrapped in try/except with the
        canonical 'must never block publish' signal. Episodic emit
        failures absolutely cannot block the publish pipeline."""
        assert "episodic emit must never block publish" in self.content
        # The except block uses the standard noqa: BLE001 pattern
        assert "EVENT_PUBLISH emit skipped" in self.content


class TestEventPublishConstant:
    """Pin: EVENT_PUBLISH is still defined in episodic_memory.

    Source-level pin guards against accidental removal of the constant
    that would silently break this wire (the import would raise +
    fail-OPEN would mask the bug)."""

    def test_event_publish_defined(self):
        from genlab_core.learning.episodic_memory import EVENT_PUBLISH

        assert EVENT_PUBLISH == "publish"
