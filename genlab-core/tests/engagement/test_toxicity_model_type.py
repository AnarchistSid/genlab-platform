"""Pin the Detoxify model_type to a value that exists in MODEL_URLS.

U-04 (2026-06-17): swapped ``original`` → ``original-small`` for ~3x
RAM relief on the 4 GB Hetzner box. If a future PR sets _MODEL_TYPE
to a name Detoxify doesn't ship, the engagement worker would crash
the first time it tried to load the model in prod — and `_get_model`
is called lazily so the crash wouldn't surface until a real comment
arrived.

This pin catches the typo at test time. It also serves as
documentation of which model_type values are valid.
"""

from __future__ import annotations

import pytest
from genlab_core.engagement.toxicity_gate import ToxicityGate


@pytest.fixture(scope="module")
def detoxify_supported_model_types() -> set[str]:
    """The set of model_type strings detoxify.load_checkpoint() accepts."""
    try:
        import detoxify.detoxify as _d

        return set(_d.MODEL_URLS.keys())
    except ImportError:
        pytest.skip("detoxify not installed")


class TestToxicityModelType:
    def test_model_type_exists_in_detoxify_registry(self, detoxify_supported_model_types):
        """U-04 anti-typo pin. If MODEL_URLS doesn't contain
        ToxicityGate._MODEL_TYPE, the first inbound comment in prod
        would raise KeyError before Detoxify even tries to fetch
        weights. Catch the regression at test time."""
        assert ToxicityGate._MODEL_TYPE in detoxify_supported_model_types, (
            f"ToxicityGate._MODEL_TYPE={ToxicityGate._MODEL_TYPE!r} is not in "
            f"detoxify.MODEL_URLS={sorted(detoxify_supported_model_types)}. "
            "Either fix the typo or update detoxify."
        )

    def test_model_type_is_a_small_variant(self):
        """We deliberately chose a ``-small`` variant for RAM relief.
        A future revert to the heavier non-small model would silently
        un-do U-04 — pin the intent so the reverter sees this test
        and either updates this assertion or files an explicit
        reason."""
        assert ToxicityGate._MODEL_TYPE.endswith("-small"), (
            f"ToxicityGate._MODEL_TYPE={ToxicityGate._MODEL_TYPE!r} is not a "
            "-small variant. The 4 GB Hetzner box can't comfortably run the "
            "heavier BERT-based 'original' model alongside the publish "
            "pipeline (R-03). If reverting was intentional, update this pin "
            "with the rationale."
        )

    def test_inbound_dims_unchanged_after_model_swap(self):
        """The Albert-based small variants emit the SAME Jigsaw category
        set as the BERT-based originals (toxicity, severe_toxicity, etc.)
        per the audit. Pin that the inbound-toxic dimension list didn't
        accidentally shift when the model swapped."""
        assert ToxicityGate._INBOUND_TOXIC_DIMS == (
            "toxicity",
            "severe_toxicity",
            "threat",
            "insult",
            "identity_attack",
        ), (
            "Inbound toxic dimensions changed — verify the new model emits "
            "all 5 categories (Detoxify -small variants should match originals)."
        )
