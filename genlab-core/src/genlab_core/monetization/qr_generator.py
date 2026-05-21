"""QR code generation for affiliate link pages.

Generates a QR code PNG that points to the channel's link-in-bio page
with a ``ref=qr`` tracking parameter. QR codes are stored in ``.tmp/qr/``
and can be included in YouTube video descriptions.

Usage:
    from genlab_core.monetization.qr_generator import generate_qr_code

    path = generate_qr_code("clutchwire")
    # Returns: Path(".tmp/qr/clutchwire_qr.png")

Requires: ``qrcode[pil]`` (qrcode + Pillow)

If qrcode is not installed, ``generate_qr_code`` returns None with a warning
instead of crashing the pipeline.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent  # → GenLab/
_QR_DIR = _PROJECT_ROOT / ".tmp" / "qr"

# Channel → niche mapping for URL generation
_CHANNEL_SLUGS = {
    "ai_creators": "blackboxbrief",
    "gaming": "criticalrush",
    "sports": "clutchwire",
    "movies": "splicereel",
    "anime": "framedrift",
}


def _default_base_url() -> str:
    """Resolve the dashboard base URL from environment."""
    domain = os.environ.get("GENLAB_DOMAIN", "localhost")
    scheme = "http" if domain == "localhost" else "https"
    return f"{scheme}://{domain}"


def generate_qr_code(
    channel_slug: str,
    base_url: str | None = None,
    output_dir: Path | None = None,
    size: int = 10,
    border: int = 2,
) -> Path | None:
    """Generate a QR code PNG for a channel's link-in-bio page.

    Args:
        channel_slug: Channel name (e.g. "clutchwire", "criticalrush").
        base_url: Base URL of the dashboard server.
        output_dir: Directory to write the QR PNG to. Defaults to .tmp/qr/.
        size: QR code box size (pixels per module). Default 10.
        border: Border width in modules. Default 2.

    Returns:
        Path to the generated QR code PNG, or None if generation fails.
    """
    # Conditional imports: `qrcode[pil]` extra brings StyledPilImage. Without
    # the [pil] extra only base qrcode is available. The styled image is
    # optional polish — if missing, base qrcode still produces a valid QR.
    try:
        import qrcode
        from qrcode.image.styledpil import StyledPilImage  # noqa: F401 — availability probe
    except ImportError:
        try:
            import qrcode
        except ImportError:
            logger.warning(
                "[QRGenerator] qrcode package not installed — run: pip install qrcode[pil]"
            )
            return None

    if base_url is None:
        base_url = _default_base_url()

    out_dir = output_dir or _QR_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    # Build the link page URL with QR tracking ref
    url = f"{base_url.rstrip('/')}/links/{channel_slug}?ref=qr"

    try:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=size,
            border=border,
        )
        qr.add_data(url)
        qr.make(fit=True)

        # Generate with dark background for brand consistency
        img = qr.make_image(fill_color="white", back_color="#09090b")

        output_path = out_dir / f"{channel_slug}_qr.png"
        img.save(str(output_path))

        logger.info("[QRGenerator] QR code saved: %s → %s", url, output_path)
        return output_path

    except Exception as e:
        logger.warning("[QRGenerator] QR generation failed: %s", e)
        return None


def generate_all_qr_codes(
    base_url: str | None = None,
    output_dir: Path | None = None,
) -> dict[str, Path | None]:
    """Generate QR codes for all 5 channels.

    Returns a dict mapping channel_slug to QR code path (or None on failure).
    """
    if base_url is None:
        base_url = _default_base_url()

    results: dict[str, Path | None] = {}
    for _niche_id, channel_slug in _CHANNEL_SLUGS.items():
        results[channel_slug] = generate_qr_code(
            channel_slug,
            base_url=base_url,
            output_dir=output_dir,
        )
    return results


def get_youtube_description_snippet(channel_slug: str) -> str:
    """Return a YouTube description snippet referencing the QR code.

    This text can be appended to YouTube video descriptions to drive
    traffic to the link-in-bio page.
    """
    return (
        f"\n---\n"
        f"Scan the QR code or visit our link page for product picks & deals.\n"
        f"All links and recommendations: {_default_base_url()}/links/{channel_slug}?ref=yt_desc"
    )
