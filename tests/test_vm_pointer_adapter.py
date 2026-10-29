"""Mock-only transport tests: these never launch xdotool or any other process."""

import asyncio
import importlib.util
from pathlib import Path
import sys
import threading
import types
from unittest import mock

import pytest


# Load the real relative-import pair without the plugin's application dependencies.
_PACKAGE = "pointer_adapter_subject"
_package = types.ModuleType(_PACKAGE)
_package.__path__ = [str(Path(__file__).resolve().parents[1] / "plugin")]
sys.modules[_PACKAGE] = _package
_SPEC = importlib.util.spec_from_file_location(
    _PACKAGE + ".vm_pointer_adapter", Path(_package.__path__[0]) / "vm_pointer_adapter.py")
adapter_module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(adapter_module)
runtime = sys.modules[_PACKAGE + ".vm_pointer_runtime"]


def frozen():
    """Make the exact immutable runtime action expected from the gate."""
    return runtime.canonical_pointer_action({
        "version": 1, "button": "left", "width": 20, "height": 20,
        "points": [{"x": 1, "y": 2, "t_ms": 0}, {"x": 3, "y": 4, "t_ms": 100}]})


@pytest.fixture
def adapter(tmp_path):
    """Use a regular inert file; all subprocess operations are mocked."""
    executable = tmp_path / "inert-command"
    executable.write_text("not executable code", encoding="utf-8")
    executable.chmod(0o700)
    return adapter_module.VmPointerAdapter(executable_path=executable, env={}, cwd=tmp_path)


def test_constructor_no_launch_and_environment_copy(adapter):
    """Construction owns a detached explicitly provided environment."""
    env = {"DISPLAY": ":trusted"}
    with mock.patch.object(adapter_module.subprocess, "Popen") as popen:
        instance = adapter_module.VmPointerAdapter(
            executable_path=adapter._executable, cwd=adapter._cwd, env=env)
    env["DISPLAY"] = ":changed"
    assert instance._env == {"DISPLAY": ":trusted"}
    popen.assert_not_called()


@pytest.mark.parametrize("argv", [
    ["xdotool", "mouseup", "1"], ("other", "mouseup", "1"),
    ("xdotool", "mouseup", "2"), ("xdotool", "mousemove", "-1", "2"),
    ("xdotool", "mousemove", "01", "2"), ("xdotool", "mousemove", "16384", "2"),
    ("xdotool", "mousemove", "1", "2", "click", "1"),
])
def test_reject_arbitrary_argv(adapter, argv):
    """No command-string, option, alternative executable or chained argv may launch."""
    with mock.patch.object(adapter_module.subprocess, "Popen") as popen:
        with pytest.raises(ValueError):
            adapter.runner(argv, 1)
    popen.assert_not_called()


def test_launch_contract_and_exit(adapter):
    """Transport fixes executable, streams, cwd, environment and session policy."""
    process = mock.Mock()
    process.wait.return_value = 7
    with mock.patch.object(adapter_module.os, "name", "posix"), \
            mock.patch.object(adapter_module, "_trusted_path"), \
            mock.patch.object(adapter_module.subprocess, "Popen", return_value=process) as popen:
        assert adapter.runner(("xdotool", "mouseup", "1"), 1) == 7
    args, kwargs = popen.call_args
    assert args == ((str(adapter._executable), "mouseup", "1"),)
    assert kwargs == {"stdin": -3, "stdout": -3, "stderr": -3, "shell": False,
                      "env": {}, "cwd": str(adapter._cwd), "start_new_session": True,
                      "close_fds": True}


def test_nonposix_refuses_before_launch(adapter):
    """Windows cannot provide the required process-group guarantee."""
    with mock.patch.object(adapter_module.os, "name", "nt"), \
            mock.patch.object(adapter_module.subprocess, "Popen") as popen:
        with pytest.raises(RuntimeError, match="platform_unavailable"):
            adapter.runner(("xdotool", "mouseup", "1"), 1)
    popen.assert_not_called()


def test_timeout_kills_owned_group_and_waits(adapter):
    """Expired commands are killed and reaped before returning an error."""
    process = mock.Mock(pid=12345)
    with mock.patch.object(adapter_module.os, "name", "posix"), \
            mock.patch.object(adapter_module, "_trusted_path"), \
            mock.patch.object(adapter_module.subprocess, "Popen", return_value=process), \
            mock.patch.object(adapter_module.time, "monotonic", side_effect=[0, 2]), \
            mock.patch.object(adapter_module.signal, "SIGKILL", 9, create=True), \
            mock.patch.object(adapter_module.os, "killpg", create=True) as kill:
        with pytest.raises(RuntimeError, match="timeout"):
            adapter.runner(("xdotool", "mousemove", "1", "2"), 1)
    kill.assert_called_once_with(12345, 9)
    process.wait.assert_called_once_with()


def test_kill_error_still_reaps():
    """Even group and direct kill failures cannot skip the wait cleanup."""
    process = mock.Mock(pid=12345)
    process.kill.side_effect = OSError("private")
    with mock.patch.object(adapter_module.signal, "SIGKILL", 9, create=True), \
            mock.patch.object(adapter_module.os, "killpg", create=True, side_effect=OSError):
        with pytest.raises(OSError):
            adapter_module._kill_and_reap(process)
    process.wait.assert_called_once_with()


def test_cancel_holds_lock_through_worker_retirement(adapter):
    """Repeated cancellation cannot admit another path before safety cleanup ends."""
    entered, release_started, finish_release = threading.Event(), threading.Event(), threading.Event()
    commands = []

    def run(argv, timeout_s, *, _stop=None):
        """Simulate a pressed button and block release to inspect lock ownership."""
        commands.append(argv)
        if argv[1] == "mousedown":
            entered.set()
        if argv[1] == "mouseup":
            release_started.set()
            assert finish_release.wait(3)
        return 0

    async def scenario():
        """Synchronize with events rather than race-prone sleeps or timer polling."""
        adapter.runner = run
        task = asyncio.create_task(adapter(frozen()))
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        assert await asyncio.to_thread(release_started.wait, 3)
        assert adapter._lock.locked()
        assert await adapter(frozen()) == {"ok": False, "error": "pointer_runtime_busy"}
        task.cancel()
        await asyncio.sleep(0)
        assert adapter._lock.locked()
        finish_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not adapter._lock.locked()

    asyncio.run(scenario())
    assert [command[1] for command in commands] == ["mousemove", "mousedown", "mouseup"]


def test_reject_foreign_frozen_and_private_errors(adapter):
    """The host accepts exact frozen types and redacts unexpected worker failures."""
    async def scenario():
        """Exercise both boundary rejection and fixed public failure receipts."""
        assert (await adapter(tuple(frozen())))["error"] == "invalid_pointer_action"
        with mock.patch.object(adapter, "_execute", side_effect=RuntimeError("secret")):
            assert await adapter(frozen()) == {"ok": False, "error": "pointer_outcome_uncertain"}
    asyncio.run(scenario())
