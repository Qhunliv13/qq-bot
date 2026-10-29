"""Observe pristine QQ's default entry, never login, transport, or readiness.

The imported runtime probe owns every isolation operation. This tool supplies a
trusted observation callback only. No QQ script, RUN_AS_NODE, sandbox-disabling
flag, native export call, host account directory, or production socket is used.
"""

import argparse
import ctypes
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time

try:
    from tools import qq_isolated_runtime_probe as runtime
except ModuleNotFoundError as exc:
    if exc.name != "tools":
        raise
    import qq_isolated_runtime_probe as runtime

# All counts and executable labels are restricted before leaving the private jail.
PROCESS_LIMIT = 64
WINDOW_LIMIT = 64
SAMPLE_INTERVAL = 0.25
SUMMARY_LIMIT = 96 * 1024
QQ_EXE = "/opt/QQ/qq"


def base_report():
    """Keep application observation distinct from native login/sendrecv completion."""
    return dict(runtime.base_report(), mode="isolated_default_application_observation",
                application_invoked=False, experiment_completed=False,
                application_readiness_verified=False, qq_gui_identity_verified=False,
                native_login_verified=False, full_sendrecv_verified=False,
                invocation={"argv": [QQ_EXE], "electron_run_as_node_present": False},
                status="not_executed")


def process_identity(pid):
    """Read one same-UID jailed process identity, never cmdline, environment or state."""
    if type(pid) is not int or not 1 < pid < 4194304:
        return None
    try:
        root = Path("/proc") / str(pid)
        executable = os.readlink(root / "exe")
        # Restrict reports to a known QQ executable, not arbitrary paths/names.
        if executable != QQ_EXE:
            return None
        before = (root / "stat").read_text(encoding="ascii")
        fields = dict(line.split(":", 1) for line in
                      (root / "status").read_text(encoding="ascii").splitlines() if ":" in line)
        # The parenthesized comm can contain spaces; never report or split it.
        tail = before[before.rfind(")") + 2:].split()
        identity = {"pid": pid, "exe": executable, "start_ticks": int(tail[19]),
                    "uid": [int(value) for value in fields["Uid"].split()],
                    "gid": [int(value) for value in fields["Gid"].split()],
                    "cap_eff_zero": int(fields["CapEff"].strip(), 16) == 0,
                    "no_new_privileges": fields["NoNewPrivs"].strip() == "1",
                    "user_namespace_matches_supervisor": os.readlink(root / "ns/user") == os.readlink("/proc/self/ns/user")}
        # A second stat/exe read refuses disappearance or PID reuse during sampling.
        after = (root / "stat").read_text(encoding="ascii")
        if (after[after.rfind(")") + 2:].split()[19] != str(identity["start_ticks"])
                or os.readlink(root / "exe") != executable):
            return None
        return identity
    except (OSError, ValueError, KeyError, IndexError):
        return None


def process_snapshot():
    """Bound the private PID-namespace scan and include only exact QQ ELF identities."""
    result = []
    # No host /proc exists after imported pivot-root and private proc verification.
    for entry in sorted(Path("/proc").iterdir(), key=lambda item: item.name):
        if entry.name.isdigit():
            identity = process_identity(int(entry.name))
            if identity is not None:
                result.append(identity)
                if len(result) >= PROCESS_LIMIT:
                    break
    return result


class PrivateXIdentity:
    """Query only X tree IDs and _NET_WM_PID via the public system X11 API.

    No window name/title/class/image is requested. This is the standard Xlib ABI,
    not a guessed QQ native ABI, and connects only to the authenticated private X.
    """

    def __init__(self):
        self.lib = ctypes.CDLL("libX11.so.6")
        pointer = ctypes.c_void_p
        ulong = ctypes.c_ulong
        self.lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.lib.XOpenDisplay.restype = pointer
        self.lib.XDefaultRootWindow.argtypes = [pointer]
        self.lib.XDefaultRootWindow.restype = ulong
        self.lib.XInternAtom.argtypes = [pointer, ctypes.c_char_p, ctypes.c_int]
        self.lib.XInternAtom.restype = ulong
        self.lib.XQueryTree.argtypes = [pointer, ulong, ctypes.POINTER(ulong),
                                       ctypes.POINTER(ulong), ctypes.POINTER(ctypes.POINTER(ulong)),
                                       ctypes.POINTER(ctypes.c_uint)]
        self.lib.XQueryTree.restype = ctypes.c_int
        self.lib.XGetWindowProperty.argtypes = [pointer, ulong, ulong, ctypes.c_long,
                                               ctypes.c_long, ctypes.c_int, ulong,
                                               ctypes.POINTER(ulong), ctypes.POINTER(ctypes.c_int),
                                               ctypes.POINTER(ulong), ctypes.POINTER(ulong),
                                               ctypes.POINTER(ctypes.c_void_p)]
        self.lib.XGetWindowProperty.restype = ctypes.c_int
        self.lib.XFree.argtypes = [pointer]
        self.lib.XCloseDisplay.argtypes = [pointer]
        self.display = self.lib.XOpenDisplay(b":99")
        if not self.display:
            raise runtime.ProbeError("private_x_identity_unavailable")
        self.root = self.lib.XDefaultRootWindow(self.display)
        self.atom = self.lib.XInternAtom(self.display, b"_NET_WM_PID", 0)

    def children(self, window):
        """Return bounded anonymous child IDs; never request titles or properties."""
        root, parent = ctypes.c_ulong(), ctypes.c_ulong()
        children = ctypes.POINTER(ctypes.c_ulong)()
        count = ctypes.c_uint()
        success = self.lib.XQueryTree(self.display, window, ctypes.byref(root), ctypes.byref(parent),
                                     ctypes.byref(children), ctypes.byref(count))
        try:
            return [int(children[index]) for index in range(min(count.value, WINDOW_LIMIT))] if success else []
        finally:
            if children:
                self.lib.XFree(children)

    def window_pid(self, window):
        """Read only a single CARDINAL PID, rejecting all other property encodings."""
        actual_type, count, remaining = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_ulong()
        actual_format, data = ctypes.c_int(), ctypes.c_void_p()
        # XA_CARDINAL=6 is the standard X11 atom; a format-32 value is a C ulong.
        result = self.lib.XGetWindowProperty(self.display, window, self.atom, 0, 1, 0, 6,
                                            ctypes.byref(actual_type), ctypes.byref(actual_format),
                                            ctypes.byref(count), ctypes.byref(remaining), ctypes.byref(data))
        try:
            if (result == 0 and actual_type.value == 6 and actual_format.value == 32
                    and count.value == 1 and remaining.value == 0 and data.value):
                return int(ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))[0])
            return None
        finally:
            if data.value:
                self.lib.XFree(data)

    def snapshot(self):
        """Resolve a bounded X subtree and cross-check PID identity at sample time."""
        pending = self.children(self.root)
        root_count = len(pending)
        seen, records = set(), []
        while pending and len(seen) < WINDOW_LIMIT:
            window = pending.pop(0)
            if window in seen:
                continue
            seen.add(window)
            pid = self.window_pid(window)
            identity = process_identity(pid) if pid is not None else None
            if identity is not None:
                records.append({"window_id": window, "identity": identity})
            pending.extend(self.children(window))
        return {"root_children": root_count, "visited_windows": len(seen),
                "qq_pid_properties": records}

    def close(self):
        """Close only this sampler's authenticated private display connection."""
        if self.display:
            self.lib.XCloseDisplay(self.display)
            self.display = None


def sample_identity():
    """Sample as QQ's UID so /proc exe reads do not require host ptrace privileges."""
    x = None
    try:
        x = PrivateXIdentity()
        return {"processes": process_snapshot(), "windows": x.snapshot(), "available": True}
    except (OSError, ValueError, runtime.ProbeError):
        return {"processes": process_snapshot(), "windows": None, "available": False}
    finally:
        if x is not None:
            x.close()


def unprivileged_sample(env, timeout=0.75):
    """Fork a bounded trusted sampler, reusing the same verified privilege hook."""
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:
        # This callback runs only in the imported PID1 supervisor; the same hook
        # checks its parent and establishes UID/GID65534, NNP and cap-zero.
        os.close(read_fd)
        try:
            runtime.drop_runtime_privileges()
            # Xlib reads its private cookie path from the supplied clean environment.
            os.environ.clear()
            os.environ.update(env)
            encoded = json.dumps(sample_identity(), separators=(",", ":")).encode("ascii")
            if len(encoded) <= SUMMARY_LIMIT:
                # One bounded record may exceed PIPE_BUF, so handle partial writes.
                position = 0
                while position < len(encoded):
                    position += os.write(write_fd, encoded[position:])
        except (OSError, ValueError, runtime.ProbeError):
            pass
        finally:
            os.close(write_fd)
            os._exit(0)
    os.close(write_fd)
    # Polling nonblocking reads avoids a wedged X client extending the watchdog.
    os.set_blocking(read_fd, False)
    captured = bytearray()
    deadline = time.monotonic() + timeout
    ended = False
    try:
        while time.monotonic() < deadline:
            try:
                chunk = os.read(read_fd, SUMMARY_LIMIT + 1 - len(captured))
                if not chunk:
                    break
                captured.extend(chunk)
                if len(captured) > SUMMARY_LIMIT:
                    break
            except BlockingIOError:
                time.sleep(0.01)
        waited, _ = os.waitpid(pid, os.WNOHANG)
        ended = waited == pid
        if len(captured) > SUMMARY_LIMIT:
            return None
        return json.loads(captured) if captured else None
    except (OSError, ValueError):
        return None
    finally:
        os.close(read_fd)
        if not ended:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.waitpid(pid, 0)


def merge_sample(observation, sample, *, baseline=False):
    """Retain bounded deduplicated identities and avoid counting unattributed windows."""
    if not isinstance(sample, dict):
        observation["unavailable_samples"] += 1
        return
    observation["samples"] += 1
    for identity in sample.get("processes", []):
        key = (identity["pid"], identity["start_ticks"])
        existing = {(item["pid"], item["start_ticks"]) for item in observation["qq_processes"]}
        if key not in existing and len(existing) < PROCESS_LIMIT:
            observation["qq_processes"].append(identity)
    windows = sample.get("windows")
    if windows is None:
        observation["unavailable_samples"] += 1
        return
    if baseline:
        observation["baseline_root_children"] = windows["root_children"]
        observation["baseline_qq_pid_properties"] = len(windows["qq_pid_properties"])
    observation["maximum_root_children"] = max(observation["maximum_root_children"] or 0,
                                                windows["root_children"])
    for record in windows["qq_pid_properties"]:
        if record not in observation["qq_window_pid_identities"] and len(observation["qq_window_pid_identities"]) < WINDOW_LIMIT:
            observation["qq_window_pid_identities"].append(record)


def observe_application(command, env, output, errors, timeout, report):
    """Observe only default QQ inside the imported jail; watchdog means completion."""
    if command != [QQ_EXE] or "ELECTRON_RUN_AS_NODE" in env or timeout != 30:
        raise runtime.ProbeError("application_invocation_contract_failed")
    observation = {"samples": 0, "unavailable_samples": 0, "baseline_root_children": None,
                   "baseline_qq_pid_properties": None, "maximum_root_children": None,
                   "qq_processes": [], "qq_window_pid_identities": [],
                   "titles_requested": False, "scope": "private_pid_and_x_namespaces"}
    report.update(base_report())
    report["identity_observation"] = observation
    merge_sample(observation, unprivileged_sample(env), baseline=True)
    start = time.monotonic()
    process = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=output,
                               stderr=errors, preexec_fn=runtime.drop_runtime_privileges, close_fds=True)
    report.update(application_invoked=True, runtime_executed=True, application_pid=process.pid)
    report["isolation"].update(unprivileged=True, no_new_privileges=True,
                              effective_capabilities_zero=True)
    try:
        while process.poll() is None and time.monotonic() - start < timeout:
            remaining = timeout - (time.monotonic() - start)
            merge_sample(observation, unprivileged_sample(env, timeout=min(0.75, max(0, remaining))))
            remaining = timeout - (time.monotonic() - start)
            if remaining > 0:
                time.sleep(min(SAMPLE_INTERVAL, remaining))
        watchdog = process.poll() is None
        report.update(status="observed", experiment_completed=True,
                      completion="watchdog_30s" if watchdog else "application_exited",
                      application_exit_code=None if watchdog else process.returncode,
                      elapsed_seconds=round(time.monotonic() - start, 2))
    finally:
        # PID1 exit also kills every descendant; no process is targeted outside jail.
        if process.poll() is None:
            process.kill()
        process.wait(timeout=1)
    # _NET_WM_PID is client-supplied: proc resolution is strong bounded correlation,
    # not cryptographic proof of X client ownership or a usable login interface.
    report["qq_gui_identity_verified"] = bool(observation["qq_window_pid_identities"])
    report["capability_scope"] = "isolation_cap0_describes_verified_launch_hook; descendant_capabilities_and_user_namespace_are_recorded_individually"
    report["x_pid_attribution_limit"] = "client_supplied_pid_correlated_with_same_sample_proc_exe_not_authenticated_client_ownership"
    errors.seek(0)
    diagnostic = errors.read(runtime.OUTPUT_LIMIT).decode("ascii", errors="ignore")
    report["diagnostic_categories"] = {
        "dbus_unavailable": "Failed to connect to the bus:" in diagnostic,
        "fontconfig_unavailable": "Fontconfig error:" in diagnostic,
        "shared_memory_unavailable": "Unable to access(W_OK|X_OK) /dev/shm" in diagnostic,
        "crash_reporter_activity": "third_party/crashpad/" in diagnostic}
    report["interpretation"] = "Default pristine app invocation observed; watchdog is experiment completion, not application failure or readiness. No nonce expected. Native login and full sendrecv remain outstanding."


def child_main(args):
    """Delegate all jail setup to the existing runtime implementation and callback."""
    return runtime.child_probe(runtime.validate_root(args.qq_root), Path(args.jail),
                               args.nonce, json.loads(args.parent_ns), args.mount_tool,
                               filesystem_mode="pivot-root", pivot_tool=args.pivot_tool,
                               umount_tool=args.umount_tool, private_display=True,
                               extended_startup=True, private_runtime_services=True,
                               application_observer=observe_application)


def observe(qq_root, *, execute=False):
    """Validate or run the fixed imported isolation helper, bounded by an outer timer."""
    report = base_report()
    try:
        if sys.platform != "linux":
            raise runtime.ProbeError("linux_required")
        root = runtime.validate_root(qq_root)
        if not execute:
            return report
        if os.geteuid() != 0:
            raise runtime.ProbeError("root_required_for_isolation")
        tools = {name: shutil.which(name, path="/usr/sbin:/usr/bin:/sbin:/bin")
                 for name in ("unshare", "mount", "pivot_root", "umount")}
        if any(not value for value in tools.values()):
            raise runtime.ProbeError("isolation_tools_missing")
        parent_ids = runtime.namespace_ids()
        parent_ids["ipc"] = os.readlink("/proc/self/ns/ipc")
        with tempfile.TemporaryDirectory(prefix="qq-application-observation-") as temporary:
            stage = Path(temporary)
            jail = stage / "root"
            jail.mkdir()
            # Stage imports, not a second implementation; all isolation stays shared.
            shutil.copyfile(Path(__file__).resolve(), stage / "qq_application_observation.py")
            shutil.copyfile(Path(runtime.__file__).resolve(), stage / "qq_isolated_runtime_probe.py")
            command = [tools["unshare"], "--ipc", "--mount", "--net", "--pid", "--fork",
                       "--kill-child", "--mount-proc", sys.executable,
                       str(stage / "qq_application_observation.py"), "--child", "--qq-root", str(root),
                       "--jail", str(jail), "--nonce", secrets.token_hex(16),
                       "--parent-ns", json.dumps(parent_ids), "--mount-tool", tools["mount"],
                       "--pivot-tool", tools["pivot_root"], "--umount-tool", tools["umount"]]
            environment = runtime.minimal_environment()
            environment.pop("ELECTRON_RUN_AS_NODE", None)
            result = subprocess.run(command, env=environment, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    timeout=40, start_new_session=True, close_fds=True, check=False)
            if result.returncode not in (0, 1) or len(result.stdout) > runtime.HELPER_OUTPUT_LIMIT:
                raise runtime.ProbeError("application_helper_failed")
            report = json.loads(result.stdout)
            if not isinstance(report, dict) or report.get("mode") != base_report()["mode"]:
                raise runtime.ProbeError("application_helper_invalid_report")
    except runtime.ProbeError as exc:
        report.update(status="refused", errors=[str(exc)])
    except subprocess.TimeoutExpired:
        report.update(status="failed", errors=["isolation_setup_or_helper_timeout"], experiment_completed=False)
    except (OSError, ValueError, subprocess.SubprocessError):
        report.update(status="failed", errors=["application_observation_failed"], experiment_completed=False)
    return report


def main(argv=None):
    """Provide explicit execution consent and fixed private child arguments only."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qq-root", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    for name in ("jail", "nonce", "parent-ns", "mount-tool", "pivot-tool", "umount-tool"):
        parser.add_argument("--" + name, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        report = child_main(args) if args.child else observe(args.qq_root, execute=args.execute)
    except (OSError, ValueError, subprocess.SubprocessError):
        report = base_report()
        report.update(status="failed", errors=["application_child_failed"])
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] in ("not_executed", "observed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
