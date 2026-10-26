"""Controlled QQ plugin deployment: verified staging, backup, atomic files, bot-only activation, rollback.

Run on the bot host from the AstrBot checkout after this exact payload has been
committed to git. This deliberately never restarts NapCat, changes credentials,
or edits private runtime config. Deployment is distinct from read-only diagnostics.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import urllib.request


def digest(path):
    """Compare exact deployed bytes, not a sanitized or reconstructed source approximation."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(arguments):
    """Require command success and retain bounded, non-secret deployment diagnostics."""
    result = subprocess.run(arguments, text=True, capture_output=True, timeout=180)
    if result.returncode:
        raise RuntimeError("command_failed: " + " ".join(arguments[:3]) + ": " + result.stderr[-300:])
    return result.stdout.strip()


def dashboard(path, credential_file, payload=None):
    """Use a pre-existing legitimate credential, not a forged JWT or password reset."""
    credential = json.loads(credential_file.read_text(encoding="utf-8"))
    headers = {"Content-Type": "application/json"}
    if credential.get("api_key"):
        headers["X-API-Key"] = credential["api_key"]
    else:
        login_request = urllib.request.Request("http://127.0.0.1:6185/api/v1/auth/login",
            data=json.dumps({"username": credential["username"], "password": credential["password"]}).encode(),
            headers=headers, method="POST")
        with urllib.request.urlopen(login_request, timeout=20) as response:
            login = json.load(response)
        token = (login.get("data") or {}).get("token")
        if not token:
            raise RuntimeError("dashboard_login_failed")
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request("http://127.0.0.1:6185/api/v1" + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers=headers, method="POST" if path.endswith("/reload") else "GET")
    with urllib.request.urlopen(request, timeout=900) as response:
        result = json.load(response)
    if result.get("status") not in ("ok", "success", None):
        raise RuntimeError("dashboard_operation_failed")
    return result


def apply(stage, target, commit, manifest_path, backup, report, credential):
    """Deploy only reviewed manifest files after baseline match; restore the complete prior set on failure."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["commit"] != commit or not commit or len(commit) != 40:
        raise ValueError("commit_manifest_mismatch")
    changes = manifest["files"]
    if target.resolve() != Path("/root/astrbot-src/data/plugins/astrbot_plugin_higan"):
        raise ValueError("unexpected_target")
    for row in changes:
        name = row["name"]
        if Path(name).name != name or not name.endswith(".py"):
            raise ValueError("invalid_release_file")
        if digest(stage / "plugin" / name) != row["sha256"]:
            raise ValueError("staged_hash_mismatch: " + name)
        existing = target / name
        before = row.get("baseline_sha256")
        if before and (not existing.exists() or digest(existing) != before):
            raise ValueError("production_drift: " + name)
        if not before and existing.exists():
            raise ValueError("new_file_already_exists: " + name)
    # Authentication and exact registered identity are required before any live file mutation.
    # A failed plugin is absent from the live registry. The framework's ordinary
    # reload(name) can fall through to reloading ALL plugins when name is absent;
    # use its exact failed-only recovery endpoint instead, never that fallback.
    failed = dashboard("/plugins/failed", credential)
    failed_plugins = failed.get("data", failed)
    if not isinstance(failed_plugins, dict):
        raise RuntimeError("unexpected_failed_plugin_registry")
    if "astrbot_plugin_higan" in failed_plugins:
        reload_path = "/plugins/failed/astrbot_plugin_higan/reload"
    else:
        installed = dashboard("/plugins/astrbot_plugin_higan", credential)
        if "astrbot_plugin_higan" not in json.dumps(installed):
            raise RuntimeError("exact_plugin_not_found")
        reload_path = "/plugins/astrbot_plugin_higan/reload"
    command(["/root/astrbot-venv/bin/python", "-m", "compileall", "-q", str(stage / "plugin")])
    before_pid = command(["systemctl", "show", "astrbot-186", "--property=MainPID", "--value"])
    napcat_before = command(["docker", "inspect", "--format", "{{.State.StartedAt}}", "napcat"])
    backup.mkdir(mode=0o700, parents=True, exist_ok=False)
    for row in changes:
        old = target / row["name"]
        if old.exists():
            shutil.copy2(old, backup / row["name"])
    (backup / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    updated = []
    result = {"commit": commit, "backup": str(backup), "pid_before": before_pid,
              "napcat_started_before": napcat_before, "status": "deploying"}
    try:
        for row in changes:
            name = row["name"]
            temporary = target / (name + ".release-stage")
            shutil.copyfile(stage / "plugin" / name, temporary)
            os.chmod(temporary, 0o600)
            os.replace(temporary, target / name)
            updated.append(name)
        for row in changes:
            if digest(target / row["name"]) != row["sha256"]:
                raise RuntimeError("deployed_hash_mismatch")
        # Keep both service processes alive; candidate initialize drains old owned work.
        result["reload_path"] = reload_path
        dashboard(reload_path, credential)
        remaining = dashboard("/plugins/failed", credential)
        failed_plugins = remaining.get("data", remaining)
        if not isinstance(failed_plugins, dict) or "astrbot_plugin_higan" in failed_plugins:
            raise RuntimeError("plugin_still_failed_after_reload")
        installed = dashboard("/plugins/astrbot_plugin_higan", credential)
        if "astrbot_plugin_higan" not in json.dumps(installed):
            raise RuntimeError("plugin_missing_after_reload")
        result.update({"status": "active", "pid_after": command([
            "systemctl", "show", "astrbot-186", "--property=MainPID", "--value"]),
            "napcat_started_after": command(["docker", "inspect", "--format", "{{.State.StartedAt}}", "napcat"])})
        if result["napcat_started_before"] != result["napcat_started_after"] or before_pid != result["pid_after"]:
            raise RuntimeError("process_changed_unexpectedly")
        result["hashes_verified"] = len(changes)
    except Exception as exc:
        result.update({"status": "rolled_back", "failure": type(exc).__name__})
        for name in updated:
            saved = backup / name
            if saved.exists():
                temporary = target / (name + ".rollback-stage")
                shutil.copyfile(saved, temporary)
                os.replace(temporary, target / name)
            else:
                # Preserve newly added scripts rather than delete them; inactive outside plugin path.
                os.replace(target / name, backup / ("unused-new-" + name))
        # Never downgrade to a restart if plugin reload fails. Preserve both running processes.
        result["status"] = "files_restored_activation_needs_review"
        raise
    finally:
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


def main():
    """Require explicit reviewed commit, manifest, staging, target and backup paths."""
    parser = argparse.ArgumentParser()
    for name in ("stage", "target", "commit", "manifest", "backup", "report", "credential"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    apply(Path(args.stage), Path(args.target), args.commit, Path(args.manifest), Path(args.backup), Path(args.report), Path(args.credential))


if __name__ == "__main__":
    main()
