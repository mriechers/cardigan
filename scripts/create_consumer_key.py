#!/usr/bin/env python3
"""Create, list, or revoke mmingest consumer API keys.

Usage
-----
Create a new key (prints plaintext once to stdout):

    python scripts/create_consumer_key.py --label "frontend-prod" \\
        --scopes mmingest:read,mmingest:stream

List all keys (hashes are never shown):

    python scripts/create_consumer_key.py --list

Revoke a key by ID (soft-delete — row is kept for audit trail):

    python scripts/create_consumer_key.py --revoke 3

The database path is read from the DATABASE_PATH environment variable
(default: ``./dashboard.db``).  Typically run from the project root with the
virtual environment activated:

    source venv/bin/activate
    DATABASE_PATH=dashboard.db python scripts/create_consumer_key.py \\
        --label "clip-finder" --scopes mmingest:read
"""

import argparse
import asyncio
import os
import sys

# Ensure the project root is on the path when run directly.
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


async def cmd_create(label: str, scopes: list[str]) -> None:
    from api.services import database
    from api.services.auth.consumer_keys import create_consumer_key

    await database.init_db()

    plaintext, consumer_id = await create_consumer_key(label=label, scopes=scopes)

    print()
    print(f"Consumer key created (id={consumer_id})")
    print(f"  label  : {label}")
    print(f"  scopes : {', '.join(sorted(scopes)) or '(none)'}")
    print()
    print("API KEY (copy now — this is the only time it will be shown):")
    print()
    print(f"  {plaintext}")
    print()


async def cmd_list() -> None:
    from api.services import database
    from api.services.auth.consumer_keys import list_consumer_keys

    await database.init_db()

    keys = await list_consumer_keys()

    if not keys:
        print("No consumer keys found.")
        return

    header = f"{'ID':>4}  {'Active':6}  {'Label':<30}  {'Scopes':<40}  Created"
    print(header)
    print("-" * len(header))

    for k in keys:
        active_str = "yes" if k["active"] else "NO"
        label = (k["label"] or "")[:30]
        scopes = (k["scopes"] or "")[:40]
        created = k["created_at"].strftime("%Y-%m-%d %H:%M") if k["created_at"] else "—"
        print(f"{k['id']:>4}  {active_str:<6}  {label:<30}  {scopes:<40}  {created}")


async def cmd_revoke(consumer_id: int) -> None:
    from api.services import database
    from api.services.auth.consumer_keys import revoke_consumer_key

    await database.init_db()

    success = await revoke_consumer_key(consumer_id)
    if success:
        print(f"Consumer key {consumer_id} revoked (marked inactive; row preserved for audit trail).")
    else:
        print(f"ERROR: Consumer key id={consumer_id} not found.", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manage mmingest consumer API keys.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # --list as a flag (alternative to the 'list' subcommand for ergonomics)
    parser.add_argument("--list", action="store_true", help="List all consumer keys")
    parser.add_argument("--label", type=str, help="Label for the new key")
    parser.add_argument(
        "--scopes",
        type=str,
        default="",
        help="Comma-separated scopes, e.g. mmingest:read,mmingest:stream",
    )
    parser.add_argument("--revoke", type=int, metavar="ID", help="Revoke a key by ID")

    args = parser.parse_args()

    if args.list:
        asyncio.run(cmd_list())
    elif args.revoke is not None:
        asyncio.run(cmd_revoke(args.revoke))
    elif args.label:
        scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
        asyncio.run(cmd_create(label=args.label, scopes=scopes))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
