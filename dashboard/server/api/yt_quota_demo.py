"""Compliance-only endpoint that surfaces the YouTube Data API v3 exchange.

Purpose: give the YouTube API Services compliance reviewer a self-contained
in-browser view of how GenLab calls `videos.insert`. The endpoint runs
`YouTubeClient.publish()` server-side against a small unlisted test asset
and returns the request summary + response payload as JSON. A companion
HTML page renders that JSON on-screen so the reviewer can read it directly
in the compliance recording — no terminal, no DevTools required.

## Safety

Gated behind `GENLAB_YT_COMPLIANCE_DEMO=1`. When unset, both routes return
HTTP 404. NEVER leave the flag set in production `.env`.

The upload is always `unlisted` privacy — the video appears at
`https://youtube.com/watch?v=<id>` for anyone with the URL but isn't
indexed on the channel's public page.

## Registration

Add to `dashboard/server/review_server.py` alongside the other blueprint
registrations:

    from server.api.yt_quota_demo import bp as yt_quota_demo_bp
    app.register_blueprint(yt_quota_demo_bp)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, render_template_string

logger = logging.getLogger(__name__)

# Blueprint name intentionally distinct from the existing `compliance_api`
# blueprint (at /api/v1/compliance/events) — that one is unrelated audit
# tooling. This one is single-purpose for the YouTube quota review.
bp = Blueprint("yt_quota_demo", __name__, url_prefix="/api/v1/yt-quota-demo")


_DEMO_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>GenLab — YouTube Data API v3 videos.insert compliance demo</title>
  <style>
    :root { color-scheme: light; }
    body {
      font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, monospace;
      padding: 100px 40px 40px;
      max-width: 1200px;
      margin: 0 auto;
      background: #f8f9fa;
    }
    h1 { color: #1a73e8; margin-top: 0; font-size: 24px; }
    p  { color: #444; font-size: 14px; line-height: 1.6; }
    #trigger-upload {
      padding: 18px 36px;
      font-size: 18px;
      font-weight: 600;
      background: #0f9d58;
      color: white;
      border: 0;
      border-radius: 10px;
      cursor: pointer;
      margin: 24px 0;
      box-shadow: 0 4px 12px rgba(15, 157, 88, 0.35);
    }
    #trigger-upload:hover  { background: #0b8043; }
    #trigger-upload:disabled { background: #999; cursor: not-allowed; }
    pre {
      background: #0d1117;
      color: #c9d1d9;
      padding: 24px;
      border-radius: 10px;
      white-space: pre-wrap;
      word-break: break-word;
      font-size: 13px;
      line-height: 1.55;
      max-height: 400px;
      overflow: auto;
    }
    .label {
      color: #5f6368;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 1.2px;
      margin-top: 28px;
      margin-bottom: 8px;
      font-weight: 600;
    }
    #video-id {
      background: #e8f0fe;
      color: #1a73e8;
      padding: 12px 20px;
      border-radius: 8px;
      font-size: 16px;
      font-weight: 600;
      display: inline-block;
    }
    .compliance-note {
      background: #fef7e0;
      border-left: 4px solid #f9ab00;
      padding: 12px 16px;
      margin: 20px 0;
      font-size: 13px;
      color: #5f6368;
    }
  </style>
</head>
<body>
  <h1>GenLab — YouTube Data API v3 <code>videos.insert</code></h1>
  <p>
    Clicking the button below invokes <code>YouTubeClient.publish()</code>
    server-side. That method calls
    <code>POST https://www.googleapis.com/upload/youtube/v3/videos?part=snippet,status&amp;uploadType=resumable</code>
    against the OAuth-authenticated channel for the configured niche.
  </p>
  <div class="compliance-note">
    Compliance mode: this endpoint uploads a small unlisted test asset
    (path configured via the <code>YT_COMPLIANCE_ASSET</code> env var).
    The endpoint is only reachable when <code>GENLAB_YT_COMPLIANCE_DEMO=1</code>
    is set on the server — it returns 404 otherwise.
  </div>

  <button id="trigger-upload">Trigger videos.insert</button>

  <div class="label">REQUEST — endpoint, params, body summary</div>
  <pre id="request-pre">—</pre>

  <div class="label">RESPONSE — from googleapis.com/youtube/v3/videos</div>
  <pre id="response-pre">—</pre>

  <div class="label">Resulting videoId</div>
  <div id="video-id">—</div>

<script>
document.getElementById('trigger-upload').onclick = async () => {
  const btn = document.getElementById('trigger-upload');
  btn.disabled = true;
  document.getElementById('response-pre').textContent = 'uploading…';
  try {
    const r = await fetch('/api/v1/yt-quota-demo/trigger', { method: 'POST' });
    const j = await r.json();
    document.getElementById('request-pre').textContent  =
      JSON.stringify(j.request  ?? {}, null, 2);
    document.getElementById('response-pre').textContent =
      JSON.stringify(j.response ?? {}, null, 2);
    const vid = j.response && (j.response.post_id || j.response.videoId);
    document.getElementById('video-id').textContent = vid || '(no id returned)';
  } catch (err) {
    document.getElementById('response-pre').textContent = 'ERROR: ' + err;
  } finally {
    btn.disabled = false;
  }
};
</script>
</body>
</html>
"""


def _enabled() -> bool:
    return os.environ.get("GENLAB_YT_COMPLIANCE_DEMO", "").strip() == "1"


@bp.route("/page", methods=["GET"])
def demo_page():
    """Render the in-browser demo page.

    Reviewer sees a single-page HTML with a button. Clicking the button
    fires the compliance-scoped upload and populates the request/response
    <pre> blocks with the JSON that came back from googleapis.com.
    """
    if not _enabled():
        return "GenLab YouTube quota compliance demo endpoint is disabled.", 404
    return render_template_string(_DEMO_HTML)


@bp.route("/trigger", methods=["POST"])
def trigger_upload():
    """Fire a single YouTubeClient.publish() call and return the exchange.

    Returns 404 unless GENLAB_YT_COMPLIANCE_DEMO=1. On success, returns
    JSON of the form:

        {
          "request":  { endpoint, params, body_summary },
          "response": { post_id, post_url, success, error, raw_response }
        }

    The browser JS in _DEMO_HTML renders both blocks on-screen for the
    Playwright compliance recording to capture.
    """
    if not _enabled():
        return jsonify({"error": "disabled"}), 404

    asset_path = os.environ.get("YT_COMPLIANCE_ASSET", "").strip()
    niche_id = os.environ.get("YT_COMPLIANCE_NICHE", "ai_creators").strip() or "ai_creators"

    if not asset_path or not Path(asset_path).is_file():
        return (
            jsonify(
                {
                    "request": {"endpoint": "POST https://www.googleapis.com/upload/youtube/v3/videos"},
                    "response": {
                        "success": False,
                        "error": (
                            f"YT_COMPLIANCE_ASSET is not a readable file: {asset_path!r}. "
                            "Set the env var on the dashboard host to an absolute path to a small MP4."
                        ),
                    },
                }
            ),
            400,
        )

    # Import lazily so this module doesn't fail-import when compliance mode
    # is off (e.g. genlab_core import failures shouldn't take the dashboard
    # down when GENLAB_YT_COMPLIANCE_DEMO isn't set).
    from genlab_core.platforms.models import PublishPayload
    from genlab_core.platforms.youtube import YouTubeClient

    request_summary: dict[str, Any] = {
        "endpoint": "POST https://www.googleapis.com/upload/youtube/v3/videos",
        "params": {"part": "snippet,status", "uploadType": "resumable"},
        "body_summary": {
            "snippet": {
                "title": "GenLab API compliance test",
                "description": (
                    "Uploaded by the GenLab platform as part of the "
                    "YouTube API Services compliance review. Unlisted."
                ),
                "categoryId": 22,
            },
            "status": {
                "privacyStatus": "unlisted",
                "selfDeclaredMadeForKids": False,
            },
        },
        "auth": "OAuth2 (per-niche refresh token — resolve_youtube_credentials)",
        "niche_id": niche_id,
        "media": str(asset_path),
    }

    payload = PublishPayload(
        caption=(
            "GenLab API compliance test — uploaded by our platform as part "
            "of the YouTube API Services quota review. Unlisted."
        ),
        media_paths=[Path(asset_path)],
        media_type="video",
        hashtags=[],
        hook="GenLab API compliance test",
        niche_id=niche_id,
    )

    try:
        client = YouTubeClient(niche_id=niche_id)
        result = client.publish(payload)
    except Exception as exc:  # noqa: BLE001 — surface any error to the reviewer
        logger.exception("[yt-quota-demo] publish failed")
        return (
            jsonify(
                {
                    "request": request_summary,
                    "response": {"success": False, "error": f"{type(exc).__name__}: {exc}"},
                }
            ),
            500,
        )

    response_body = {
        "success": result.success,
        "post_id": result.post_id,
        "post_url": result.post_url,
        "error": result.error,
        # `raw_response` from PublishResult carries the googleapis.com body
        # (video id, status, snippet, upload details). Include verbatim so
        # the reviewer sees the actual API response payload.
        "raw_response": result.raw_response,
    }

    return jsonify({"request": request_summary, "response": response_body})
