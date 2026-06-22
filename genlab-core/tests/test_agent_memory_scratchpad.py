r"""Pin: agent-memory scratchpad generator + hook generator consumer.

The 2026-06-22 'make-the-agent-smarter' research synthesis identified
persistent agent memory (Cat 3, Agent D) as the lever that unlocks the
next architectural tier. This module SHIPS that layer:

  1. ``generate_weekly_scratchpad()`` reads past 7 days of
     episodic_events, asks Opus to identify patterns, writes
     markdown to .tmp/agent_memory.md
  2. ``read_scratchpad()`` is the consumer-facing helper
  3. Hook generator prepends the scratchpad to its system prompt

Cost: 1 Opus call/week × ~\$0.50 = ~\$2/month. Worth the spend.

These pins guard:
- Generator: handles no-events / no-backend / LLM-error / empty-LLM
  gracefully (always writes SOMETHING readable to the output path)
- Fallback markdown is well-formed + says why it's empty
- Header includes the model name + event count (operator audit trail)
- Consumer: reads gracefully, returns '' on missing/error
- Consumer: truncates to max_chars to bound prompt cost
- Hook generator: prepends scratchpad when present, omits when empty
- systemd unit: schedules weekly Sunday + runs as genlab + uses
  proper module entry (-m, not fragile -c)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch


class TestFallbackMarkdown:
    def test_no_backend_writes_readable_fallback(self, tmp_path, monkeypatch):
        """Pin: when episodic backend is unavailable, generator writes
        a fallback markdown explaining why. The hook consumer reads
        this and gets a graceful 'no scratchpad context' message,
        NOT an exception or empty file."""
        from genlab_core.learning import scratchpad

        output = tmp_path / "scratchpad.md"
        with patch(
            "genlab_core.learning.episodic_memory._get_default_backend",
            return_value=None,
        ):
            ok = scratchpad.generate_weekly_scratchpad(output_path=output)
        assert ok is False
        assert output.exists()
        text = output.read_text()
        assert "Agent Memory" in text
        # The fallback reason must be present (operator can grep for it)
        assert "no" in text.lower() or "unavailable" in text.lower()

    def test_no_events_writes_no_data_fallback(self, tmp_path):
        """Pin: backend exists but no events in window → fallback
        markdown says 'no events'. Common state during the first
        week of episodic memory deployment."""
        from genlab_core.learning import scratchpad

        output = tmp_path / "scratchpad.md"
        fake_backend = MagicMock()
        fake_backend.query.return_value = []
        with patch(
            "genlab_core.learning.episodic_memory._get_default_backend",
            return_value=fake_backend,
        ):
            ok = scratchpad.generate_weekly_scratchpad(output_path=output)
        assert ok is False
        text = output.read_text()
        assert "no events" in text.lower() or "no event" in text.lower()


class TestSuccessfulGeneration:
    def test_writes_markdown_with_header(self, tmp_path):
        """Pin: successful generation writes header (timestamp +
        model + event count) + LLM response. Operator audit trail
        depends on these fields."""
        from genlab_core.learning import episodic_memory, scratchpad

        output = tmp_path / "scratchpad.md"
        fake_events = [
            episodic_memory.EpisodicEvent(
                event_type="operator_review",
                niche_id="gaming",
                blueprint_id="bp1",
                timestamp="2026-06-20T12:00:00+00:00",
                payload={"action": "rejected", "feedback_issue": "weak_hook"},
            ),
        ]
        fake_backend = MagicMock()
        fake_backend.query.return_value = fake_events
        fake_response = MagicMock()
        fake_response.content = [MagicMock(text="## Top wins\n- example win\n")]

        with patch(
            "genlab_core.learning.episodic_memory._get_default_backend",
            return_value=fake_backend,
        ):
            with patch("anthropic.Anthropic") as fake_client_cls:
                fake_client = MagicMock()
                fake_client.messages.create.return_value = fake_response
                fake_client_cls.return_value = fake_client
                with patch(
                    "genlab_core.http.circuit_breaker.ANTHROPIC_CB.call",
                    side_effect=lambda f: f(),
                ):
                    with patch("genlab_core.intelligence.cost_accumulator.record_anthropic_usage"):
                        ok = scratchpad.generate_weekly_scratchpad(output_path=output)
        assert ok is True
        text = output.read_text()
        # Header sections
        assert "Agent Memory" in text
        assert "Source:" in text
        assert "1 episodic events" in text  # event count
        # LLM output
        assert "Top wins" in text
        assert "example win" in text

    def test_llm_error_writes_fallback_not_raise(self, tmp_path):
        """Pin: LLM call exception writes a fallback markdown AND
        returns False. Generator MUST NOT raise — fail-OPEN at the
        process boundary so systemd doesn't see a unit failure
        for an API hiccup."""
        from genlab_core.learning import episodic_memory, scratchpad

        output = tmp_path / "scratchpad.md"
        fake_events = [
            episodic_memory.EpisodicEvent(
                event_type="operator_review",
                niche_id="gaming",
                blueprint_id="bp1",
                timestamp="2026-06-20T12:00:00+00:00",
                payload={"action": "approved"},
            )
        ]
        fake_backend = MagicMock()
        fake_backend.query.return_value = fake_events

        with patch(
            "genlab_core.learning.episodic_memory._get_default_backend",
            return_value=fake_backend,
        ):
            with patch("anthropic.Anthropic", side_effect=RuntimeError("api down")):
                # MUST NOT raise
                ok = scratchpad.generate_weekly_scratchpad(output_path=output)
        assert ok is False
        text = output.read_text()
        # Fallback explains the failure reason for operator triage
        assert "Agent Memory" in text
        assert "LLM" in text or "api" in text.lower() or "failed" in text.lower()


class TestReadScratchpad:
    def test_returns_empty_when_missing(self, monkeypatch, tmp_path):
        """Pin: missing file → empty string. Consumers (hook generator)
        rely on '' as the 'no scratchpad' signal."""
        from genlab_core.learning import scratchpad

        monkeypatch.setenv("AGENT_MEMORY_PATH", str(tmp_path / "does_not_exist.md"))
        result = scratchpad.read_scratchpad()
        assert result == ""

    def test_returns_file_contents_when_present(self, monkeypatch, tmp_path):
        """Pin: existing file is read + returned verbatim."""
        from genlab_core.learning import scratchpad

        p = tmp_path / "scratchpad.md"
        p.write_text("# Insights\n- thing 1\n- thing 2\n")
        monkeypatch.setenv("AGENT_MEMORY_PATH", str(p))
        result = scratchpad.read_scratchpad()
        assert "Insights" in result
        assert "thing 1" in result

    def test_truncates_to_max_chars(self, monkeypatch, tmp_path):
        """Pin: huge scratchpad (would bloat prompts) is truncated.
        Default 4000 chars; truncation marker preserves transparency."""
        from genlab_core.learning import scratchpad

        p = tmp_path / "huge.md"
        p.write_text("x" * 10000)  # 10KB
        monkeypatch.setenv("AGENT_MEMORY_PATH", str(p))
        result = scratchpad.read_scratchpad(max_chars=1000)
        assert len(result) <= 1000 + len("\n\n... [truncated]")
        assert result.endswith("[truncated]")


class TestHookGeneratorConsumer:
    """Source-level pins: hook generator imports + uses read_scratchpad
    and prepends to the system prompt."""

    def setup_method(self):
        self.src = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "genlab_core"
            / "writing"
            / "llm_hook_generator.py"
        )
        self.content = self.src.read_text()

    def test_imports_read_scratchpad(self):
        """Pin: imports read_scratchpad from the scratchpad module."""
        assert "from genlab_core.learning.scratchpad import read_scratchpad" in self.content

    def test_prepends_scratchpad_when_present(self):
        """Pin: when scratchpad is non-empty, it's wrapped in
        ``## Recent learnings (scratchpad)`` and prepended BEFORE
        the constitution. Position matters — LLMs attend more to
        early prompt content."""
        # Wrapper header is present
        assert "## Recent learnings (scratchpad)" in self.content
        # Empty scratchpad → empty block (no header pollution)
        assert "scratchpad_block = " in self.content
        # Block is concatenated INTO the system prompt assembly
        assert "+ scratchpad_block" in self.content


class TestSystemdUnits:
    def setup_method(self):
        self.deploy = Path(__file__).resolve().parents[2] / "deploy" / "systemd-phase2"

    def test_service_file_exists(self):
        assert (self.deploy / "genlab-agent-memory-scratchpad.service").exists()

    def test_timer_file_exists(self):
        assert (self.deploy / "genlab-agent-memory-scratchpad.timer").exists()

    def test_service_runs_as_genlab_not_root(self):
        """Pin: scratchpad writes to /opt/genlab/.tmp/ which is
        genlab-owned. Running as root would create root-owned files
        + trigger the permission_drift alert."""
        content = (self.deploy / "genlab-agent-memory-scratchpad.service").read_text()
        assert "User=genlab" in content
        assert "Group=genlab" in content

    def test_service_invokes_canonical_module_entry(self):
        """Pin: ExecStart uses ``python -m`` (not fragile inline -c).
        The -c shape was the quoting trap that bit
        preference_collector's earlier setup."""
        content = (self.deploy / "genlab-agent-memory-scratchpad.service").read_text()
        assert "python -m genlab_core.learning.scratchpad" in content

    def test_timer_fires_sunday(self):
        """Pin: weekly Sunday cadence. Matches the Sunday job stack
        (gate_tuner + preference_collector + dpo_export all on Sunday)."""
        content = (self.deploy / "genlab-agent-memory-scratchpad.timer").read_text()
        assert "OnCalendar=Sun" in content

    def test_timer_fires_before_dpo_export(self):
        """Pin: scratchpad fires BEFORE dpo-export (05:30 UTC) so the
        Monday hook generations read fresh markdown. Anything ≥ 05:30
        would land AFTER dpo-export and miss the synergy window."""
        content = (self.deploy / "genlab-agent-memory-scratchpad.timer").read_text()
        import re

        m = re.search(r"OnCalendar=Sun \*-\*-\* (\d{2}):(\d{2})", content)
        assert m, "Timer must use OnCalendar=Sun *-*-* HH:MM format"
        hour = int(m.group(1))
        assert hour < 5, f"Scratchpad must fire before dpo-export at 05:30 UTC; got {hour:02d}:00"

    def test_timer_persistent_for_reboot_catchup(self):
        """Pin: missed Sunday after reboot catches up. Without
        Persistent=true a Sunday-night VPS reboot would skip the
        weekly scratchpad entirely."""
        content = (self.deploy / "genlab-agent-memory-scratchpad.timer").read_text()
        assert "Persistent=true" in content
