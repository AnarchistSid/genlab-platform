#!/usr/bin/env python3
"""Add ReplyText, ProcessedAt, and ErrorMessage columns to PendingEngagement.

Run once:
    cd /Users/anarchistsid/GenLab/genlab-core
    uv run python scripts/add_engagement_reply_columns.py
"""
from __future__ import annotations
import asyncio
import os
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

SITE_ID = (
    "veritasonellp.sharepoint.com,"
    "4020953b-b622-4a33-a0ea-763386c6af24,"
    "9a1be041-799b-4c47-982d-07bb5ceb099e"
)
LIST_ID = "5d03ac7f-d0d6-4291-93e6-c61e310e92f3"

NEW_COLUMNS = [
    ("ReplyText",    "note",     "Generated reply text posted to the platform (truncated to 2000 chars)"),
    ("ProcessedAt",  "dateTime", "ISO-8601 UTC timestamp when this event was processed"),
    ("ErrorMessage", "text",     "Error message if processing failed (truncated to 500 chars)"),
]


async def main():
    from azure.identity import ClientSecretCredential
    from msgraph import GraphServiceClient
    from msgraph.generated.models.column_definition import ColumnDefinition
    from msgraph.generated.models.text_column import TextColumn
    from msgraph.generated.models.date_time_column import DateTimeColumn

    credential = ClientSecretCredential(
        os.environ["AZURE_TENANT_ID"],
        os.environ["AZURE_CLIENT_ID"],
        os.environ["AZURE_CLIENT_SECRET"],
    )
    client = GraphServiceClient(credential, scopes=["https://graph.microsoft.com/.default"])
    lists = client.sites.by_site_id(SITE_ID).lists.by_list_id(LIST_ID)

    log.info("Adding columns to PendingEngagement list %s", LIST_ID)

    for col_name, col_type, description in NEW_COLUMNS:
        col = ColumnDefinition()
        col.name = col_name
        col.description = description
        col.enforce_unique_values = False
        col.indexed = False

        if col_type == "text":
            col.text = TextColumn()
        elif col_type == "note":
            tc = TextColumn()
            tc.allow_multiple_lines = True
            col.text = tc
        elif col_type == "dateTime":
            dt = DateTimeColumn()
            dt.display_as = "default"
            col.date_time = dt

        try:
            await lists.columns.post(col)
            log.info("  added: %s (%s)", col_name, col_type)
        except Exception as e:
            if "nameAlreadyExists" in str(e):
                log.info("  skipped (already exists): %s", col_name)
            else:
                log.error("  FAILED: %s — %s", col_name, e)
                raise

    log.info("Done. PendingEngagement now has ReplyText, ProcessedAt, ErrorMessage columns.")


if __name__ == "__main__":
    asyncio.run(main())
