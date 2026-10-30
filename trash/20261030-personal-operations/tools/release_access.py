"""Explicit root-only AstrBot release API-key creation/revocation; never run on import.

This is a MUTATING administrative tool, not a read-only probe. Authorization for
production database writes is required separately. Creation backs up the existing
SQLite database, uses the native ApiKeyService, grants only plugin scope for one
day (the service minimum), and stores the one-time secret in a mode-0600 file.
Framework import initializes configuration/logging and must be reviewed for the
installed version before use; configuration hashes are checked for drift.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from datetime import datetime, timezone


NAME = "context-tools-20261025"
# This tool authorizes plugin release keys only; command administration requires
# separate authorization and must not silently expand the release key's scope.
SCOPES = ("plugin",)
# Require these exact framework source hashes; fail before import on drift.
EXPECTED_FRAMEWORK = {
    "astrbot/core/db/sqlite.py": "cde20f8d33df9feb6a3580c95499a65c3b94ec490ed34f1c1d041516b5a070d4",
    "astrbot/core/db/__init__.py": "72978bdfe56266815c1c24d7c3e198c0c69de331c0b5039047a16f46f9162e80",
    "astrbot/dashboard/services/api_key_service.py": "054820944555715c8ee6d1011e8d8324b9dc527d32e1bebb2cc97cfc101cfd1a",
    "astrbot/core/__init__.py": "f393410ddecc1f44c48d5b8efd82a009c0823df4014285919da1dea607362e2e",
}


def fingerprint(path: Path) -> str:
    """Compare config bytes without exposing configuration or credentials."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect_database(path: Path) -> None:
    """Require an existing API-key table without initializing or migrating schema."""
    if not path.is_file():
        raise RuntimeError("Existing production database is required")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(api_keys)")}
        required = {"key_id", "name", "key_hash", "key_prefix", "scopes", "created_by", "expires_at", "revoked_at"}
        if not required.issubset(columns):
            raise RuntimeError("API-key schema is not ready; migrations are prohibited")
    finally:
        connection.close()


def backup_database(source: Path, destination: Path) -> None:
    """Take a consistent SQLite backup before credential creation, never overwrite."""
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    original = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
    backup = sqlite3.connect(str(destination))
    try:
        original.backup(backup)
    finally:
        backup.close()
        original.close()


async def refuse_migration() -> None:
    """Fail closed if any operation unexpectedly attempts database initialization."""
    raise RuntimeError("Database initialization/migrations are prohibited")


async def run(args: argparse.Namespace) -> dict:
    """Perform exactly one explicit native-service action and dispose its engine."""
    root = Path(args.root).resolve(strict=True)
    if not (root / "astrbot" / "core" / "db" / "sqlite.py").is_file():
        raise RuntimeError("Root does not identify the installed AstrBot source tree")
    configured_root = os.environ.get("ASTRBOT_ROOT")
    if configured_root and Path(configured_root).resolve() != root:
        raise RuntimeError("ASTRBOT_ROOT disagrees with the explicit source/data root")
    for relative, expected in EXPECTED_FRAMEWORK.items():
        if fingerprint(root / relative) != expected:
            raise RuntimeError("Inspected framework version/hash differs; refusing execution")
    data = root / "data"
    db_path = data / "data_v4.db"
    config_path = data / "cmd_config.json"
    inspect_database(db_path)
    config_digest = fingerprint(config_path)
    credential = Path(args.credential).resolve() if args.action == "create" else None
    descriptor = None
    backup = None
    if credential is not None:
        if not credential.parent.is_dir():
            raise RuntimeError("Private credential parent directory must already exist")
        # Reserve a restricted file before any key exists; never replace old secrets.
        descriptor = os.open(credential, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.fchmod(descriptor, 0o600)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = Path(args.backup).resolve() if args.backup else data / ("data_v4.pre-release-access-" + stamp + ".db")
        backup_database(db_path, backup)
    os.chdir(root)
    sys.path.insert(0, str(root))
    # These are the installed framework implementations, not copied key/hash logic.
    from astrbot.core import db_helper
    from astrbot.dashboard.services.api_key_service import ApiKeyService

    if Path(db_helper.db_path).resolve() != db_path.resolve():
        raise RuntimeError("Framework database path differs from the verified database")
    if fingerprint(config_path) != config_digest:
        raise RuntimeError("Framework import changed configuration; no key action performed")
    # Existing schema was checked read-only. Bypass automatic global schema migration.
    db_helper.inited = True
    db_helper.initialize = refuse_migration
    service = ApiKeyService(db_helper)
    # A distinct name plus exact scope binds revocation to the same narrow key.
    scope = getattr(args, "scope", "plugin")
    if scope not in SCOPES:
        raise ValueError("Unsupported administrative scope")
    name = NAME if scope == "plugin" else "command-toggle-20261027"
    created_id = None
    try:
        if args.action == "revoke":
            # Limit revocation to this tool's named plugin-only release key.
            existing = await db_helper.get_api_key_by_id(args.key_id)
            if existing is None or existing.name != name or existing.scopes != [scope]:
                raise RuntimeError("Refusing to revoke an unrelated API key")
            success = await service.revoke_api_key(args.key_id)
            return {"action": "revoke", "key_id": args.key_id, "success": bool(success)}
        result = await service.create_api_key(
            {"name": name, "scopes": [scope], "expires_in_days": 1},
            created_by=args.created_by,
        )
        created_id = result["key_id"]
        # The raw secret is written only to the already restricted descriptor.
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = None
            json.dump({"key_id": created_id, "api_key": result["api_key"], "scopes": [scope], "expires_at": result["expires_at"]}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        return {"action": "create", "key_id": created_id, "scope": scope, "success": True}
    except Exception:
        if created_id is not None:
            # A failed secret delivery must not leave an untracked usable credential.
            await service.revoke_api_key(created_id)
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)
        await db_helper.engine.dispose()


def main() -> None:
    """Require root and an explicit action; never echo exception values or secrets."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--scope", choices=SCOPES, default="plugin")
    actions = parser.add_subparsers(dest="action", required=True)
    create = actions.add_parser("create")
    create.add_argument("--credential", required=True)
    create.add_argument("--created-by", required=True)
    create.add_argument("--backup")
    revoke = actions.add_parser("revoke")
    revoke.add_argument("--key-id", required=True)
    args = parser.parse_args()
    if os.name != "posix" or os.geteuid() != 0:
        parser.error("This explicit administrative tool requires a POSIX root process")
    try:
        result = asyncio.run(run(args))
    except Exception as exc:
        print(json.dumps({"success": False, "error_type": type(exc).__name__}), file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(result))


if __name__ == "__main__":
    main()
