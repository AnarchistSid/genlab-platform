"""Run Twitter (X) OAuth 1.0a 3-legged flow for a niche account.

Why this exists: per-niche access tokens (for sports/movies/anime/gaming
accounts) cannot be generated via the Developer Portal one-click button —
that only works for the app owner's account. Other accounts must complete
the 3-legged OAuth dance.

Usage::

    uv run --package genlab-core python -m genlab_core.tools.provision_twitter \\
        --niche sports

Reads the global X_API_KEY / X_API_SECRET (the app's consumer keys), prints
an authorization URL, asks for the PIN that Twitter shows after authorizing,
and prints the .env lines you need to paste in.

The browser MUST be logged in to the niche's Twitter account before opening
the URL — that's how Twitter knows which account to issue tokens for.
"""

from __future__ import annotations

import argparse
import os
import sys

from genlab_core.publishing.niche_credentials import NICHE_CREDENTIAL_PREFIXES


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--niche",
        required=True,
        choices=sorted(NICHE_CREDENTIAL_PREFIXES),
        help="Niche to provision tokens for (must be logged into that niche's Twitter account)",
    )
    args = parser.parse_args()

    consumer_key = os.environ.get("X_API_KEY", "").strip()
    consumer_secret = os.environ.get("X_API_SECRET", "").strip()
    if not consumer_key or not consumer_secret:
        print(
            "ERROR: X_API_KEY and X_API_SECRET must be set in the environment "
            "(these are the app's consumer keys, shared across all niches).",
            file=sys.stderr,
        )
        return 2

    try:
        import tweepy
    except ImportError:
        print(
            "ERROR: tweepy not installed. Run via `uv run --package genlab-core ...`.",
            file=sys.stderr,
        )
        return 2

    auth = tweepy.OAuth1UserHandler(consumer_key, consumer_secret, callback="oob")
    try:
        url = auth.get_authorization_url()
    except tweepy.TweepyException as exc:
        print(f"ERROR: failed to get authorization URL: {exc}", file=sys.stderr)
        return 1

    prefix = NICHE_CREDENTIAL_PREFIXES[args.niche]
    print()
    print(f"Niche:    {args.niche}")
    print(f"Prefix:   {prefix}")
    print()
    print("STEP 1 — log in to the niche's Twitter account in your browser FIRST.")
    print("STEP 2 — open this URL and click 'Authorize app':")
    print()
    print(f"  {url}")
    print()
    pin = input("STEP 3 — paste the PIN Twitter shows you: ").strip()
    if not pin:
        print("ERROR: no PIN entered", file=sys.stderr)
        return 1

    try:
        access_token, access_secret = auth.get_access_token(pin)
    except tweepy.TweepyException as exc:
        print(f"ERROR: failed to exchange PIN: {exc}", file=sys.stderr)
        return 1

    print()
    print("Add these lines to /opt/genlab/.env on Hetzner (and your local .env):")
    print()
    print(f"{prefix}_X_ACCESS_TOKEN={access_token}")
    print(f"{prefix}_X_ACCESS_SECRET={access_secret}")
    print()
    print("Then verify with:")
    print(
        '  uv run --package genlab-core python -c "'
        "from genlab_core.publishing.niche_credentials import resolve_twitter_credentials; "
        f"print(all(resolve_twitter_credentials('{args.niche}').values()))\""
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
