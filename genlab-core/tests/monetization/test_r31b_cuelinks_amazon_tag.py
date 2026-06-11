"""R-31 (b): the example affiliate catalog's Cuelinks URLs must
preserve the Amazon affiliate ``tag=`` parameter.

Background
----------
R-31's audit row had three sub-items:
  (a) EarnKaro/Impact/ShareASale/CJ adapters NotImplementedError
  (b) "Cuelinks wrapping strips the Amazon tag → 0 commission"  ← THIS
  (c) Matching is weak (1-keyword hit + evergreen fallback)

Sub-item (c) was shipped in the prior commit
(``fix(affiliate): tighten static catalog match to >=2 keyword hits``);
sub-item (a) is multi-day per-network integration work; sub-item (b)
ships here.

The bug
-------
The example catalog at ``genlab-core/config/affiliate_catalog.example.yaml``
ships 33 Cuelinks URLs of the shape:

    https://linksredirect.com/?cid=${CUELINKS_PUBLISHER_ID}&source=linkkit
        &url=https%3A%2F%2Fwww.amazon.in%2Fdp%2FB0CY5QW186

Decode the embedded URL → ``https://www.amazon.in/dp/B0CY5QW186``.
No ``tag=`` parameter. So:
  * Cuelinks records the click (commission_pct: 7.0)
  * Amazon Associates gets ZERO commission on the landing page —
    no tag → no attribution.

This was the audit's "0 commission" claim: Amazon Associates is
the higher-commission tier in many real categories; losing it on
every Cuelinks-wrapped click is the structural revenue leak the
audit flagged.

The fix
-------
Every Cuelinks URL whose embedded URL is an Amazon link must
carry ``?tag=${AMAZON_IN_AFFILIATE_TAG}`` inside the embedded URL.
This test pins that invariant — a future hand-edit of the example
catalog can't silently re-introduce the strip.

Live catalogs aren't tracked here (operator-specific), so this pin
covers the EXAMPLE catalog only. The shape of the bug is what
matters: copying the example as a starting point used to bake in
the strip.
"""

from __future__ import annotations

import re
import urllib.parse
from pathlib import Path

_GENLAB_ROOT = Path(__file__).resolve().parents[3]
_CATALOG = _GENLAB_ROOT / "genlab-core" / "config" / "affiliate_catalog.example.yaml"

_CUELINKS_URL = re.compile(r"https://linksredirect\.com/\?[^\s]+")


def _embedded_amazon_url(cuelinks_url: str) -> str | None:
    """Return the decoded embedded URL iff it's an Amazon link;
    None otherwise (the Cuelinks wrapper can legitimately wrap
    non-Amazon merchants too)."""
    parsed = urllib.parse.urlparse(cuelinks_url)
    qs = urllib.parse.parse_qs(parsed.query)
    embedded_encoded = qs.get("url", [""])[0]
    if not embedded_encoded:
        return None
    embedded = urllib.parse.unquote(embedded_encoded)
    return embedded if "amazon" in embedded else None


def test_r31b_example_catalog_has_cuelinks_entries() -> None:
    """Sanity: the example catalog actually contains Cuelinks URLs
    (so the next pin isn't a vacuous pass against an empty file)."""
    text = _CATALOG.read_text()
    cuelinks = _CUELINKS_URL.findall(text)
    assert len(cuelinks) >= 10, (
        f"Example catalog has only {len(cuelinks)} Cuelinks URLs — "
        "expected the historical ~33. Did the file move?"
    )


def test_r31b_every_cuelinks_amazon_url_carries_tag() -> None:
    """R-31 (b) regression pin: every Cuelinks URL whose embedded
    URL points at Amazon must include ``tag=...`` in the embedded
    URL. Without the tag, Amazon Associates earns zero commission
    on the click."""
    text = _CATALOG.read_text()
    cuelinks = _CUELINKS_URL.findall(text)

    offenders: list[str] = []
    for url in cuelinks:
        embedded = _embedded_amazon_url(url)
        if embedded is None:
            continue  # non-Amazon wrapper; not in scope
        if "tag=" not in embedded:
            offenders.append(embedded[:120])

    assert not offenders, (
        "R-31 (b) regression: example catalog has Cuelinks URLs "
        "wrapping Amazon links without a ``tag=`` parameter — "
        "Amazon Associates commission lost on every click. Fix by "
        "appending ``?tag=${AMAZON_IN_AFFILIATE_TAG}`` to each "
        f"embedded URL.\n\nOffenders ({len(offenders)}):\n  "
        + "\n  ".join(offenders[:5])
        + ("\n  ..." if len(offenders) > 5 else "")
    )


def test_r31b_tag_uses_env_placeholder_not_hardcoded() -> None:
    """The embedded ``tag=`` must reference the env-var placeholder
    (``${AMAZON_IN_AFFILIATE_TAG}``) — never a hard-coded value
    that would leak the operator's affiliate ID into the example
    file."""
    text = _CATALOG.read_text()
    cuelinks = _CUELINKS_URL.findall(text)

    bad_hardcoded: list[str] = []
    for url in cuelinks:
        embedded = _embedded_amazon_url(url)
        if embedded is None:
            continue
        m = re.search(r"[?&]tag=([^&]+)", embedded)
        if m and "${" not in m.group(1):
            bad_hardcoded.append(m.group(1))

    assert not bad_hardcoded, (
        "R-31 (b) regression: example catalog Cuelinks URLs carry "
        "hard-coded ``tag=`` values instead of the "
        "``${AMAZON_IN_AFFILIATE_TAG}`` placeholder. Hard-coded "
        "values leak the original operator's affiliate ID into the "
        f"example file. Offending values: {bad_hardcoded[:5]}"
    )
