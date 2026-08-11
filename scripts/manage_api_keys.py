#!/usr/bin/env python
# scripts/manage_api_keys.py

"""
CLI for issuing, revoking, and listing TinyAgentOS API keys.

This is the "even a CLI script is fine to start" remediation flagged in
docs/SECURITY.md's "API Key Authentication" section -- prior to this,
there was no way to issue a real key at all; `verify_api_key()` accepted
any string starting with "sk-".

Issuing a key is the ONLY time the raw key is ever shown. Only its
SHA-256 hash (via infrastructure.security.SecurityManager.hash_api_key)
is persisted, in storage.models.ApiKeyModel. A lost key cannot be
recovered -- issue a new one and revoke the old one.

Usage:
    python scripts/manage_api_keys.py issue --label "ops-dashboard"
    python scripts/manage_api_keys.py list
    python scripts/manage_api_keys.py revoke <api_key_id>
"""

import argparse
import sys

from infrastructure.config import get_settings
from infrastructure.security import SecurityManager
from storage.database import Database


def _get_db() -> Database:
    settings = get_settings()
    db = Database(settings.DATABASE_URL)
    db.init_db()  # safe no-op if tables already exist
    return db


def cmd_issue(args: argparse.Namespace) -> int:
    db = _get_db()

    # generate_api_key() returns a bare secrets.token_urlsafe(32) with no
    # prefix -- "sk-" is added here to match the prefix every existing
    # doc/check assumes ("sk-" was previously the *entire* validation, now
    # it's just a human/tooling-recognizable convention on top of a real
    # credential check).
    raw_key = "sk-" + SecurityManager.generate_api_key()
    key_hash = SecurityManager.hash_api_key(raw_key)

    api_key_row = db.create_api_key(key_hash=key_hash, label=args.label)

    print("API key created. This is the only time the raw key will be shown:")
    print()
    print(f"    {raw_key}")
    print()
    print(f"  id:    {api_key_row.id}")
    print(f"  label: {api_key_row.label or '(none)'}")
    print()
    print("Store it now (secrets manager, password manager, etc). There is no")
    print("recovery path for a lost key -- issue a new one and revoke this one.")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    db = _get_db()
    keys = db.list_api_keys()

    if not keys:
        print("No API keys issued yet.")
        return 0

    print(f"{'id':<38} {'label':<24} {'revoked':<8} {'created_at':<26} last_used_at")
    for key in keys:
        label = key.label or "(none)"
        last_used = key.last_used_at.isoformat() if key.last_used_at else "(never)"
        print(
            f"{key.id:<38} {label:<24} {str(key.revoked):<8} "
            f"{key.created_at.isoformat():<26} {last_used}"
        )
    return 0


def cmd_revoke(args: argparse.Namespace) -> int:
    db = _get_db()
    ok = db.revoke_api_key(args.api_key_id)

    if not ok:
        print(f"No API key found with id: {args.api_key_id}", file=sys.stderr)
        return 1

    print(f"Revoked API key: {args.api_key_id}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage TinyAgentOS API keys.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    issue_parser = subparsers.add_parser("issue", help="Issue a new API key.")
    issue_parser.add_argument(
        "--label",
        default=None,
        help="Optional human-readable label (e.g. the caller/service name).",
    )
    issue_parser.set_defaults(func=cmd_issue)

    list_parser = subparsers.add_parser("list", help="List all issued API keys (metadata only).")
    list_parser.set_defaults(func=cmd_list)

    revoke_parser = subparsers.add_parser("revoke", help="Revoke an API key by id.")
    revoke_parser.add_argument("api_key_id", help="The id shown by `list` or at issuance.")
    revoke_parser.set_defaults(func=cmd_revoke)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())