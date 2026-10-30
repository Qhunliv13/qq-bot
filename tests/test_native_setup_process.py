"""Exercise real Windows job containment using local, file-backed Python fixtures."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event
import time
import venv

import pytest

from tools import native_setup_process as setup

# Linux collection remains supported; real Win32 behavior is never mocked there.
_WINDOWS = os.name == "nt"
windows_only = pytest.mark.skipif(not _WINDOWS, reason="Requires Windows 10 job-list attributes")

# One real script covers the redirector/tree and Unicode checks without -c code.
_FIXTURE_SOURCE = '''"""Local-only fixture; never install packages or contact a network."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    """Publish PIDs before waiting so tests can retain authoritative OS handles."""
    mode = sys.argv[1]
    directory = Path(sys.argv[2])
    if mode == "exit":
        print("discarded stdout")
        print("discarded stderr", file=sys.stderr)
        raise SystemExit(int(sys.argv[3]))
    if mode == "unicode":
        (directory / "result.json").write_text(json.dumps({
            "cwd": os.getcwd(), "env": os.environ["SETUP_UNICODE"],
            "argument": sys.argv[3], "stdin": sys.stdin.read(),
        }, ensure_ascii=False), encoding="utf-8")
        return
    if mode == "handles":
        import ctypes
        value = int(sys.argv[3])
        flags = ctypes.c_ulong()
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.GetHandleInformation.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        api.GetHandleInformation.restype = ctypes.c_int
        inherited = bool(api.GetHandleInformation(value, ctypes.byref(flags)))
        (directory / "inherited.json").write_text(json.dumps(inherited), encoding="utf-8")
        return
    (directory / (mode + ".pid")).write_text(str(os.getpid()), encoding="ascii")
    if mode in {"root", "rootexit"}:
        subprocess.Popen([sys.executable, __file__, "child", str(directory)], close_fds=True)
    elif mode == "child":
        subprocess.Popen([sys.executable, __file__, "grandchild", str(directory)], close_fds=True)
    if mode == "rootexit":
        while not (directory / "release").exists():
            time.sleep(0.01)
        return
    time.sleep(120)


if __name__ == "__main__":
    main()
'''

_SUPERVISOR_SOURCE = '''"""Run the helper as a disposable supervisor to test parent death."""
import os
from pathlib import Path
import sys

sys.path.insert(0, sys.argv[1])
from tools.native_setup_process import run_setup_step


def main():
    """Keep the job handle only in this process; external test retains no copy."""
    run_setup_step([sys.argv[2], sys.argv[3], "root", sys.argv[4]],
                   Path(sys.argv[4]), dict(os.environ), 60)


if __name__ == "__main__":
    main()
'''


@pytest.fixture
def process_fixture(tmp_path):
    """Create an auditable local child script outside source and vendor trees."""
    fixture = tmp_path / "process_fixture.py"
    fixture.write_text(_FIXTURE_SOURCE, encoding="utf-8")
    return fixture


@pytest.fixture
def handle_api():
    """Use held process handles, not PID/name polling, to prove descendant exit."""
    if not _WINDOWS:
        pytest.skip("Win32 handle checks require Windows")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    api.TerminateProcess.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    return api


def _hold_processes(api, directory, modes):
    """Read fixture-owned PIDs once and hold exact objects across possible PID reuse."""
    handles = []
    deadline = time.monotonic() + 8
    try:
        for mode in modes:
            path = directory / (mode + ".pid")
            while True:
                try:
                    pid = int(path.read_text(encoding="ascii"))
                    break
                except (FileNotFoundError, ValueError):
                    if time.monotonic() >= deadline:
                        pytest.fail("Local process fixture did not become ready")
                    time.sleep(0.01)
            handle = api.OpenProcess(0x100000 | 0x0001, False, pid)
            assert handle, "Could not retain fixture process handle"
            handles.append(handle)
        return handles
    except BaseException:
        _close_processes(api, handles)
        raise


def _close_processes(api, handles):
    """Bound test failures too: kill only exact retained fixture objects, then close."""
    for handle in handles:
        if api.WaitForSingleObject(handle, 0) == 258:
            api.TerminateProcess(handle, 1)
            api.WaitForSingleObject(handle, 3000)
        api.CloseHandle(handle)


@windows_only
@pytest.mark.parametrize("code", [0, 7, 259])
def test_returns_root_exit_code(process_fixture, tmp_path, code):
    """Return nonzero and STILL_ACTIVE-valued exits without treating them as running."""
    assert setup.run_setup_step([sys.executable, str(process_fixture), "exit", str(tmp_path),
                                str(code)], tmp_path, dict(os.environ), 10) == code


@windows_only
def test_unicode_cwd_environment_arguments_and_nul_stdio(process_fixture, tmp_path):
    """Use real Unicode CreateProcess inputs and demonstrate stdin is NUL/EOF."""
    directory = tmp_path / "安装路径 Ω"
    directory.mkdir()
    environment = dict(os.environ, SETUP_UNICODE="环境值 Ω 🐍")
    argument = '引号 "quoted" trailing\\'
    assert setup.run_setup_step([sys.executable, str(process_fixture), "unicode", str(directory),
                                argument], directory, environment, 10) == 0
    result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
    assert Path(result["cwd"]) == directory
    assert result["env"] == environment["SETUP_UNICODE"]
    assert result["argument"] == argument
    assert result["stdin"] == ""


@windows_only
@pytest.mark.parametrize("mode", ["root", "rootexit"])
def test_timeout_kills_child_and_immediate_grandchild(process_fixture, tmp_path, handle_api, mode):
    """A completed root is not success while any descendant is still alive."""
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(setup.run_setup_step,
                                 [sys.executable, str(process_fixture), mode, str(tmp_path)],
                                 tmp_path, dict(os.environ), 4)
        handles = _hold_processes(handle_api, tmp_path, [mode, "child", "grandchild"])
        try:
            if mode == "rootexit":
                (tmp_path / "release").touch()
                assert handle_api.WaitForSingleObject(handles[0], 2000) == 0
                assert not future.done(), "Root exit must not ignore its remaining job"
            with pytest.raises(subprocess.TimeoutExpired) as caught:
                future.result(timeout=18)
            assert caught.value.output is None
            assert caught.value.stderr is None
            # Job accounting reaches zero during exit teardown; handle signaling
            # can follow a moment later, so prove each exact process is signaled.
            assert all(handle_api.WaitForSingleObject(handle, 3000) == 0 for handle in handles)
        finally:
            _close_processes(handle_api, handles)


@windows_only
def test_parent_death_kills_venv_redirector_descendants(process_fixture, tmp_path, handle_api):
    """Killing the exact supervisor closes its sole job handle, even through venv."""
    # No pip, installation, or global mutation: only local stdlib venv launchers.
    environment_root = tmp_path / "venv"
    venv.EnvBuilder(with_pip=False).create(environment_root)
    python = environment_root / "Scripts" / "python.exe"
    supervisor = tmp_path / "supervisor.py"
    supervisor.write_text(_SUPERVISOR_SOURCE, encoding="utf-8")
    repository = Path(__file__).resolve().parents[1]
    with subprocess.Popen([sys.executable, str(supervisor), str(repository), str(python),
                           str(process_fixture), str(tmp_path)], stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as parent:
        handles = []
        try:
            handles = _hold_processes(handle_api, tmp_path, ["root", "child", "grandchild"])
            parent.kill()
            parent.wait(timeout=5)
            assert all(handle_api.WaitForSingleObject(handle, 5000) == 0 for handle in handles)
        finally:
            _close_processes(handle_api, handles)
            if parent.poll() is None:
                parent.kill()
                parent.wait(timeout=5)


@windows_only
def test_unrelated_inheritable_handle_is_not_inherited(process_fixture, tmp_path):
    """Capture a fresh inheritable event and prove HANDLE_LIST excludes it."""
    api = setup._load_api()
    api.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    api.CreateEventW.restype = wintypes.HANDLE
    security = setup._SecurityAttributes(ctypes.sizeof(setup._SecurityAttributes), None, True)
    event = api.CreateEventW(ctypes.byref(security), True, False, None)
    assert event
    try:
        assert setup.run_setup_step([sys.executable, str(process_fixture), "handles", str(tmp_path),
                                    str(event)], tmp_path, dict(os.environ), 10) == 0
        assert json.loads((tmp_path / "inherited.json").read_text(encoding="utf-8")) is False
    finally:
        api.CloseHandle(event)


class _FailingApi:
    """Delegate to real Win32 while injecting one fail-closed setup operation."""

    def __init__(self, api, failure):
        self.api = api
        self.failure = failure
        self.created = 0
        self.closed = []

    def __getattr__(self, name):
        """Keep the test wrapper small instead of rebuilding Win32 as a mock."""
        return getattr(self.api, name)

    def CreateJobObjectW(self, *arguments):
        """Record the owned job or fail before any process can be created."""
        if self.failure == "job":
            return 0
        return self.api.CreateJobObjectW(*arguments)

    def SetInformationJobObject(self, *arguments):
        """Refuse launching unless kill-on-close configuration succeeds."""
        if self.failure == "limits":
            return 0
        return self.api.SetInformationJobObject(*arguments)

    def UpdateProcThreadAttribute(self, *arguments):
        """Reject either required attribute without an uncontained fallback."""
        if self.failure == arguments[2]:
            return 0
        return self.api.UpdateProcThreadAttribute(*arguments)

    def CreateProcessW(self, *arguments):
        """Track creation so setup failures prove no uncontained spawn occurred."""
        self.created += 1
        if self.failure == "create":
            return 0
        return self.api.CreateProcessW(*arguments)

    def CloseHandle(self, handle):
        """Record cleanup while still closing the actual owned OS handles."""
        self.closed.append(handle)
        return self.api.CloseHandle(handle)


@windows_only
@pytest.mark.parametrize("failure", ["job", "limits", setup._JOB_LIST, setup._HANDLE_LIST, "create"])
def test_failed_creation_is_closed_and_never_falls_back(process_fixture, tmp_path, monkeypatch, failure):
    """Inject narrow API failures and retain real allocation/closure behavior."""
    api = _FailingApi(setup._load_api(), failure)
    monkeypatch.setattr(setup, "_load_api", lambda: api)
    with pytest.raises(OSError, match="Native setup process operation failed"):
        setup.run_setup_step([sys.executable, str(process_fixture), "exit", str(tmp_path), "0"],
                             tmp_path, dict(os.environ), 5)
    assert api.created == (1 if failure == "create" else 0)
    assert len(api.closed) == (0 if failure == "job" else 1 if failure == "limits" else 2)


@windows_only
def test_uncertain_cleanup_has_fixed_retry_blocking_error(process_fixture, tmp_path, monkeypatch):
    """Simulate missing proof after a real kill; never deliberately leave an orphan."""
    original = setup._terminate_and_confirm

    def cannot_confirm(api, job, process):
        """Perform genuine cleanup, then inject uncertainty for the caller contract."""
        assert original(api, job, process)
        return False

    monkeypatch.setattr(setup, "_terminate_and_confirm", cannot_confirm)
    with pytest.raises(setup.SetupCleanupError) as caught:
        setup.run_setup_step([sys.executable, str(process_fixture), "root", str(tmp_path)],
                             tmp_path, dict(os.environ), 0.1)
    assert str(caught.value) == setup._CLEANUP_MESSAGE
    assert str(tmp_path) not in str(caught.value)


@windows_only
def test_wait_error_kills_entire_job(process_fixture, tmp_path, handle_api, monkeypatch):
    """A failed job query during waiting must clean real children before raising."""
    original = setup._active_processes
    ready = Event()
    injected = False

    def fail_once_with_tree(api, job):
        """Inject only after exact handles are retained; cleanup queries stay functional."""
        nonlocal injected
        if not injected and ready.is_set():
            injected = True
            raise OSError("Injected accounting failure")
        return original(api, job)

    monkeypatch.setattr(setup, "_active_processes", fail_once_with_tree)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(setup.run_setup_step,
                                 [sys.executable, str(process_fixture), "root", str(tmp_path)],
                                 tmp_path, dict(os.environ), 10)
        handles = _hold_processes(handle_api, tmp_path, ["root", "child", "grandchild"])
        try:
            ready.set()
            with pytest.raises(OSError, match="Injected accounting failure"):
                future.result(timeout=15)
            assert injected
            assert all(handle_api.WaitForSingleObject(handle, 3000) == 0 for handle in handles)
        finally:
            _close_processes(handle_api, handles)


@windows_only
def test_late_completed_job_does_not_bypass_deadline(process_fixture, tmp_path, monkeypatch):
    """Descheduling past a finite cap must not turn a late exit into accepted success."""
    original = setup._active_processes
    real_clock = time.monotonic
    delayed = False

    def late_accounting(api, job):
        """Wait until the finite fixture ended, then model a delayed supervisor tick."""
        nonlocal delayed
        active = original(api, job)
        if active == 0:
            delayed = True
        return active

    def clock():
        """Advance only after actual job-empty observation, leaving cleanup bounded."""
        return real_clock() + (30 if delayed else 0)

    monkeypatch.setattr(setup, "_active_processes", late_accounting)
    monkeypatch.setattr(setup.time, "monotonic", clock)
    with pytest.raises(subprocess.TimeoutExpired):
        setup.run_setup_step([sys.executable, str(process_fixture), "exit", str(tmp_path), "0"],
                             tmp_path, dict(os.environ), 10)
    assert delayed


@windows_only
def test_success_and_timeout_release_all_owned_handles(process_fixture, tmp_path):
    """Sequential success/failure loops must not leak jobs, threads, NUL or process handles."""
    api = setup._load_api()
    api.GetCurrentProcess.argtypes = []
    api.GetCurrentProcess.restype = wintypes.HANDLE
    api.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    api.GetProcessHandleCount.restype = wintypes.BOOL

    def count_handles():
        """Count only this test process's handles, without enumerating other processes."""
        count = wintypes.DWORD()
        assert api.GetProcessHandleCount(api.GetCurrentProcess(), ctypes.byref(count))
        return count.value

    # Warm ctypes/Python lazy machinery before the baseline comparison.
    command = [sys.executable, str(process_fixture), "exit", str(tmp_path), "0"]
    assert setup.run_setup_step(command, tmp_path, dict(os.environ), 10) == 0
    baseline = count_handles()
    for _ in range(4):
        assert setup.run_setup_step(command, tmp_path, dict(os.environ), 10) == 0
        with pytest.raises(subprocess.TimeoutExpired):
            setup.run_setup_step(command, tmp_path, dict(os.environ), 0)
    assert count_handles() <= baseline


@pytest.mark.parametrize("arguments,environment,timeout", [
    (["python.exe"], {}, 1), (["\\rooted.exe"], {}, 1),
    (["C:\\python.exe"], {"SECRET": "value\0secret"}, 1),
    (["C:\\python.exe"], {"A": "1", "a": "2"}, 1),
    (["C:\\python.exe"], {}, float("inf")),
    (["C:\\python.exe"], {}, -1),
])
def test_invalid_inputs_are_safe_oserrors(arguments, environment, timeout):
    """Reject malformed input before loading platform APIs or disclosing secrets."""
    with pytest.raises(OSError, match="^Native setup process arguments are invalid\\.$"):
        setup.run_setup_step(arguments, ".", environment, timeout)


def test_environment_block_is_sorted_and_double_terminated():
    """Keep this pure input invariant test portable for Linux test collection."""
    _, _, _, command, block = setup._prepare_inputs(
        ["C:\\trusted python.exe", "quoted value"], ".", {"z": "last", "A": "first"}, 1)
    assert command.value == '"C:\\trusted python.exe" "quoted value"'
    assert block.raw.decode("utf-16-le") == "A=first\0z=last\0\0"


@pytest.mark.skipif(_WINDOWS, reason="Non-Windows unsupported-path check")
def test_non_windows_is_explicitly_unsupported():
    """The integrating parent, not an uncontained fallback here, owns portability."""
    with pytest.raises(OSError, match="requires Windows 10"):
        setup.run_setup_step(["C:\\python.exe"], ".", {}, 1)
