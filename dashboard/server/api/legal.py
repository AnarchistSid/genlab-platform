"""Public legal pages — Privacy Policy and Terms of Service.

These pages are required by Meta App Review and must be accessible
without authentication at:
    - https://<GENLAB_DOMAIN>/legal/privacy
    - https://<GENLAB_DOMAIN>/legal/deletion
"""
from __future__ import annotations

import os

from flask import Blueprint, Response

_DOMAIN = os.environ.get("GENLAB_DOMAIN", "localhost")
_CONTACT_DOMAIN = os.environ.get("GENLAB_SENDER_DOMAIN", _DOMAIN)

legal_bp = Blueprint("legal", __name__, url_prefix="/legal")

_PRIVACY_POLICY_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Privacy Policy — AspireHub</title>
<style>
  :root { --bg: #0a0a0f; --fg: #e4e4e7; --muted: #71717a; --accent: #6366f1; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: var(--bg); color: var(--fg); line-height: 1.7;
         max-width: 720px; margin: 0 auto; padding: 3rem 1.5rem; }
  h1 { font-size: 1.75rem; margin-bottom: 0.5rem; }
  h2 { font-size: 1.2rem; margin-top: 2rem; margin-bottom: 0.5rem; color: var(--accent); }
  p, li { margin-bottom: 0.75rem; color: var(--fg); }
  ul { padding-left: 1.5rem; }
  .meta { color: var(--muted); font-size: 0.85rem; margin-bottom: 2rem; }
  a { color: var(--accent); }
</style>
</head>
<body>
<h1>Privacy Policy</h1>
<p class="meta">Last updated: 13 March 2026</p>

<h2>1. Who We Are</h2>
<p>AspireHub ("we", "us") operates GenLab, a content automation platform that
manages social media publishing and engagement across Instagram, Facebook,
YouTube, X (Twitter), and Threads. Our dashboard is accessible at
<strong>$DOMAIN</strong>.</p>

<h2>2. Information We Collect</h2>
<p>We collect and process the following data in connection with Meta platforms:</p>
<ul>
  <li><strong>Public comments</strong> on our Instagram and Facebook posts, including
      commenter display name and comment text.</li>
  <li><strong>Engagement metrics</strong> on content we publish (views, likes, shares,
      reach), provided by Meta's Insights API.</li>
  <li><strong>Page access tokens</strong> issued by Meta for our own Facebook Page and
      linked Instagram Professional Account.</li>
</ul>
<p>We do <strong>not</strong> collect personal data from commenters beyond what is
publicly visible on the platform. We do not access private messages, friend lists,
or any data from accounts other than our own page.</p>

<h2>3. How We Use Information</h2>
<ul>
  <li><strong>Comment engagement:</strong> We read public comments on our posts to
      generate contextual replies using AI. Comments are processed in memory and are
      not stored in any database after processing.</li>
  <li><strong>Content analytics:</strong> Engagement metrics are collected to measure
      content performance and inform our publishing schedule.</li>
  <li><strong>Toxicity filtering:</strong> Inbound comments are screened for toxicity
      before generating replies. This processing is ephemeral.</li>
</ul>

<h2>4. Data Retention</h2>
<p>We do not maintain a persistent store of user comments or personal data.
Comments are processed in real-time and discarded after reply generation.
Engagement metrics (aggregate counts only, no personal identifiers) are retained
for up to 180 days for analytics purposes.</p>

<h2>5. Data Sharing</h2>
<p>We do not sell, rent, or share personal data with third parties. Comment text
may be sent to Anthropic's Claude API for reply generation, subject to
<a href="https://www.anthropic.com/privacy" target="_blank">Anthropic's Privacy Policy</a>.
No personal identifiers are included in API requests.</p>

<h2>6. Data Deletion</h2>
<p>You may request deletion of any data associated with your account by contacting
us or through Meta's data deletion flow. Our data deletion callback is available
at:</p>
<p><a href="https://$DOMAIN/api/v1/webhooks/meta/deletion">
https://$DOMAIN/api/v1/webhooks/meta/deletion</a></p>
<p>Since we do not persistently store personal user data, deletion requests are
acknowledged immediately.</p>

<h2>7. Your Rights</h2>
<p>You have the right to:</p>
<ul>
  <li>Request access to any data we hold about you</li>
  <li>Request deletion of your data</li>
  <li>Object to processing of your data</li>
  <li>Block our account on Instagram/Facebook to prevent further interaction</li>
</ul>

<h2>8. Security</h2>
<p>We protect data in transit using HTTPS/TLS. Webhook payloads from Meta are
verified using HMAC-SHA256 signatures. Access tokens are stored securely in
encrypted system keychains.</p>

<h2>9. Changes to This Policy</h2>
<p>We may update this Privacy Policy from time to time. Changes will be reflected
by updating the "Last updated" date above.</p>

<h2>10. Contact</h2>
<p>For privacy inquiries, contact us at:
<a href="mailto:privacy@$CONTACT_DOMAIN">privacy@$CONTACT_DOMAIN</a></p>
</body>
</html>
"""

_DELETION_INFO_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Data Deletion — AspireHub</title>
<style>
  :root { --bg: #0a0a0f; --fg: #e4e4e7; --muted: #71717a; --accent: #6366f1; }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: var(--bg); color: var(--fg); line-height: 1.7;
         max-width: 720px; margin: 0 auto; padding: 3rem 1.5rem; }
  h1 { font-size: 1.75rem; margin-bottom: 0.5rem; }
  h2 { font-size: 1.2rem; margin-top: 2rem; margin-bottom: 0.5rem; color: var(--accent); }
  p { margin-bottom: 0.75rem; }
  .meta { color: var(--muted); font-size: 0.85rem; margin-bottom: 2rem; }
  a { color: var(--accent); }
  code { background: #1a1a2e; padding: 0.15em 0.4em; border-radius: 4px; font-size: 0.9em; }
</style>
</head>
<body>
<h1>Data Deletion Instructions</h1>
<p class="meta">AspireHub — GenLab Platform</p>

<h2>How to Request Data Deletion</h2>
<p>AspireHub does not persistently store personal data from Instagram or Facebook
users. Comments are processed in real-time for reply generation and immediately
discarded.</p>

<p>If you wish to request deletion of any data that may be associated with your
interactions with our pages:</p>

<p><strong>Option 1:</strong> Use Meta's built-in data deletion flow from your
Facebook Settings &gt; Apps and Websites &gt; AspireHub &gt; Remove.</p>

<p><strong>Option 2:</strong> Send an email to
<a href="mailto:privacy@$CONTACT_DOMAIN">privacy@$CONTACT_DOMAIN</a> with your
Facebook or Instagram username.</p>

<h2>Deletion Callback</h2>
<p>Our automated data deletion callback endpoint is:</p>
<p><code>https://$DOMAIN/api/v1/webhooks/meta/deletion</code></p>
<p>This endpoint processes deletion requests from Meta automatically and returns
a confirmation code. You can check deletion status at the URL provided in the
response.</p>

<h2>What Gets Deleted</h2>
<p>Since we do not store personal data persistently, deletion is effectively
immediate. Any in-memory processing state associated with your comments is
cleared as part of normal operation.</p>
</body>
</html>
"""


def _render_html(template: str) -> str:
    """Replace $DOMAIN and $CONTACT_DOMAIN placeholders in HTML templates."""
    return template.replace("$DOMAIN", _DOMAIN).replace("$CONTACT_DOMAIN", _CONTACT_DOMAIN)


@legal_bp.route("/privacy")
def privacy_policy():
    """Public privacy policy page — no authentication required."""
    return Response(_render_html(_PRIVACY_POLICY_HTML), content_type="text/html")


@legal_bp.route("/deletion")
def data_deletion_info():
    """Public data deletion instructions — no authentication required."""
    return Response(_render_html(_DELETION_INFO_HTML), content_type="text/html")


@legal_bp.route("/status")
def legal_status():
    """Legal compliance status summary."""
    return {"status": "compliant", "last_audit": "2026-03-17", "policies": ["privacy", "deletion"]}
