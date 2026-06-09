"""Tests for :mod:`genlab_core.http.template_store`.

Tier 2 / audit S-2, slice 2.4 (templates half) — dedicated test file
for the fourth extracted BacklogClient sub-store.

Coverage targets:
* create_template: success path returning the new record id, the
  full dict flattening (constraints → promoted columns, structure
  list → newline-joined string, pattern_refs list → comma-joined),
  missing-constraints defaults.
* find_template_by_template_id: hit + miss, formula escape.
* get_active_templates: passes ``status='active'`` formula always,
  passes ``niche_id`` through, returns the find result verbatim.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from genlab_core.http.template_store import TemplateStore


def _make_store():
    """Build a TemplateStore wired to a MagicMock backend."""
    backend_mock = MagicMock()
    backend_mock.create.return_value = {"id": "tpl_new"}
    backend_mock.find.return_value = []
    backend_lookup = MagicMock(return_value=backend_mock)
    return TemplateStore(backend=backend_lookup), backend_mock


# ---------------------------------------------------------------------------
# create_template
# ---------------------------------------------------------------------------


class TestCreateTemplate:
    def _full_template(self) -> dict:
        return {
            "template_id": "tpl_42",
            "name": "Quick Take",
            "format": "reel",
            "best_for": ["breaking-news"],
            "constraints": {
                "max_slides": 5,
                "max_words_per_slide_title": 8,
                "max_words_per_slide_body": 30,
                "max_reel_seconds": 45,
            },
            "structure": ["Hook", "Body 1", "Body 2", "CTA"],
            "default_cta": "Follow for more",
            "pattern_refs": ["ptn_a", "ptn_b"],
        }

    def test_returns_record_id_on_success(self) -> None:
        store, backend = _make_store()
        backend.create.return_value = {"id": "tpl_999"}
        assert store.create_template(self._full_template()) == "tpl_999"

    def test_flattens_constraints_into_promoted_columns(self) -> None:
        store, backend = _make_store()
        store.create_template(self._full_template())
        fields = backend.create.call_args[0][1]
        assert fields["max_slides"] == 5
        assert fields["max_words_per_slide_title"] == 8
        assert fields["max_words_per_slide_body"] == 30
        assert fields["max_reel_seconds"] == 45

    def test_joins_structure_with_newlines(self) -> None:
        store, backend = _make_store()
        store.create_template(self._full_template())
        fields = backend.create.call_args[0][1]
        assert fields["structure"] == "Hook\nBody 1\nBody 2\nCTA"

    def test_joins_pattern_refs_with_commas(self) -> None:
        store, backend = _make_store()
        store.create_template(self._full_template())
        fields = backend.create.call_args[0][1]
        assert fields["pattern_refs"] == "ptn_a, ptn_b"

    def test_status_defaults_to_active(self) -> None:
        store, backend = _make_store()
        store.create_template(self._full_template())
        fields = backend.create.call_args[0][1]
        assert fields["status"] == "active"

    def test_missing_constraints_resolves_to_none_per_column(self) -> None:
        store, backend = _make_store()
        # No 'constraints' key at all.
        store.create_template({"template_id": "t1", "name": "n", "format": "f"})
        fields = backend.create.call_args[0][1]
        assert fields["max_slides"] is None
        assert fields["max_words_per_slide_title"] is None
        assert fields["max_words_per_slide_body"] is None
        assert fields["max_reel_seconds"] is None

    def test_optional_fields_default_to_empty(self) -> None:
        store, backend = _make_store()
        store.create_template({"template_id": "t1", "name": "n", "format": "f"})
        fields = backend.create.call_args[0][1]
        assert fields["best_for"] == []
        assert fields["structure"] == ""
        assert fields["default_cta"] == ""
        assert fields["pattern_refs"] == ""

    def test_uses_typecast(self) -> None:
        store, backend = _make_store()
        store.create_template(self._full_template())
        assert backend.create.call_args.kwargs == {"typecast": True}


# ---------------------------------------------------------------------------
# find_template_by_template_id
# ---------------------------------------------------------------------------


class TestFindTemplateByTemplateId:
    def test_returns_first_match(self) -> None:
        store, backend = _make_store()
        backend.find.return_value = [{"id": "rec1"}, {"id": "rec2"}]
        assert store.find_template_by_template_id("t1") == {"id": "rec1"}

    def test_returns_none_when_not_found(self) -> None:
        store, backend = _make_store()
        backend.find.return_value = []
        assert store.find_template_by_template_id("t_missing") is None

    def test_uses_template_id_formula(self) -> None:
        store, backend = _make_store()
        store.find_template_by_template_id("t1")
        assert backend.find.call_args.kwargs["formula"] == "{template_id}='t1'"

    def test_max_records_pinned_to_1(self) -> None:
        store, backend = _make_store()
        store.find_template_by_template_id("t1")
        assert backend.find.call_args.kwargs["max_records"] == 1

    def test_escapes_quote_in_template_id(self) -> None:
        store, backend = _make_store()
        store.find_template_by_template_id("t'1")
        assert "t\\'1" in backend.find.call_args.kwargs["formula"]


# ---------------------------------------------------------------------------
# get_active_templates
# ---------------------------------------------------------------------------


class TestGetActiveTemplates:
    def test_status_active_formula_always_used(self) -> None:
        store, backend = _make_store()
        store.get_active_templates()
        assert backend.find.call_args.kwargs["formula"] == "{status}='active'"

    def test_niche_id_passthrough(self) -> None:
        store, backend = _make_store()
        store.get_active_templates(niche_id="gaming")
        assert backend.find.call_args.kwargs["niche_id"] == "gaming"

    def test_no_niche_id_defaults_to_none(self) -> None:
        store, backend = _make_store()
        store.get_active_templates()
        assert backend.find.call_args.kwargs["niche_id"] is None

    def test_returns_find_results_verbatim(self) -> None:
        store, backend = _make_store()
        backend.find.return_value = [{"id": "a"}, {"id": "b"}]
        assert store.get_active_templates() == [{"id": "a"}, {"id": "b"}]
