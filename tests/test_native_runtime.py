"""Offline contracts for the full native runtime; never install or start a host."""

import ast
import asyncio
from contextlib import contextmanager
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools import native_assets
from tools import native_runtime as native
from tools import setup_wizard


# All credentials and model names are synthetic; no operator secrets are read.
MODEL = {"base_url": "https://offline.invalid/v1", "api_key": "sk-offline-native-test-only", "model": "offline-full-model"}
PASSWORD = "offline-dashboard-password-only"
NAME = "offline-native-full"
# Keep the real helper reachable for the focused actual-Windows ACL smoke test.
REAL_PRIVATE_DIRECTORY = native.private_directory
REAL_SUBPROCESS_RUN = subprocess.run
REAL_SUBPROCESS_POPEN = subprocess.Popen


@pytest.fixture
def seeder():
    """Load the first-party config seeder independently of any AstrBot module import."""
    return native._entrypoint()


@pytest.fixture
def standalone_plugin(tmp_path):
    """Provide a small inert plugin tree for copy integrity and config-seeding tests."""
    plugin = tmp_path / "bundled plugin"
    plugin.mkdir()
    (plugin / "main.py").write_text("# inert first-party fixture\n", encoding="utf-8")
    (plugin / "metadata.yaml").write_text("name: offline-fixture\n", encoding="utf-8")
    (plugin / "__pycache__").mkdir()
    (plugin / "__pycache__" / "ignored.pyc").write_bytes(b"ignored compiled cache")
    return plugin


@pytest.fixture(autouse=True)
def isolated_native(monkeypatch):
    """Replace unfinished ACL setup and fail closed on any unexpected child process."""
    def private_directory(path):
        """Create only pytest-owned paths without exercising the separate ACL helper."""
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @contextmanager
    def first_install_ports(web, onebot, **kwargs):
        """Preserve requested port selection without binding even a loopback socket."""
        yield {"web_port": web, "onebot_port": onebot}

    monkeypatch.setattr(native, "private_directory", private_directory)
    monkeypatch.setattr(native, "first_install_ports", first_install_ports)
    monkeypatch.setattr(native.secrets, "token_urlsafe", lambda size: PASSWORD)
    monkeypatch.delenv("BOT_WEB_PORT", raising=False)
    monkeypatch.delenv("BOT_ONEBOT_PORT", raising=False)
    monkeypatch.setattr(subprocess, "Popen", Mock(side_effect=AssertionError("unexpected process")))
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=AssertionError("unexpected command")))
    monkeypatch.setattr(native.urllib.request, "build_opener", Mock(side_effect=AssertionError("unexpected network")))


@pytest.fixture
def install(tmp_path):
    """Create one owned external full install using the real settings initializer."""
    data = tmp_path / "external state with spaces"
    native.initialize_settings(data, NAME, dict(MODEL), ["12345", "12345", "67890"], "offline persona")
    return data


def _snapshot(root):
    """Capture file bytes so refusals and repeated setup cannot hide overwrites."""
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def test_initialize_settings_seeds_full_owned_secret_state_once(install):
    """Save full-model configuration, deduplicated owners and the native-only marker."""
    settings = native.load_install(install, NAME)
    assert settings == {"schema_version": 1, "model": MODEL, "owner_qqs": ["12345", "67890"],
                        "persona": "offline persona", "dashboard_password": PASSWORD,
                        "deployment": {"web_port": 6185, "onebot_port": 6199}}
    assert json.loads((install / native.MARKER).read_text(encoding="utf-8")) == {
        "schema_version": 1, "runtime_name": NAME, "runtime": "windows-native-full"}
    assert (install / "higan").is_dir()
    before = _snapshot(install)
    native.initialize_settings(install, NAME, dict(MODEL, model="replacement"), ["99999"], "replacement")
    assert _snapshot(install) == before


@pytest.mark.parametrize("foreign", ["settings.json", ".bootstrap.json", "lite.json", "operator.txt"])
def test_initialize_settings_refuses_foreign_nonempty_state(tmp_path, foreign):
    """Never adopt or reset container, Lite, or unrelated preexisting operator files."""
    data = tmp_path / "foreign"
    data.mkdir()
    (data / foreign).write_text("operator-owned bytes", encoding="utf-8")
    before = _snapshot(data)
    with pytest.raises(ValueError, match="native_refuses_unmanaged_nonempty_directory"):
        native.initialize_settings(data, NAME, dict(MODEL), [], "")
    assert _snapshot(data) == before


def test_existing_runtime_name_mismatch_preserves_all_state(install):
    """An existing native marker authorizes exactly its own runtime name."""
    before = _snapshot(install)
    with pytest.raises(ValueError, match="native_install_marker_mismatch"):
        native.initialize_settings(install, "another-native", dict(MODEL), [], "")
    assert _snapshot(install) == before


@pytest.mark.parametrize("marker", [
    {"schema_version": 1, "runtime_name": NAME},
    {"schema_version": 1, "runtime_name": NAME, "runtime": "lite"},
    {"schema_version": 2, "runtime_name": NAME, "runtime": "windows-native-full"},
])
def test_load_install_rejects_foreign_marker_without_rewriting(install, marker):
    """Do not silently upgrade or adopt a different deployment schema or runtime."""
    (install / native.MARKER).write_text(json.dumps(marker), encoding="utf-8")
    before = _snapshot(install)
    with pytest.raises(ValueError, match="native_install_marker_mismatch"):
        native.load_install(install, NAME)
    assert _snapshot(install) == before


def test_cli_init_uses_external_root_and_key_environment_only(tmp_path, monkeypatch, capsys):
    """Accept keys by environment reference, without argv disclosure or console output."""
    data = tmp_path / "external cli state"
    monkeypatch.setenv("NATIVE_OFFLINE_KEY", MODEL["api_key"])
    argv = ["init", "--data-dir", str(data), "--runtime-name", NAME, "--non-interactive",
            "--model-base", MODEL["base_url"], "--model", MODEL["model"],
            "--api-key-env", "NATIVE_OFFLINE_KEY", "--owner-qq", "12345"]
    assert MODEL["api_key"] not in argv
    assert native.main(argv) == 0
    assert native.load_install(data, NAME)["model"] == MODEL
    assert MODEL["api_key"] not in capsys.readouterr().out


@pytest.mark.parametrize("target", ["root", "child", "ancestor"])
def test_cli_rejects_checkout_or_ancestor_data_before_writes(target):
    """Keep all runtime settings and private data outside the shipped checkout."""
    paths = {"root": native.REPO_ROOT, "child": native.REPO_ROOT / "native-test-forbidden",
             "ancestor": native.REPO_ROOT.parent}
    with pytest.raises(ValueError, match="outside"):
        native.main(["init", "--data-dir", str(paths[target]), "--non-interactive"])


@pytest.mark.parametrize("version", [(3, 12), (3, 13)])
def test_check_engine_windows_supported_python_is_read_only(tmp_path, monkeypatch, capsys, version):
    """Doctor checks bundled files only; no dependency installation or model call occurs."""
    monkeypatch.setattr(native, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(native, "sys", SimpleNamespace(version_info=version))
    data = tmp_path / "uncreated"
    native.check_engine(data)
    assert not data.exists()
    assert "no WSL or Docker required" in capsys.readouterr().out


@pytest.mark.parametrize("platform,version", [("posix", (3, 12)), ("nt", (3, 11)), ("nt", (3, 14))])
def test_check_engine_rejects_unsupported_platform_without_state(tmp_path, monkeypatch, platform, version):
    """Reject incompatible native interpreters before creating or modifying state."""
    monkeypatch.setattr(native, "os", SimpleNamespace(name=platform))
    monkeypatch.setattr(native, "sys", SimpleNamespace(version_info=version))
    data = tmp_path / "uncreated"
    with pytest.raises(ValueError, match="native_full_requires_windows_python_312_or_313"):
        native.check_engine(data, quiet=True)
    assert not data.exists()


def test_prepare_host_delegates_exact_copy_loopback_and_selected_settings(install, monkeypatch):
    """Native setup must reuse defaults and the one shared seeder, not patch Python source."""
    settings = native.load_install(install, NAME)
    root = install / "runtime" / "astrbot"
    defaults = {"dashboard": {"host": "upstream-host", "port": 6185}, "nested": {"preserve": True}}
    original = copy.deepcopy(defaults)
    expected = {"HIGAN_ROOT": str(install / "higan")}
    entry = SimpleNamespace(load_upstream_defaults=Mock(return_value=(defaults, "4.28.1")),
                            prepare=Mock(return_value=expected))
    monkeypatch.setattr(native, "_entrypoint", Mock(return_value=entry))
    monkeypatch.setenv("BOT_WEB_PORT", "26185")
    result = native.prepare_host(install, settings, {"astrbot_root": root})
    entry.load_upstream_defaults.assert_called_once_with(root)
    args, kwargs = entry.prepare.call_args
    assert args[:4] == (install / "settings.json", root / "data", install / "higan", native.REPO_ROOT / "plugin")
    assert args[4] == {"dashboard": {"host": "127.0.0.1", "port": 26185}, "nested": {"preserve": True}}
    assert kwargs == {"plugin_mode": "copy", "dashboard_host": "127.0.0.1", "dashboard_port": 26185}
    assert defaults == original
    assert result == expected
    assert not root.exists()


def test_standalone_seeder_copies_plugin_and_seeds_full_configuration(install, standalone_plugin, seeder):
    """Exercise actual first-party config seeding without importing a live vendor actor."""
    root = install / "runtime" / "astrbot"
    data = root / "data"
    defaults = {"dashboard": {"preserved": "upstream"}, "provider_settings": {"streaming": True},
                "agent_runner": {"runner_type": "local", "config": {"sentinel": True}}}
    original = copy.deepcopy(defaults)
    environment = seeder.prepare(install / "settings.json", data, install / "higan", standalone_plugin,
                                 defaults, plugin_mode="copy", dashboard_host="127.0.0.1", dashboard_port=26185)
    config = json.loads((data / "cmd_config.json").read_text(encoding="utf-8"))
    assert config["provider_sources"] == [{"id": "qhunliv-provider", "type": "openai_chat_completion",
        "provider_type": "chat_completion", "api_base": MODEL["base_url"], "key": [MODEL["api_key"]], "timeout": 120}]
    assert config["provider"] == [{"id": "qhunliv-model", "provider_source_id": "qhunliv-provider",
                                   "model": MODEL["model"], "enable": True}]
    assert config["provider_settings"] == {"streaming": True, "default_provider_id": "qhunliv-model"}
    assert config["agent_runner"]["config"]["model"]["provider_id"] == "qhunliv-model"
    assert config["admins_id"] == ["12345", "67890"]
    assert config["platform"] == []
    assert config["dashboard"] == {"preserved": "upstream", "enable": True, "host": "127.0.0.1", "port": 26185,
        "username": "astrbot", "password": hashlib.md5(PASSWORD.encode("utf-8")).hexdigest(),
        "pbkdf2_password": "", "password_storage_upgraded": False, "password_change_required": False}
    copied = data / "plugins" / seeder.PLUGIN_NAME
    assert copied.is_dir() and not copied.is_symlink()
    assert (copied / "main.py").read_bytes() == (standalone_plugin / "main.py").read_bytes()
    assert not (copied / "__pycache__").exists()
    assert defaults == original
    assert environment == {"HIGAN_ROOT": str(install / "higan"), "ASTRBOT_ROOT": str(root),
                           "HIGAN_SETTINGS_FILE": str(install / "settings.json"),
                           "HIGAN_OWNER_QQS": json.dumps(["12345", "67890"])}


def test_standalone_copy_is_idempotent_and_preserves_operator_config(install, standalone_plugin, seeder):
    """A verified same-content copy must not reset later operator model/dashboard changes."""
    data = install / "runtime" / "astrbot" / "data"
    args = (install / "settings.json", data, install / "higan", standalone_plugin, {})
    seeder.prepare(*args, plugin_mode="copy", dashboard_host="127.0.0.1", dashboard_port=26185)
    config_path = data / "cmd_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["dashboard"]["password"] = "operator-changed-hash"
    config["provider"][0]["model"] = "operator-selected-model"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    before = _snapshot(data)
    seeder.prepare(*args, plugin_mode="copy", dashboard_host="127.0.0.1", dashboard_port=26186)
    assert _snapshot(data) == before


@pytest.mark.parametrize("changed_tree", ["source", "copy", "extra-copy-file", "missing-copy"])
def test_standalone_copy_rejects_content_mismatch_without_overwrite(install, standalone_plugin, seeder, changed_tree):
    """Reject both source upgrades and local copy tampering instead of silently repairing."""
    data = install / "runtime" / "astrbot" / "data"
    args = (install / "settings.json", data, install / "higan", standalone_plugin, {})
    seeder.prepare(*args, plugin_mode="copy", dashboard_host="127.0.0.1", dashboard_port=26185)
    copied = data / "plugins" / seeder.PLUGIN_NAME
    if changed_tree == "source":
        (standalone_plugin / "main.py").write_text("# upgraded source\n", encoding="utf-8")
    elif changed_tree == "copy":
        (copied / "main.py").write_text("# locally modified copy\n", encoding="utf-8")
    elif changed_tree == "extra-copy-file":
        (copied / "foreign.txt").write_text("foreign", encoding="utf-8")
    else:
        # Rename an inert fixture file; never delete an existing repository script.
        (copied / "main.py").rename(copied / "moved-main.txt")
    before = _snapshot(data)
    with pytest.raises(ValueError, match="controlled update"):
        seeder.prepare(*args, plugin_mode="copy", dashboard_host="127.0.0.1", dashboard_port=26185)
    assert _snapshot(data) == before


def test_standalone_seeder_refuses_foreign_host_state(install, standalone_plugin, seeder):
    """Do not take ownership of a preexisting upstream data directory."""
    data = install / "foreign host"
    data.mkdir()
    (data / "cmd_config.json").write_text("operator config", encoding="utf-8")
    before = _snapshot(data)
    with pytest.raises(ValueError, match="not fresh"):
        seeder.prepare(install / "settings.json", data, install / "higan", standalone_plugin, {}, plugin_mode="copy")
    assert _snapshot(data) == before


@pytest.fixture
def fake_launch(install, monkeypatch):
    """Stub asset provision, host preparation and process handles; perform no installation."""
    assets = {"python": install / "runtime" / ".venv" / "Scripts" / "python.exe",
              "astrbot_root": install / "runtime" / "astrbot",
              "dashboard": install / "runtime" / "astrbot" / "astrbot" / "dashboard" / "dist"}
    child = Mock()
    child.poll.return_value = None
    popen = Mock(return_value=child)
    prepare = Mock(return_value=assets)
    host = Mock(return_value={})
    dashboard = Mock()
    held = set()
    lock_events = []

    @contextmanager
    def instance_lock(data, *, lock_name="instance.lock"):
        """Model independent nonblocking lock names and expose their exact lifetimes."""
        assert data == install
        if lock_name in held:
            raise ValueError("native_instance_already_running")
        (data / "run").mkdir(parents=True, exist_ok=True)
        held.add(lock_name)
        lock_events.append(("acquire", lock_name))
        try:
            yield
        finally:
            held.remove(lock_name)
            lock_events.append(("release", lock_name))

    monkeypatch.setattr(native, "instance_lock", instance_lock)
    monkeypatch.setattr(native, "dashboard_ready", dashboard)
    monkeypatch.setattr(native_assets, "prepare_assets", prepare)
    monkeypatch.setattr(native, "prepare_host", host)
    monkeypatch.setattr(native, "check_engine", Mock())
    monkeypatch.setattr(native.subprocess, "Popen", popen)
    monkeypatch.setattr(native.time, "sleep", Mock())
    return SimpleNamespace(assets=assets, child=child, popen=popen, prepare=prepare, host=host,
                           dashboard=dashboard, held=held, lock_events=lock_events)


def test_up_launches_private_venv_full_serve_not_lite_or_container(install, fake_launch, monkeypatch):
    """The only new child is unchanged full runtime through the owned venv and _serve."""
    health = Mock(side_effect=[OSError("offline"), {"ok": True, "pid": 123456}])
    monkeypatch.setattr(native, "health", health)
    native.up(install, NAME, wait_timeout=10)
    fake_launch.prepare.assert_called_once_with(install, native.REPO_ROOT)
    fake_launch.host.assert_called_once_with(install, native.load_install(install, NAME), fake_launch.assets)
    args, kwargs = fake_launch.popen.call_args
    assert args == ([str(fake_launch.assets["python"]), str(Path(native.__file__).resolve()), "_serve",
                     "--data-dir", str(install), "--runtime-name", NAME],)
    assert kwargs["cwd"] == fake_launch.assets["astrbot_root"]
    assert kwargs["shell"] is False
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stdout"] is kwargs["stderr"]
    assert kwargs["creationflags"] == (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                                        | getattr(subprocess, "DETACHED_PROCESS", 0))
    assert kwargs["env"]["HIGAN_ROOT"] == str(install / "higan")
    assert kwargs["env"]["HIGAN_SETTINGS_FILE"] == str(install / "settings.json")
    assert kwargs["env"]["HIGAN_NATIVE_DATA"] == str(install)
    assert kwargs["env"]["ASTRBOT_ROOT"] == str(fake_launch.assets["astrbot_root"])
    assert MODEL["api_key"] not in repr(args)
    assert PASSWORD not in repr(args)
    assert all(word not in repr(args).lower() for word in ("lite.py", "bootstrap.py", "wsl.exe", "docker"))
    fake_launch.child.terminate.assert_not_called()
    fake_launch.child.kill.assert_not_called()
    assert health.call_count == 2
    fake_launch.dashboard.assert_called_once_with(install, NAME)


def test_up_holds_setup_and_host_locks_in_safe_order(install, fake_launch, monkeypatch):
    """Protect all provisioning, release host lock for the child, and retain setup through readiness."""
    def prepare_assets(data, repo):
        """Pip and copied host preparation require both exclusive ownership locks."""
        assert fake_launch.held == {"setup.lock", "instance.lock"}
        return fake_launch.assets

    def prepare_host(data, settings, assets):
        """Config seeding is protected from a live or stopping host as well as other installers."""
        assert fake_launch.held == {"setup.lock", "instance.lock"}

    def launch(*args, **kwargs):
        """The new child must be able to acquire its own host lock before becoming healthy."""
        assert fake_launch.held == {"setup.lock"}
        return fake_launch.child

    def health(data, name):
        """Readiness checks stay inside setup ownership, while the initial probe stays read-only."""
        if not fake_launch.lock_events:
            assert not fake_launch.held
            raise OSError("not yet started")
        assert fake_launch.held == {"setup.lock"}
        return {"ok": True, "pid": 123456}

    fake_launch.prepare.side_effect = prepare_assets
    fake_launch.host.side_effect = prepare_host
    fake_launch.popen.side_effect = launch
    monkeypatch.setattr(native, "health", health)
    native.up(install, NAME)
    assert fake_launch.lock_events == [("acquire", "setup.lock"), ("acquire", "instance.lock"),
                                     ("release", "instance.lock"), ("release", "setup.lock")]
    assert not fake_launch.held


@pytest.mark.parametrize("busy_lock", ["setup.lock", "instance.lock"])
def test_up_refuses_busy_setup_or_live_unhealthy_host_before_mutating(install, fake_launch, monkeypatch, busy_lock):
    """No dependency/config write or spawn may race another wizard or an unhealthy live host."""
    monkeypatch.setattr(native, "health", Mock(side_effect=OSError("unconfirmed")))
    fake_launch.held.add(busy_lock)
    with pytest.raises(ValueError, match="native_instance_already_running"):
        native.up(install, NAME)
    fake_launch.prepare.assert_not_called()
    fake_launch.host.assert_not_called()
    fake_launch.popen.assert_not_called()
    assert fake_launch.held == {busy_lock}
    fake_launch.held.remove(busy_lock)


def test_up_releases_both_locks_after_provisioning_failure(install, fake_launch, monkeypatch):
    """An installation failure leaves neither ownership lock held and never spawns a child."""
    monkeypatch.setattr(native, "health", Mock(side_effect=OSError("not started")))
    fake_launch.prepare.side_effect = RuntimeError("offline provision failure")
    with pytest.raises(RuntimeError, match="offline provision failure"):
        native.up(install, NAME)
    assert not fake_launch.held
    assert fake_launch.lock_events[-2:] == [("release", "instance.lock"), ("release", "setup.lock")]
    fake_launch.popen.assert_not_called()


def test_up_reuses_authenticated_ready_instance_without_launch(install, fake_launch, monkeypatch):
    """An already healthy owned full instance needs no install or duplicate child."""
    monkeypatch.setattr(native, "health", Mock(return_value={"ok": True, "pid": 123456}))
    native.up(install, NAME)
    fake_launch.prepare.assert_not_called()
    fake_launch.host.assert_not_called()
    fake_launch.popen.assert_not_called()
    fake_launch.dashboard.assert_called_once_with(install, NAME)


def test_up_preserves_live_owned_host_when_dashboard_is_not_ready(install, fake_launch, monkeypatch):
    """A live authenticated host must not be provisioned or duplicated for a dashboard failure."""
    health = Mock(return_value={"ok": True, "pid": 123456})
    monkeypatch.setattr(native, "health", health)
    fake_launch.dashboard.side_effect = ValueError("native_dashboard_not_ready")
    before = _snapshot(install)
    with pytest.raises(ValueError, match="native_dashboard_not_ready"):
        native.up(install, NAME)
    health.assert_called_once_with(install, NAME)
    fake_launch.dashboard.assert_called_once_with(install, NAME)
    fake_launch.prepare.assert_not_called()
    fake_launch.host.assert_not_called()
    fake_launch.popen.assert_not_called()
    native.check_engine.assert_not_called()
    assert _snapshot(install) == before


def test_dashboard_ready_uses_saved_loopback_port_html_and_no_proxy(install, monkeypatch):
    """Inspect a bounded HTML response from this install, bypassing ambient proxies."""
    path = install / "settings.json"
    settings = native.load_install(install, NAME)
    settings["deployment"]["web_port"] = 26185
    path.write_text(json.dumps(settings), encoding="utf-8")
    response = Mock()
    response.getcode.return_value = 200
    response.headers = {"Content-Type": "text/html; charset=utf-8"}
    response.read.return_value = b"<!doctype html><HTML><body>offline dashboard</body></HTML>"
    context = Mock()
    context.__enter__ = Mock(return_value=response)
    context.__exit__ = Mock(return_value=False)
    opener = Mock()
    opener.open.return_value = context
    build = Mock(return_value=opener)
    monkeypatch.setattr(native.urllib.request, "build_opener", build)
    native.dashboard_ready(install, NAME)
    opener.open.assert_called_once_with("http://127.0.0.1:26185/", timeout=5)
    response.read.assert_called_once_with(65536)
    handler = build.call_args.args[0]
    assert isinstance(handler, native.urllib.request.ProxyHandler)
    assert handler.proxies == {}
    assert MODEL["api_key"] not in repr(opener.open.call_args)
    assert PASSWORD not in repr(opener.open.call_args)


@pytest.mark.parametrize("status,content_type,body", [
    (503, "text/html", b"<html>not ready</html>"),
    (200, "application/json", b'{"ok": true}'),
    (200, "text/html", b"upstream without dashboard"),
])
def test_dashboard_ready_rejects_invalid_content_without_success(install, monkeypatch, status, content_type, body):
    """An open port, non-HTML API, or error page cannot establish dashboard readiness."""
    response = Mock()
    response.getcode.return_value = status
    response.headers = {"Content-Type": content_type}
    response.read.return_value = body
    context = Mock()
    context.__enter__ = Mock(return_value=response)
    context.__exit__ = Mock(return_value=False)
    opener = Mock()
    opener.open.return_value = context
    monkeypatch.setattr(native.urllib.request, "build_opener", Mock(return_value=opener))
    with pytest.raises(ValueError, match="native_dashboard_not_ready"):
        native.dashboard_ready(install, NAME)
    opener.open.assert_called_once_with("http://127.0.0.1:6185/", timeout=5)


@pytest.mark.parametrize("failure", ["timeout", "exited"])
def test_up_failure_does_not_kill_or_signal_unknown_process(install, fake_launch, monkeypatch, failure):
    """Startup uncertainty may report failure but never kill by port, PID guess or process name."""
    monkeypatch.setattr(native, "health", Mock(side_effect=OSError("unconfirmed")))
    kill = Mock(side_effect=AssertionError("must not signal unconfirmed process"))
    monkeypatch.setattr(native.os, "kill", kill)
    if failure == "exited":
        fake_launch.child.poll.return_value = 1
        monkeypatch.setattr(native.time, "monotonic", Mock(side_effect=[0, 0]))
        expected = "native_host_exited_check_private_logs"
    else:
        monkeypatch.setattr(native.time, "monotonic", Mock(side_effect=[0, 2]))
        expected = "native_startup_not_confirmed_process_not_killed"
    with pytest.raises(ValueError, match=expected):
        native.up(install, NAME, wait_timeout=1)
    fake_launch.child.terminate.assert_not_called()
    fake_launch.child.kill.assert_not_called()
    kill.assert_not_called()
    assert not fake_launch.held
    assert fake_launch.lock_events[-1] == ("release", "setup.lock")


def test_serve_executes_exact_full_host_main_with_dashboard_under_instance_lock(install, monkeypatch):
    """Import unchanged upstream entry and run its real lifecycle under the owned lock."""
    root = install / "runtime" / "astrbot"
    dashboard = root / "astrbot" / "dashboard" / "dist"
    assets = {"ready": True, "astrbot_root": root, "dashboard": dashboard}
    inspect = Mock(return_value=assets)
    events = []
    broker = object()
    logger = object()
    manager = SimpleNamespace(set_queue_handler=Mock())

    async def main_async(webui):
        """Represent upstream main without initializing any vendor dependencies."""
        assert events == ["locked", "environment-checked", "broker-created"]
        assert webui == str(dashboard)
        assert main_async.__globals__["log_broker"] is broker
        events.append("upstream-main")

    def check_env():
        """Require the original upstream environment check before lifecycle startup."""
        assert events == ["locked"]
        events.append("environment-checked")

    def log_broker():
        """Provide the exact broker instance wired into upstream globals and logging."""
        events.append("broker-created")
        return broker

    host = {"check_env": check_env, "LogBroker": log_broker, "LogManager": manager,
            "logger": logger, "main_async": main_async}

    @contextmanager
    def instance_lock(data):
        """Expose lock lifetime without requiring native handles on non-Windows CI."""
        assert data == install
        events.append("locked")
        yield
        events.append("unlocked")

    def import_full_main(path, *, run_name):
        """Load upstream without triggering its unwrapped __main__ block."""
        assert events == ["locked"]
        assert path == str(root / "main.py")
        assert run_name == "qhunliv_native_upstream"
        assert native.sys.argv == [str(root / "main.py"), "--webui-dir", str(dashboard)]
        return host

    run_path = Mock(side_effect=import_full_main)
    monkeypatch.setattr(native_assets, "inspect_assets", inspect)
    monkeypatch.setattr(native, "instance_lock", instance_lock)
    monkeypatch.setattr(native, "os", SimpleNamespace(name="nt", chdir=Mock(), getpid=lambda: 345678, environ={}))
    monkeypatch.setattr(native, "sys", SimpleNamespace(path=list(sys.path), argv=[], modules={}))
    monkeypatch.setattr(native.signal, "signal", Mock())
    monkeypatch.setattr(native.signal, "SIGBREAK", 21, raising=False)
    monkeypatch.setattr(native.runpy, "run_path", run_path)
    monkeypatch.setitem(main_async.__globals__, "log_broker", None)
    native.serve(install, NAME)
    inspect.assert_called_once_with(install, native.REPO_ROOT)
    run_path.assert_called_once()
    native.os.chdir.assert_called_once_with(root)
    manager.set_queue_handler.assert_called_once_with(logger, broker)
    assert events == ["locked", "environment-checked", "broker-created", "upstream-main", "unlocked"]
    assert native.sys.path[0] == str(root)
    assert native.os.environ == {"HIGAN_ROOT": str(install / "higan"), "ASTRBOT_ROOT": str(root),
                                 "HIGAN_SETTINGS_FILE": str(install / "settings.json")}
    assert "qhunliv_native_host_control" not in native.sys.modules


def test_run_host_exposes_exact_control_then_cancels_and_cleans_up(install, monkeypatch):
    """Authenticated stop callback cancels upstream once and awaits its final cleanup."""
    modules = {}
    events = []
    dashboard = install / "runtime" / "astrbot" / "astrbot" / "dashboard" / "dist"
    monkeypatch.setattr(native, "sys", SimpleNamespace(modules=modules))
    monkeypatch.setattr(native, "os", SimpleNamespace(getpid=lambda: 345678))

    async def main_async(webui):
        """Keep an inert host pending until the registered shutdown callback is invoked."""
        assert webui == str(dashboard)
        control = modules["qhunliv_native_host_control"]
        assert control.root == str(install / "higan")
        assert control.pid == 345678
        assert set(vars(control)) == {"root", "pid", "request_stop"}
        events.append("started")
        # Schedule rather than directly cancel to exercise the real Event waiter.
        asyncio.get_running_loop().call_soon(control.request_stop)
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            events.append("cancelled")
            raise
        finally:
            events.append("cleanup")

    asyncio.run(native.run_host(main_async, dashboard, install))
    assert events == ["started", "cancelled", "cleanup"]
    assert "qhunliv_native_host_control" not in modules


def test_run_host_cleans_control_when_upstream_fails(install, monkeypatch):
    """Upstream exceptions propagate rather than being mistaken for graceful readiness."""
    modules = {}
    monkeypatch.setattr(native, "sys", SimpleNamespace(modules=modules))

    async def main_async(webui):
        """Fail before becoming ready without touching a real host or model."""
        assert "qhunliv_native_host_control" in modules
        raise ValueError("offline upstream initialization failed")

    with pytest.raises(ValueError, match="offline upstream initialization failed"):
        asyncio.run(native.run_host(main_async, install / "dashboard", install))
    assert "qhunliv_native_host_control" not in modules


def test_serve_refuses_unready_assets_before_running_code(install, monkeypatch):
    """A missing private full host cannot fall back to Lite or the checkout source."""
    monkeypatch.setattr(native_assets, "inspect_assets", Mock(return_value={"ready": False}))
    run_path = Mock(side_effect=AssertionError("must not execute source"))
    monkeypatch.setattr(native.runpy, "run_path", run_path)
    with pytest.raises(ValueError, match="native_assets_not_ready"):
        native.serve(install, NAME)
    run_path.assert_not_called()


def test_health_uses_authenticated_selected_plugin_not_port_probe(install, monkeypatch):
    """Only the owned plugin health payload counts as full-runtime readiness."""
    request = Mock(return_value={"ok": True, "pid": 123456})
    monkeypatch.setattr(native, "local_request", request)
    assert native.health(install, NAME) == {"ok": True, "pid": 123456}
    request.assert_called_once_with(install, {"op": "health"}, timeout=5)
    request.return_value = {"ok": False}
    with pytest.raises(ValueError, match="native_plugin_unhealthy"):
        native.health(install, NAME)


def test_local_request_uses_this_install_operator_transport(install, monkeypatch):
    """Reuse the existing same-instance client instead of adding a direct model actor."""
    from tools import local_runtime_client
    request = Mock(return_value={"ok": True})
    monkeypatch.setattr(local_runtime_client, "request", request)
    payload = {"op": "run", "prompt": "offline question"}
    assert native.local_request(install, payload, timeout=None) == {"ok": True}
    request.assert_called_once_with(install / "higan" / "run" / "local.sock", payload, timeout=None)


@pytest.mark.parametrize("pid", [None, True, 0, -1, "123", "self"])
def test_stop_refuses_unconfirmed_pid_without_signals(install, monkeypatch, pid):
    """Health cannot authorize a missing, ambiguous, invalid, or own-process PID."""
    current = 234567
    monkeypatch.setattr(native, "health", Mock(return_value={"ok": True, "pid": current if pid == "self" else pid}))
    kill = Mock(side_effect=AssertionError("unconfirmed process"))
    request = Mock(side_effect=AssertionError("unconfirmed shutdown"))
    monkeypatch.setattr(native, "os", SimpleNamespace(name="nt", getpid=lambda: current, kill=kill))
    monkeypatch.setattr(native, "local_request", request)
    with pytest.raises(ValueError, match="native_process_identity_unconfirmed"):
        native.stop(install, NAME)
    kill.assert_not_called()
    request.assert_not_called()


def test_stop_requests_authenticated_shutdown_without_os_signals(install, monkeypatch):
    """Stop is accepted only by the same authenticated full plugin, never os.kill."""
    health = Mock(return_value={"ok": True, "pid": 345678})
    monkeypatch.setattr(native, "health", health)
    kill = Mock(side_effect=AssertionError("no console or process signals"))
    request = Mock(return_value={"ok": True, "status": "stopping", "pid": 345678})
    monkeypatch.setattr(native, "os", SimpleNamespace(name="nt", getpid=lambda: 234567, kill=kill))
    monkeypatch.setattr(native, "local_request", request)
    native.stop(install, NAME)
    health.assert_called_once_with(install, NAME)
    request.assert_called_once_with(install, {"op": "shutdown"}, timeout=5)
    kill.assert_not_called()


@pytest.mark.parametrize("response", [
    {"ok": False, "status": "stopping", "pid": 345678},
    {"ok": 1, "status": "stopping", "pid": 345678},
    {"ok": True, "status": "running", "pid": 345678},
    {"ok": True, "status": "stopping", "pid": 999999},
    {"ok": True, "status": "stopping"},
    {},
])
def test_stop_refuses_unconfirmed_shutdown_reply_without_signals(install, monkeypatch, response):
    """A failed or mismatched reply never authorizes killing any process as fallback."""
    monkeypatch.setattr(native, "health", Mock(return_value={"ok": True, "pid": 345678}))
    kill = Mock(side_effect=AssertionError("must not signal unknown process"))
    monkeypatch.setattr(native, "os", SimpleNamespace(name="nt", getpid=lambda: 234567, kill=kill))
    request = Mock(return_value=response)
    monkeypatch.setattr(native, "local_request", request)
    with pytest.raises(ValueError, match="native_stop_not_confirmed"):
        native.stop(install, NAME)
    request.assert_called_once_with(install, {"op": "shutdown"}, timeout=5)
    kill.assert_not_called()


@pytest.mark.parametrize("command", ["status", "health"])
def test_cli_readiness_requires_full_plugin_and_dashboard(install, monkeypatch, capsys, command):
    """Do not publish successful CLI readiness while the owned dashboard is unavailable."""
    response = {"ok": True, "pid": 345678}
    health = Mock(return_value=response)
    dashboard = Mock(side_effect=ValueError("native_dashboard_not_ready"))
    monkeypatch.setattr(native, "health", health)
    monkeypatch.setattr(native, "dashboard_ready", dashboard)
    argv = [command, "--data-dir", str(install), "--runtime-name", NAME]
    with pytest.raises(ValueError, match="native_dashboard_not_ready"):
        native.main(argv)
    assert capsys.readouterr().out == ""
    health.assert_called_once_with(install, NAME)
    dashboard.assert_called_once_with(install, NAME)
    dashboard.side_effect = None
    assert native.main(argv) == 0
    assert json.loads(capsys.readouterr().out) == response


def test_cli_stop_reports_accepted_not_completed_shutdown(install, monkeypatch, capsys):
    """The CLI acknowledges authenticated admission without claiming all teardown has finished."""
    monkeypatch.setattr(native, "health", Mock(return_value={"ok": True, "pid": 345678}))
    monkeypatch.setattr(native, "os", SimpleNamespace(name="nt", getpid=lambda: 234567))
    request = Mock(return_value={"ok": True, "status": "stopping", "pid": 345678})
    monkeypatch.setattr(native, "local_request", request)
    assert native.main(["stop", "--data-dir", str(install), "--runtime-name", NAME]) == 0
    output = capsys.readouterr().out
    assert "stop accepted" in output and "data retained" in output
    assert "fully stopped" not in output and "stop completed" not in output
    request.assert_called_once_with(install, {"op": "shutdown"}, timeout=5)


def test_cli_info_hides_model_key_and_password_unless_explicit(install, capsys):
    """Normal metadata stays nonsecret; explicit disclosure is dashboard-only."""
    base = ["info", "--data-dir", str(install), "--runtime-name", NAME, "--json"]
    assert native.main(base) == 0
    output = capsys.readouterr().out
    value = json.loads(output)
    assert value["runtime"] == "windows-native-full"
    assert value["dashboard_url"] == "http://127.0.0.1:6185"
    assert value["readiness_checked"] is False
    assert MODEL["api_key"] not in output and PASSWORD not in output
    assert native.main(base + ["--show-password"]) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["initial_dashboard_password"] == PASSWORD
    assert MODEL["api_key"] not in output


def test_cli_chat_forwards_stdin_once_to_full_plugin(install, monkeypatch, capsys):
    """Chat performs the real same-instance operation, not a hardcoded success response."""
    monkeypatch.setattr(native.sys, "stdin", io.StringIO("offline prompt with spaces"))
    response = {"ok": True, "outputs": [{"components": [{"text": "actual mocked full reply"}]}]}
    request = Mock(return_value=response)
    monkeypatch.setattr(native, "local_request", request)
    assert native.main(["chat", "--data-dir", str(install), "--runtime-name", NAME]) == 0
    request.assert_called_once_with(install, {"op": "run", "prompt": "offline prompt with spaces"}, timeout=None)
    assert json.loads(capsys.readouterr().out) == response


def test_cli_wizard_delegates_native_core_and_no_browser(tmp_path, monkeypatch):
    """Pass the native module into the shared wizard without launching an HTTP server."""
    serve = Mock()
    monkeypatch.setattr(setup_wizard, "serve", serve)
    data = tmp_path / "external wizard"
    assert native.main(["wizard", "--data-dir", str(data), "--runtime-name", NAME, "--no-browser"]) == 0
    serve.assert_called_once_with(native, data, NAME, open_browser=False)
    assert not data.exists()


def test_wizard_controller_delegates_native_cli_without_keys_on_argv(install, monkeypatch):
    """Shared UI must use native CLI_FILE, never container bootstrap, for this core."""
    controller = setup_wizard.Controller(native, install, NAME)
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(setup_wizard.subprocess, "run", run)
    controller._command("health", timeout=30)
    args, kwargs = run.call_args
    assert args == ([sys.executable, str(native.REPO_ROOT / "tools" / "native_runtime.py"), "health",
                     "--data-dir", str(install), "--runtime-name", NAME, "--non-interactive"],)
    assert kwargs["shell"] is False and kwargs["check"] is True and kwargs["timeout"] == 30
    assert kwargs["stdout"] == subprocess.DEVNULL and kwargs["stderr"] == subprocess.DEVNULL
    assert MODEL["api_key"] not in repr(args) and PASSWORD not in repr(args)
    snapshot = json.dumps(controller.snapshot(), ensure_ascii=False)
    assert "windows-native" in snapshot
    assert MODEL["api_key"] not in snapshot and PASSWORD not in snapshot


def test_wizard_chat_uses_native_stdin_and_redacts_visible_secrets(install, monkeypatch):
    """Only actual visible reply components establish model readiness, with secrets removed."""
    controller = setup_wizard.Controller(native, install, NAME)
    reply = {"ok": True, "outputs": [{"components": [
        {"text": "real full reply " + MODEL["api_key"] + " / " + PASSWORD},
        {"type": "image", "url": "https://offline.invalid/private"}]}]}
    run = Mock(return_value=SimpleNamespace(stdout=json.dumps(reply).encode("utf-8")))
    monkeypatch.setattr(setup_wizard.subprocess, "run", run)
    prompt = "deliberate offline chat"
    controller._worker("chat", {"prompt": prompt})
    args, kwargs = run.call_args
    assert args[0][1:3] == [str(native.REPO_ROOT / "tools" / "native_runtime.py"), "chat"]
    assert kwargs["input"] == prompt.encode("utf-8")
    assert prompt not in repr(args) and MODEL["api_key"] not in repr(args)
    snapshot = controller.snapshot()
    assert snapshot["model"] == "ready" and snapshot["qq"] == "unverified"
    assert snapshot["answer"].startswith("real full reply ")
    assert MODEL["api_key"] not in json.dumps(snapshot) and PASSWORD not in json.dumps(snapshot)
    assert "https://offline.invalid/private" not in snapshot["answer"]
    assert run.call_count == 1


@pytest.mark.parametrize("failure", ["exception", "empty", "not-ok"])
def test_wizard_failed_chat_is_not_retried_or_secret_logged(install, monkeypatch, failure):
    """Unknown model outcomes remain failed, without replaying charged requests or raw errors."""
    controller = setup_wizard.Controller(native, install, NAME)
    if failure == "exception":
        run = Mock(side_effect=RuntimeError(MODEL["api_key"] + PASSWORD))
    else:
        response = {"ok": failure == "empty", "outputs": []}
        run = Mock(return_value=SimpleNamespace(stdout=json.dumps(response).encode("utf-8")))
    monkeypatch.setattr(setup_wizard.subprocess, "run", run)
    controller._worker("chat", {"prompt": "offline test"})
    snapshot = controller.snapshot()
    assert snapshot["model"] == "failed"
    assert MODEL["api_key"] not in json.dumps(snapshot) and PASSWORD not in json.dumps(snapshot)
    assert run.call_count == 1


@pytest.mark.skipif(sys.platform != "win32", reason="Actual Windows DACL verification requires Windows")
def test_real_windows_fresh_settings_directory_is_private_before_secrets(tmp_path, monkeypatch):
    """Exercise only native ACL utilities on fresh state, never a model or installer."""
    from plugin import local_runtime
    monkeypatch.setattr(native, "private_directory", REAL_PRIVATE_DIRECTORY)
    # The helper's only allowed subprocesses are finite System32 identity/DACL tools.
    def acl_command(argv, **kwargs):
        """Allow only the two known OS security utilities; reject every other process."""
        assert Path(argv[0]).name.lower() in {"whoami.exe", "icacls.exe"}
        return REAL_SUBPROCESS_RUN(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", acl_command)
    monkeypatch.setattr(subprocess, "Popen", REAL_SUBPROCESS_POPEN)
    data = tmp_path / "new scaffolding" / "private full state"
    native.initialize_settings(data, NAME, dict(MODEL), [], "")
    local_runtime._require_private_windows_path(data, directory=True)
    local_runtime._require_private_windows_path(data / "higan", directory=True)
    # Settings inherit the private directory grants; unlike endpoint capabilities,
    # the shared bootstrap writer does not create a separately protected file DACL.
    assert native.load_install(data, NAME)["model"] == MODEL


@pytest.mark.skipif(sys.platform != "win32", reason="Native byte-range instance locks require Windows")
def test_real_windows_instance_lock_rejects_second_owner_then_releases(install):
    """An OS lock prevents duplicate full hosts and is reusable after the first exits."""
    with native.instance_lock(install):
        with pytest.raises(ValueError, match="native_instance_already_running"):
            with native.instance_lock(install):
                pytest.fail("a second host acquired the same native state")
    with native.instance_lock(install):
        # Windows byte locks also deny reading the locked byte from another handle.
        assert (install / "run" / "instance.lock").is_file()
    assert (install / "run" / "instance.lock").read_bytes() == b"0"


@pytest.mark.skipif(sys.platform != "win32", reason="Native byte-range setup locks require Windows")
def test_real_windows_setup_lock_is_independent_and_rejects_duplicate_owner(install):
    """Setup and host ownership are independent, nonblocking, and both release reliably."""
    with native.instance_lock(install, lock_name="setup.lock"):
        with native.instance_lock(install):
            with pytest.raises(ValueError, match="native_instance_already_running"):
                with native.instance_lock(install, lock_name="setup.lock"):
                    pytest.fail("a duplicate installer acquired setup ownership")
    with native.instance_lock(install, lock_name="setup.lock"):
        pass
    assert (install / "run" / "setup.lock").read_bytes() == b"0"


@pytest.mark.parametrize("lock_name", ["foreign.lock", "../setup.lock", "", "instance.lock/child"])
def test_instance_lock_refuses_nonallowlisted_names_before_writing(tmp_path, lock_name):
    """The shared lock helper must not expose arbitrary file creation through its selector."""
    data = tmp_path / "uncreated"
    with pytest.raises(ValueError, match="native_invalid_lock_name"):
        with native.instance_lock(data, lock_name=lock_name):
            pytest.fail("an untrusted lock path was accepted")
    assert not data.exists()


def test_all_test_helpers_and_cases_have_docstrings():
    """Keep the repository's function documentation convention enforceable in new tests."""
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    assert all(ast.get_docstring(node) for node in ast.walk(tree)
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))
