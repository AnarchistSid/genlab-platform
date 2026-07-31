#!/usr/bin/env python3
"""Terminal-visible YouTube Data API v3 videos.insert invocation for compliance.

Purpose
-------
The YouTube API Services team's third-and-final quota-review notice asks for
a screencast that shows *how* GenLab uses the API to upload videos, from the
production client location, through to the end result. A browser-only demo
(dashboard button → server-side call → in-browser response render) hides the
API call behind the UI. A terminal-visible invocation puts the request and
the full googleapis response body on screen where the reviewer can read
them directly.

This script imports the production ``YouTubeClient`` and runs one real
``videos.insert`` via the same ``publish()`` code path the daily pipeline
uses (quota gate, channel verification, Layer 4 attribution — all of it).
It prints:

  1. The request summary (endpoint, params, snippet + status body)
  2. Progress log as the resumable upload proceeds
  3. The full googleapis response body from ``PublishResult.raw_response``
  4. The public Shorts URL (matching ``PublishResult.post_url``,
     ``https://youtube.com/shorts/<video_id>``)

No mocks. No shortcuts. Same client, same auth, same defense stack as prod.

Usage
-----
    python compliance/youtube-quota/run_compliance_upload.py \\
        --niche ai_creators \\
        --asset /opt/genlab/compliance-test.mp4

Prereqs
-------
- Runs on the dashboard host (Hetzner nbg1) so the client location matches
  where prod actually uploads from.
- ``.env`` on the host has the per-niche YouTube OAuth bundle for the
  chosen niche (same env the ``genlab-*`` systemd services use).
- ``--asset`` is a real, readable MP4 (≤60s recommended). The upload will
  land on the niche's real channel as ``privacyStatus=unlisted`` — safe to
  delete after the compliance thread closes.
- ``GENLAB_YT_COMPLIANCE_DEMO`` is *not* required (this script bypasses
  the Flask gate — it talks to ``YouTubeClient`` directly).

Exit codes
----------
- 0: upload succeeded, video_id printed (or --check passed)
- 2: bad CLI args or missing asset file
- 3: publish() returned success=False (auth, quota, channel-mismatch, layer4)
- 4: unexpected exception
- 5: --check failed (auth couldn't complete or asset unreadable)

--check mode
------------
When invoked with ``--check``, the script validates the environment WITHOUT
uploading:

  1. Confirms ``--asset`` is a readable file (same check as real mode).
  2. Instantiates ``YouTubeClient(niche_id=...)`` — surfaces credential
     resolution errors from ``resolve_youtube_credentials``.
  3. Exchanges the refresh token for an access token via Google's OAuth
     endpoint — surfaces expired/revoked-token errors.
  4. Calls ``channels.list?part=id&mine=true`` — 1 quota unit (cheapest
     authenticated read; see ``genlab_core/monitoring/youtube_quota.py:96``)
     — and prints the resolved channel ID.

If all four pass, the operator can be confident a subsequent no-flag run will
succeed. Cost: 1 quota unit against the daily 10,000 budget; safe to run
repeatedly during a recording day.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


def _serialize(obj: Any) -> Any:
    """JSON-safe conversion: dataclass -> dict, Path -> str, fallback str()."""
    if is_dataclass(obj):
        return {k: _serialize(v) for k, v in asdict(obj).items()}
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(v) for v in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def _print_block(label: str, payload: Any) -> None:
    bar = "=" * 78
    print(f"\n{bar}\n== {label}\n{bar}")
    print(json.dumps(_serialize(payload), indent=2, sort_keys=False))
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fire one real videos.insert via the production YouTubeClient.",
    )
    parser.add_argument(
        "--niche",
        required=True,
        choices=["ai_creators", "gaming", "sports", "movies", "anime"],
        help="Niche whose YouTube OAuth bundle should authenticate the upload.",
    )
    parser.add_argument(
        "--asset",
        required=True,
        type=Path,
        help="Absolute path to a readable MP4 to upload as an unlisted test video.",
    )
    parser.add_argument(
        "--title",
        default="GenLab API compliance test",
        help="Video title. Default: 'GenLab API compliance test'.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging on the YouTubeClient module.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Dry-run: validate asset + credentials + channel-list read WITHOUT "
            "uploading. Costs 1 quota unit (channels.list). Exits 0 on success, "
            "5 on failure."
        ),
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    if not args.asset.is_file():
        print(f"ERROR: --asset is not a readable file: {args.asset}", file=sys.stderr)
        return 2

    # Lazy imports: keep import errors close to the failing line for reviewers.
    # Wrapped so a missing genlab_core (e.g. wrong PYTHONPATH) becomes a
    # readable one-line message instead of a bare traceback — especially
    # important for --check, whose whole job is diagnosing setup issues
    # before recording.
    try:
        from genlab_core.platforms.models import PublishPayload, YouTubeSpecific
        from genlab_core.platforms.youtube import YouTubeClient
    except ImportError as exc:
        print(
            f"ERROR: could not import genlab_core ({exc}). "
            "Ensure PYTHONPATH includes the workspace or the venv with "
            "genlab-core installed is activated.",
            file=sys.stderr,
        )
        return 5 if args.check else 4

    if args.check:
        print(f"[check] asset OK — {args.asset} ({args.asset.stat().st_size} bytes)")
        try:
            client = YouTubeClient(niche_id=args.niche, enable_quota_gate=False)
        except Exception as exc:  # noqa: BLE001
            print(
                f"[check] FAIL — YouTubeClient instantiation raised "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            print(
                "[check] hint: this is where per-niche OAuth resolution runs. "
                "If a credential is missing, the error above names it. Load the "
                "per-niche .env (e.g. sourcing /opt/genlab/.env) before running "
                "again.",
                file=sys.stderr,
            )
            return 5
        try:
            token = client._get_access_token()  # noqa: SLF001 — dry-run intentional
        except Exception as exc:  # noqa: BLE001
            print(
                f"[check] FAIL — OAuth token exchange raised "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return 5
        print("[check] OAuth token exchange OK")
        channel_id = client._get_channel_id(token)  # noqa: SLF001
        if not channel_id:
            print(
                "[check] FAIL — channels.list?mine=true returned no items. "
                "Refresh token may be revoked or bound to a Google account with "
                "no channel.",
                file=sys.stderr,
            )
            return 5
        print(f"[check] channels.list OK — token resolves to channel {channel_id}")
        print("[check] PASS — safe to run without --check to perform the upload")
        return 0

    # Caption carries the Layer 4 attribution marker required by the platform's
    # own defense stack (see genlab_core/platforms/caption_validation.py).
    # Using "Footage:" (not "🎬 Original:") because this is not a repurposed
    # creator clip — it's a first-party test video. Layer 4 accepts both.
    caption = (
        f"{args.title} — uploaded by the GenLab platform as part of the "
        "YouTube API Services quota review. Unlisted.\n\n"
        f"Footage: https://youtube.com/@GenLab (self-produced compliance sample)"
    )

    payload = PublishPayload(
        caption=caption,
        media_paths=[args.asset],
        media_type="video",
        hashtags=[],
        hook=args.title,
        niche_id=args.niche,
        platform_specific=YouTubeSpecific(
            shorts_title=args.title,
            privacy_status="unlisted",
            category_id="24",  # Entertainment
            tags=["compliance", "api-test"],
        ),
    )

    request_summary = {
        "endpoint": "POST https://www.googleapis.com/upload/youtube/v3/videos",
        "params": {"part": "snippet,status", "uploadType": "resumable"},
        "auth": (
            "OAuth2 bearer token (per-niche refresh_token resolved via "
            "genlab_core.publishing.niche_credentials.resolve_youtube_credentials)"
        ),
        "client_location": "Hetzner nbg1 (production dashboard host)",
        "niche_id": args.niche,
        "media_path": str(args.asset),
        "media_bytes": args.asset.stat().st_size,
        "body_summary": {
            "snippet": {
                "title": args.title,
                "description_prefix": caption.split("\n", 1)[0],
                "categoryId": "24",
                "tags": ["compliance", "api-test"],
            },
            "status": {
                "privacyStatus": "unlisted",
                "selfDeclaredMadeForKids": False,
            },
        },
    }
    _print_block("REQUEST — what we're about to send to googleapis.com", request_summary)

    print("\n-- calling YouTubeClient.publish() ... --", flush=True)

    try:
        client = YouTubeClient(niche_id=args.niche)
        result = client.publish(payload)
    except Exception as exc:  # noqa: BLE001 — surface all errors to the reviewer
        print(f"\nERROR: publish() raised {type(exc).__name__}: {exc}", file=sys.stderr)
        logging.exception("compliance upload failed")
        return 4

    _print_block(
        "RESPONSE — PublishResult (raw_response.youtube_response is the "
        "verbatim googleapis videos.insert body)",
        {
            "platform": result.platform,
            "success": result.success,
            "post_id": result.post_id,
            "post_url": result.post_url,
            "error": result.error,
            "raw_response": result.raw_response,
        },
    )

    if not result.success:
        print(f"\nFAILED: {result.error}", file=sys.stderr)
        return 3

    print("\n-- END RESULT --", flush=True)
    print(f"Video is live at: {result.post_url}")
    print(f"Video ID:         {result.post_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
