"""Opt-in POSIX transport; importing/constructing never sends desktop input.

A trusted host may assign an instance to ``_vm_pointer_executor``. No automatic
installation or environment activation exists. One instance serializes its own
paths only: an external exclusive GUI owner is still mandatory. In particular,
the gate captures its screenshot BEFORE this lock; that race is not atomic.
Executable, its ancestors, environment and cwd must remain administrator-owned;
path checks are not protection against concurrent trusted-filesystem replacement.
"""

import asyncio
import math
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import threading
import time

from .vm_execution_lease import ExecutionLease
from .vm_pointer_runtime import (
    COMMAND_TIMEOUT_SECONDS, MAX_DIMENSION, canonical_pointer_action,
    execute_pointer_path, frozen_pointer_action_payload,
)


# Timed blocking waits bound cancellation latency without a busy-spin loop.
_WAIT_SLICE_SECONDS = 0.05


def _trusted_path(path, *, directory=False):
    """Reject relative, symlinked or noncanonical paths without resolving silently."""
    if not isinstance(path, Path) or not path.is_absolute():
        raise ValueError("invalid_pointer_configuration")
    if path.is_symlink() or path.resolve(strict=True) != path:
        raise ValueError("invalid_pointer_configuration")
    mode = path.stat().st_mode
    if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
        raise ValueError("invalid_pointer_configuration")
    if not directory and os.name == "posix" and not os.access(path, os.X_OK):
        raise ValueError("invalid_pointer_configuration")
    return path


def _checked_argv(argv):
    """Accept only the logical commands emitted by the pointer primitive."""
    if type(argv) is not tuple or any(type(part) is not str for part in argv):
        raise ValueError("invalid_pointer_command")
    if argv in (("xdotool", "mousedown", "1"), ("xdotool", "mouseup", "1")):
        return argv
    if (len(argv) == 4 and argv[:2] == ("xdotool", "mousemove")
            and all(re.fullmatch(r"0|[1-9][0-9]{0,4}", value)
                    and int(value) < MAX_DIMENSION for value in argv[2:])):
        return argv
    raise ValueError("invalid_pointer_command")


def _kill_and_reap(process):
    """Kill only the session created for this child, then reap even on kill errors."""
    try:
        # start_new_session makes the still-owned child's PID its process-group ID.
        # Never discover or kill a desktop-wide group, or use the caller's group.
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        # A group failure must not skip the direct-child cleanup attempt.
        process.kill()
    finally:
        process.wait()


class VmPointerAdapter:
    """Explicit trusted-host capability, not a model-facing subprocess interface."""

    def __init__(self, *, executable_path, env, cwd):
        self._executable = _trusted_path(executable_path)
        self._cwd = _trusted_path(cwd, directory=True)
        if (type(env) is not dict
                or any(type(key) is not str or type(value) is not str
                       or not key or "=" in key or "\0" in key or "\0" in value
                       for key, value in env.items())):
            raise ValueError("invalid_pointer_configuration")
        # No ambient environ inheritance, PATH lookup, shell, or mutable caller alias.
        self._env = env.copy()
        self._lock = asyncio.Lock()

    def runner(self, argv, timeout_s, *, _stop=None):
        """Execute once with a deadline, then fail-closed kill/reap before returning.

        An uninterruptible kernel wait can delay reap indefinitely; safety retains
        the lock rather than claiming retirement and permitting more desktop input.
        This is a trusted xdotool transport, not a sandbox for daemonizing binaries.
        """
        argv = _checked_argv(argv)
        if (type(timeout_s) not in (int, float) or not math.isfinite(timeout_s)
                or not 0 < timeout_s <= COMMAND_TIMEOUT_SECONDS):
            raise ValueError("invalid_pointer_timeout")
        if os.name != "posix":
            # Windows kill() cannot guarantee descendant teardown; fail before launch.
            raise RuntimeError("pointer_platform_unavailable")
        release = argv == ("xdotool", "mouseup", "1")
        if _stop is not None and _stop.is_set() and not release:
            raise RuntimeError("pointer_cancelled")
        _trusted_path(self._executable)
        process = subprocess.Popen(
            (str(self._executable), *argv[1:]), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False,
            env=self._env.copy(), cwd=str(self._cwd), start_new_session=True,
            close_fds=True,
        )
        deadline = time.monotonic() + timeout_s
        finished = False
        try:
            while True:
                if _stop is not None and _stop.is_set() and not release:
                    raise RuntimeError("pointer_cancelled")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("pointer_command_timeout")
                try:
                    result = process.wait(timeout=min(remaining, _WAIT_SLICE_SECONDS))
                    finished = True
                    return result
                except subprocess.TimeoutExpired:
                    # This is a timed blocking wait, not command retry or busy polling.
                    continue
        finally:
            if not finished:
                _kill_and_reap(process)

    def _execute(self, payload, stop, lease=None):
        """Use the single runtime implementation with cooperative runner and sleeper."""
        def run(argv, timeout_s):
            """Permit runtime safety release even after cancellation."""
            return self.runner(argv, timeout_s, _stop=stop)

        def sleep(seconds):
            """Wake a scheduled hold immediately when the owner cancels."""
            if stop.wait(seconds):
                raise RuntimeError("pointer_cancelled")

        def before_start():
            """Check cancellation and the original gate deadline after thread queueing."""
            if stop.is_set():
                raise RuntimeError("pointer_cancelled")
            if lease is not None:
                lease.check()

        return execute_pointer_path(payload, runner=run, sleep=sleep, clock=time.monotonic,
                                    before_start=before_start)

    async def __call__(self, canonical):
        """Preserve the standalone trusted-host API; it does not grant gate freshness."""
        return await self._submit(canonical)

    async def execute_authorized(self, canonical, *, lease):
        """Accept explicit gate-only timing authority, never a field in model actions."""
        if type(lease) is not ExecutionLease:
            return {"ok": False, "error": "invalid_execution_lease"}
        return await self._submit(canonical, lease=lease)

    async def _submit(self, canonical, *, lease=None):
        """Retain the instance lock until cancelled worker cleanup actually finishes."""
        try:
            payload = frozen_pointer_action_payload(canonical)
            canonical_pointer_action(payload)
        except ValueError:
            return {"ok": False, "error": "invalid_pointer_action"}
        # Do not queue a screenshot-authorized action behind an earlier long path.
        # On the owning event loop no await intervenes before the free lock acquisition.
        if self._lock.locked():
            return {"ok": False, "error": "pointer_runtime_busy"}
        async with self._lock:
            stop = threading.Event()
            worker = asyncio.create_task(asyncio.to_thread(self._execute, payload, stop, lease))
            try:
                return await asyncio.shield(worker)
            except asyncio.CancelledError:
                stop.set()
                # shield alone is insufficient: the thread would keep sending input.
                # Repeated cancellation cannot release the lock before cleanup retires.
                while not worker.done():
                    try:
                        await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not worker.cancelled():
                    worker.exception()  # Retrieve failures without exposing private text.
                raise
            except Exception:
                # Standalone host callers also receive no subprocess/private details.
                return {"ok": False, "error": "pointer_outcome_uncertain"}
