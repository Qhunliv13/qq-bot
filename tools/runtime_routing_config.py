"""Narrow, explicit native-dashboard routing update; dry-read by default.

No framework is imported during module import or dry-read. The two reviewed
platform flags, builtin-command switch and wake prefixes are the only changes;
permissions, administrators, providers and QQ persona remain untouched. Apply
requires reviewed framework hashes, a private fresh backup and an exact disk hash.
The native service's minimum one-day config key is immediately revoked in finally.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

from release_access import (
    EXPECTED_FRAMEWORK,
    backup_database,
    fingerprint,
    inspect_database,
    refuse_migration,
)


# The dashboard's actual native endpoint takes the complete raw profile on PUT.
PROFILE_PATH = "/api/v1/config-profiles/default"
TARGET_FLAGS = ("no_permission_reply", "empty_mention_waiting")


def patch_config(config: dict) -> dict:
    """Return an independent copy changing only approved routing/command settings.

    Missing or malformed platform settings are rejected rather than manufactured,
    so a wrong profile cannot silently be replaced with a partial configuration.
    """
    if not isinstance(config, dict):
        raise ValueError("Configuration must be a dictionary")
    if not isinstance(config.get("platform_settings"), dict):
        raise ValueError("platform_settings must be a dictionary")
    patched = deepcopy(config)
    for flag in TARGET_FLAGS:
        patched["platform_settings"][flag] = False
    # Explicit expanded authorization suppresses builtin chat commands and prefix
    # wakeups (including /alone), not provider enablement or ACL evaluation.
    patched["disable_builtin_commands"] = True
    patched["wake_prefix"] = []
    return patched


class NoRedirect(HTTPRedirectHandler):
    """Prevent a dashboard redirect from leaking the temporary authentication key."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Reject every redirect, including redirects to another loopback port."""
        raise RuntimeError("Dashboard redirects are prohibited")


def request(method: str, api_key: str, payload: dict | None = None) -> dict:
    """Call only the original loopback dashboard, without proxies or redirects."""
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = Request(
        "http://127.0.0.1:6185" + PROFILE_PATH,
        data=body,
        headers={"X-API-Key": api_key, "Content-Type": "application/json"},
        method=method,
    )
    opener = build_opener(ProxyHandler({}), NoRedirect())
    with opener.open(req, timeout=30) as response:
        raw = response.read()
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError):
            print(json.dumps({"diagnostic": "non_json_dashboard_response", "method": method,
                              "status": response.status, "bytes": len(raw),
                              "content_type": response.headers.get("Content-Type", "")}), flush=True)
            raise RuntimeError("Native dashboard returned non-JSON") from None
    if not isinstance(result, dict) or result.get("status") != "ok":
        raise RuntimeError("Native dashboard request failed")
    data = result.get("data")
    if method == "GET":
        # ConfigService.get_profile returns {config, metadata} inside the envelope.
        if not isinstance(data, dict) or not isinstance(data.get("config"), dict):
            raise RuntimeError("Native dashboard returned an invalid profile")
        return data["config"]
    return result


async def run(args: argparse.Namespace) -> dict:
    """Apply one compare-before-write update, always revoking its native API key."""
    root = Path(args.root).resolve(strict=True)
    config_path = root / "data" / "cmd_config.json"
    before = json.loads(config_path.read_text(encoding="utf-8"))
    expected = patch_config(before)
    flags = {flag: expected["platform_settings"][flag] for flag in TARGET_FLAGS}
    flags.update(disable_builtin_commands=True, wake_prefix=[])
    if not args.apply:
        # Dry-read is deliberately independent of DB/framework initialization.
        return {"apply": False, "desired": flags, "changed": expected != before}
    if os.name != "posix" or os.geteuid() != 0:
        raise RuntimeError("Apply requires a POSIX root process")
    if not args.backup or not args.expected_sha256:
        raise ValueError("Apply requires --backup and --expected-sha256")
    config_digest = fingerprint(config_path)
    if config_digest != args.expected_sha256:
        raise RuntimeError("Configuration hash differs; refusing apply")
    configured_root = os.environ.get("ASTRBOT_ROOT")
    if configured_root and Path(configured_root).resolve() != root:
        raise RuntimeError("ASTRBOT_ROOT differs from the explicit root")
    for relative, digest in EXPECTED_FRAMEWORK.items():
        if fingerprint(root / relative) != digest:
            raise RuntimeError("Reviewed framework source hash differs")
    db_path = root / "data" / "data_v4.db"
    inspect_database(db_path)
    if before == expected:
        return {"apply": True, "changed": False, "desired": flags}
    backup = Path(args.backup).absolute()
    # mkdir refuses existing files, directories and symlinks, never reusing secrets.
    backup.mkdir(mode=0o700, parents=False, exist_ok=False)
    os.chmod(backup, 0o700)
    descriptor = os.open(backup / "cmd_config.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(config_path.read_bytes())
        handle.flush()
        os.fsync(handle.fileno())
    backup_database(db_path, backup / "data_v4.db")
    if (
        fingerprint(config_path) != config_digest
        or fingerprint(backup / "cmd_config.json") != config_digest
        or json.loads((backup / "cmd_config.json").read_text(encoding="utf-8")) != before
    ):
        raise RuntimeError("Configuration changed during backup")
    os.chdir(root)
    sys.path.insert(0, str(root))
    # Reuse original native service and reviewed release safety, never key/JWT logic.
    from astrbot.core import db_helper
    from astrbot.dashboard.services.api_key_service import ApiKeyService

    created_id = None
    service = None
    try:
        if Path(db_helper.db_path).resolve() != db_path.resolve():
            raise RuntimeError("Framework database path differs")
        if fingerprint(config_path) != config_digest:
            raise RuntimeError("Framework import changed configuration")
        db_helper.inited = True
        db_helper.initialize = refuse_migration
        service = ApiKeyService(db_helper)
        # Original SDK supports only integer days (minimum one); the approved
        # config-only key is revoked immediately in finally, never persisted.
        result = await service.create_api_key(
            {"name": "runtime-routing-config", "scopes": ["config"], "expires_in_days": 1},
            created_by="root-ssh-config-release",
        )
        created_id = result["key_id"]
        # The native service expands config to its documented bot/provider
        # dependency scopes. Reject unrelated privileges, not native expansion.
        scopes = result.get("scopes")
        if (not isinstance(scopes, list) or any(not isinstance(scope, str) for scope in scopes)
                or "config" not in scopes or not set(scopes) <= {"config", "bot", "provider"}):
            raise RuntimeError("Temporary key scope differs")
        api_key = result["api_key"]
        current = await asyncio.to_thread(request, "GET", api_key)
        if current != before or fingerprint(config_path) != config_digest:
            print(json.dumps({"diagnostic": "profile_drift_keys", "keys": sorted(
                key for key in set(current) | set(before) if current.get(key) != before.get(key))}), flush=True)
            raise RuntimeError("Current dashboard/disk profile differs; refusing PUT")
        await asyncio.to_thread(request, "PUT", api_key, expected)
        actual = await asyncio.to_thread(request, "GET", api_key)
        disk = json.loads(config_path.read_text(encoding="utf-8"))
        if actual != expected or disk != expected:
            # Do not blindly undo a concurrent administrator's write.
            raise RuntimeError("Post-update profile differs; inspect private backup; no blind rollback")
        return {"apply": True, "changed": True, "desired": flags}
    finally:
        try:
            if created_id is not None and service is not None:
                if not await service.revoke_api_key(created_id):
                    raise RuntimeError("Temporary key revocation failed")
        finally:
            await db_helper.engine.dispose()


def main() -> None:
    """Parse explicit apply guards and emit only safe flags or exception types."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="/root/astrbot-src")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", help="New private backup directory; must not exist")
    parser.add_argument("--expected-sha256", help="Exact current cmd_config.json SHA-256")
    args = parser.parse_args()
    try:
        result = asyncio.run(run(args))
    except Exception as exc:
        # Native errors may contain credentials/profile fields; never print values.
        safe_reasons = {
            "Current dashboard/disk profile differs; refusing PUT": "profile_drift",
            "Temporary key scope differs": "key_scope_mismatch",
            "Native dashboard request failed": "dashboard_rejected",
            "Native dashboard returned an invalid profile": "invalid_profile_envelope",
            "Framework import changed configuration": "import_changed_config",
            "Post-update profile differs; inspect private backup; no blind rollback": "post_update_drift",
        }
        print(json.dumps({"success": False, "error_type": type(exc).__name__,
                          "error_code": safe_reasons.get(str(exc), "guard_or_transport_failure")}), file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(result))


if __name__ == "__main__":
    main()
