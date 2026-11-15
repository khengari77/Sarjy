"""Local operator CLI for reviewer invitations.

Usage::

    uv run python scripts/reviewer_invitations.py create --base-url https://host --link-file /secure/link
    uv run python scripts/reviewer_invitations.py list
    uv run python scripts/reviewer_invitations.py revoke --invitation-id ID

No public admin HTTP route exists.  The raw token is written only to the
explicitly supplied new file and never printed or passed as an argument.
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))

from app.config import Settings  # noqa: E402
from app.services.reviewer_access import (  # noqa: E402
    DEFAULT_MAX_CALLS,
    DEFAULT_MAX_IDENTITIES,
    create_invitation,
    list_invitations,
    now_utc,
    revoke_invitation,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402


def parse_expiry(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("expiry must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("expiry must include a UTC offset")
    return parsed.astimezone(UTC)


def validate_base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("base URL must be an HTTPS origin without credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("base URL must not contain a query or fragment")
    return value.rstrip("/")


def write_link(path: Path, link: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(link + "\n")


def _session_factory():
    settings = Settings()
    engine = create_async_engine(settings.database_url)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _create(args) -> int:
    base_url = validate_base_url(args.base_url)
    expires_at = parse_expiry(args.expires_at) if args.expires_at else None
    link_path = Path(args.link_file)
    if link_path.exists():
        print("reviewer-cli: link file already exists", file=sys.stderr)
        return 1
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            invitation, token = await create_invitation(
                db,
                expires_at=expires_at,
                now=now_utc(),
                max_identities=args.max_identities,
                max_calls=args.max_calls,
            )
    finally:
        await engine.dispose()
    try:
        write_link(link_path, f"{base_url}/review#invitation={token}")
    except FileExistsError:
        print("reviewer-cli: link file already exists", file=sys.stderr)
        return 1
    except OSError:
        print("reviewer-cli: could not write link file", file=sys.stderr)
        return 1
    print(f"invitation_id: {invitation.id}")
    print(f"link_file: {args.link_file}")
    print(f"expires_at: {invitation.expires_at}")
    print(f"max_identities: {invitation.max_identities}")
    print(f"remaining_identities: {invitation.max_identities - invitation.identities_created}")
    print(f"max_calls: {invitation.max_calls}")
    return 0


async def _list(_args) -> int:
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            rows = await list_invitations(db)
    finally:
        await engine.dispose()
    print(json.dumps(rows, indent=2))
    return 0


async def _revoke(args) -> int:
    engine, factory = _session_factory()
    try:
        async with factory() as db:
            found = await revoke_invitation(db, args.invitation_id, now_utc())
    finally:
        await engine.dispose()
    if not found:
        print("reviewer-cli: invitation not found", file=sys.stderr)
        return 2
    print(f"revoked: {args.invitation_id}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage local reviewer invitations.")
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="Create one bounded reusable invitation.")
    create.add_argument("--base-url", required=True)
    create.add_argument(
        "--expires-at",
        default=None,
        help="ISO-8601 expiry with UTC offset (default: now + 72 hours)",
    )
    create.add_argument("--max-identities", type=int, default=DEFAULT_MAX_IDENTITIES)
    create.add_argument("--max-calls", type=int, default=DEFAULT_MAX_CALLS)
    create.add_argument("--link-file", required=True)
    create.set_defaults(handler=_create)

    listing = commands.add_parser("list", help="List sanitized invitation metadata.")
    listing.set_defaults(handler=_list)

    revoke = commands.add_parser("revoke", help="Irreversibly revoke an invitation.")
    revoke.add_argument("--invitation-id", required=True)
    revoke.set_defaults(handler=_revoke)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(args.handler(args))
    except ValueError:
        print("reviewer-cli: invalid arguments", file=sys.stderr)
        return 2
    except Exception:
        # Never echo exception text: it may include database details or URLs.
        print("reviewer-cli: operation failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
