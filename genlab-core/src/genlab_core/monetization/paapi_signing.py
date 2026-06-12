"""AWS Signature Version 4 signing for Amazon Product Advertising API.

R-31 (a) PA-API half: closes the deepest stub in the affiliate
stack — the ``_signed_request`` body in ``paapi_client.py`` that
raised ``NotImplementedError`` for the lack of an in-tree SigV4
implementation. With this module shipped + wired, only setting
``PAAPI_ACCESS_KEY`` + ``PAAPI_SECRET_KEY`` stands between the
pipeline and live PA-API queries.

Why an in-tree implementation
-----------------------------

`boto3` / `botocore` would solve this in two lines but pull in a
~50 MB transitive dep that's already not in our lockfile. The
algorithm is a deterministic 4-stage HMAC-SHA256 chain on top of
stdlib only — about 150 LOC including comments. Same shape can
be reused for any future AWS-flavoured API (Bedrock, S3, SES)
without the install footprint.

What this module implements
---------------------------

* ``sign_request_v4`` — full public API. Takes the raw HTTP
  request bits + AWS credentials, returns the headers dict ready
  to attach to a ``requests.post``.
* ``_canonical_request`` — stage 1 of the SigV4 spec.
* ``_string_to_sign`` — stage 2.
* ``_derive_signing_key`` — stage 3 (the 4-HMAC chain).
* ``_compute_signature`` — stage 4 (the final HMAC).

The implementation matches AWS's official test vectors at
https://docs.aws.amazon.com/general/latest/gr/sigv4_testsuite.html
— see ``tests/test_paapi_signing.py`` for the pinning.

PA-API-specific notes
---------------------

* PA-API service name: ``ProductAdvertisingAPI``
* PA-API region mapping: US → ``us-east-1``; IN → ``eu-west-1``;
  UK → ``eu-west-1``; JP → ``us-west-2`` (per Amazon's docs).
* PA-API requires these additional headers BEFORE signing:
  ``Content-Type: application/json; charset=UTF-8``,
  ``X-Amz-Target: com.amazon.paapi5.v1.ProductAdvertisingAPIv1.<Op>``,
  ``Content-Encoding: amz-1.0``.
* The payload hash is the SHA-256 of the JSON body; for SearchItems
  / GetItems the body is the full operation payload (no AWS-style
  empty body shenanigans).
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from urllib.parse import quote

# Canonical AWS algorithm name in signing-key derivation + auth header.
_ALGORITHM = "AWS4-HMAC-SHA256"

# PA-API region map. Sourced from Amazon's "PA-API 5.0 endpoints" page.
# Keys match the values the PaapiClient already uses (``us``/``in``/``uk``);
# JP added because the host map in paapi_client.py is trivially extensible
# and the SigV4 layer should match what the client knows.
PAAPI_REGION = {
    "us": "us-east-1",
    "in": "eu-west-1",
    "uk": "eu-west-1",
    "jp": "us-west-2",
}

# PA-API service identifier — fixed string, NOT derived from anywhere.
PAAPI_SERVICE = "ProductAdvertisingAPI"


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    """One-shot HMAC-SHA256 helper. Returns raw digest bytes."""
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _sha256_hex(payload: str) -> str:
    """SHA-256 → lowercase hex string (the form SigV4 expects)."""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonical_uri(path: str) -> str:
    """SigV4 canonical URI: URL-encode path segments except ``/``.

    PA-API paths (``/paapi5/searchitems``, etc.) are ASCII-clean so
    this rarely changes anything, but the spec requires it and our
    test vectors exercise non-ASCII paths.
    """
    if not path:
        return "/"
    # ``safe="/"`` keeps slashes intact; everything else URL-encoded.
    return quote(path, safe="/~")


def _canonical_query_string(query_params: dict[str, str] | None) -> str:
    """SigV4 canonical query string: sorted, URL-encoded, ``key=value``
    joined with ``&``. PA-API requests are POST with JSON bodies so this
    is usually empty, but the SigV4 spec requires the field."""
    if not query_params:
        return ""
    # Sort by key (then value, per the spec) and URL-encode both sides
    # without preserving any characters as ``safe``.
    pairs = sorted(query_params.items())
    return "&".join(f"{quote(k, safe='~')}={quote(v, safe='~')}" for k, v in pairs)


def _canonical_headers(headers: dict[str, str]) -> tuple[str, str]:
    """Return (canonical_headers_block, signed_headers_list).

    Per spec:
      * Header names lowercased
      * Sorted alphabetically by name
      * Values trimmed of leading/trailing whitespace + sequential
        whitespace collapsed (we do trim only — PA-API never sends
        multi-space values).
      * Block is ``name:value\\n`` for each, with a trailing newline.
      * Signed-headers list is ``;``-joined lowercase names.
    """
    lowered = sorted((name.lower(), value.strip()) for name, value in headers.items())
    block = "".join(f"{name}:{value}\n" for name, value in lowered)
    signed_headers = ";".join(name for name, _ in lowered)
    return block, signed_headers


def _canonical_request(
    method: str,
    path: str,
    query_params: dict[str, str] | None,
    headers: dict[str, str],
    payload: str,
) -> tuple[str, str]:
    """Stage 1: build the canonical request string + signed-headers list.

    Returns (canonical_request, signed_headers). The canonical
    request is the input to stage 2; the signed-headers list is
    needed in the final Authorization header.
    """
    headers_block, signed_headers = _canonical_headers(headers)
    payload_hash = _sha256_hex(payload)
    canonical = (
        f"{method.upper()}\n"
        f"{_canonical_uri(path)}\n"
        f"{_canonical_query_string(query_params)}\n"
        f"{headers_block}\n"
        f"{signed_headers}\n"
        f"{payload_hash}"
    )
    return canonical, signed_headers


def _string_to_sign(
    timestamp: str,
    credential_scope: str,
    canonical_request: str,
) -> str:
    """Stage 2: the string-to-sign. Fixed 4-line format per spec."""
    return f"{_ALGORITHM}\n{timestamp}\n{credential_scope}\n{_sha256_hex(canonical_request)}"


def _derive_signing_key(
    secret_key: str,
    date_stamp: str,
    region: str,
    service: str,
) -> bytes:
    """Stage 3: derive the per-request signing key.

    Four-HMAC chain — the SigV4 spec mandates this exact sequence
    so a leaked signing key reveals nothing about the secret key
    beyond the chain it was derived through (date/region/service-
    bound). Returns the raw 32-byte HMAC-SHA256 key.
    """
    k_date = _hmac_sha256(f"AWS4{secret_key}".encode(), date_stamp)
    k_region = _hmac_sha256(k_date, region)
    k_service = _hmac_sha256(k_region, service)
    k_signing = _hmac_sha256(k_service, "aws4_request")
    return k_signing


def _compute_signature(signing_key: bytes, string_to_sign: str) -> str:
    """Stage 4: the final HMAC. Returns lowercase hex digest."""
    return hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()


def sign_request_v4(
    *,
    method: str,
    host: str,
    path: str,
    payload: str,
    access_key: str,
    secret_key: str,
    region: str,
    service: str = PAAPI_SERVICE,
    extra_headers: dict[str, str] | None = None,
    query_params: dict[str, str] | None = None,
    now: datetime | None = None,
) -> dict[str, str]:
    """Return the headers dict for a SigV4-signed HTTPS request.

    The returned dict carries everything ``requests`` (or any HTTP
    client) needs to make the call: ``Host``, ``X-Amz-Date``, all
    caller-supplied ``extra_headers``, plus the computed
    ``Authorization`` header.

    Args:
        method: HTTP method (``"POST"`` for PA-API ops).
        host: Hostname (e.g. ``webservices.amazon.in``).
        path: URL path (e.g. ``/paapi5/searchitems``).
        payload: Raw request body (JSON string for PA-API).
        access_key: AWS access key ID.
        secret_key: AWS secret access key.
        region: Region the service is in (e.g. ``us-east-1``).
        service: AWS service identifier (defaults to PA-API's).
        extra_headers: Additional headers to sign (e.g.
            ``Content-Type``, ``X-Amz-Target``). Will be merged
            with the base set the signer always adds.
        query_params: Query-string params if any. PA-API uses
            none for POST ops, but the SigV4 spec requires the
            field — empty dict / None both render as empty.
        now: Override the timestamp for testing. Defaults to
            ``datetime.now(UTC)``.

    Returns:
        A dict of header name → value suitable for
        ``requests.post(..., headers=...)``. The caller is
        responsible for the URL construction and the body.

    Raises:
        ValueError: If ``access_key`` or ``secret_key`` is empty
            — early surfacing of misconfiguration is preferable
            to a 403 from AWS later.
    """
    if not access_key or not secret_key:
        raise ValueError("sign_request_v4 requires non-empty access_key and secret_key")

    now = now or datetime.now(UTC)
    timestamp = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")

    # Base headers the SigV4 spec + AWS service requires. Caller-
    # supplied ``extra_headers`` are merged on top (and signed).
    headers = {
        "Host": host,
        "X-Amz-Date": timestamp,
    }
    if extra_headers:
        headers.update(extra_headers)

    canonical, signed_headers = _canonical_request(
        method=method,
        path=path,
        query_params=query_params,
        headers=headers,
        payload=payload,
    )

    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    sts = _string_to_sign(timestamp, credential_scope, canonical)

    signing_key = _derive_signing_key(secret_key, date_stamp, region, service)
    signature = _compute_signature(signing_key, sts)

    authorization = (
        f"{_ALGORITHM} "
        f"Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )

    return {**headers, "Authorization": authorization}
