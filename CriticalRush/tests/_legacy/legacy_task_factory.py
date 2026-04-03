"""Tests for the Prefect task factory."""

import pytest


class TestMakeStageTask:
    def test_creates_callable(self):
        """make_stage_task returns a callable Prefect task."""
        from genlab_core.orchestration.task_factory import make_stage_task

        class DummyStage:
            def execute(self, context):
                context["dummy_ran"] = True
                return context

        task_fn = make_stage_task(DummyStage, tags=["test"])
        # The returned object should be callable (it's a Prefect task)
        assert callable(task_fn)

    def test_task_has_correct_name(self):
        """Task should be named after the stage class."""
        from genlab_core.orchestration.task_factory import make_stage_task

        class MyCustomStage:
            def execute(self, context):
                return context

        task_fn = make_stage_task(MyCustomStage)
        assert task_fn.name == "MyCustomStage"

    def test_task_executes_stage(self):
        """When called directly (outside Prefect), the task should run the stage."""
        from genlab_core.orchestration.task_factory import make_stage_task

        class AccumulatorStage:
            def execute(self, context):
                context["count"] = context.get("count", 0) + 1
                return context

        task_fn = make_stage_task(AccumulatorStage)
        # Call the underlying function directly (bypass Prefect runtime)
        result = task_fn.fn({"count": 5})
        assert result["count"] == 6

    def test_task_propagates_exceptions(self):
        """Stage exceptions should propagate through the task."""
        from genlab_core.orchestration.task_factory import make_stage_task

        class FailingStage:
            def execute(self, context):
                raise ValueError("stage failed")

        task_fn = make_stage_task(FailingStage)
        with pytest.raises(ValueError, match="stage failed"):
            task_fn.fn({})
