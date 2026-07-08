"""Unit tests for genlab_core.http.graph_proxy.GraphTableProxy.

Locks in the behaviour of the SharePoint List / Microsoft Graph table
proxy that gates every Meta / SharePoint API call in the platform.

Prior to this file, only the pure-function ``formula_to_odata``
helper was covered in ``tests/test_http.py``. The class methods
(``all``, ``get``, ``create``, ``update``, ``delete``,
``batch_create``, ``batch_update``, ``upload_attachment``) had **no
coverage** — the audit flagged this as a top-priority gap.

Design decisions:
  * The ``msgraph`` SDK is NOT installed in test env — every method
    that does ``from msgraph.generated... import ...`` at call time
    must be exercised with ``sys.modules`` stubs in place.
  * ``_load_column_map`` is called from ``__init__``; we always patch
    it out and hand-populate the column map on the instance.
  * Graph client chains (``client.sites.by_site_id(...).lists...``)
    are wired with a small helper that installs an ``AsyncMock`` at
    the leaf — MagicMock chains auto-vivify, so we only care about
    the terminal awaited call.
  * We NEVER touch ``ig_refresh_token`` — per
    ``.claude/rules/security.md`` EAA page tokens are permanent and
    refresh is an intentional no-op. Testing it would enshrine a
    dead code path.
"""

from __future__ import annotations

import sys
import types
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── msgraph SDK stubs ────────────────────────────────────────────────
# GraphTableProxy imports these AT CALL TIME (inside async methods).
# We install lightweight stubs so the imports succeed under test.


def _install_msgraph_stubs() -> None:
    """Install stub msgraph modules used by GraphTableProxy at call time.

    GraphTableProxy._all_async / _get_async / _create_async / _update_async
    each do a local ``from msgraph.generated...`` import. Stubbing here
    keeps every test hermetic.
    """
    if "msgraph" in sys.modules:
        return

    # Fake ItemsRequestBuilder with the two nested config/query classes
    # the proxy uses.
    class _FakeQueryParameters:
        def __init__(self, expand=None):
            self.expand = expand
            self.filter = None
            self.top = None

    class _FakeHeaders:
        def __init__(self):
            self._store = {}

        def add(self, key, value):
            self._store[key] = value

    class _FakeRequestConfiguration:
        def __init__(self, query_parameters=None):
            self.query_parameters = query_parameters
            self.headers = _FakeHeaders()

    class _FakeItemsRequestBuilder:
        ItemsRequestBuilderGetQueryParameters = _FakeQueryParameters
        ItemsRequestBuilderGetRequestConfiguration = _FakeRequestConfiguration

    class _FakeListItemItemRequestBuilder:
        ListItemItemRequestBuilderGetQueryParameters = _FakeQueryParameters
        ListItemItemRequestBuilderGetRequestConfiguration = _FakeRequestConfiguration

    class _FakeFieldValueSet:
        def __init__(self, additional_data=None):
            self.additional_data = additional_data or {}

    class _FakeListItem:
        def __init__(self, fields=None):
            self.fields = fields

    def _make_module(name: str) -> types.ModuleType:
        mod = types.ModuleType(name)
        sys.modules[name] = mod
        return mod

    _make_module("msgraph")
    _make_module("msgraph.generated")
    _make_module("msgraph.generated.sites")
    _make_module("msgraph.generated.sites.item")
    _make_module("msgraph.generated.sites.item.lists")
    _make_module("msgraph.generated.sites.item.lists.item")
    _make_module("msgraph.generated.sites.item.lists.item.items")
    items_req_mod = _make_module(
        "msgraph.generated.sites.item.lists.item.items.items_request_builder"
    )
    items_req_mod.ItemsRequestBuilder = _FakeItemsRequestBuilder

    _make_module("msgraph.generated.sites.item.lists.item.items.item")
    list_item_mod = _make_module(
        "msgraph.generated.sites.item.lists.item.items.item.list_item_item_request_builder"
    )
    list_item_mod.ListItemItemRequestBuilder = _FakeListItemItemRequestBuilder

    _make_module("msgraph.generated.models")
    fvs_mod = _make_module("msgraph.generated.models.field_value_set")
    fvs_mod.FieldValueSet = _FakeFieldValueSet
    li_mod = _make_module("msgraph.generated.models.list_item")
    li_mod.ListItem = _FakeListItem


_install_msgraph_stubs()


# ── Helpers ──────────────────────────────────────────────────────────


def _make_proxy(list_id: str = "list-1"):
    """Build a GraphTableProxy with mocked Graph client and column map.

    Skips the real ``_load_column_map`` (which would call the async
    Graph SDK) and hand-populates the display↔internal maps.
    """
    from genlab_core.http.graph_proxy import GraphTableProxy

    with patch.object(GraphTableProxy, "_load_column_map"):
        proxy = GraphTableProxy(MagicMock(), "site-1", list_id, "TestList")
    proxy._display_to_internal = {"hook_text": "hook"}
    proxy._internal_to_display = {"hook": "hook_text"}
    return proxy


def _make_field_item(item_id: str, fields_dict: dict, created: datetime | None = None):
    """Build a Graph-shaped ListItem mock returning fields_dict from .fields.additional_data."""
    item = MagicMock()
    item.id = item_id
    item.fields = MagicMock()
    item.fields.additional_data = fields_dict
    item.created_date_time = created
    return item


def _wire_items_get(proxy, result):
    """Install an AsyncMock at ``proxy._client.sites.by_site_id(...).lists.by_list_id(...).items.get``.

    ``result`` becomes the awaited return value. Returns the AsyncMock so
    tests can inspect call arguments.
    """
    items_req = proxy._client.sites.by_site_id.return_value.lists.by_list_id.return_value.items
    items_req.get = AsyncMock(return_value=result)
    return items_req


def _wire_get_by_id(proxy, result):
    """Install AsyncMock at the ``items.by_list_item_id(id).get`` leaf."""
    single = proxy._client.sites.by_site_id.return_value.lists.by_list_id.return_value.items.by_list_item_id.return_value
    single.get = AsyncMock(return_value=result)
    return single


def _wire_post(proxy, result):
    """Install AsyncMock at ``items.post``."""
    items_req = proxy._client.sites.by_site_id.return_value.lists.by_list_id.return_value.items
    items_req.post = AsyncMock(return_value=result)
    return items_req


def _wire_patch(proxy, result=None):
    """Install AsyncMock at ``items.by_list_item_id(id).fields.patch``."""
    single = proxy._client.sites.by_site_id.return_value.lists.by_list_id.return_value.items.by_list_item_id.return_value
    single.fields.patch = AsyncMock(return_value=result)
    return single


def _wire_delete(proxy, result=None):
    """Install AsyncMock at ``items.by_list_item_id(id).delete``."""
    single = proxy._client.sites.by_site_id.return_value.lists.by_list_id.return_value.items.by_list_item_id.return_value
    single.delete = AsyncMock(return_value=result)
    return single


class _FakeResult:
    """Duck-typed replacement for msgraph ItemsResponse.

    Exposes ``value`` (list of items) + ``odata_next_link`` (pagination
    cursor). We use a class instead of MagicMock to prevent auto-vivify
    from turning missing attributes into truthy sentinels — pagination
    logic keys off ``getattr(result, "odata_next_link", None)`` and
    we need to control that value exactly.
    """

    def __init__(self, value, odata_next_link=None):
        self.value = value
        self.odata_next_link = odata_next_link


# ── Constructor ──────────────────────────────────────────────────────


class TestConstructor:
    """Locks in constructor contract: state fields + column-map load call.

    The proxy stores site_id / list_id / list_name for every downstream
    request. If any of these silently drop, every SharePoint call
    misroutes — the platform's blast radius is 5 channels × every
    write. Pin the shape.
    """

    def test_stores_client_and_ids(self):
        """Every subsequent Graph call uses these three fields; drop-shim safety pin."""
        from genlab_core.http.graph_proxy import GraphTableProxy

        client = MagicMock()
        with patch.object(GraphTableProxy, "_load_column_map"):
            proxy = GraphTableProxy(client, "site-abc", "list-xyz", "Blueprints")
        assert proxy._client is client
        assert proxy._site_id == "site-abc"
        assert proxy._list_id == "list-xyz"
        assert proxy._list_name == "Blueprints"

    def test_default_list_name_is_empty(self):
        """list_name is optional (used only for log messages); default must be empty string not None."""
        from genlab_core.http.graph_proxy import GraphTableProxy

        with patch.object(GraphTableProxy, "_load_column_map"):
            proxy = GraphTableProxy(MagicMock(), "s", "l")
        assert proxy._list_name == ""

    def test_calls_load_column_map_on_construct(self):
        """Column map load is a one-shot on construct — cache TTL controls
        whether the real Graph fetch fires; if construct stops calling
        _load_column_map, every proxy would ship with an empty map and
        every field name would round-trip unmapped."""
        from genlab_core.http.graph_proxy import GraphTableProxy

        with patch.object(GraphTableProxy, "_load_column_map") as mock_load:
            GraphTableProxy(MagicMock(), "s", "l", "Name")
        mock_load.assert_called_once()

    def test_initial_column_maps_are_empty_dicts(self):
        """Sanity: column map dicts exist and are empty pre-load, so
        _to_internal_name / _to_display_name have valid attributes to
        consult even if _load_column_map hasn't populated yet."""
        from genlab_core.http.graph_proxy import GraphTableProxy

        with patch.object(GraphTableProxy, "_load_column_map"):
            proxy = GraphTableProxy(MagicMock(), "s", "l")
        assert proxy._display_to_internal == {}
        assert proxy._internal_to_display == {}


# ── all() ────────────────────────────────────────────────────────────


class TestAll:
    """Locks in .all() batch-fetch behavior: happy path, empty, filter
    translation, error fallback, pagination, and max_records cap.

    ``.all()`` is the single most-called method in the platform (used
    by every dashboard endpoint + every scoring pass) so its
    behavioural contract is load-bearing."""

    def test_returns_records_on_happy_path(self):
        """Standard fetch: two items → two records in standard shape."""
        proxy = _make_proxy()
        items = [
            _make_field_item("101", {"title": "First", "status": "INTAKE"}),
            _make_field_item("102", {"title": "Second", "status": "INTAKE"}),
        ]
        _wire_items_get(proxy, _FakeResult(items))
        result = proxy.all()
        assert len(result) == 2
        assert result[0]["id"] == "101"
        assert result[0]["fields"]["title"] == "First"

    def test_empty_response_returns_empty_list(self):
        """No matching items → [] (not None, not error)."""
        proxy = _make_proxy()
        _wire_items_get(proxy, _FakeResult([]))
        assert proxy.all() == []

    def test_null_result_returns_empty_list(self):
        """Graph SDK returning None (rare) must degrade to [] not crash."""
        proxy = _make_proxy()
        _wire_items_get(proxy, None)
        assert proxy.all() == []

    def test_formula_translated_to_odata_filter(self):
        """Verifies the .all()→formula_to_odata→filter integration point.
        This is the boundary between the legacy formula grammar callers
        expect and the OData Graph enforces — regression here would
        break every ``formula='{status}=...'`` call."""
        proxy = _make_proxy()
        items_req = _wire_items_get(proxy, _FakeResult([]))
        proxy.all(formula="{status}='INTAKE'")

        call = items_req.get.await_args
        config = call.kwargs["request_configuration"]
        assert config.query_parameters.filter == "fields/status eq 'INTAKE'"

    def test_display_names_translated_to_internal_in_filter(self):
        """Formula uses display name → filter uses internal.
        SharePoint's OData refuses display names; if the translation
        silently regressed, every filter would 400.

        Note: SharePoint display names with spaces get encoded (space →
        ``_x0020_``) in field references BEFORE reaching this proxy —
        the ``_translate_filter_names`` regex is ``fields/(\\w+)`` which
        stops at whitespace, so only word-char display names are
        translated. Use a realistic single-token display name here."""
        proxy = _make_proxy()
        proxy._display_to_internal = {"PublishDate": "publishDate0"}
        proxy._internal_to_display = {"publishDate0": "PublishDate"}
        items_req = _wire_items_get(proxy, _FakeResult([]))
        proxy.all(formula="{PublishDate}='2026-01-01'")

        config = items_req.get.await_args.kwargs["request_configuration"]
        # Filter should reference the INTERNAL column name
        assert "publishDate0" in config.query_parameters.filter
        assert "PublishDate" not in config.query_parameters.filter

    def test_max_records_applied_as_top(self):
        """``max_records=N`` must set OData $top so we don't drain a
        multi-thousand-item list when caller only wants 10."""
        proxy = _make_proxy()
        items_req = _wire_items_get(proxy, _FakeResult([]))
        proxy.all(max_records=25)

        config = items_req.get.await_args.kwargs["request_configuration"]
        assert config.query_parameters.top == 25

    def test_max_records_caps_client_side(self):
        """Belt-and-suspenders: even if Graph returned MORE than
        max_records (rare — top is a hint), the client-side slice
        must cap the return."""
        proxy = _make_proxy()
        items = [_make_field_item(str(i), {"n": i}) for i in range(10)]
        _wire_items_get(proxy, _FakeResult(items))
        result = proxy.all(max_records=3)
        assert len(result) == 3

    def test_error_without_formula_returns_empty_list(self):
        """Graph error with no filter → return [] (fail-open) NOT crash.
        This is the "SharePoint is degraded but pipeline must continue"
        guarantee. Any change to raise instead would dark every niche
        on a Graph blip."""
        proxy = _make_proxy()
        items_req = proxy._client.sites.by_site_id.return_value.lists.by_list_id.return_value.items
        items_req.get = AsyncMock(side_effect=RuntimeError("Graph unavailable"))
        assert proxy.all() == []

    def test_error_with_formula_falls_back_to_client_filter(self):
        """When OData filter fails (e.g. bad field name), the proxy
        MUST fall back to unfiltered fetch + client-side filter.
        This is the "OData quirks don't dark the pipeline" guarantee.
        Regression here would silently return [] instead of the real
        matching row set."""
        proxy = _make_proxy()
        items_req = proxy._client.sites.by_site_id.return_value.lists.by_list_id.return_value.items

        # First call (filtered) raises; second call (unfiltered) returns full set.
        matching = _make_field_item("1", {"status": "INTAKE"})
        non_matching = _make_field_item("2", {"status": "DRAFTED"})
        call_count = {"n": 0}

        async def side_effect(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("OData rejected filter")
            return _FakeResult([matching, non_matching])

        items_req.get = AsyncMock(side_effect=side_effect)
        result = proxy.all(formula="{status}='INTAKE'")
        # Client-side filter kept only the INTAKE row.
        assert len(result) == 1
        assert result[0]["id"] == "1"

    def test_paginates_via_odata_next_link(self):
        """``.all()`` MUST drain @odata.nextLink; if pagination
        regressed, every result set would silently cap at the first
        page (typically 200) and hide records from downstream
        scoring."""
        proxy = _make_proxy()
        items_req = proxy._client.sites.by_site_id.return_value.lists.by_list_id.return_value.items

        page1 = _FakeResult(
            [_make_field_item("1", {"n": 1}), _make_field_item("2", {"n": 2})],
            odata_next_link="https://graph/next-page-token",
        )
        page2 = _FakeResult([_make_field_item("3", {"n": 3})])

        # First get(): initial call; second get(): after .with_url(next_link).
        items_req.get = AsyncMock(return_value=page1)
        # .with_url(...) returns a mock whose .get() returns page2.
        second_page_builder = MagicMock()
        second_page_builder.get = AsyncMock(return_value=page2)
        items_req.with_url = MagicMock(return_value=second_page_builder)

        result = proxy.all()
        assert len(result) == 3
        assert [r["id"] for r in result] == ["1", "2", "3"]

    def test_pagination_stops_when_max_records_reached(self):
        """Pagination MUST honor max_records even mid-loop — otherwise
        a caller asking for 2 could pay for 3 pages of fetches on a
        large list."""
        proxy = _make_proxy()
        items_req = proxy._client.sites.by_site_id.return_value.lists.by_list_id.return_value.items
        page1 = _FakeResult(
            [_make_field_item("1", {}), _make_field_item("2", {})],
            odata_next_link="next",
        )
        items_req.get = AsyncMock(return_value=page1)
        # If pagination is honored despite max_records=2, this would fire:
        second = MagicMock()
        second.get = AsyncMock(return_value=_FakeResult([_make_field_item("3", {})]))
        items_req.with_url = MagicMock(return_value=second)

        result = proxy.all(max_records=2)
        assert len(result) == 2
        # Second page should NOT have been requested
        second.get.assert_not_called()

    def test_pagination_error_returns_partial_records(self):
        """If a subsequent page fetch fails, we return what we already
        have — better a partial result than losing the first page's
        work entirely."""
        proxy = _make_proxy()
        items_req = proxy._client.sites.by_site_id.return_value.lists.by_list_id.return_value.items
        page1 = _FakeResult(
            [_make_field_item("1", {}), _make_field_item("2", {})],
            odata_next_link="next",
        )
        items_req.get = AsyncMock(return_value=page1)
        second = MagicMock()
        second.get = AsyncMock(side_effect=RuntimeError("Page 2 timeout"))
        items_req.with_url = MagicMock(return_value=second)

        result = proxy.all()
        assert len(result) == 2  # Kept page 1


# ── get() ────────────────────────────────────────────────────────────


class TestGet:
    """Locks in single-record fetch semantics."""

    def test_returns_record_shape(self):
        """Standard record shape: {id, fields, createdTime}. Anything
        else breaks downstream consumers that unpack these three
        keys."""
        proxy = _make_proxy()
        item = _make_field_item(
            "abc-1",
            {"title": "Hello"},
            created=datetime(2026, 1, 1, tzinfo=UTC),
        )
        _wire_get_by_id(proxy, item)
        result = proxy.get("abc-1")
        assert result["id"] == "abc-1"
        assert result["fields"]["title"] == "Hello"
        assert result["createdTime"] == "2026-01-01T00:00:00+00:00"

    def test_strips_metadata_fields(self):
        """SharePoint round-trips ContentType, GUID, etc. — the proxy
        must strip these so consumers don't accidentally forward
        SharePoint bookkeeping into blueprints."""
        proxy = _make_proxy()
        item = _make_field_item(
            "1",
            {
                "title": "Real",
                "ContentType": "Item",
                "GUID": "abc-def",
                "@odata.etag": '"1"',
                "_ComplianceFlags": "",
            },
        )
        _wire_get_by_id(proxy, item)
        result = proxy.get("1")
        assert "title" in result["fields"]
        assert "ContentType" not in result["fields"]
        assert "GUID" not in result["fields"]
        assert "@odata.etag" not in result["fields"]

    def test_error_propagates(self):
        """Get by ID has no fail-open — 404 must surface as an
        exception, or callers silently proceed on a null blueprint."""
        proxy = _make_proxy()
        single = _wire_get_by_id(proxy, None)
        single.get = AsyncMock(side_effect=RuntimeError("not found"))
        with pytest.raises(RuntimeError, match="not found"):
            proxy.get("missing")

    def test_hook_alias_applied(self):
        """The proxy renames field ``hook`` → ``hook_text`` on read via
        _FIELD_ALIASES. Regression would break every writer consumer
        that reads ``fields['hook_text']``."""
        proxy = _make_proxy()
        # Ensure the internal name ``hook`` doesn't get mapped away by
        # the display-map lookup — force identity so we exercise the
        # alias code path.
        proxy._internal_to_display = {}
        item = _make_field_item("1", {"hook": "catchy line"})
        _wire_get_by_id(proxy, item)
        result = proxy.get("1")
        assert result["fields"].get("hook_text") == "catchy line"

    def test_link_field_becomes_list(self):
        """Any ``<key>_link`` field is exploded into a single-element
        list under ``<key>``. This is the Airtable-compat shim; every
        linked-record read depends on it."""
        proxy = _make_proxy()
        proxy._internal_to_display = {}  # no rename
        item = _make_field_item("1", {"story_link": "rec-999"})
        _wire_get_by_id(proxy, item)
        result = proxy.get("1")
        assert result["fields"].get("story") == ["rec-999"]
        assert "story_link" not in result["fields"]


# ── create() ─────────────────────────────────────────────────────────


class TestCreate:
    """Locks in insert semantics + post-create re-read shape."""

    def test_happy_path_creates_and_returns_record(self):
        """Post + re-read: create() must return the freshly-persisted
        record shape (id + hydrated fields) so callers can pass it
        forward without a second Graph call."""
        proxy = _make_proxy()
        posted = MagicMock()
        posted.id = "new-1"
        _wire_post(proxy, posted)

        fetched = _make_field_item("new-1", {"title": "New"})
        _wire_get_by_id(proxy, fetched)

        result = proxy.create({"title": "New"})
        assert result["id"] == "new-1"
        assert result["fields"]["title"] == "New"

    def test_post_error_propagates(self):
        """Create failures MUST raise — a silent-drop on POST would
        leave the caller thinking a row exists that doesn't. That's
        the fastest way to silent-fail a publish."""
        proxy = _make_proxy()
        items_req = proxy._client.sites.by_site_id.return_value.lists.by_list_id.return_value.items
        items_req.post = AsyncMock(side_effect=RuntimeError("403 Forbidden"))
        with pytest.raises(RuntimeError, match="403"):
            proxy.create({"title": "X"})

    def test_prepared_fields_wrapped_in_field_value_set(self):
        """The POST body must be a ListItem(fields=FieldValueSet(...)) —
        anything else is rejected by Graph. Pin the body shape."""
        proxy = _make_proxy()
        posted = MagicMock()
        posted.id = "n"
        items_req = _wire_post(proxy, posted)
        _wire_get_by_id(proxy, _make_field_item("n", {}))

        proxy.create({"title": "T"})
        body = items_req.post.await_args.args[0]
        # Body is our fake ListItem — assert it has a .fields with the
        # additional_data our prepare pipeline produced.
        assert body.fields.additional_data.get("title") == "T"


# ── update() ─────────────────────────────────────────────────────────


class TestUpdate:
    """Locks in patch semantics."""

    def test_happy_path_returns_reread_record(self):
        """Update = patch fields + re-read; return value is the
        post-update shape."""
        proxy = _make_proxy()
        _wire_patch(proxy)
        fetched = _make_field_item("1", {"status": "PUBLISHED"})
        _wire_get_by_id(proxy, fetched)

        result = proxy.update("1", {"status": "PUBLISHED"})
        assert result["fields"]["status"] == "PUBLISHED"

    def test_patch_error_propagates(self):
        """A 403 or 404 on update must surface. Silent-drop here
        would let SCHEDULED-guard bypasses go undetected."""
        proxy = _make_proxy()
        single = _wire_patch(proxy)
        single.fields.patch = AsyncMock(side_effect=RuntimeError("404 not found"))
        with pytest.raises(RuntimeError, match="404"):
            proxy.update("gone", {"x": 1})

    def test_prepared_fields_wrapped_in_field_value_set(self):
        """Body shape pin — FieldValueSet(additional_data=...) is what
        Graph accepts for PATCH."""
        proxy = _make_proxy()
        single = _wire_patch(proxy)
        _wire_get_by_id(proxy, _make_field_item("1", {}))
        proxy.update("1", {"foo": "bar"})

        body = single.fields.patch.await_args.args[0]
        assert body.additional_data.get("foo") == "bar"


# ── delete() ─────────────────────────────────────────────────────────


class TestDelete:
    """Locks in delete semantics."""

    def test_happy_path_returns_none(self):
        """delete() returns None (per source signature). Consumers
        should not rely on any return value."""
        proxy = _make_proxy()
        _wire_delete(proxy)
        assert proxy.delete("1") is None

    def test_error_propagates(self):
        """404-on-delete is NOT idempotent-swallowed by the proxy.
        Any change to swallow would silently mask "row already gone"
        vs "auth broken" — very different ops signals."""
        proxy = _make_proxy()
        single = _wire_delete(proxy)
        single.delete = AsyncMock(side_effect=RuntimeError("410 Gone"))
        with pytest.raises(RuntimeError, match="410"):
            proxy.delete("stale")

    def test_calls_correct_item_id(self):
        """Verify the record_id is routed through to by_list_item_id."""
        proxy = _make_proxy()
        _wire_delete(proxy)
        proxy.delete("target-id")
        proxy._client.sites.by_site_id.return_value.lists.by_list_id.return_value.items.by_list_item_id.assert_called_with(
            "target-id"
        )


# ── batch_create() ──────────────────────────────────────────────────


class TestBatchCreate:
    """Locks in batch semantics: partial-failure tolerance."""

    def test_all_succeed(self):
        """All-happy → all results returned."""
        proxy = _make_proxy()
        posted = MagicMock()
        posted.id = "x"
        _wire_post(proxy, posted)
        _wire_get_by_id(proxy, _make_field_item("x", {"a": 1}))
        result = proxy.batch_create([{"a": 1}, {"a": 2}])
        assert len(result) == 2

    def test_partial_failure_continues(self):
        """batch_create MUST NOT abort on the first failure — one
        malformed row should not tank a whole batch. This is the
        "collect what you can, log the rest" contract."""
        proxy = _make_proxy()
        items_req = proxy._client.sites.by_site_id.return_value.lists.by_list_id.return_value.items
        counter = {"n": 0}
        posted = MagicMock()
        posted.id = "ok"

        async def flaky_post(body):
            counter["n"] += 1
            if counter["n"] == 2:
                raise RuntimeError("bad row")
            return posted

        items_req.post = AsyncMock(side_effect=flaky_post)
        _wire_get_by_id(proxy, _make_field_item("ok", {"a": 1}))

        result = proxy.batch_create([{"a": 1}, {"a": 2}, {"a": 3}])
        assert len(result) == 2  # The bad row was skipped, others kept


# ── batch_update() ──────────────────────────────────────────────────


class TestBatchUpdate:
    """Locks in batch update semantics."""

    def test_skips_records_missing_id(self):
        """Records without ``id`` are skipped silently — this is the
        "some rows are noop" case (e.g. incomplete migrations). Pin so
        a change to raise doesn't cause a pipeline outage."""
        proxy = _make_proxy()
        _wire_patch(proxy)
        _wire_get_by_id(proxy, _make_field_item("1", {"a": 1}))
        result = proxy.batch_update(
            [
                {"id": "1", "fields": {"a": 1}},
                {"fields": {"a": 2}},  # missing id — must skip
                {"id": "", "fields": {"a": 3}},  # empty id — must skip
            ]
        )
        assert len(result) == 1

    def test_continues_on_individual_failure(self):
        """Same partial-failure contract as batch_create."""
        proxy = _make_proxy()
        single = _wire_patch(proxy)
        counter = {"n": 0}

        async def flaky_patch(body):
            counter["n"] += 1
            if counter["n"] == 1:
                raise RuntimeError("row-1 failed")
            return None

        single.fields.patch = AsyncMock(side_effect=flaky_patch)
        _wire_get_by_id(proxy, _make_field_item("2", {}))
        result = proxy.batch_update(
            [
                {"id": "1", "fields": {"a": 1}},
                {"id": "2", "fields": {"a": 2}},
            ]
        )
        assert len(result) == 1


# ── upload_attachment() ────────────────────────────────────────────


class TestUploadAttachment:
    """Locks in attachment upload contract: safe on missing file, safe
    on Graph error. Callers can't distinguish these three failure
    modes today because they all return None — this is intentional
    per the "log warning + degrade" pattern for non-critical assets."""

    def test_returns_none_when_file_missing(self, tmp_path):
        """Nonexistent file → None; no exception."""
        proxy = _make_proxy()
        result = proxy.upload_attachment("1", "photo", str(tmp_path / "nope.png"))
        assert result is None

    def test_returns_metadata_on_success(self, tmp_path):
        """Successful upload → {filename, size} dict."""
        proxy = _make_proxy()
        f = tmp_path / "img.png"
        f.write_bytes(b"PNG-BYTES")

        single = proxy._client.sites.by_site_id.return_value.lists.by_list_id.return_value.items.by_list_item_id.return_value
        single.drive_item.content.put = AsyncMock(return_value=None)

        result = proxy.upload_attachment("1", "photo", str(f))
        assert result == {"filename": "img.png", "size": 9}

    def test_returns_none_on_upload_error(self, tmp_path):
        """Graph error → None (log + degrade)."""
        proxy = _make_proxy()
        f = tmp_path / "img.png"
        f.write_bytes(b"x")

        single = proxy._client.sites.by_site_id.return_value.lists.by_list_id.return_value.items.by_list_item_id.return_value
        single.drive_item.content.put = AsyncMock(side_effect=RuntimeError("413 too large"))

        result = proxy.upload_attachment("1", "photo", str(f))
        assert result is None


# ── _translate_filter_names ─────────────────────────────────────────


class TestTranslateFilterNames:
    """Locks in the display→internal name substitution inside an OData
    filter string. Regression would break every named-column filter."""

    def test_translates_matched_field(self):
        proxy = _make_proxy()
        proxy._display_to_internal = {"MyCol": "myInternal"}
        result = proxy._translate_filter_names("fields/MyCol eq 'foo'")
        assert result == "fields/myInternal eq 'foo'"

    def test_leaves_unmapped_field_alone(self):
        """No mapping entry → pass through unchanged (fields/x already
        internal-named or a system field)."""
        proxy = _make_proxy()
        proxy._display_to_internal = {}
        result = proxy._translate_filter_names("fields/xyz eq 'v'")
        assert result == "fields/xyz eq 'v'"

    def test_translates_multiple_fields(self):
        proxy = _make_proxy()
        proxy._display_to_internal = {"A": "aa", "B": "bb"}
        result = proxy._translate_filter_names("fields/A eq '1' and fields/B eq '2'")
        assert result == "fields/aa eq '1' and fields/bb eq '2'"


# ── _to_record edge cases ───────────────────────────────────────────


class TestToRecord:
    """Locks in _to_record edge cases not covered by the get() tests."""

    def test_missing_fields_yields_empty_dict(self):
        """Item with no .fields attribute → record with fields={}."""
        proxy = _make_proxy()
        item = MagicMock(spec=["id", "created_date_time"])
        item.id = "1"
        item.created_date_time = None
        result = proxy._to_record(item)
        assert result == {"id": "1", "fields": {}, "createdTime": ""}

    def test_none_fields_yields_empty_dict(self):
        """Item with .fields=None → record with fields={}. This is the
        "Graph returned a bare item" degradation case."""
        proxy = _make_proxy()
        item = MagicMock()
        item.id = "1"
        item.fields = None
        item.created_date_time = None
        result = proxy._to_record(item)
        assert result["fields"] == {}

    def test_empty_link_field_yields_empty_list(self):
        """``story_link=""`` → ``story=[]`` not ``story=[""]`` — the
        empty-string case must not spawn a stray empty linked-ID."""
        proxy = _make_proxy()
        proxy._internal_to_display = {}
        item = _make_field_item("1", {"story_link": ""})
        result = proxy._to_record(item)
        assert result["fields"].get("story") == []


# ── column-map cache (module level) ─────────────────────────────────


class TestColumnMapCache:
    """Locks in the module-level column map cache so repeated proxy
    constructions for the same list don't re-fetch."""

    def test_cache_populated_hits_short_circuit_load(self):
        """When a fresh entry exists in _column_map_cache, __init__
        must skip the async fetch entirely and copy the cached dicts."""
        import time as _time

        from genlab_core.http import graph_proxy

        graph_proxy._column_map_cache["cache-list"] = (
            {"disp": "internal"},
            {"internal": "disp"},
            _time.monotonic(),
        )
        try:
            # Call the REAL _load_column_map — expected to short-circuit.
            proxy = graph_proxy.GraphTableProxy(MagicMock(), "s", "cache-list", "Cached")
            assert proxy._display_to_internal == {"disp": "internal"}
            assert proxy._internal_to_display == {"internal": "disp"}
        finally:
            graph_proxy._column_map_cache.pop("cache-list", None)

    def test_expired_cache_entry_triggers_reload(self):
        """Cache older than TTL must be treated as stale."""
        from genlab_core.http import graph_proxy

        # Stamp entry with a very old timestamp
        graph_proxy._column_map_cache["stale-list"] = (
            {"old": "old"},
            {"old": "old"},
            0.0,  # forever in the past
        )
        try:
            # A stale entry MUST trigger a reload attempt; we can detect
            # it by checking that _display_to_internal is NOT populated
            # from the stale cache. Reload will fail (no real client)
            # and log a warning — we don't care, we only care that the
            # short-circuit did NOT return the stale dicts.
            mock_client = MagicMock()
            mock_client.sites.by_site_id.return_value.lists.by_list_id.return_value.columns.get = (
                AsyncMock(side_effect=RuntimeError("boom"))
            )
            proxy = graph_proxy.GraphTableProxy(mock_client, "s", "stale-list", "Stale")
            # Reload attempted, failed, dicts remain empty (not the stale value).
            assert proxy._display_to_internal == {}
        finally:
            graph_proxy._column_map_cache.pop("stale-list", None)


# ── CLEAR sentinel ──────────────────────────────────────────────────


class TestClearSentinel:
    """Locks in the CLEAR sentinel — the ONLY way to explicitly send
    ``null`` to a SharePoint column. ``None`` values are dropped."""

    def test_clear_is_class_attribute(self):
        """CLEAR must be a distinct sentinel object on the class."""
        from genlab_core.http.graph_proxy import GraphTableProxy

        assert GraphTableProxy.CLEAR is not None
        # It's a plain object() sentinel — no rich equality.
        assert GraphTableProxy.CLEAR is GraphTableProxy.CLEAR
