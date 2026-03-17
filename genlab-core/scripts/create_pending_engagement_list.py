#!/usr/bin/env python3
"""
One-time setup script: creates the PendingEngagement SharePoint list.
Run from the genlab-core repo root:
    cd /Users/anarchistsid/GenLab/genlab-core
    uv run python scripts/create_pending_engagement_list.py

Pass --list-id=<id> to skip list creation and just add columns to an existing list.

The site ID is hardcoded from the confirmed site:
  veritasonellp.sharepoint.com,4020953b-b622-4a33-a0ea-763386c6af24,9a1be041-799b-4c47-982d-07bb5ceb099e
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

SITE_ID = (
    "veritasonellp.sharepoint.com,"
    "4020953b-b622-4a33-a0ea-763386c6af24,"
    "9a1be041-799b-4c47-982d-07bb5ceb099e"
)

LIST_NAME        = "PendingEngagement"
LIST_DESCRIPTION = (
    "Incoming comment events captured by the engagement engine pollers. "
    "Status lifecycle: pending -> replied | skipped | toxic"
)

COLUMNS = [
    ("Platform",    "text",     "Platform: instagram | youtube | twitter | facebook | threads"),
    ("PostId",      "text",     "Platform-native ID of the published post / video"),
    ("CommentText", "note",     "Raw comment text (truncated to 2000 chars)"),
    ("AuthorName",  "text",     "Display name of the commenter"),
    ("AuthorId",    "text",     "Platform-native user ID of the commenter"),
    ("CreatedAt",   "dateTime", "ISO-8601 UTC timestamp of the original comment"),
    ("NicheId",     "text",     "Niche that owns this post: ai_creators | gaming | sports | movies | anime"),
    ("Status",      "text",     "Processing status: pending | replied | skipped | toxic"),
    ("IsReply",     "boolean",  "True if this is a reply to another comment"),
    ("ParentId",    "text",     "Parent comment ID for nested replies (nullable)"),
]

INDEXED_COLUMNS = {"NicheId", "Status", "Platform", "PostId"}


def get_graph_client():
    from azure.identity import ClientSecretCredential
    from msgraph import GraphServiceClient

    credential = ClientSecretCredential(
        os.environ["AZURE_TENANT_ID"],
        os.environ["AZURE_CLIENT_ID"],
        os.environ["AZURE_CLIENT_SECRET"],
    )
    return GraphServiceClient(credential, scopes=["https://graph.microsoft.com/.default"])


async def create_list(client) -> str:
    from msgraph.generated.models.list_ import List_
    from msgraph.generated.models.list_info import ListInfo

    body = List_()
    body.display_name = LIST_NAME
    body.description  = LIST_DESCRIPTION
    info = ListInfo()
    info.template = "genericList"
    body.list = info

    result = await client.sites.by_site_id(SITE_ID).lists.post(body)
    log.info("Created list '%s' -> id=%s", LIST_NAME, result.id)
    return result.id


async def add_columns(client, list_id: str) -> None:
    from msgraph.generated.models.boolean_column import BooleanColumn
    from msgraph.generated.models.column_definition import ColumnDefinition
    from msgraph.generated.models.date_time_column import DateTimeColumn
    from msgraph.generated.models.text_column import TextColumn

    lists = client.sites.by_site_id(SITE_ID).lists.by_list_id(list_id)

    for col_name, col_type, description in COLUMNS:
        col = ColumnDefinition()
        col.name        = col_name
        col.description = description
        col.enforce_unique_values = False
        col.indexed     = col_name in INDEXED_COLUMNS

        if col_type == "text":
            col.text = TextColumn()
        elif col_type == "note":
            tc = TextColumn()
            tc.allow_multiple_lines = True
            col.text = tc
        elif col_type == "boolean":
            col.boolean = BooleanColumn()
        elif col_type == "dateTime":
            dt = DateTimeColumn()
            dt.display_as = "default"
            col.date_time = dt

        try:
            await lists.columns.post(col)
            log.info("  added column: %s (%s)", col_name, col_type)
        except Exception as e:
            if "nameAlreadyExists" in str(e):
                log.info("  skipped column (already exists): %s", col_name)
            else:
                raise


async def rename_title_to_comment_id(client, list_id: str) -> None:
    from msgraph.generated.models.column_definition import ColumnDefinition

    lists = client.sites.by_site_id(SITE_ID).lists.by_list_id(list_id)
    cols = await lists.columns.get()
    title_col = next((c for c in cols.value if c.name == "Title"), None)

    if title_col:
        update = ColumnDefinition()
        update.name        = "CommentId"
        update.description = "Platform-native comment ID (primary key)"
        await lists.columns.by_column_definition_id(title_col.id).patch(update)
        log.info("  renamed Title -> CommentId")
    else:
        log.warning("  Title column not found -- skipping rename")


async def async_main(existing_list_id: str | None = None):
    log.info("=== Creating PendingEngagement list in SharePoint ===")
    log.info("Site: %s", SITE_ID)

    client = get_graph_client()

    if existing_list_id:
        list_id = existing_list_id
        log.info("Using existing list: %s", list_id)
    else:
        list_id = await create_list(client)

    log.info("Adding columns...")
    await add_columns(client, list_id)
    await rename_title_to_comment_id(client, list_id)

    log.info("")
    log.info("=== Done ===")
    log.info("List ID: %s", list_id)
    log.info("")
    log.info("Add this to genlab_core/data/backlog_client.py LIST_IDS:")
    log.info('  "PendingEngagement": "%s"', list_id)


def main():
    existing_id = None
    for arg in sys.argv[1:]:
        if arg.startswith("--list-id="):
            existing_id = arg.split("=", 1)[1]

    asyncio.run(async_main(existing_id))


if __name__ == "__main__":
    main()
