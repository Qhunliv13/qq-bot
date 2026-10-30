"""Run the complete bundled Bot natively on Windows, without WSL or Docker.

The existing setup UI, settings validation, full upstream host and plugin are
reused. Runtime packages live in a private external venv, never global Python.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import runpy
import secrets
import signal
import subprocess
import sys
import time
import urllib.request
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from tools import bootstrap  # noqa: E402
from tools.setup_inputs import collect_model_inputs  # noqa: E402
from tools.setup_ports import first_install_ports  # noqa: E402

CLI_FILE = "native_runtime.py"
DEPLOYMENT_MODE = "windows-native"
MARKER = ".native-bootstrap.json"
DEFAULT_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "QhunlivBot" / "Full"
# Reuse setup UI's port contract without invoking Docker or maintaining new ports.
docker_environment = bootstrap.docker_environment
validate_model_field = bootstrap.validate_model_field
validate_owner_ids = bootstrap.validate_owner_ids


def private_directory(path):
    """Create a private owned Windows directory before writing any capability or key."""
    from plugin.local_runtime import ensure_private_windows_path
    if os.name == "nt":
        path = Path(path)
        # Only the selected leaf gets a private DACL. Missing non-secret parent
        # scaffolding is created normally; never rewrite an existing ancestor ACL.
        path.parent.mkdir(parents=True, exist_ok=True)
        return ensure_private_windows_path(path)
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def load_install(data, name):
    """Refuse adopting container/Lite state or overwriting an existing operator config."""
    if not data.is_dir() or not (data / MARKER).is_file():
        raise ValueError("native_install_not_initialized")
    private_directory(data)
    marker = json.loads((data / MARKER).read_text(encoding="utf-8"))
    if marker != {"schema_version": 1, "runtime_name": name, "runtime": "windows-native-full"}:
        raise ValueError("native_install_marker_mismatch")
    return bootstrap.validate_settings(json.loads((data / "settings.json").read_text(encoding="utf-8")))


def initialize_settings(data, name, model, owners, persona):
    """Seed isolated native settings once; saved installs are validated, never reset."""
    if (data / MARKER).exists():
        load_install(data, name)
        return
    if data.exists() and any(data.iterdir()):
        raise ValueError("native_refuses_unmanaged_nonempty_directory")
    settings = bootstrap.validate_settings({"schema_version": 1, "model": model,
        "owner_qqs": list(dict.fromkeys(owners)), "persona": persona,
        "dashboard_password": secrets.token_urlsafe(24),
        "deployment": {"web_port": 6185, "onebot_port": 6199}})
    json.dumps(settings, ensure_ascii=False).encode("utf-8")
    ports = docker_environment(data, settings)
    with first_install_ports(int(ports["BOT_WEB_PORT"]), int(ports["BOT_ONEBOT_PORT"]),
            web_explicit="BOT_WEB_PORT" in os.environ, onebot_explicit="BOT_ONEBOT_PORT" in os.environ) as selected:
        settings["deployment"] = selected
        private_directory(data)
        private_directory(data / "higan")
        bootstrap.private_write(data / "settings.json", json.dumps(settings, ensure_ascii=False, indent=2))
        bootstrap.private_write(data / MARKER, json.dumps({"schema_version": 1, "runtime_name": name,
                                                         "runtime": "windows-native-full"}))


def check_engine(data, *, quiet=False):
    """Check native prerequisites without installing, starting, or requesting a model."""
    if os.name != "nt" or not (3, 12) <= sys.version_info[:2] < (3, 14):
        raise ValueError("native_full_requires_windows_python_312_or_313")
    for path in ("vendor/astrbot/main.py", "vendor/astrbot/pyproject.toml", "plugin/main.py"):
        if not (REPO_ROOT / path).is_file():
            raise ValueError("native_bundled_source_missing")
    if data.exists():
        private_directory(data)
    if not quiet:
        print("Windows native full runtime prerequisites OK; no WSL or Docker required.")


def _entrypoint():
    """Reuse the shared frozen default/config seeder without importing a live host."""
    spec = importlib.util.spec_from_file_location("qhunliv_native_entrypoint", REPO_ROOT / "deploy" / "entrypoint.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare_host(data, settings, assets):
    """Configure the full native host with verified plugin copy instead of admin symlinks."""
    root = Path(assets["astrbot_root"])
    entry = _entrypoint()
    defaults, _version = entry.load_upstream_defaults(root)
    ports = docker_environment(data, settings)
    defaults = copy.deepcopy(defaults)
    defaults.setdefault("dashboard", {}).update(host="127.0.0.1", port=int(ports["BOT_WEB_PORT"]))
    # Shared seeder owns provider/admin/password semantics, with native transport
    # injected explicitly rather than mutating the vendored runtime.
    return entry.prepare(data / "settings.json", root / "data", data / "higan",
                         REPO_ROOT / "plugin", defaults, plugin_mode="copy",
                         dashboard_host="127.0.0.1", dashboard_port=int(ports["BOT_WEB_PORT"]))


def local_request(data, payload, *, timeout=None):
    """Use the same authenticated operator entry, never a standalone Lite/model path."""
    from tools.local_runtime_client import request
    return request(data / "higan" / "run" / "local.sock", payload, timeout=timeout)


def health(data, name):
    """Require this exact full plugin's authenticated readiness, not just an open port."""
    load_install(data, name)
    response = local_request(data, {"op": "health"}, timeout=5)
    if not response.get("ok"):
        raise ValueError("native_plugin_unhealthy")
    return response


def dashboard_ready(data, name):
    """Verify the saved loopback dashboard really serves HTML, with no model call."""
    settings = load_install(data, name)
    url = "http://127.0.0.1:" + docker_environment(data, settings)["BOT_WEB_PORT"] + "/"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=5) as response:
        if (response.getcode() != 200 or not response.headers.get("Content-Type", "").lower().startswith("text/html")
                or b"<html" not in response.read(65536).lower()):
            raise ValueError("native_dashboard_not_ready")


def up(data, name, *, wait_timeout=300):
    """Provision and start an owned full native process; never replace another instance."""
    settings = load_install(data, name)
    try:
        health(data, name)
    except (OSError, ValueError, RuntimeError):
        pass
    else:
        # A live owned host must not be reconfigured or duplicated merely because
        # its dashboard is still starting or unavailable. A later retry can check it.
        dashboard_ready(data, name)
        return
    # Serialize the entire install/start/readiness operation, not just the child.
    # Another wizard must fail before pip/config writes or a duplicate spawn.
    with instance_lock(data, lock_name="setup.lock"):
        check_engine(data, quiet=True)
        from tools.native_assets import prepare_assets
        # A live host may be stopping or temporarily unhealthy. Its OS lock, not
        # a failed health request, determines whether mutable state is safe to edit.
        with instance_lock(data):
            assets = prepare_assets(data, REPO_ROOT)
            prepare_host(data, settings, assets)
        # Release the host lock before launch so the child can acquire it itself;
        # retain setup ownership until readiness or failure has been established.
        environment = os.environ.copy()
        environment.update(HIGAN_ROOT=str(data / "higan"), ASTRBOT_ROOT=str(assets["astrbot_root"]),
            HIGAN_SETTINGS_FILE=str(data / "settings.json"), HIGAN_NATIVE_DATA=str(data),
            PYTHONUNBUFFERED="1", PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
        # Detach from the installer console; graceful stop uses the authenticated
        # operator channel, never taskkill, process-name matching or console signals.
        command = [str(assets["python"]), str(Path(__file__).resolve()), "_serve", "--data-dir", str(data),
                   "--runtime-name", name]
        with open(data / "run" / "runtime.log", "ab") as log:
            child = subprocess.Popen(command, cwd=assets["astrbot_root"], env=environment,
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, shell=False,
                creationflags=(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                               | getattr(subprocess, "DETACHED_PROCESS", 0)))
        deadline = time.monotonic() + wait_timeout
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise ValueError("native_host_exited_check_private_logs")
            try:
                health(data, name)
                dashboard_ready(data, name)
                return
            except (OSError, ValueError, RuntimeError):
                time.sleep(0.5)
        raise ValueError("native_startup_not_confirmed_process_not_killed")


@contextmanager
def instance_lock(data, *, lock_name="instance.lock"):
    """Hold one allowlisted OS byte lock for host state or serialized install/start."""
    if lock_name not in {"instance.lock", "setup.lock"}:
        raise ValueError("native_invalid_lock_name")
    import msvcrt
    private_directory(data / "run")
    with open(data / "run" / lock_name, "a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise ValueError("native_instance_already_running") from None
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def remove_stale_endpoint(data):
    """Remove only a private same-instance locator whose recorded OS process is dead.

    Caller holds the instance byte lock. Access-denied, reused live PIDs, malformed
    locators and mismatched roots fail closed; no unknown process is ever killed.
    """
    endpoint = data / "higan" / "run" / "local-endpoint.json"
    if not endpoint.exists():
        return
    from plugin.local_runtime import read_windows_endpoint
    metadata = read_windows_endpoint(endpoint)
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x100000, False, metadata["pid"])
    if not handle:
        if ctypes.get_last_error() != 87:
            raise ValueError("native_stale_process_unverified")
    else:
        try:
            if kernel.WaitForSingleObject(handle, 0) != 0:
                raise ValueError("native_recorded_process_still_alive")
        finally:
            kernel.CloseHandle(handle)
    # read_windows_endpoint already checked DACL, exact private root and schema;
    # recheck content immediately before removing only the same locator.
    if read_windows_endpoint(endpoint) != metadata:
        raise ValueError("native_locator_changed")
    endpoint.unlink()


def serve(data, name):
    """Run unchanged vendored main in the selected private host, with graceful signals."""
    load_install(data, name)
    from tools.native_assets import inspect_assets
    assets = inspect_assets(data, REPO_ROOT)
    if not assets["ready"]:
        raise ValueError("native_assets_not_ready")
    root = Path(assets["astrbot_root"])
    os.environ.update(HIGAN_ROOT=str(data / "higan"), ASTRBOT_ROOT=str(root),
                      HIGAN_SETTINGS_FILE=str(data / "settings.json"))
    os.chdir(root)
    sys.path.insert(0, str(root))
    if os.name == "nt":
        # Upstream asyncio.run handles KeyboardInterrupt and tears down its tasks.
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    sys.argv = [str(root / "main.py"), "--webui-dir", str(assets["dashboard"])]
    with instance_lock(data):
        remove_stale_endpoint(data)
        # Import the exact upstream entry without executing its __main__ block,
        # then run its original main_async/InitialLoader and cancellation cleanup.
        # The small lifecycle wrapper adds only an authenticated shutdown Event.
        host = runpy.run_path(str(root / "main.py"), run_name="qhunliv_native_upstream")
        host["check_env"]()
        broker = host["LogBroker"]()
        host["main_async"].__globals__["log_broker"] = broker
        host["LogManager"].set_queue_handler(host["logger"], broker)
        asyncio.run(run_host(host["main_async"], assets["dashboard"], data))


async def run_host(main_async, dashboard, data):
    """Add a stop Event around the unchanged host; upstream owns teardown of its tasks."""
    stopped = asyncio.Event()
    control = SimpleNamespace(root=str(data / "higan"), pid=os.getpid(), request_stop=stopped.set)
    sys.modules["qhunliv_native_host_control"] = control
    task = asyncio.create_task(main_async(str(dashboard)))
    waiter = asyncio.create_task(stopped.wait())
    try:
        done, _pending = await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            await task
            return
        # Close new native admission and allow already-owned QQ/tool workers
        # to finish. Never cancel a model/send as a shortcut for a user stop.
        plugin = getattr(control, "plugin", None)
        if plugin is not None:
            plugin._stopping = True
            workers = list(getattr(plugin, "_scope_workers", {}).values())
            if workers:
                await asyncio.gather(*workers, return_exceptions=True)
            lock = getattr(plugin, "_execution_lock", None)
            if lock is not None:
                async with lock:
                    pass
        # LocalRuntime already drained accepted local requests before signalling;
        # InitialLoader cancellation invokes upstream core_lifecycle.stop().
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        sys.modules.pop("qhunliv_native_host_control", None)


def stop(data, name):
    """Ask only the exact authenticated full instance to drain and stop, never taskkill."""
    response = health(data, name)
    pid = response.get("pid")
    if type(pid) is not int or pid <= 0 or pid == os.getpid() or os.name != "nt":
        raise ValueError("native_process_identity_unconfirmed")
    result = local_request(data, {"op": "shutdown"}, timeout=5)
    if result.get("ok") is not True or result.get("status") != "stopping" or result.get("pid") != pid:
        raise ValueError("native_stop_not_confirmed")
    print("Graceful stop accepted by the authenticated native instance; data retained.")


def main(argv=None):
    """Expose the same first-run UI and native same-instance lifecycle commands."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("wizard", "init", "up", "doctor", "info", "status", "stop", "logs", "health", "chat", "_serve"))
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--runtime-name")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--show-password", action="store_true")
    parser.add_argument("--wait-timeout", type=bootstrap.wait_timeout, default=300)
    parser.add_argument("--model-base")
    parser.add_argument("--model")
    parser.add_argument("--api-key-env")
    parser.add_argument("--persona-file")
    parser.add_argument("--owner-qq", action="append", default=[])
    args = parser.parse_args(argv)
    data = bootstrap.data_path(args.data_dir)
    name = bootstrap.runtime_name(args.runtime_name, data)
    if args.command == "wizard":
        from tools.setup_wizard import serve as serve_setup
        serve_setup(sys.modules[__name__], data, name, open_browser=not args.no_browser)
        return 0
    if args.command in {"init", "up"} and not (data / MARKER).exists():
        base, key, model, owners = collect_model_inputs(args)
        persona = Path(args.persona_file).read_text(encoding="utf-8") if args.persona_file else ""
        initialize_settings(data, name, {"base_url": base, "api_key": key, "model": model}, owners, persona)
    if args.command == "_serve":
        serve(data, name)
    elif args.command == "up":
        up(data, name, wait_timeout=args.wait_timeout)
        print("Full native Bot ready. Dashboard: http://127.0.0.1:" + docker_environment(data, load_install(data, name))["BOT_WEB_PORT"])
    elif args.command == "doctor":
        check_engine(data)
    elif args.command in {"status", "health"}:
        result = health(data, name)
        dashboard_ready(data, name)
        print(json.dumps(result, ensure_ascii=False))
    elif args.command == "info":
        settings = load_install(data, name)
        result = {"runtime": "windows-native-full", "data_dir": str(data),
            "dashboard_url": "http://127.0.0.1:" + docker_environment(data, settings)["BOT_WEB_PORT"],
            "initial_dashboard_username": "astrbot", "readiness_checked": False}
        if args.show_password:
            result["initial_dashboard_password"] = settings["dashboard_password"]
        print(json.dumps(result, ensure_ascii=False))
    elif args.command == "stop":
        stop(data, name)
    elif args.command == "logs":
        print("Private runtime log (may contain chat/URLs; do not share): " + str(data / "run" / "runtime.log"))
    elif args.command == "chat":
        load_install(data, name)
        print(json.dumps(local_request(data, {"op": "run", "prompt": sys.stdin.read()}, timeout=None), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        print("Native full runtime operation failed. Check environment/network/disk and the private install log; existing data was not reset.", file=sys.stderr)
        raise SystemExit(2) from None
