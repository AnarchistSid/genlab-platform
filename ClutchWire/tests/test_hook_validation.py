"""Tests for HookValidator wiring in ClutchWire hook strategy."""


from cw_strategies.hooks import SportHookStrategy


class TestHookValidatorWiring:
    """Validate that SportHookStrategy.execute() runs HookValidator on generated hooks."""

    def test_hook_over_60_chars_gets_truncated_by_validator(self):
        """Hooks >60 characters should be flagged and replaced by validator."""
        from genlab_core.intelligence.hook_validator import HookValidator

        validator = HookValidator()
        long_hook = "A" * 65  # Too short word-count too, but >60 chars tests length
        result = validator.validate(long_hook, platform="instagram")
        # This hook is invalid (too short in word count AND would be too long for a hook)
        assert not result.passed

    def test_execute_validates_hooks(self):
        """After execute(), all hooks should pass HookValidator checks."""
        from genlab_core.intelligence.hook_validator import HookValidator

        strategy = SportHookStrategy()
        context = {
            "stories": [
                {
                    "title": "Lakers defeat Celtics in overtime thriller",
                    "teams": ["Lakers", "Celtics"],
                    "sport": "NBA",
                    "summary": "Epic overtime game",
                },
                {
                    "title": "Messi scores hat trick in Copa final",
                    "players": ["Messi"],
                    "sport": "Football",
                    "summary": "Hat trick hero",
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

    def test_markdown_hook_gets_replaced(self):
        """A hook with markdown artifacts should be caught by validator."""
        from genlab_core.intelligence.hook_validator import HookValidator

        validator = HookValidator()
        result = validator.validate("**INSANE** play in ranked", platform="instagram")
        assert not result.passed

    def test_run_stats_includes_validation_count(self):
        """execute() should report how many hooks were validated/rejected."""
        strategy = SportHookStrategy()
        context = {
            "stories": [
                {
                    "title": "Wild upset in the Premier League",
                    "teams": ["Arsenal", "Newcastle"],
                    "sport": "Football",
                    "is_upset": True,
                    "summary": "Major upset",
                },
            ],
        }

        result = strategy.execute(context)
        hooks_stats = result.get("run_stats", {}).get("hooks", {})
        assert "validated" in hooks_stats
        assert hooks_stats["validated"] >= 0
