"""Run Windows installer steps inside an atomically assigned, kill-on-close job.

Windows 10+ is required. No process is launched if job-list attributes are not
available; callers keep their existing non-Windows runner. This is containment
for trusted installers, not a sandbox for hostile executables.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import math
import ntpath
import os
import subprocess
import sys
import time

# Win32 constants from processthreadsapi.h, winbase.h and jobapi2.h.
_JOB_LIST = 0x2000D
_HANDLE_LIST = 0x20002
_KILL_ON_JOB_CLOSE = 0x2000
_EXTENDED_STARTUPINFO_PRESENT = 0x80000
_CREATE_UNICODE_ENVIRONMENT = 0x400
_STARTF_USESTDHANDLES = 0x100
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258
_WAIT_FAILED = 0xFFFFFFFF
# Cleanup is separately bounded: retries are forbidden if this cannot be proven.
_CLEANUP_SECONDS = 10.0
_POLL_SECONDS = 0.02
_CLEANUP_MESSAGE = "Native setup cleanup could not be confirmed; retry must be blocked."


class SetupCleanupError(OSError):
    """Signal uncertain teardown; callers must persist a retry-blocking marker."""


class _SecurityAttributes(ctypes.Structure):
    """Match SECURITY_ATTRIBUTES, used only for the fresh inheritable NUL handle."""

    _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p),
                ("bInheritHandle", wintypes.BOOL)]


class _StartupInfo(ctypes.Structure):
    """Match STARTUPINFOW without exposing the parent's standard handles."""

    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
                ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
                ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
                ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                ("lpReserved2", ctypes.c_void_p), ("hStdInput", wintypes.HANDLE),
                ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]


class _StartupInfoEx(ctypes.Structure):
    """Keep the attribute-list pointer adjacent to STARTUPINFOW as Win32 requires."""

    _fields_ = [("StartupInfo", _StartupInfo), ("lpAttributeList", ctypes.c_void_p)]


class _ProcessInformation(ctypes.Structure):
    """Own both CreateProcessW handles until their unconditional final closure."""

    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


class _BasicLimit(ctypes.Structure):
    """Match JOBOBJECT_BASIC_LIMIT_INFORMATION, including pointer-sized fields."""

    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]


class _IoCounters(ctypes.Structure):
    """Supply the six ULONG64 fields embedded in extended job limits."""

    _fields_ = [(name, ctypes.c_uint64) for name in
                ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                 "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _ExtendedLimit(ctypes.Structure):
    """Match JOBOBJECT_EXTENDED_LIMIT_INFORMATION for KILL_ON_JOB_CLOSE."""

    _fields_ = [("BasicLimitInformation", _BasicLimit), ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


class _Accounting(ctypes.Structure):
    """Read job-wide ActiveProcesses, never enumerate or act on process names/PIDs."""

    _fields_ = [(name, ctypes.c_int64) for name in
                ("TotalUserTime", "TotalKernelTime", "ThisPeriodTotalUserTime",
                 "ThisPeriodTotalKernelTime")] + [(name, wintypes.DWORD) for name in
                ("TotalPageFaultCount", "TotalProcesses", "ActiveProcesses",
                 "TotalTerminatedProcesses")]


def _load_api():
    """Bind exact Win32 signatures lazily so importing on other platforms is safe."""
    if os.name != "nt" or sys.getwindowsversion().major < 10:
        raise OSError("Native setup process containment requires Windows 10 or newer.")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.c_void_p
    size = ctypes.c_size_t
    signatures = {
        "CreateJobObjectW": ([pointer, wintypes.LPCWSTR], wintypes.HANDLE),
        "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, pointer, wintypes.DWORD], wintypes.BOOL),
        "CreateFileW": ([wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, pointer,
                         wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE], wintypes.HANDLE),
        "InitializeProcThreadAttributeList": ([pointer, wintypes.DWORD, wintypes.DWORD,
                                               ctypes.POINTER(size)], wintypes.BOOL),
        "UpdateProcThreadAttribute": ([pointer, wintypes.DWORD, size, pointer, size,
                                       pointer, pointer], wintypes.BOOL),
        "DeleteProcThreadAttributeList": ([pointer], None),
        "CreateProcessW": ([wintypes.LPCWSTR, wintypes.LPWSTR, pointer, pointer,
                            wintypes.BOOL, wintypes.DWORD, pointer, wintypes.LPCWSTR,
                            pointer, ctypes.POINTER(_ProcessInformation)], wintypes.BOOL),
        "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
        "GetExitCodeProcess": ([wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        "QueryInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, pointer,
                                       wintypes.DWORD, pointer], wintypes.BOOL),
        "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
    }
    # Explicit signatures prevent 64-bit HANDLE truncation by ctypes defaults.
    for name, (arguments, result) in signatures.items():
        # Older/incomplete API sets must fail closed with a safe public error.
        try:
            function = getattr(api, name)
        except AttributeError:
            raise OSError("Native setup process containment APIs are unavailable.") from None
        function.argtypes = arguments
        function.restype = result
    return api


def _check(result):
    """Keep native errors diagnostic but fixed-text, excluding commands and paths."""
    if not result:
        raise OSError(ctypes.get_last_error(), "Native setup process operation failed.")
    return result


def _active_processes(api, job):
    """Query authoritative accounting for this owned job, including grandchildren."""
    accounting = _Accounting()
    _check(api.QueryInformationJobObject(job, 1, ctypes.byref(accounting),
                                         ctypes.sizeof(accounting), None))
    return accounting.ActiveProcesses


def _root_exited(api, process):
    """Distinguish a signaled root from a failed wait without trusting exit code 259."""
    state = api.WaitForSingleObject(process, 0)
    if state == _WAIT_FAILED:
        _check(False)
    if state not in (_WAIT_OBJECT_0, _WAIT_TIMEOUT):
        raise OSError("Native setup process wait failed.")
    return state == _WAIT_OBJECT_0


def _terminate_and_confirm(api, job, process):
    """Kill the entire owned job, then prove both job-empty and root-signaled."""
    deadline = time.monotonic() + _CLEANUP_SECONDS
    while True:
        try:
            # Retry transient termination failures, but never infer success from it.
            api.TerminateJobObject(job, 1)
            root_exited = _root_exited(api, process)
            active = _active_processes(api, job)
            if root_exited and active == 0:
                return True
        except OSError:
            # A transient accounting failure cannot be treated as empty/success.
            pass
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(_POLL_SECONDS, remaining))


def _prepare_inputs(arguments, cwd, environment, timeout):
    """Validate trusted executable selection and build writable Unicode buffers."""
    try:
        arguments = [os.fspath(argument) for argument in arguments]
        cwd = os.fspath(cwd)
        timeout = float(timeout)
        if (not arguments or any(not isinstance(arg, str) or "\0" in arg for arg in arguments)
                or not ntpath.isabs(arguments[0]) or not ntpath.splitdrive(arguments[0])[0]
                or not isinstance(cwd, str) or "\0" in cwd
                or not math.isfinite(timeout) or timeout < 0
                or not math.isfinite(time.monotonic() + timeout)):
            raise ValueError
        entries = []
        seen = set()
        for key, value in environment.items():
            if (not isinstance(key, str) or not isinstance(value, str) or not key
                    or "=" in key or "\0" in key or "\0" in value or key.upper() in seen):
                raise ValueError
            seen.add(key.upper())
            entries.append((key, value))
        # Windows requires a case-insensitively sorted UTF-16 block with two NULs.
        block = "\0".join(f"{key}={value}" for key, value in
                           sorted(entries, key=lambda entry: entry[0].upper())) + "\0\0"
        command = ctypes.create_unicode_buffer(subprocess.list2cmdline(arguments))
        # Encode explicitly: len(str) undercounts UTF-16 surrogate pairs on Windows.
        encoded_environment = block.encode("utf-16-le")
        env = ctypes.create_string_buffer(encoded_environment, len(encoded_environment))
        return arguments, cwd, timeout, command, env
    except (TypeError, ValueError, AttributeError, OverflowError):
        raise OSError("Native setup process arguments are invalid.") from None


def run_setup_step(arguments, cwd, environment, timeout) -> int:
    """Run a trusted absolute Python executable with finite, fail-closed containment.

    Return the root exit code only after its entire job is empty. Timeout kills
    descendants and raises TimeoutExpired without captured output. Cleanup that
    cannot be confirmed raises SetupCleanupError instead: retry is then unsafe.
    JOB_LIST assigns the job during CreateProcessW, closing the suspended-process
    parent-death race, including Windows venv redirector children. Never fall back
    to post-creation assignment, breakaway, shell execution, or uncontained spawn.
    """
    deadline = time.monotonic()
    arguments, cwd, timeout, command, env = _prepare_inputs(arguments, cwd, environment, timeout)
    deadline += timeout
    api = _load_api()
    job = None
    nul = None
    attribute_initialized = False
    process = _ProcessInformation()
    try:
        # NULL security attributes make the sole job handle non-inheritable.
        job = _check(api.CreateJobObjectW(None, None))
        limits = _ExtendedLimit()
        limits.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
        _check(api.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
        security = _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), None, True)
        # This fresh handle is the complete inheritance allow-list, all stdio=NUL.
        nul = api.CreateFileW("NUL", 0xC0000000, 3, ctypes.byref(security), 3, 0, None)
        if nul == ctypes.c_void_p(-1).value:
            nul = None
            _check(False)
        _check(nul)
        length = ctypes.c_size_t()
        # The sizing call deliberately fails with ERROR_INSUFFICIENT_BUFFER.
        api.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(length))
        if not length.value:
            _check(False)
        attribute_storage = ctypes.create_string_buffer(length.value)
        _check(api.InitializeProcThreadAttributeList(attribute_storage, 2, 0, ctypes.byref(length)))
        attribute_initialized = True
        # Attribute values and backing storage stay alive through list deletion.
        jobs = (wintypes.HANDLE * 1)(job)
        handles = (wintypes.HANDLE * 1)(nul)
        _check(api.UpdateProcThreadAttribute(attribute_storage, 0, _JOB_LIST,
                                             jobs, ctypes.sizeof(jobs), None, None))
        _check(api.UpdateProcThreadAttribute(attribute_storage, 0, _HANDLE_LIST,
                                             handles, ctypes.sizeof(handles), None, None))
        startup = _StartupInfoEx()
        startup.StartupInfo.cb = ctypes.sizeof(startup)
        startup.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
        startup.StartupInfo.hStdInput = nul
        startup.StartupInfo.hStdOutput = nul
        startup.StartupInfo.hStdError = nul
        startup.lpAttributeList = ctypes.cast(attribute_storage, ctypes.c_void_p)
        # bInheritHandles must be TRUE with HANDLE_LIST; nothing else is inherited.
        _check(api.CreateProcessW(arguments[0], command, None, None, True,
                                  _EXTENDED_STARTUPINFO_PRESENT | _CREATE_UNICODE_ENVIRONMENT,
                                  env, cwd, ctypes.byref(startup), ctypes.byref(process)))
        while True:
            # Query both every iteration, even while the root is still running.
            root_exited = _root_exited(api, process.hProcess)
            active = _active_processes(api, job)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(arguments, timeout)
            if root_exited and active == 0:
                code = wintypes.DWORD()
                _check(api.GetExitCodeProcess(process.hProcess, ctypes.byref(code)))
                # Do not accept a late completion after the supervisor was descheduled.
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(arguments, timeout)
                return int(code.value)
            time.sleep(min(_POLL_SECONDS, remaining))
    except BaseException:
        # Also contain KeyboardInterrupt/SystemExit; no exception may orphan a child.
        if process.hProcess:
            try:
                confirmed = _terminate_and_confirm(api, job, process.hProcess)
            except BaseException:
                # A second cancellation during teardown cannot certify cleanup.
                confirmed = False
            if not confirmed:
                raise SetupCleanupError(_CLEANUP_MESSAGE) from None
        raise
    finally:
        # Closing the last job handle also kills descendants after parent death.
        if attribute_initialized:
            api.DeleteProcThreadAttributeList(attribute_storage)
        for handle in (process.hThread, process.hProcess, nul, job):
            if handle:
                api.CloseHandle(handle)
