"""Routing release tests are isolated from production, credentials and framework."""
import argparse
import asyncio
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest


@pytest.fixture
def tool(monkeypatch):
    """Import only the standard-library administrative modules, never AstrBot."""
    # Archive-local imports keep private administrative dependencies out of the active tools directory.
    tools = Path(__file__).parents[1] / "trash" / "20261030-personal-operations" / "tools"
    monkeypatch.syspath_prepend(str(tools))
    spec = importlib.util.spec_from_file_location("runtime_routing_config", tools / "runtime_routing_config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def config():
    """Model sensitive fields which an exact narrow patch must preserve."""
    return {
        "platform_settings": {"no_permission_reply": True, "empty_mention_waiting": True,
                              "no_permission": ["secret-acl"], "nested": {"items": [1]}},
        "disable_builtin_commands": False,
        "wake_prefix": ["/", "!"],
        "admins_id": ["unchanged-admin"],
        "provider_settings": {"enable": True, "prompt": "unchanged prompt"},
        "persona": {"name": "QQ", "prompt": "unchanged persona"},
        "platform": [{"id": "qq", "token": "never-print-this"}],
    }


def test_exact_patch_is_independent_and_preserves_acl_persona(tool, config):
    """Only approved flags and prefixes change, including no_permission text."""
    original = deepcopy(config)
    actual = tool.patch_config(config)
    expected = deepcopy(original)
    expected["platform_settings"]["no_permission_reply"] = False
    expected["platform_settings"]["empty_mention_waiting"] = False
    expected["disable_builtin_commands"] = True
    expected["wake_prefix"] = []
    assert actual == expected
    assert config == original
    actual["platform_settings"]["nested"]["items"].append(2)
    assert config == original


def test_patch_is_idempotent(tool, config):
    """Repeat application performs no additional configuration edits."""
    once = tool.patch_config(config)
    assert tool.patch_config(once) == once


@pytest.mark.parametrize("value", [None, [], "config", 1, {}, {"platform_settings": None}, {"platform_settings": []}])
def test_invalid_config_is_rejected(tool, value):
    """Wrong/missing profile structures must not produce partial replacement config."""
    with pytest.raises(ValueError):
        tool.patch_config(value)


def test_missing_flags_are_added_without_other_platform_edits(tool):
    """An existing settings dictionary is sufficient; ACL text is not synthesized."""
    result = tool.patch_config({"platform_settings": {"no_permission": "keep"}})
    assert result["platform_settings"] == {
        "no_permission": "keep", "no_permission_reply": False, "empty_mention_waiting": False,
    }


def test_dry_read_has_no_framework_db_or_network_effects(tool, config, tmp_path, monkeypatch):
    """Default read succeeds without a DB or native packages and never emits config."""
    data = tmp_path / "data"
    data.mkdir()
    path = data / "cmd_config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    before = path.read_bytes()
    def forbidden(*args, **kwargs):
        """Fail immediately if a dry-read attempts any mutating capability."""
        raise AssertionError("Unexpected side effect")
    monkeypatch.setattr(tool, "inspect_database", forbidden)
    monkeypatch.setattr(tool, "request", forbidden)
    monkeypatch.setattr(tool, "backup_database", forbidden)
    imported = set(sys.modules)
    result = asyncio.run(tool.run(argparse.Namespace(root=str(tmp_path), apply=False)))
    assert result["apply"] is False and result["changed"] is True
    assert "never-print-this" not in json.dumps(result)
    assert path.read_bytes() == before
    assert not any(name.startswith("astrbot") for name in set(sys.modules) - imported)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["data"]


def test_request_uses_native_envelope_and_raw_config(tool, config, monkeypatch):
    """GET unwraps ConfigService while PUT sends a complete raw profile."""
    calls = []
    class Response:
        """Provide a context-managed JSON response for urllib without networking."""
        def __enter__(self):
            """Open the simulated HTTP response."""
            return self
        def __exit__(self, *args):
            """Close the simulated response without suppressing errors."""
            return False
        def read(self):
            """Return the actual native service response shape."""
            return json.dumps({"status": "ok", "data": {"config": config, "metadata": {}}}).encode()
    class Opener:
        """Capture the one original loopback request."""
        def open(self, req, timeout):
            """Return local test content while capturing method/body/header."""
            calls.append((req, timeout))
            return Response()
    monkeypatch.setattr(tool, "build_opener", lambda *args: Opener())
    assert tool.request("GET", "test-only-key") == config
    tool.request("PUT", "test-only-key", config)
    req, timeout = calls[-1]
    assert req.full_url == "http://127.0.0.1:6185/api/v1/config-profiles/default"
    assert req.get_method() == "PUT" and timeout == 30
    assert json.loads(req.data) == config
    assert req.get_header("X-api-key") == "test-only-key"


def test_redirects_are_refused(tool):
    """Authentication must never be forwarded to a redirect destination."""
    with pytest.raises(RuntimeError, match="redirect"):
        tool.NoRedirect().redirect_request(None, None, 302, "", {}, "http://example.invalid")


@pytest.mark.parametrize("failure", [None, "concurrent", "put", "post", "scope", "revoke"])
def test_apply_uses_native_key_and_always_revokes(tool, config, tmp_path, monkeypatch, failure):
    """Exercise backup/order/native signature and finally on success or failure."""
    data = tmp_path / "data"
    data.mkdir()
    path = data / "cmd_config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    digest = tool.fingerprint(path)
    calls = []
    class Engine:
        """Track cleanup without loading any real database engine."""
        async def dispose(self):
            """Record guaranteed engine cleanup."""
            calls.append("dispose")
    db = SimpleNamespace(db_path=str(data / "data_v4.db"), engine=Engine())
    class Service:
        """Model the reviewed SDK signature without generating a credential."""
        def __init__(self, helper):
            assert helper is db and helper.inited is True
        async def create_api_key(self, payload, *, created_by):
            """Check minimum native expiry and explicit approved config scope."""
            assert payload == {"name": "runtime-routing-config", "scopes": ["config"], "expires_in_days": 1}
            assert created_by == "root-ssh-config-release"
            assert (tmp_path / "backup" / "data_v4.db").exists()
            calls.append("create")
            return {"key_id": "test-id", "api_key": "test-secret", "scopes": ["plugin"] if failure == "scope" else ["config"]}
        async def revoke_api_key(self, key_id):
            """Record revocation on every post-creation branch."""
            assert key_id == "test-id"
            calls.append("revoke")
            return failure != "revoke"
    core = ModuleType("astrbot.core")
    core.db_helper = db
    service_module = ModuleType("astrbot.dashboard.services.api_key_service")
    service_module.ApiKeyService = Service
    monkeypatch.setitem(sys.modules, "astrbot", ModuleType("astrbot"))
    monkeypatch.setitem(sys.modules, "astrbot.core", core)
    monkeypatch.setitem(sys.modules, "astrbot.dashboard.services.api_key_service", service_module)
    # Use a module-local OS facade, not a global os.name patch (Path is Windows).
    monkeypatch.setattr(tool, "os", SimpleNamespace(
        name="posix", geteuid=lambda: 0, environ={}, chmod=os.chmod,
        open=os.open, O_WRONLY=os.O_WRONLY, O_CREAT=os.O_CREAT, O_EXCL=os.O_EXCL,
        fdopen=os.fdopen, fchmod=lambda *args: None, fsync=os.fsync, chdir=lambda *args: None,
    ))
    monkeypatch.setattr(tool, "EXPECTED_FRAMEWORK", {})
    monkeypatch.setattr(tool, "inspect_database", lambda *args: None)
    def backup(source, destination):
        """Reserve a stand-in for the already separately tested SQLite helper."""
        destination.write_bytes(b"consistent-test-backup")
    monkeypatch.setattr(tool, "backup_database", backup)
    state = deepcopy(config)
    def native(method, api_key, payload=None):
        """Model native profile persistence and concurrency without network calls."""
        assert api_key == "test-secret"
        calls.append(method)
        if method == "PUT":
            if failure == "put":
                raise RuntimeError("simulated failure")
            state.clear()
            state.update(deepcopy(payload))
            if failure == "post":
                state["admins_id"] = ["concurrent-admin"]
            path.write_text(json.dumps(state), encoding="utf-8")
            return {}
        if failure == "concurrent":
            return {**state, "unexpected": True}
        return deepcopy(state)
    monkeypatch.setattr(tool, "request", native)
    args = argparse.Namespace(root=str(tmp_path), apply=True, backup=str(tmp_path / "backup"), expected_sha256=digest)
    if failure is None:
        assert asyncio.run(tool.run(args))["changed"] is True
        assert json.loads(path.read_text()) == tool.patch_config(config)
    else:
        with pytest.raises(RuntimeError):
            asyncio.run(tool.run(args))
    assert calls[-2:] == ["revoke", "dispose"]
    if failure in {"scope", "concurrent"}:
        assert "PUT" not in calls
    assert json.loads((tmp_path / "backup" / "cmd_config.json").read_text()) == config
