"""Initialize only a fresh AstrBot data directory, then run frozen upstream."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import runpy
import tomllib
from typing import Any
from urllib.parse import urlsplit


PLUGIN_NAME = "astrbot_plugin_higan"


def read_settings(path: Path) -> dict[str, Any]:
    """Validate the external bootstrap contract without logging private values."""
    settings = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(settings, dict) or settings.get("schema_version") != 1:
        raise ValueError("Unsupported settings schema")
    model = settings.get("model")
    if not isinstance(model, dict):
        raise ValueError("Missing model settings")
    for key in ("base_url", "model", "api_key"):
        if not isinstance(model.get(key), str) or not model[key].strip():
            raise ValueError("Incomplete model settings")
    parsed = urlsplit(model["base_url"])
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Invalid model URL")
    owners = settings.get("owner_qqs")
    if not isinstance(owners, list) or any(not isinstance(owner, str) or not owner.isdecimal() for owner in owners):
        raise ValueError("Invalid owner list")
    if not isinstance(settings.get("persona"), str):
        raise ValueError("Invalid persona")
    password = settings.get("dashboard_password")
    if not isinstance(password, str) or len(password) < 16:
        raise ValueError("A private dashboard password of at least 16 characters is required")
    return settings


def prepare(
    settings_path: Path,
    data_root: Path,
    higan_root: Path,
    plugin_root: Path,
    default_config: dict[str, Any],
) -> dict[str, str]:
    """Seed new state once; refuse existing foreign plugins instead of replacing them.

    A symlink always resolves to the immutable plugin bundled in this image. A
    content manifest prevents silent plugin upgrades when an old data volume is
    reused with a different image; controlled upgrades must be explicit.
    """
    settings = read_settings(settings_path)
    plugin_root = plugin_root.resolve(strict=True)
    if not (plugin_root / "main.py").is_file():
        raise ValueError("Bundled plugin is incomplete")
    # Include all shipped regular files so every image change is detected.
    manifest = hashlib.sha256()
    for path in sorted(plugin_root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            manifest.update(path.relative_to(plugin_root).as_posix().encode("utf-8"))
            manifest.update(b"\0")
            manifest.update(path.read_bytes())
    expected_marker = {"schema_version": 1, "plugin_sha256": manifest.hexdigest()}
    marker = data_root / ".qhunliv-bootstrap.json"
    plugin_link = data_root / "plugins" / PLUGIN_NAME
    config_path = data_root / "cmd_config.json"
    if marker.exists():
        if json.loads(marker.read_text(encoding="utf-8")) != expected_marker:
            raise ValueError("Plugin image changed; controlled update is required")
        if not plugin_link.is_symlink() or plugin_link.resolve() != plugin_root:
            raise ValueError("Plugin installation changed; controlled update is required")
        if not config_path.is_file():
            raise ValueError("Initialized configuration is missing")
    else:
        # Never adopt unrelated persisted data or override operator changes.
        if data_root.exists() and any(data_root.iterdir()):
            raise ValueError("AstrBot data is not fresh; explicit migration is required")
        # Never follow a foreign symlink while fixing the fresh bind-mount root.
        if higan_root.is_symlink():
            raise ValueError("Higan state root must not be a symlink")
        data_root.mkdir(parents=True, exist_ok=True)
        data_root.chmod(0o700)
        higan_root.mkdir(parents=True, exist_ok=True)
        # Bootstrap may run as a normal WSL user, so its bind mount retains that
        # UID inside the root container. LocalRuntime requires this exact root
        # directory to belong to root; chmod alone cannot satisfy that check.
        # Change only the fresh mount root, never descendants or existing installs.
        if sys.platform.startswith("linux") and os.geteuid() == 0:
            os.chown(higan_root, 0, 0)
        higan_root.chmod(0o700)
        config = copy.deepcopy(default_config)
        model = settings["model"]
        config["provider_sources"] = [{
            "id": "qhunliv-provider", "type": "openai_chat_completion",
            "provider_type": "chat_completion", "api_base": model["base_url"],
            "key": [model["api_key"]], "timeout": 120,
        }]
        config["provider"] = [{
            "id": "qhunliv-model", "provider_source_id": "qhunliv-provider",
            "model": model["model"], "enable": True,
        }]
        config.setdefault("provider_settings", {})["default_provider_id"] = "qhunliv-model"
        # v4.28.1 also stores the local runner's active model in its own block.
        runner = config.setdefault("agent_runner", {"runner_type": "local"})
        runner.setdefault("config", {}).setdefault("model", {})["provider_id"] = "qhunliv-model"
        config["admins_id"] = list(settings["owner_qqs"])
        # No QQ transport is enabled automatically. Operators configure an
        # external adapter explicitly; this image contains no QQ client.
        config["platform"] = []
        dashboard = config.setdefault("dashboard", {})
        dashboard.update({
            "enable": True, "host": "0.0.0.0", "port": 6185,
            "username": "astrbot",
            # Upstream auth_password.py explicitly supports this legacy hash.
            "password": hashlib.md5(settings["dashboard_password"].encode("utf-8")).hexdigest(),
            "pbkdf2_password": "", "password_storage_upgraded": False,
            "password_change_required": False,
        })
        plugin_link.parent.mkdir(parents=True, exist_ok=True)
        plugin_link.symlink_to(plugin_root, target_is_directory=True)
        # Exclusive creation and restrictive mode avoid overwriting secrets.
        descriptor = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(config, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(expected_marker, stream, sort_keys=True)
            stream.write("\n")
    return {
        "HIGAN_ROOT": str(higan_root), "ASTRBOT_ROOT": str(data_root.parent),
        "HIGAN_SETTINGS_FILE": str(settings_path),
        "HIGAN_OWNER_QQS": json.dumps(settings["owner_qqs"]),
    }


def load_upstream_defaults(root: Path) -> tuple[dict[str, Any], str]:
    """Evaluate the pinned default expression without importing side-effectful core.

    Importing astrbot.core initializes the DB and writes default credentials, so
    only the trusted vendored DEFAULT_CONFIG AST expression is evaluated here.
    Its two nonliteral defaults come from standalone upstream configuration files.
    """
    core = root / "astrbot" / "core"
    namespace = runpy.run_path(str(core / "computer" / "booters" / "cua_defaults.py"))
    namespace.update(runpy.run_path(str(core / "config" / "agent_runner.py")))
    tree = ast.parse((core / "config" / "default.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "DEFAULT_CONFIG" for target in node.targets):
            expression = ast.Expression(body=node.value)
            defaults = eval(compile(expression, "<vendored DEFAULT_CONFIG>", "eval"), namespace)
            version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
            return defaults, version
    raise ValueError("Upstream DEFAULT_CONFIG was not found")


def main() -> None:
    """Validate local assets and settings before exec, without downloading a UI."""
    astrbot_root = Path("/AstrBot")
    os.chdir(astrbot_root)
    sys.path.insert(0, str(astrbot_root))
    default_config, version = load_upstream_defaults(astrbot_root)

    dashboard = astrbot_root / "astrbot" / "dashboard" / "dist"
    if not (dashboard / "index.html").is_file() or (dashboard / "assets" / "version").read_text(encoding="utf-8").strip().removeprefix("v") != version:
        raise ValueError("Bundled dashboard is missing or version-mismatched")
    environment = prepare(
        Path("/run/qhunliv/settings.json"), astrbot_root / "data",
        Path("/root/higan"), Path("/opt/qhunliv/plugin"), default_config,
    )
    os.environ.update(environment)
    # Explicit assets bypass upstream's automatic download/repair path.
    os.execv(sys.executable, [sys.executable, "main.py", "--webui-dir", str(dashboard)])


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Do not include exception values: JSON errors can contain credentials.
        print(f"Bootstrap refused startup ({type(exc).__name__}); check private configuration and state.", file=sys.stderr)
        raise SystemExit(1) from None
