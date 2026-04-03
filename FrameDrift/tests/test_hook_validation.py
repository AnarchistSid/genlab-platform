"""Tests for HookValidator wiring in FrameDrift hook strategy."""


from fd_strategies.hooks import AnimeHookStrategy


class TestHookValidatorWiring:
    """Validate that AnimeHookStrategy.execute() runs HookValidator on generated hooks."""

    def test_execute_validates_hooks(self):
        """After execute(), all hooks should pass HookValidator checks."""
        from genlab_core.intelligence.hook_validator import HookValidator

        strategy = AnimeHookStrategy()
        context = {
            "stories": [
                {
                    "title": "Jujutsu Kaisen Season 3 premiere date",
                    "is_new_release": True,
                    "trend_cycle_stage": "peak",
                    "summary": "Season 3 confirmed",
                },
                {
                    "title": "One Piece live action gets renewed",
                    "is_collab": False,
                    "trend_cycle_stage": "emerging",
                    "trend_name": "anime live action",
                    "summary": "Netflix renewal",
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
        strategy = AnimeHookStrategy()
        context = {
            "stories": [
                {
                    "title": "Dragon Ball Daima breaks records",
                    "is_new_release": True,
                    "trend_cycle_stage": "peak",
                    "summary": "New anime premiere",
                },
            ],
        }

        result = strategy.execute(context)
        hooks_stats = result.get("run_stats", {}).get("hooks", {})
        assert "validated" in hooks_stats
        assert hooks_stats["validated"] >= 0
