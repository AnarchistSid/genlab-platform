"""R-31 (a) — tests for ``paapi_signing.sign_request_v4``.

The pins fall into three categories:

1. **AWS official test vectors** — small selection from the
   ``aws4_testsuite`` (the same vectors every AWS SDK ships
   against). Documented at
   https://docs.aws.amazon.com/general/latest/gr/sigv4_testsuite.html
   . If any of these fail, the algorithm is wrong, not just
   wired oddly — they're a regression net on the stages 1–4
   chain.

2. **PA-API-specific shape pins** — verify the public
   ``sign_request_v4`` returns the headers PA-API expects
   (``Host`` / ``X-Amz-Date`` / ``Authorization``) and that the
   ``Authorization`` header carries the canonical SigV4 syntax
   (``AWS4-HMAC-SHA256 Credential=... SignedHeaders=...
   Signature=...``).

3. **Misuse guards** — non-empty credential requirement, etc.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from genlab_core.monetization.paapi_signing import (
    PAAPI_REGION,
    PAAPI_SERVICE,
    _canonical_headers,
    _canonical_query_string,
    _canonical_request,
    _canonical_uri,
    _compute_signature,
    _derive_signing_key,
    _string_to_sign,
    sign_request_v4,
)

# AWS Sig v4 official test-suite credentials. These are public
# (published in AWS's docs); not a leak.
_TEST_ACCESS_KEY = "AKIDEXAMPLE"
_TEST_SECRET_KEY = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
_TEST_REGION = "us-east-1"
_TEST_SERVICE = "service"
_TEST_TIMESTAMP = datetime(2015, 8, 30, 12, 36, 0, tzinfo=UTC)


# ── 1. AWS official test vectors ─────────────────────────────────


def test_get_vanilla_canonical_request_matches_aws_published_vector() -> None:
    """``get-vanilla`` test case from AWS's Sig v4 test suite.

    Canonical request published by AWS for this case is exactly
    what the spec stages 1–2 must produce. Pins the URI / query /
    headers / payload-hash composition end-to-end.
    """
    headers = {"Host": "example.amazonaws.com", "X-Amz-Date": "20150830T123600Z"}
    canonical, signed_headers = _canonical_request(
        method="GET",
        path="/",
        query_params=None,
        headers=headers,
        payload="",
    )

    expected = (
        "GET\n"
        "/\n"
        "\n"
        "host:example.amazonaws.com\n"
        "x-amz-date:20150830T123600Z\n"
        "\n"
        "host;x-amz-date\n"
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    assert canonical == expected
    assert signed_headers == "host;x-amz-date"


def test_get_vanilla_string_to_sign_matches_aws_published_vector() -> None:
    """``get-vanilla`` string-to-sign per AWS's docs.

    Format is fixed at 4 lines:
      ``AWS4-HMAC-SHA256\\n<timestamp>\\n<scope>\\n<sha256(canonical)>``
    The canonical-request hash for ``get-vanilla`` is the AWS-
    published value below; if the canonical-request stage drifts,
    THIS line breaks before the final signature test does — a
    nicer diagnosis surface than "signature mismatch."
    """
    headers = {"Host": "example.amazonaws.com", "X-Amz-Date": "20150830T123600Z"}
    canonical, _ = _canonical_request(
        method="GET",
        path="/",
        query_params=None,
        headers=headers,
        payload="",
    )
    sts = _string_to_sign(
        timestamp="20150830T123600Z",
        credential_scope="20150830/us-east-1/service/aws4_request",
        canonical_request=canonical,
    )
    # AWS's published canonical-request SHA-256 for get-vanilla
    expected_canonical_hash = "bb579772317eb040ac9ed261061d46c1f17a8133879d6129b6e1c25292927e63"
    expected = (
        "AWS4-HMAC-SHA256\n"
        "20150830T123600Z\n"
        "20150830/us-east-1/service/aws4_request\n"
        f"{expected_canonical_hash}"
    )
    assert sts == expected


def test_get_vanilla_signature_matches_aws_published_vector() -> None:
    """The full chain end-to-end against AWS's published signature
    for ``get-vanilla``. If this passes, the algorithm exactly
    matches what every AWS SDK does — boto, the JVM SDK, all of
    them produce this same hex string on this input."""
    headers = {"Host": "example.amazonaws.com", "X-Amz-Date": "20150830T123600Z"}
    canonical, _ = _canonical_request(
        method="GET",
        path="/",
        query_params=None,
        headers=headers,
        payload="",
    )
    sts = _string_to_sign(
        timestamp="20150830T123600Z",
        credential_scope="20150830/us-east-1/service/aws4_request",
        canonical_request=canonical,
    )
    signing_key = _derive_signing_key(
        secret_key=_TEST_SECRET_KEY,
        date_stamp="20150830",
        region=_TEST_REGION,
        service=_TEST_SERVICE,
    )
    signature = _compute_signature(signing_key, sts)
    # Published expected signature from AWS for the get-vanilla case
    assert signature == "5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"


def test_derive_signing_key_chain_correctness() -> None:
    """The 4-HMAC chain must produce a 32-byte key (HMAC-SHA256
    output width) that round-trips through the final ``HMAC(key,
    string_to_sign)`` step to AWS's published get-vanilla
    signature. The end-to-end test above already proves that;
    this test independently pins ``_derive_signing_key`` so a
    regression that lands a wrong key but a coincidentally-right
    signature (vanishingly unlikely but possible if some other
    bug compensates) still surfaces.

    AWS doesn't publish the intermediate key hex for the
    aws4_testsuite (only canonical requests + final signatures).
    We instead pin: the chain shape (4 HMAC ops produce 32 bytes)
    + the chain's two key invariants (region-bound; service-
    bound). The end-to-end signature match in the test above
    is the actual algorithm-correctness gate.
    """
    signing_key = _derive_signing_key(
        secret_key=_TEST_SECRET_KEY,
        date_stamp="20150830",
        region=_TEST_REGION,
        service=_TEST_SERVICE,
    )
    # HMAC-SHA256 output width — 32 bytes
    assert len(signing_key) == 32

    # Changing the region must produce a different key (region-
    # bound credential scope is the *point* of the chain).
    other_region_key = _derive_signing_key(
        secret_key=_TEST_SECRET_KEY,
        date_stamp="20150830",
        region="eu-west-1",
        service=_TEST_SERVICE,
    )
    assert signing_key != other_region_key

    # Same for the service.
    other_service_key = _derive_signing_key(
        secret_key=_TEST_SECRET_KEY,
        date_stamp="20150830",
        region=_TEST_REGION,
        service="iam",
    )
    assert signing_key != other_service_key


# ── Stage helpers — pinning the smaller building blocks ──────────


def test_canonical_uri_keeps_slashes_url_encodes_rest() -> None:
    """``/paapi5/searchitems`` is ASCII-safe — passes through. A
    path with reserved characters must be URL-encoded per spec."""
    assert _canonical_uri("/paapi5/searchitems") == "/paapi5/searchitems"
    assert _canonical_uri("") == "/"
    # Non-ASCII / reserved — slashes preserved, space + ñ encoded
    assert _canonical_uri("/path with/ñ") == "/path%20with/%C3%B1"


def test_canonical_query_string_sorted_and_encoded() -> None:
    """Pairs sorted by key, both sides URL-encoded, ``&``-joined."""
    assert _canonical_query_string({"b": "2", "a": "1"}) == "a=1&b=2"
    assert _canonical_query_string({}) == ""
    assert _canonical_query_string(None) == ""
    # URL-encode special chars on both sides
    assert _canonical_query_string({"key": "a b", "x y": "z"}) == "key=a%20b&x%20y=z"


def test_canonical_headers_lowercased_sorted_signed_header_list() -> None:
    """Names lowercased, sorted; block has trailing ``\\n`` on last
    header; signed-headers list is ``;``-joined."""
    block, signed = _canonical_headers({"Host": "example.com", "X-Amz-Date": "20150830T123600Z"})
    assert block == "host:example.com\nx-amz-date:20150830T123600Z\n"
    assert signed == "host;x-amz-date"


def test_canonical_headers_trims_whitespace_in_values() -> None:
    """Per spec, leading/trailing whitespace in values is stripped
    before signing. Pin so a future "let's normalize harder" edit
    doesn't break compatibility with AWS."""
    block, _ = _canonical_headers({"Host": "  example.com  "})
    assert block == "host:example.com\n"


# ── 2. PA-API-specific public-API shape ──────────────────────────


def test_sign_request_v4_returns_required_paapi_headers() -> None:
    """``sign_request_v4`` must return ``Host`` / ``X-Amz-Date``
    / ``Authorization`` at minimum, plus all caller-supplied
    extras. Pin so a future "let's simplify the return" edit
    can't break the PaapiClient HTTP path."""
    payload = json.dumps(
        {
            "Keywords": "PS5 console",
            "SearchIndex": "VideoGames",
            "PartnerTag": "your-tag-20",
            "PartnerType": "Associates",
        }
    )
    extras = {
        "Content-Type": "application/json; charset=UTF-8",
        "X-Amz-Target": "com.amazon.paapi5.v1.ProductAdvertisingAPIv1.SearchItems",
        "Content-Encoding": "amz-1.0",
    }
    headers = sign_request_v4(
        method="POST",
        host="webservices.amazon.com",
        path="/paapi5/searchitems",
        payload=payload,
        access_key=_TEST_ACCESS_KEY,
        secret_key=_TEST_SECRET_KEY,
        region="us-east-1",
        service=PAAPI_SERVICE,
        extra_headers=extras,
        now=_TEST_TIMESTAMP,
    )

    # Required fields
    assert headers["Host"] == "webservices.amazon.com"
    assert headers["X-Amz-Date"] == "20150830T123600Z"
    assert headers["Content-Type"] == "application/json; charset=UTF-8"
    assert headers["X-Amz-Target"] == "com.amazon.paapi5.v1.ProductAdvertisingAPIv1.SearchItems"
    assert headers["Content-Encoding"] == "amz-1.0"
    # Authorization header syntax
    auth = headers["Authorization"]
    assert auth.startswith("AWS4-HMAC-SHA256 ")
    assert "Credential=AKIDEXAMPLE/20150830/us-east-1/ProductAdvertisingAPI/aws4_request" in auth
    assert "SignedHeaders=" in auth
    assert "Signature=" in auth
    # Signature is 64 hex chars
    sig = auth.split("Signature=", 1)[1]
    assert len(sig) == 64
    assert all(c in "0123456789abcdef" for c in sig)


def test_sign_request_v4_authorization_signed_headers_alphabetical() -> None:
    """The signed-headers list inside the Authorization header
    must list each signed header name in alphabetical order,
    lowercased, ``;``-joined. PA-API will reject the request with
    a generic 403 if the order is wrong — pinning catches that."""
    extras = {
        "Content-Type": "application/json; charset=UTF-8",
        "X-Amz-Target": "com.amazon.paapi5.v1.ProductAdvertisingAPIv1.SearchItems",
        "Content-Encoding": "amz-1.0",
    }
    headers = sign_request_v4(
        method="POST",
        host="webservices.amazon.com",
        path="/paapi5/searchitems",
        payload="{}",
        access_key=_TEST_ACCESS_KEY,
        secret_key=_TEST_SECRET_KEY,
        region="us-east-1",
        extra_headers=extras,
        now=_TEST_TIMESTAMP,
    )
    auth = headers["Authorization"]
    signed = auth.split("SignedHeaders=", 1)[1].split(",", 1)[0]
    # Alphabetical, lowercased
    assert signed == "content-encoding;content-type;host;x-amz-date;x-amz-target"


def test_sign_request_v4_deterministic_for_fixed_timestamp() -> None:
    """Same inputs + same ``now`` → identical signature. Pin so
    no future "let's add nonces" edit silently breaks
    reproducibility (which is what makes the AWS test-vector
    suite work)."""
    args = dict(
        method="POST",
        host="webservices.amazon.in",
        path="/paapi5/getitems",
        payload='{"ItemIds":["B000EXAMPLE"]}',
        access_key=_TEST_ACCESS_KEY,
        secret_key=_TEST_SECRET_KEY,
        region="eu-west-1",
        now=_TEST_TIMESTAMP,
    )
    a = sign_request_v4(**args)  # type: ignore[arg-type]
    b = sign_request_v4(**args)  # type: ignore[arg-type]
    assert a["Authorization"] == b["Authorization"]


def test_sign_request_v4_signature_changes_with_payload() -> None:
    """Different payload → different signature. Pin the obvious
    so a refactor that accidentally hashes the empty string for
    every request is caught immediately."""
    base = dict(
        method="POST",
        host="webservices.amazon.com",
        path="/paapi5/searchitems",
        access_key=_TEST_ACCESS_KEY,
        secret_key=_TEST_SECRET_KEY,
        region="us-east-1",
        now=_TEST_TIMESTAMP,
    )
    sig_a = sign_request_v4(**base, payload='{"Keywords":"PS5"}')["Authorization"]
    sig_b = sign_request_v4(**base, payload='{"Keywords":"Xbox"}')["Authorization"]
    assert sig_a != sig_b


def test_sign_request_v4_signature_changes_with_region() -> None:
    """Different region → different signing key → different
    signature. Pin so the region-bound credential scope can't be
    accidentally dropped."""
    base = dict(
        method="POST",
        host="webservices.amazon.com",
        path="/paapi5/searchitems",
        payload="{}",
        access_key=_TEST_ACCESS_KEY,
        secret_key=_TEST_SECRET_KEY,
        now=_TEST_TIMESTAMP,
    )
    us = sign_request_v4(**base, region="us-east-1")["Authorization"]
    eu = sign_request_v4(**base, region="eu-west-1")["Authorization"]
    assert us != eu
    assert "us-east-1" in us
    assert "eu-west-1" in eu


def test_sign_request_v4_includes_query_params_in_signature() -> None:
    """When ``query_params`` is supplied, it MUST flow into the
    canonical request — otherwise a tampering attack could mutate
    the query without invalidating the signature. PA-API's POST
    ops don't use query params, but the implementation must
    handle them per the SigV4 spec."""
    base = dict(
        method="POST",
        host="webservices.amazon.com",
        path="/paapi5/searchitems",
        payload="{}",
        access_key=_TEST_ACCESS_KEY,
        secret_key=_TEST_SECRET_KEY,
        region="us-east-1",
        now=_TEST_TIMESTAMP,
    )
    a = sign_request_v4(**base, query_params=None)["Authorization"]
    b = sign_request_v4(**base, query_params={"foo": "bar"})["Authorization"]
    assert a != b


# ── 3. Misuse guards ─────────────────────────────────────────────


def test_sign_request_v4_raises_on_empty_access_key() -> None:
    """Misconfiguration must surface at signing time, not as a
    403 from AWS minutes later in a pipeline run."""
    with pytest.raises(ValueError, match="access_key"):
        sign_request_v4(
            method="POST",
            host="x",
            path="/y",
            payload="{}",
            access_key="",
            secret_key="secret",
            region="us-east-1",
        )


def test_sign_request_v4_raises_on_empty_secret_key() -> None:
    """Same for an empty secret_key."""
    with pytest.raises(ValueError, match="secret_key"):
        sign_request_v4(
            method="POST",
            host="x",
            path="/y",
            payload="{}",
            access_key="AKIDEXAMPLE",
            secret_key="",
            region="us-east-1",
        )


def test_paapi_region_map_covers_all_paapi_client_hosts() -> None:
    """The ``PAAPI_REGION`` map must cover every host key the
    ``PaapiClient`` knows about so ``_signed_request`` can always
    look up the right region. Pin so adding a host to
    paapi_client without adding the region here surfaces as a
    test failure rather than a runtime KeyError."""
    from genlab_core.monetization.paapi_client import _HOSTS

    for host_key in _HOSTS:
        assert host_key in PAAPI_REGION, (
            f"PaapiClient host key '{host_key}' missing from PAAPI_REGION"
        )
