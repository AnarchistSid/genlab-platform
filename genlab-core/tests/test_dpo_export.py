"""Pin: DPO preference-pair JSONL export.

The 2026-06-22 roadmap identified DPO from operator+engagement edits
as the highest single intelligence lever. preference_collector has
been populating ``preference_data`` weekly since shipping but no
consumer EXPORTS those pairs in a training-consumable format. This
module closes that gap.

These pins guard:
- JSONL format: each line is {prompt, chosen, rejected, metadata}
- Idempotency: used_in_training flag prevents re-export
- Recovery: --dry-run does NOT mark rows (operator can inspect first)
- Fail-OPEN: missing DATABASE_URL → return 0 (silent no-op)
- Prompt reconstruction: stored prompt > story title > generic fallback
- Output path: $GENLAB_PROJECT_ROOT/.tmp/dpo/preference_data_YYYY-MM-DD.jsonl
- systemd units exist + reference correct module
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch


class TestJsonlFormat:
    def test_each_line_is_valid_json_with_required_keys(self, tmp_path):
        """Pin: each JSONL line must parse + contain prompt+chosen+rejected.
        This is the canonical DPO training format consumed by TRL,
        Anthropic fine-tune API, and most preference-learning libs."""
        from genlab_core.learning.dpo_export import write_jsonl

        rows = [
            {
                "id": "id-1",
                "niche_id": "gaming",
                "platform": "instagram",
                "generation_context": {"story_title": "Insane play"},
                "chosen_hook": "You won't believe this play",
                "chosen_caption": "wow",
                "rejected_hook": "Just a play",
                "rejected_caption": "ok",
                "engagement_ratio": 3.2,
            },
        ]
        output = tmp_path / "test.jsonl"
        n = write_jsonl(rows, output)
        assert n == 1

        lines = output.read_text().strip().split("\n")
        assert len(lines) == 1
        parsed = json.loads(lines[0])
        # Required keys for DPO
        assert "prompt" in parsed
        assert "chosen" in parsed
        assert "rejected" in parsed
        # Values
        assert parsed["chosen"] == "You won't believe this play"
        assert parsed["rejected"] == "Just a play"
        # Metadata for filtering / weighting
        assert parsed["metadata"]["niche_id"] == "gaming"
        assert parsed["metadata"]["platform"] == "instagram"
        assert parsed["metadata"]["engagement_ratio"] == 3.2

    def test_multiple_rows_one_per_line(self, tmp_path):
        """Pin: N rows → N lines. JSONL contract: each row is its own
        valid JSON object, separated by newlines. NO array wrapper."""
        from genlab_core.learning.dpo_export import write_jsonl

        rows = [
            {
                "id": f"id-{i}",
                "niche_id": "gaming",
                "platform": "instagram",
                "generation_context": {},
                "chosen_hook": f"chosen {i}",
                "chosen_caption": "",
                "rejected_hook": f"rejected {i}",
                "rejected_caption": "",
                "engagement_ratio": 2.0,
            }
            for i in range(5)
        ]
        output = tmp_path / "multi.jsonl"
        n = write_jsonl(rows, output)
        assert n == 5
        lines = output.read_text().strip().split("\n")
        assert len(lines) == 5
        # NOT a JSON array
        first_line = lines[0]
        assert first_line.startswith("{")
        assert not first_line.startswith("[")


class TestPromptReconstruction:
    def test_stored_prompt_preferred(self):
        """Pin: when generation_context.prompt is present, use it."""
        from genlab_core.learning.dpo_export import _build_prompt

        result = _build_prompt(
            "gaming",
            "instagram",
            {"prompt": "Original stored prompt with full context"},
        )
        assert result == "Original stored prompt with full context"

    def test_story_title_fallback(self):
        """Pin: no stored prompt but story_title present → reconstruct
        a meaningful prompt using the title."""
        from genlab_core.learning.dpo_export import _build_prompt

        result = _build_prompt(
            "gaming",
            "instagram",
            {"story_title": "Insane esports moment"},
        )
        assert "Insane esports moment" in result
        assert "gaming" in result
        assert "instagram" in result

    def test_empty_context_uses_generic_fallback(self):
        """Pin: empty/None context → generic template per (niche, platform)
        — still useful conditioning even without recorded prompt."""
        from genlab_core.learning.dpo_export import _build_prompt

        result = _build_prompt("gaming", "instagram", {})
        assert "gaming" in result
        assert "instagram" in result

        result_none = _build_prompt("anime", "youtube", None)
        assert "anime" in result_none


class TestFailOpen:
    def test_no_database_url_returns_empty(self, monkeypatch):
        """Pin: DATABASE_URL unset → empty list (no crash). Exporter
        must work safely in dev/test where Postgres isn't running."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        from genlab_core.learning.dpo_export import fetch_unexported_pairs

        result = fetch_unexported_pairs()
        assert result == []

    def test_export_with_no_rows_returns_zero(self, monkeypatch, tmp_path):
        """Pin: zero unexported rows → write nothing, return 0. NOT an
        error condition; just a quiet weekend with no new pairs."""
        from genlab_core.learning import dpo_export

        with patch.object(dpo_export, "fetch_unexported_pairs", return_value=[]):
            n = dpo_export.export(output_dir=tmp_path)
        assert n == 0


class TestIdempotency:
    def test_dry_run_does_not_mark_exported(self, monkeypatch, tmp_path):
        """Pin: dry-run mode writes JSONL but does NOT call
        mark_rows_exported. Operator can inspect output before
        committing (used_in_training=true is harder to reverse)."""
        from genlab_core.learning import dpo_export

        rows = [
            {
                "id": "id-1",
                "niche_id": "gaming",
                "platform": "instagram",
                "generation_context": {},
                "chosen_hook": "a",
                "chosen_caption": "",
                "rejected_hook": "b",
                "rejected_caption": "",
                "engagement_ratio": 2.0,
            }
        ]
        mark_called = []
        with patch.object(dpo_export, "fetch_unexported_pairs", return_value=rows):
            with patch.object(
                dpo_export,
                "mark_rows_exported",
                side_effect=lambda ids: mark_called.append(ids) or 0,
            ):
                dpo_export.export(output_dir=tmp_path, mark_exported=False)
        # File written
        files = list(tmp_path.glob("preference_data_*.jsonl"))
        assert len(files) == 1
        # But mark NOT called
        assert mark_called == [], "dry-run must NOT call mark_rows_exported"

    def test_normal_run_marks_exported(self, monkeypatch, tmp_path):
        """Pin: default (non-dry-run) calls mark_rows_exported with the
        full id list. Idempotency depends on this — next run skips
        these rows."""
        from genlab_core.learning import dpo_export

        rows = [
            {
                "id": f"id-{i}",
                "niche_id": "gaming",
                "platform": "instagram",
                "generation_context": {},
                "chosen_hook": "a",
                "chosen_caption": "",
                "rejected_hook": "b",
                "rejected_caption": "",
                "engagement_ratio": 2.0,
            }
            for i in range(3)
        ]
        marked = []
        with patch.object(dpo_export, "fetch_unexported_pairs", return_value=rows):
            with patch.object(
                dpo_export, "mark_rows_exported", side_effect=lambda ids: marked.extend(ids) or 3
            ):
                dpo_export.export(output_dir=tmp_path, mark_exported=True)
        assert sorted(marked) == ["id-0", "id-1", "id-2"]


class TestSystemdUnits:
    def test_dpo_export_service_exists(self):
        path = (
            Path(__file__).resolve().parents[2]
            / "deploy"
            / "systemd-phase2"
            / "genlab-dpo-export.service"
        )
        assert path.exists()

    def test_dpo_export_timer_exists(self):
        path = (
            Path(__file__).resolve().parents[2]
            / "deploy"
            / "systemd-phase2"
            / "genlab-dpo-export.timer"
        )
        assert path.exists()

    def test_service_invokes_dpo_export_module(self):
        """Pin: ExecStart calls the canonical -m entry, NOT inline
        -c (which is fragile with quoting)."""
        path = (
            Path(__file__).resolve().parents[2]
            / "deploy"
            / "systemd-phase2"
            / "genlab-dpo-export.service"
        )
        content = path.read_text()
        assert "python -m genlab_core.learning.dpo_export" in content

    def test_timer_fires_after_preference_collector(self):
        """Pin: timer must fire ON Sunday and AFTER the
        preference-collector's 04:00 UTC fire. The collector populates
        preference_data; the exporter consumes it — if order is
        reversed, the exporter ships the prior week's data forever."""
        path = (
            Path(__file__).resolve().parents[2]
            / "deploy"
            / "systemd-phase2"
            / "genlab-dpo-export.timer"
        )
        content = path.read_text()
        # OnCalendar must mention Sun (preference-collector also fires Sun)
        assert "Sun " in content
        # And must fire AFTER 04:00 UTC (the collector's fire time)
        import re

        m = re.search(r"OnCalendar=Sun \*-\*-\* (\d{2}):(\d{2})", content)
        assert m, "Timer must use OnCalendar=Sun *-*-* HH:MM format"
        hour = int(m.group(1))
        # collector fires at 04:00 UTC; exporter must fire later same day
        assert hour > 4, f"Exporter must fire after collector (04:00 UTC); got {hour:02d}:00"


class TestPreferenceCollectorCLI:
    def test_collector_has_main_entrypoint(self):
        """Pin: preference_collector.py has a `main()` function +
        __main__ block. Replaces the older inline `-c` invocation in
        the systemd service unit (which had quoting fragility)."""
        from pathlib import Path

        src = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "genlab_core"
            / "learning"
            / "preference_collector.py"
        )
        content = src.read_text()
        assert "def main()" in content
        assert '__name__ == "__main__"' in content
