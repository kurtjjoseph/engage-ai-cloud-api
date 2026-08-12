#!/usr/bin/env python3
"""End-to-end smoke test of the Postiz relay against a REAL Postiz instance.

tests/test_postiz.py proves Engage AI sends the right request. Only this proves
a real Postiz accepts it - the shapes of `/integrations`, `/upload` and `/posts`
are the parts a mock cannot verify, and they are the parts that move between
Postiz versions.

    export POSTIZ_API_KEY=...                          # Postiz > Settings > Public API
    export POSTIZ_BASE_URL=https://postiz.example/api  # omit for hosted Postiz
    .venv/bin/python scripts/postiz_smoke.py                 # read-only: list accounts
    .venv/bin/python scripts/postiz_smoke.py --publish x     # ACTUALLY POSTS to X

Read-only by default. `--publish <channel>` sends a real post to a real
audience, so it is opt-in per run and prints what it is about to do first.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.channels.postiz import (  # noqa: E402
    PostizClient,
    PostizError,
    default_settings,
    missing_required_settings,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--publish",
        metavar="CHANNEL",
        help="Send a real post to this Engage AI channel (facebook, twitter_x, ...).",
    )
    parser.add_argument(
        "--schedule-in",
        type=int,
        default=0,
        metavar="MINUTES",
        help="Schedule the post this many minutes out instead of sending it now.",
    )
    parser.add_argument(
        "--text",
        default="Engage AI relay smoke test - please ignore.",
        help="What to post.",
    )
    args = parser.parse_args()

    api_key = os.environ.get("POSTIZ_API_KEY")
    if not api_key:
        print("POSTIZ_API_KEY is not set.", file=sys.stderr)
        return 2

    client = PostizClient(os.environ.get("POSTIZ_BASE_URL"), api_key)
    print(f"Postiz public API: {client.base_url}\n")

    try:
        integrations = client.integrations()
    except PostizError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1

    if not integrations:
        print("No accounts are connected in this Postiz workspace.")
        return 1

    print(f"{len(integrations)} account(s) connected:")
    for integration in integrations:
        mapped = integration.channel or "-- no Engage AI channel, skipped --"
        flags = " [disabled]" if integration.disabled else ""
        print(f"  {integration.id:<26} {integration.identifier:<20} -> {mapped}{flags}")
        print(f"    {integration.name}")

    if not args.publish:
        print("\nRead-only run. Pass --publish <channel> to send a real post.")
        return 0

    target = next(
        (i for i in integrations if i.channel == args.publish and not i.disabled), None
    )
    if target is None:
        print(f"\nNo usable account for channel '{args.publish}'.", file=sys.stderr)
        return 1

    when = (
        datetime.now(timezone.utc) + timedelta(minutes=args.schedule_in)
        if args.schedule_in
        else None
    )
    settings = default_settings(target.channel, target.identifier, "Smoke test")
    missing = missing_required_settings(target.channel, settings)
    if missing:
        print(
            f"\n{target.channel} needs {', '.join(missing)} before it can be posted to.",
            file=sys.stderr,
        )
        return 1

    print(
        f"\nAbout to post to {target.name} ({target.identifier}) "
        f"{'at ' + when.isoformat() if when else 'NOW'}:\n  {args.text}\n"
    )
    if input("Type 'yes' to send: ").strip().lower() != "yes":
        print("Aborted. Nothing was posted.")
        return 0

    try:
        created = client.create_post(
            integration_id=target.id,
            content=args.text,
            settings=settings,
            when=when,
        )
    except PostizError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1

    print(f"Accepted by Postiz. post id: {created['id']}")
    print(f"release URL: {created['release_url'] or '(not released yet - queued)'}")
    print(
        "\nQueued posts get their permalink from "
        "POST /organizations/{id}/postiz/reconcile once Postiz releases them."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
