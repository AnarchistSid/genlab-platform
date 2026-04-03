"""Tests for HookValidator wiring in SpliceReel hook strategy."""


from sr_strategies.hooks import MovieHookStrategy


class TestHookValidatorWiring:
    """Validate that MovieHookStrategy.execute() runs HookValidator on generated hooks."""

    def test_execute_validates_hooks(self):
        """After execute(), all hooks should pass HookValidator checks."""
        from genlab_core.intelligence.hook_validator import HookValidator

        strategy = MovieHookStrategy()
        context = {
            "stories": [
                {
                    "title": "Dune Part Three trailer drops",
                    "film_title": "Dune Part Three",
                    "is_trailer_drop": True,
                    "summary": "New trailer released",
                },
                {
                    "title": "Barbie sequel announced by Warner Bros",
                    "film_title": "Barbie 2",
                    "lifecycle_stage": "pre_release",
                    "franchise": "Barbie",
                    "summary": "Sequel confirmed",
                },
            ],
        }

        result = strategy.execute(context)

        validator = HookValidator()
        for story in result["stories"]:
            hook = story.get("content", {}).get("hook", "")
            if hook:
                vr = validator.validate(hook, platform="instagram")
                assert vr.passed, (
                    f"Hook '{hook}' failed validation: "
                    f"{[f.value for f in vr.failures]}"
                )

    def test_run_stats_includes_validation_count(self):
        """execute() should report how many hooks were validated/rejected."""
        strategy = MovieHookStrategy()
        context = {
            "stories": [
                {
                    "title": "Top Gun Maverick breaks records",
                    "film_title": "Top Gun Maverick",
                    "is_box_office_news": True,
                    "summary": "Record box office",
                },
            ],
        }

        result = strategy.execute(context)
        hooks_stats = result.get("run_stats", {}).get("hooks", {})
        assert "validated" in hooks_stats
        assert hooks_stats["validated"] >= 0
