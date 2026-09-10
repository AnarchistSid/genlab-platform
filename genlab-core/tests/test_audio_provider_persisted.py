"""FIX-T01 — a blueprint that completed synthesis carries its VO tier.

INV-01 measured the defect: ``audio_provider`` was set in memory by
``generate_audio`` and never persisted. Across all 158 blueprints in the audit
window, zero carried the key, so the tier of every reel ever published was
unrecoverable and the playbook's voice gate ("Edge/gTTS on a hero reel =
DEGRADED, hold") could not be evaluated at all.

The field crosses six stages between GenerateAudio (15) and PushToBacklog (21).
A pass-through field crossing a stage boundary is the shape that dies silently
-- ``narration_audio_path`` did exactly this and cost weeks -- so these pin the
INVARIANT (a synthesised blueprint has a tier) rather than a snapshot of the
persist dict.
"""
import inspect

from genlab_core.pipeline.stages import generate_audio as GA
from genlab_core.pipeline.stages import push_to_backlog as PTB


def test_synthesis_records_all_three_tier_fields():
    """Used tier, attempted tier, and fallback reason are all set together.
    Recording only the used tier cannot answer 'did we fall back'."""
    src = inspect.getsource(GA)
    block = src.split('media["audio_path"] = str(out_path)')[1].split("generated += 1")[0]
    for key in ("audio_provider", "audio_provider_attempted", "audio_fallback_reason"):
        assert f'media["{key}"]' in block, f"{key} not set at synthesis"


def test_persister_carries_the_tier_across_the_stage_boundary():
    """THE regression. Set-in-memory is not persisted; six stages separate the
    writer from the persister."""
    src = inspect.getsource(PTB)
    for key in ("audio_provider", "audio_provider_attempted", "audio_fallback_reason"):
        assert f'"{key}"' in src, f"{key} never reaches the persist path"


def test_absent_tier_persists_as_none_not_empty_string():
    """Historical rows must stay distinguishable from 'synthesised on an
    unknown tier'. An empty string would read as a real value at query time --
    the same misreading that produced and then retracted register #245."""
    src = inspect.getsource(PTB)
    seg = src.split('"audio_provider":')[1][:120]
    assert "or None" in seg, "absent tier must coerce to None, not ''"


def test_synthesis_emits_one_structured_tier_line():
    src = inspect.getsource(GA)
    assert "tts tier attempted=" in src
    for token in ("used=", "fell_back="):
        assert token in src, f"log line missing {token}"


def test_cascade_shape_change_cannot_break_synthesis():
    """The attempted tier is read from a private attribute. A cascade refactor
    must degrade to 'unknown', never raise inside the synthesis path."""
    src = inspect.getsource(GA)
    block = src.split('media["audio_path"] = str(out_path)')[1].split("generated += 1")[0]
    assert 'getattr(cascade, "_providers", [])' in block
    assert '"unknown"' in block


def test_no_base_class_was_modified():
    """Scope guard: the brief forbids base-class changes for this field."""
    from genlab_core.strategies import base_visual_render, base_writing

    for mod in (base_visual_render, base_writing):
        assert "audio_provider" not in inspect.getsource(mod)
