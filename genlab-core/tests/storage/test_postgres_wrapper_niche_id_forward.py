"""PR #525 (2026-06-24): wrapper-layer forward of ``niche_id`` kwarg.

PR #517 added ``niche_id`` to ``PostgresBackend.create()`` +
``batch_create()`` to wire SR-C tenant isolation (``SET LOCAL
app.niche_id`` before INSERT). But the wrapper layer
(``BacklogClient.create()`` / ``.batch_create()`` at
``postgres.py:899``) silently swallowed the kwarg via ``**kwargs``
and never forwarded it — making PR #517's mechanism *dormant for
every store-level caller* (BlueprintStore, TemplateStore,
ABTestStore, SourceStore, StoryStore).

This PR ships the wrapper-forward so the kwarg can actually reach
the backend. Without these pins:

* an Edit removing ``niche_id=niche_id`` from the wrapper's
  delegation re-introduces the silent swallow (PR #517 looks live
  in tests, but no store call ever benefits);
* an Edit dropping the kwarg from the wrapper signature breaks
  the migration path for the 5 store classes that will follow up;
* a refactor that splits the wrapper without updating both paths
  (one keeps forwarding, one stops) creates a silent fork where
  some store calls land with tenant context and others don't.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ── source pins (regression-proof against silent swallow) ──────────


@pytest.fixture(scope="module")
def postgres_source() -> str:
    import genlab_core.storage.postgres as mod

    return Path(mod.__file__).read_text()


def test_wrapper_create_signature_accepts_niche_id(postgres_source):
    """The wrapper's signature must include ``niche_id: str | None``
    so a static-typed caller sees the kwarg and IDE autocomplete
    surfaces it."""
    # Locate the wrapper class's create() — there are TWO create()
    # methods in the file: the backend (line ~429) and the wrapper
    # (line ~899). The wrapper is the second occurrence.
    create_positions = [
        i for i in range(len(postgres_source)) if postgres_source.startswith("    def create(", i)
    ]
    assert len(create_positions) >= 2, (
        f"Expected ≥2 create() methods (backend + wrapper); found {len(create_positions)}."
    )
    wrapper_create = postgres_source[create_positions[1] : create_positions[1] + 600]
    assert "niche_id: str | None = None" in wrapper_create, (
        "Wrapper create() signature must declare niche_id — without "
        "it, callers passing niche_id= land in **kwargs and get "
        "silently dropped before reaching the backend."
    )


def test_wrapper_create_forwards_niche_id_to_backend(postgres_source):
    """The wrapper body must pass ``niche_id=niche_id`` to
    ``self._backend.create(...)``. Without this, even if the kwarg
    appears in the signature, the call to the backend drops it."""
    # The delegating call site in the wrapper:
    assert "niche_id=niche_id," in postgres_source, (
        "Wrapper create() body must forward niche_id=niche_id to "
        "self._backend.create(...). The whole point of PR #525 is "
        "closing the silent-swallow gap between wrapper and backend."
    )


def test_wrapper_batch_create_signature_accepts_niche_id(postgres_source):
    """Same shape for batch_create — the wrapper must declare the
    kwarg explicitly (NOT swallow via **kwargs). Heterogeneous-
    tenant batch validation lives in the backend; the wrapper has
    to actually deliver the kwarg for that validation to fire."""
    # Find the wrapper's batch_create (second occurrence in the file).
    bc_positions = [
        i
        for i in range(len(postgres_source))
        if postgres_source.startswith("    def batch_create(", i)
    ]
    assert len(bc_positions) >= 2, (
        f"Expected ≥2 batch_create() methods (backend + wrapper); found {len(bc_positions)}."
    )
    wrapper_bc = postgres_source[bc_positions[1] : bc_positions[1] + 600]
    assert "niche_id: str | None = None" in wrapper_bc


# ── behavioral pins (verify the forward actually works) ────────────


class _StubBackend:
    """Captures every call so we can assert the wrapper forwarded
    everything the caller passed."""

    def __init__(self):
        self.create_calls: list[dict] = []
        self.batch_calls: list[dict] = []

    def create(self, table, fields, *, typecast=False, niche_id=None):
        self.create_calls.append(
            {
                "table": table,
                "fields": fields,
                "typecast": typecast,
                "niche_id": niche_id,
            }
        )
        return "new-id"

    def batch_create(self, table, records, *, niche_id=None):
        self.batch_calls.append({"table": table, "records": records, "niche_id": niche_id})
        return ["id-1", "id-2"]


def _make_wrapper(stub_backend):
    """Build a wrapper instance using monkey-injection — avoids
    spinning up a real Postgres pool just to test the forward."""
    from genlab_core.storage.postgres import PostgresTableProxy

    # Construct via the public API the wrapper uses: pass a stub
    # for ``_backend`` and the table name.
    proxy = PostgresTableProxy.__new__(PostgresTableProxy)
    proxy._backend = stub_backend
    proxy._table = "Blueprints"
    return proxy


def test_create_with_niche_id_forwards_to_backend():
    """Pass niche_id at the wrapper — backend MUST receive it."""
    stub = _StubBackend()
    proxy = _make_wrapper(stub)

    proxy.create(fields={"hook_text": "test"}, niche_id="gaming")

    assert len(stub.create_calls) == 1
    assert stub.create_calls[0]["niche_id"] == "gaming", (
        "Backend received niche_id=None instead of 'gaming' — the wrapper-forward is broken."
    )


def test_create_without_niche_id_passes_none_for_backward_compat():
    """Existing callers (every store today) pass no niche_id —
    the wrapper must default to None so the backend's admin-mode
    fallback fires. Migration is incremental."""
    stub = _StubBackend()
    proxy = _make_wrapper(stub)

    proxy.create(fields={"hook_text": "test"})

    assert len(stub.create_calls) == 1
    assert stub.create_calls[0]["niche_id"] is None


def test_batch_create_with_niche_id_forwards_to_backend():
    stub = _StubBackend()
    proxy = _make_wrapper(stub)

    proxy.batch_create(
        records=[{"hook_text": "a"}, {"hook_text": "b"}],
        niche_id="sports",
    )

    assert len(stub.batch_calls) == 1
    assert stub.batch_calls[0]["niche_id"] == "sports"


def test_batch_create_without_niche_id_passes_none():
    stub = _StubBackend()
    proxy = _make_wrapper(stub)

    proxy.batch_create(records=[{"hook_text": "a"}])

    assert len(stub.batch_calls) == 1
    assert stub.batch_calls[0]["niche_id"] is None


def test_create_preserves_typecast_kwarg_while_forwarding_niche_id():
    """Combined kwarg test — both typecast AND niche_id reach the
    backend (so a refactor that drops one doesn't break the other)."""
    stub = _StubBackend()
    proxy = _make_wrapper(stub)

    proxy.create(
        fields={"hook_text": "test"},
        typecast=True,
        niche_id="anime",
    )

    call = stub.create_calls[0]
    assert call["typecast"] is True
    assert call["niche_id"] == "anime"


def test_create_dict_first_positional_form_still_works():
    """The wrapper has a 'fields-as-first-positional' compat shape:
    ``proxy.create({"x": 1})`` puts the dict into ``table`` and shuffles.
    The niche_id forward must work alongside that compat path."""
    stub = _StubBackend()
    proxy = _make_wrapper(stub)

    # Old-style call: just a dict, no table.
    proxy.create({"hook_text": "test"}, niche_id="movies")

    call = stub.create_calls[0]
    assert call["table"] == "Blueprints"  # falls back to proxy._table
    assert call["fields"] == {"hook_text": "test"}
    assert call["niche_id"] == "movies"


# ── negative pin (catches accidental kwarg-swallow regression) ────


def test_kwargs_swallow_does_not_silently_drop_niche_id(postgres_source):
    """Subtle regression: a future refactor might re-introduce
    ``**kwargs`` after ``niche_id`` and accidentally make a typo
    like ``niche_id_=`` get swallowed silently. Pin that the
    forward line is exact + structurally adjacent to the wrapper."""
    # The wrapper's body must contain the forward — not behind
    # any conditional. Looking for: niche_id=niche_id, on its own
    # line in the body of the wrapper create().
    create_positions = [
        i for i in range(len(postgres_source)) if postgres_source.startswith("    def create(", i)
    ]
    wrapper_create = postgres_source[create_positions[1] : create_positions[1] + 1200]
    # The kwarg must reach the .create call line that delegates
    # to the backend.
    assert "self._backend.create(" in wrapper_create
    assert wrapper_create.count("niche_id=niche_id,") >= 1, (
        "Wrapper create() body must unconditionally forward "
        "niche_id=niche_id to backend.create. A conditional or "
        "renamed kwarg defeats SR-C."
    )
