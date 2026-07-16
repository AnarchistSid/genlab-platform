"""Pin the ``_skip_llm`` filter in QCGates (2026-06-20 fix).

## What this regresses against

Pre-fix, QCGates iterated over every story in ``context["stories"]``,
including stories that earlier stages had marked as
``_skip_llm=True`` (set by VideoGate when no clip exists, or by
WritingStrategy when the LLM returned an empty hook for banned /
off-topic / un-writable source material).

Those stories will be DROPPED at PushToBacklog
(``push_to_backlog.py:1173``) before reaching the backlog. They are
NOT publishable. Including them in QC produced misleading
"Missing required field: caption/body" failures because they had a
recovered hook (from HookStrategy's title-derived recovery at
``base_hooks.py:275, 296``) but no caption — writing was skipped.

The operator-facing impact: gaming dashboard showed 60% QC pass rate
on 2026-06-20, attributing the failure to writing quality. The real
cause was upstream (RSS-fed gaming-news articles with no associated
video clips). The misleading QC signal would have made the operator
chase the wrong layer.

## What the fix does

1. Filter ``_skip_llm`` stories BEFORE the QC loop. They're dropped
   downstream regardless; their inclusion only pollutes the report.
2. Surface ``excluded_skip_llm`` in ``run_stats["qc"]`` so the
   dashboard can distinguish "writing regression" from "writing
   correctly declined un-publishable source material".
3. Early-return when ALL stories are LLM-skipped (writes a degenerate
   QC stats dict instead of crashing on empty input).
"""

from __future__ import annotations

from genlab_core.pipeline.stages.qc_gates import QCGates


def _make_complete_story(title: str = "Real Story", story_id: str = "s1") -> dict:
    """Return a story that should pass QC (all required fields set)."""
    return {
        "story_id": story_id,
        "title": title,
        "source_url": "https://example.com/article",
        "content": {
            "hook": "This is a real hook with substance",
            "caption": "A real caption with body text describing the story.",
            "instagram": {"caption": "A real caption with body text."},
        },
    }


def _make_skip_llm_ghost(title: str = "Ghost Story", story_id: str = "g1") -> dict:
    """Return a story with ``_skip_llm=True`` and a recovered hook.

    This is the exact shape that pre-fix would have been QC'd and
    failed for "Missing required field: caption/body". Hook is
    populated (HookStrategy's title-derived recovery), but caption /
    body is empty (writing was skipped).
    """
    return {
        "story_id": story_id,
        "title": title,
        "source_url": "https://example.com/article",
        "_skip_llm": True,
        "content": {
            "hook": "Hook recovered from title",
            # No caption, no body, no content.instagram.caption
        },
    }


class TestSkipLLMFilter:
    def test_skip_llm_story_excluded_from_qc(self):
        """A story with ``_skip_llm=True`` is not validated by QC."""
        ctx = {"stories": [_make_skip_llm_ghost()], "niche_config": {}}
        result = QCGates().execute(ctx)
        qc = result["run_stats"]["qc"]
        assert qc["total"] == 0, (
            "skip-llm ghost should NOT count toward QC total; pre-fix it would "
            "have failed with 'Missing caption/body' and inflated failed count"
        )
        assert qc["failed"] == 0
        assert qc["passed"] == 0
        assert qc["excluded_skip_llm"] == 1

    def test_real_story_alongside_ghost_still_validated(self):
        """One ghost + one real story → QC validates only the real one."""
        ctx = {
            "stories": [_make_complete_story(), _make_skip_llm_ghost()],
            "niche_config": {},
        }
        result = QCGates().execute(ctx)
        qc = result["run_stats"]["qc"]
        assert qc["total"] == 1, "ghost excluded, real story validated"
        assert qc["passed"] == 1
        assert qc["failed"] == 0
        assert qc["excluded_skip_llm"] == 1

    def test_all_ghosts_returns_degenerate_qc_stats(self):
        """When every story is skip-llm, QC short-circuits cleanly."""
        ctx = {
            "stories": [_make_skip_llm_ghost("a", "1"), _make_skip_llm_ghost("b", "2")],
            "niche_config": {},
        }
        result = QCGates().execute(ctx)
        qc = result["run_stats"]["qc"]
        assert qc["total"] == 0
        assert qc["pass_rate"] == "n/a"
        assert qc["excluded_skip_llm"] == 2, "both ghosts counted in exclusion"

    def test_real_dashboard_scenario_60_percent_becomes_100_percent(self):
        """The actual 2026-06-20 gaming run shape: 5 stories, 3 real + 2 ghosts.

        Pre-fix: 3/5 passed = 60% pass rate, 2 "Missing caption/body" failures.
        Post-fix: 3/3 = 100% pass rate, 2 excluded as skip-llm.

        Use semantically distinct titles + URLs so DedupEngine (which
        runs inside QCGates at qc_gates.py:47) doesn't collapse them
        — that's a separate concern from the skip-llm filter we're
        testing here.
        """

        def real(title, story_id, hook, caption, url):
            return {
                "story_id": story_id,
                "title": title,
                "source_url": url,
                "content": {
                    "hook": hook,
                    "caption": caption,
                    "instagram": {"caption": caption},
                },
            }

        ctx = {
            "stories": [
                real(
                    "Grand Theft Auto VI cover art reveal",
                    "r1",
                    "GTA VI cover art is finally here",
                    "Rockstar dropped the GTA VI cover. Here's what we see.",
                    "https://example.com/gta6",
                ),
                real(
                    "Elden Ring Nightreign multiplayer beta launches",
                    "r2",
                    "Nightreign beta finally lets us co-op the Lands Between",
                    "FromSoftware's first proper multiplayer expansion is here.",
                    "https://example.com/elden-ring-nightreign",
                ),
                real(
                    "Valorant adds new map Sunset to ranked rotation",
                    "r3",
                    "Sunset enters ranked — meta shift incoming?",
                    "Riot just pushed Sunset to ranked. Defender side advantages explained.",
                    "https://example.com/valorant-sunset",
                ),
                _make_skip_llm_ghost("Ghost story 1 (writing skipped)", "g1"),
                _make_skip_llm_ghost("Ghost story 2 (writing skipped)", "g2"),
            ],
            "niche_config": {},
        }
        result = QCGates().execute(ctx)
        qc = result["run_stats"]["qc"]
        assert qc["total"] == 3, "only real stories validated"
        assert qc["passed"] == 3
        assert qc["failed"] == 0
        assert qc["pass_rate"] == "100.0%", (
            "the operator dashboard should now show 100% pass rate, "
            "not 60% — the gaming bug was misleading attribution"
        )
        assert qc["excluded_skip_llm"] == 2
        assert "Missing required field" not in str(qc["failure_reasons"]), (
            "no 'Missing required field' failures should appear when "
            "the underlying cause is upstream LLM-skip, not QC regression"
        )

    def test_video_gate_origin_skip_llm_also_excluded(self):
        """``_skip_llm`` set by VideoGate (not WritingStrategy) is also excluded.

        VideoGate also sets ``_skip_llm=True`` (video_gate.py:205) when
        no clip is available. Most are filtered at video_gate.py:228
        but the filter only triggers ``if skipped > 0`` and runs only
        once. If a story is re-introduced into ``context["stories"]``
        by a later stage with ``_skip_llm`` still set, QC should still
        exclude it.
        """
        # Story shape that VideoGate produced — caption/body completely
        # absent because writing never ran
        video_gate_skip = {
            "story_id": "vg1",
            "title": "Clipless story",
            "source_url": "https://example.com/article",
            "_skip_llm": True,
            # No content dict at all — most degenerate shape
        }
        ctx = {"stories": [video_gate_skip], "niche_config": {}}
        result = QCGates().execute(ctx)
        qc = result["run_stats"]["qc"]
        assert qc["excluded_skip_llm"] == 1
        assert qc["total"] == 0


class TestExistingBehaviorPreserved:
    """Pin that the fix doesn't break the normal QC validation path."""

    def test_no_stories_returns_unchanged(self):
        """Empty stories list: log + return, no exception, no qc stats."""
        ctx = {"stories": [], "niche_config": {}}
        result = QCGates().execute(ctx)
        # The original early-return path is untouched
        assert "qc" not in result.get("run_stats", {})

    def test_normal_complete_story_still_passes(self):
        """A story with all required fields passes QC as before."""
        ctx = {"stories": [_make_complete_story()], "niche_config": {}}
        result = QCGates().execute(ctx)
        qc = result["run_stats"]["qc"]
        assert qc["total"] == 1
        assert qc["passed"] == 1
        assert qc["failed"] == 0
        assert qc["excluded_skip_llm"] == 0

    def test_missing_caption_bucketed_as_excluded_incomplete(self):
        """2026-07-17: missing caption/body → `excluded_incomplete_content`
        bucket, NOT `failed`.

        Prior behaviour: this counted as `failed` and inflated the QC
        failure rate. Deep-cuts audit round 2 found 3 of 5 pipelines
        showing 0-50% "QC pass rate" — 100% of failures were this same
        empty-body shape (bare-title stories: base_writing.py:652 skipped
        LLM on sub-40-char summaries → base_hooks.py:306-318
        template-formula recovery gave the row a hook → QC then hit
        "Missing required field: caption/body" and mis-attributed as
        writing quality failure).

        New contract: bp["_skip_llm"] = True (so downstream drops it)
        + counted separately from real QC failures. Pass rate now
        reflects blueprints that COULD have shipped.

        Debugging signal still available in `failure_examples`.
        """
        broken_story = {
            "story_id": "b1",
            "title": "Genuinely broken story",
            "source_url": "https://example.com/article",
            "content": {
                "hook": "Has a hook",
                # No caption / body / instagram.caption set. The blueprint
                # cannot ship regardless of whether the cause is
                # upstream-thin OR writer-bug — both are doomed and are
                # bucketed together as "excluded_incomplete_content".
            },
        }
        ctx = {"stories": [broken_story], "niche_config": {}}
        result = QCGates().execute(ctx)
        qc = result["run_stats"]["qc"]
        assert qc["total"] == 0, (
            "Missing-body case is now excluded from the QC total (bucketed "
            "as excluded_incomplete_content). Prior semantics counted it "
            "as failed, deflating pass_rate."
        )
        assert qc["failed"] == 0
        assert qc["excluded_incomplete_content"] == 1, (
            "Missing caption/body must go to excluded_incomplete_content "
            "bucket so operator can distinguish real QC failures from "
            "upstream-doomed rows."
        )
        # And the story must be marked _skip_llm=True so downstream drops it
        assert broken_story.get("_skip_llm") is True
