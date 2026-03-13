"""Verify old engagement_engine imports still work via deprecation shim."""
import warnings
import pytest


class TestDeprecationWarning:
    def test_import_emits_deprecation_warning(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            import importlib
            import genlab_core.platform.engagement_engine
            importlib.reload(genlab_core.platform.engagement_engine)
            deprecation_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
            assert len(deprecation_warnings) >= 1
            assert "deprecated" in str(deprecation_warnings[0].message).lower()

    def test_reexports_toxicity_gate(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            from genlab_core.platform.engagement_engine import ToxicityGate
            from genlab_core.engagement.toxicity_gate import ToxicityGate as Canonical
            assert ToxicityGate is Canonical

    def test_reexports_human_like_delay(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            from genlab_core.platform.engagement_engine import human_like_delay_seconds
            assert callable(human_like_delay_seconds)
