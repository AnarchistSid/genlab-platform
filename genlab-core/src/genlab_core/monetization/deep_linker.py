"""Mobile deep link generation for affiliate URLs.

Detects mobile devices from User-Agent and generates platform-specific
deep links that open products in native apps (Amazon).
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass

_MOBILE_PATTERNS = [re.compile(p, re.I) for p in [r"iPhone|iPad|iPod", r"Android", r"Mobile"]]
_IOS_PATTERN = re.compile(r"iPhone|iPad|iPod", re.I)
_ANDROID_PATTERN = re.compile(r"Android", re.I)
_AMAZON_ASIN_PATTERN = re.compile(r"/dp/([A-Z0-9]{10})")
_AMAZON_TAG_PATTERN = re.compile(r"tag=([a-zA-Z0-9-]+)")


@dataclass
class DeepLink:
    app_url: str
    web_url: str
    is_mobile: bool
    is_ios: bool
    is_android: bool


def is_mobile(user_agent: str) -> bool:
    """Detect if the User-Agent indicates a mobile device."""
    return any(p.search(user_agent) for p in _MOBILE_PATTERNS)


def generate_deep_link(affiliate_url: str, user_agent: str) -> DeepLink | None:
    """Generate an app deep link for Amazon URLs on mobile devices.

    Returns None if:
    - Not a mobile device
    - Not an Amazon URL
    - No ASIN extractable from URL
    """
    if not is_mobile(user_agent):
        return None

    # Only support Amazon deep links for now
    asin_match = _AMAZON_ASIN_PATTERN.search(affiliate_url)
    if not asin_match:
        return None

    asin = asin_match.group(1)
    tag_match = _AMAZON_TAG_PATTERN.search(affiliate_url)
    tag = tag_match.group(1) if tag_match else ""

    # Determine the Amazon domain from URL
    is_india = "amazon.in" in affiliate_url
    domain = "amazon.in" if is_india else "amazon.com"

    web_url = affiliate_url
    ua = user_agent
    is_ios_device = bool(_IOS_PATTERN.search(ua))
    is_android_device = bool(_ANDROID_PATTERN.search(ua))

    tag_param = f"?tag={tag}" if tag else ""

    if is_android_device:
        # Android Intent URL — tries app first, falls back to web
        app_url = (
            f"intent://www.{domain}/dp/{asin}{tag_param}"
            f"#Intent;scheme=https;"
            f"package=com.amazon.mShop.android.shopping;"
            f"S.browser_fallback_url={web_url};end"
        )
    elif is_ios_device:
        # iOS Universal Link
        app_url = f"amzn://www.{domain}/dp/{asin}{tag_param}"
    else:
        # Generic mobile — use web URL
        app_url = web_url

    return DeepLink(
        app_url=app_url,
        web_url=web_url,
        is_mobile=True,
        is_ios=is_ios_device,
        is_android=is_android_device,
    )


def render_deep_link_page(deep_link: DeepLink, product_name: str = "") -> str:
    """Render an HTML redirect page that tries the app first, falls back to web."""
    safe_name = html.escape(product_name or "product")
    safe_web = html.escape(deep_link.web_url)
    # Use json.dumps() for proper JS string context escaping (prevents XSS)
    js_app = json.dumps(deep_link.app_url)
    js_web = json.dumps(deep_link.web_url)
    return f"""<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="2;url={safe_web}">
<title>Opening {safe_name}...</title>
<style>
  body {{ background: #09090b; color: #fafafa; font-family: -apple-system, sans-serif;
         display: flex; align-items: center; justify-content: center; min-height: 100vh;
         text-align: center; padding: 20px; }}
  .container {{ max-width: 320px; }}
  p {{ margin: 12px 0; font-size: 14px; color: #a1a1aa; }}
  a {{ color: #3b82f6; text-decoration: none; }}
  .spinner {{ width: 32px; height: 32px; border: 3px solid #27272a; border-top: 3px solid #3b82f6;
              border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto 16px; }}
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
</style>
<script>
  window.location = {js_app};
  setTimeout(function() {{ window.location = {js_web}; }}, 1500);
</script>
</head>
<body>
<div class="container">
  <div class="spinner"></div>
  <p>Opening in app...</p>
  <p><a href="{safe_web}">Click here</a> if not redirected</p>
</div>
</body></html>"""
