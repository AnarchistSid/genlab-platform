"""A stage that discarded everything did not complete.

2026-09-21, the real run this pins. PushToBacklog received 3 stories and
produced 0 blueprints — every insert died on

    column "source_url" of relation "blueprints" does not exist

It logged one WARNING per blueprint, reported "Stage PushToBacklog completed
in 14.5s", the run exited 0, systemd recorded success and the timer went
green. Four niches produced nothing for 24 hours and nothing paged. Every
piece of information was present; none of it was a failure signal.

The condition is deliberately narrow: items received AND none produced. An
empty input is a legitimate outcome ("nothing trending today") and must stay
quiet, or the signal is worth nothing.
"""

from __future__ import annotations

import logging

from genlab_core.pipeline.stage_runner import AllDiscarded, LocalStageRunner, StageResult

REAL_ERROR = 'column "source_url" of relation "blueprints" does not exist'


class PushToBacklog:
    """Minimal stand-in; the runner names the stage from the class."""

    def __init__(self, sentinel: str | None = None):
        self._sentinel = sentinel

    def execute(self, context):
        if self._sentinel is not None:
            context["_all_discarded"] = self._sentinel
        return context


def _run(stage, context) -> StageResult:
    return LocalStageRunner().run_stage(stage, context, None)


class TestAllDiscarded:
    def test_a_stage_that_discarded_everything_fails_the_run(self, caplog):
        ctx = {"stories": [{"id": 1}, {"id": 2}, {"id": 3}]}
        with caplog.at_level(logging.ERROR):
            result = _run(PushToBacklog(REAL_ERROR), ctx)
        assert result.success is False, "'completed' must not be available here"
        assert isinstance(result.error, AllDiscarded)
        assert "all_discarded" in str(result.error)
        assert REAL_ERROR in str(result.error)

    def test_it_logs_at_error_not_warning(self, caplog):
        """A per-item WARNING is what let this run green for a day."""
        ctx = {"stories": [{"id": 1}]}
        with caplog.at_level(logging.DEBUG):
            _run(PushToBacklog(REAL_ERROR), ctx)
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors, "must be ERROR — WARNING is what was already there"
        assert any("all_discarded" in r.getMessage() for r in errors)

    def test_a_stage_that_produced_something_still_completes(self):
        assert _run(PushToBacklog(None), {"stories": [{"id": 1}]}).success is True

    def test_an_empty_input_is_not_a_discard(self):
        """"Nothing trending today" is a real outcome and must stay quiet."""
        assert _run(PushToBacklog(None), {"stories": []}).success is True

    def test_the_sentinel_does_not_leak_to_the_next_stage(self):
        ctx = {"stories": [{"id": 1}]}
        _run(PushToBacklog(REAL_ERROR), ctx)
        assert "_all_discarded" not in ctx, "a leaked sentinel fails the next stage too"


class TestPushToBacklogSetsIt:
    def test_the_condition_is_items_in_and_none_out(self):
        """Pin the shape in the source: received > 0 AND produced == 0."""
        import inspect

        from genlab_core.pipeline.stages import push_to_backlog

        src = inspect.getsource(push_to_backlog)
        assert 'context["_all_discarded"]' in src
        assert "if candidates_in and not blueprints_pushed:" in src, (
            "the guard must require BOTH items received and none produced; "
            "firing on an empty input makes the signal worthless"
        )
