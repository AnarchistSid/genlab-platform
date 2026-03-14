"""Verify engagement module imports work correctly."""


class TestEngagementImports:
    def test_toxicity_gate_importable(self):
        from genlab_core.engagement.toxicity_gate import ToxicityGate
        assert ToxicityGate is not None

    def test_human_delay_importable(self):
        from genlab_core.engagement.timing import human_delay
        assert callable(human_delay)
