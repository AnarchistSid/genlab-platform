"""Assets persistence, extracted from BacklogClient.

Six methods live in this module:

* :meth:`AssetStore.create_asset` — record a single asset row,
  optionally linking it to a story and/or blueprint via injected
  finder callables. Falls back to a no-quality-fields create if the
  backend rejects unknown columns.
* :meth:`AssetStore.find_asset_by_asset_id` — single-row lookup.
* :meth:`AssetStore.find_asset_by_url` — single-row lookup by URL.
* :meth:`AssetStore.find_assets_by_story_id` — find by story link
  (uses the injected ``find_story`` callable to translate
  ``story_id → record_id`` first).
* :meth:`AssetStore.update_asset_status` — find by asset_id then
  update; raises ValueError if missing.
* :meth:`AssetStore.batch_create_assets` — bulk insert with dedupe
  on ``asset_id``; loops over ``create_asset``.

Tier 2 / audit S-2, slice 2.5b — sixth focused extraction from the
BacklogClient god class. Companion to slice 2.5a (StoryStore).

Constructor takes four callables — three from BacklogClient (or
its already-extracted StoryStore) plus the backend lookup:

* ``backend``: live backend lookup.
* ``find_story``: ``StoryStore.find_story_by_story_id`` — needed for
  the story-link resolution in ``create_asset`` /
  ``find_assets_by_story_id``.
* ``find_blueprint``: ``BacklogClient.find_blueprint_by_candidate_id``
  — needed for the blueprint-link resolution in ``create_asset``.
  (Stays on BacklogClient because the Blueprints surface hasn't been
  extracted yet — it's the next major slice on the roadmap.)
* ``normalize_asset_source_type``:
  ``BacklogClient._normalize_asset_source_type`` — kept centralised
  because it depends on the ``ASSET_SOURCE_TYPE_ALLOWED`` constant
  shared with config validators.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from genlab_core.http.analytics_store import _esc

logger = logging.getLogger(__name__)


class AssetStore:
    """CRUD over the Assets table."""

    def __init__(
        self,
        backend: Callable[[str], Any],
        find_story: Callable[..., dict | None],
        find_blueprint: Callable[..., dict | None],
        normalize_asset_source_type: Callable[[str | None, str], str],
    ) -> None:
        self._backend = backend
        self._find_story = find_story
        self._find_blueprint = find_blueprint
        self._normalize_asset_source_type = normalize_asset_source_type

    # ── Create ─────────────────────────────────────────────────────

    def create_asset(self, asset: dict) -> str:
        """Record a single asset.

        If ``story_id`` is provided, links the asset to that story
        (via the injected ``find_story`` callable). Same for
        ``blueprint_id`` (via ``find_blueprint``).

        Quality fields (``quality_tier``, ``width``, ``height``) are
        opportunistically included — the create retries without them
        if the backend rejects unknown columns (graceful migration
        path for old SharePoint schemas).
        """
        fields = {
            "asset_id": asset["asset_id"],
            "type": asset["type"],
            "url": asset.get("source_url", asset.get("url", "")),
            "status": asset.get("status", "GENERATING"),
            "source_type": self._normalize_asset_source_type(
                asset.get("source"), asset.get("type", "")
            ),
            "alt_text": asset.get("alt_text", ""),
            "tool_used": asset.get("tool_used"),
            "generation_params": asset.get("generation_params", ""),
            "error_log": asset.get("error_log", asset.get("error", "")),
        }

        quality_fields: dict[str, Any] = {}
        if asset.get("quality_tier"):
            quality_fields["quality_tier"] = asset["quality_tier"]
        if asset.get("width"):
            quality_fields["width"] = asset["width"]
        if asset.get("height"):
            quality_fields["height"] = asset["height"]

        if asset.get("story_id"):
            story = self._find_story(asset["story_id"])
            if story:
                fields["story"] = [story["id"]]

        if asset.get("blueprint_id"):
            blueprint = self._find_blueprint(asset["blueprint_id"])
            if blueprint:
                fields["blueprint"] = [blueprint["id"]]

        source_url = asset.get("source_url", asset.get("url", ""))
        if source_url and asset.get("status") == "READY":
            fields["file"] = [{"url": source_url}]

        be = self._backend("Assets")
        if quality_fields:
            try:
                record = be.create(
                    "Assets",
                    {**fields, **quality_fields},
                    typecast=True,
                )
                return record["id"]
            except Exception as e:
                # Graceful fallback: old SP schemas don't know about
                # quality columns. Retry without them.
                if "UNKNOWN_FIELD_NAME" not in str(e) and "columnNotFound" not in str(e):
                    raise

        record = be.create("Assets", fields, typecast=True)
        return record["id"]

    # ── Read ───────────────────────────────────────────────────────

    def find_asset_by_asset_id(
        self,
        asset_id: str,
        *,
        niche_id: str | None = None,
    ) -> dict | None:
        formula = f"{{asset_id}}='{_esc(asset_id)}'"
        records = self._backend("Assets").find(
            "Assets",
            formula=formula,
            niche_id=niche_id,
            max_records=1,
        )
        return records[0] if records else None

    def find_asset_by_url(
        self,
        url: str,
        *,
        niche_id: str | None = None,
    ) -> dict | None:
        # Note: the original used ``url.replace("'", "\\'")`` inline
        # instead of ``_esc``. They produce the same output, so just
        # use ``_esc`` for consistency with the rest of the file.
        formula = f"{{url}}='{_esc(url)}'"
        records = self._backend("Assets").find(
            "Assets",
            formula=formula,
            niche_id=niche_id,
            max_records=1,
        )
        return records[0] if records else None

    def find_assets_by_story_id(
        self,
        story_id: str,
        *,
        niche_id: str | None = None,
    ) -> list[dict]:
        story = self._find_story(story_id, niche_id=niche_id)
        if not story:
            return []
        formula = f"{{story_link}}='{_esc(story['id'])}'"
        return self._backend("Assets").find(
            "Assets",
            formula=formula,
            niche_id=niche_id,
        )

    # ── Update ─────────────────────────────────────────────────────

    def update_asset_status(
        self,
        asset_id: str,
        status: str,
        *,
        niche_id: str | None = None,
        **kwargs,
    ) -> None:
        """Set an asset's status (and any other ``**kwargs`` fields).

        Raises ``ValueError`` if no row matches — matches the
        historical behaviour callers rely on.
        """
        formula = f"{{asset_id}}='{_esc(asset_id)}'"
        records = self._backend("Assets").find(
            "Assets",
            formula=formula,
            niche_id=niche_id,
            max_records=1,
        )
        if not records:
            raise ValueError(f"Asset {asset_id} not found")
        self._backend("Assets").update(
            "Assets",
            records[0]["id"],
            {"status": status, **kwargs},
        )

    # ── Batch ──────────────────────────────────────────────────────

    def batch_create_assets(self, assets: list[dict]) -> list[str]:
        """Bulk-insert assets, deduping on ``asset_id``.

        Unlike ``StoryStore.batch_create_stories`` this is a loop
        over ``create_asset`` (not a true backend.batch_create) —
        each asset needs its own story/blueprint link resolution +
        graceful quality-fields retry path, which can't be done in
        a single bulk call. Failures on individual assets are
        logged + skipped, not raised.
        """
        created_ids = []
        for asset in assets:
            existing = self.find_asset_by_asset_id(asset["asset_id"])
            if existing:
                continue
            try:
                record_id = self.create_asset(asset)
                created_ids.append(record_id)
            except Exception as exc:
                logger.error("Failed to create asset %s: %s", asset["asset_id"], exc)
        return created_ids
