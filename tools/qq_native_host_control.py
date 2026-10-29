"""Bounded native binding-load control in pristine QQ's isolated native host.

The existing runtime owns pivot-root, private display/services, namespace checks
and privilege dropping. This file supplies only its fixed trusted callback. No
native export, getter, login, account/profile, or network backend is exercised.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time

try:
    from tools import qq_application_observation as application
except ModuleNotFoundError as exc:
    if exc.name != "tools":
        raise
    import qq_application_observation as application

runtime = application.runtime
# Only these authored inspector bytes and fixed commands may enter the jail.
INSPECTOR_SOURCE = Path(__file__).with_name("qq_private_inspector.js")
INSPECTOR_PATH = "/tmp/qq-private-inspector.js"
INSPECTOR_LIMIT = 16 * 1024
INSPECTOR_TIMEOUT = 12
QQ_TIMEOUT = 30
OUTER_TIMEOUT = 45
QQ_COMMAND = [application.QQ_EXE, "--inspect=127.0.0.1:9229"]
IP_TOOL = "/usr/sbin/ip"


def base_report():
    """Never equate a load-only experiment with login or backend readiness."""
    report = runtime.base_report()
    report.update(mode="isolated_native_host_control", application_invoked=False,
                  experiment_completed=False, native_host_binding_load_verified=False,
                  native_login_verified=False, full_sendrecv_verified=False,
                  inspector_record=None, inspector_invoked=False,
                  invocation={"argv": QQ_COMMAND, "electron_run_as_node_present": False},
                  bounds_seconds={"qq": QQ_TIMEOUT, "inspector": INSPECTOR_TIMEOUT,
                                  "outer": OUTER_TIMEOUT})
    return report


def validate_private_network(parent_netns):
    """Permit loopback activation only after proving a different loopback-only netns."""
    current = os.readlink("/proc/self/ns/net")
    if (not re.fullmatch(r"net:\[\d+\]", parent_netns or "")
            or current == parent_netns):
        raise runtime.ProbeError("private_network_identity_failed")
    interfaces = [line.split(":", 1)[0].strip() for line in
                  Path("/proc/net/dev").read_text(encoding="ascii").splitlines()[2:]]
    if interfaces != ["lo"]:
        raise runtime.ProbeError("private_network_interfaces_failed")
    # Only loopback routes may exist; never configure a route or external device.
    for name in ("route", "ipv6_route"):
        lines = Path("/proc/net", name).read_text(encoding="ascii").splitlines()
        for line in lines[1:] if name == "route" else lines:
            fields = line.split()
            device = fields[0] if name == "route" else fields[-1]
            if device != "lo":
                raise runtime.ProbeError("private_network_routes_failed")
    return current


def prepare_inspector(source, parent_netns):
    """Stage fixed source before privilege drop and raise only private loopback."""
    validate_private_network(parent_netns)
    if not source or len(source) > 64 * 1024:
        raise runtime.ProbeError("inspector_source_missing_or_oversize")
    destination = Path(INSPECTOR_PATH)
    # Exclusive creation prevents substitution through the world-writable tmpfs.
    with destination.open("xb") as stream:
        stream.write(source)
    destination.chmod(0o444)
    subprocess.run([IP_TOOL, "link", "set", "dev", "lo", "up"], check=True,
                   timeout=2, env={"PATH": "/usr/sbin:/usr/bin:/bin", "LANG": "C"},
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, close_fds=True)
    validate_private_network(parent_netns)
    return hashlib.sha256(source).hexdigest()


def parse_inspector(raw):
    """Reject extra output or oversized records instead of retaining diagnostics."""
    prefix = b"QQ_NATIVE_INSPECTOR:"
    if not raw or len(raw) > INSPECTOR_LIMIT or not raw.startswith(prefix):
        raise runtime.ProbeError("inspector_output_invalid")
    try:
        record = json.loads(raw[len(prefix):])
    except (ValueError, UnicodeError) as exc:
        raise runtime.ProbeError("inspector_output_invalid") from exc
    # The companion author sanitizes nested values; require its exact envelope
    # and deny readiness escalation even if a future helper changes accidentally.
    if (not isinstance(record, dict) or set(record) != {
            "schema_version", "ok", "error", "runtime", "binding", "readiness"}
            or record["schema_version"] != 1 or type(record["ok"]) is not bool
            or not all(isinstance(record[key], dict) for key in ("runtime", "binding", "readiness"))):
        raise runtime.ProbeError("inspector_output_invalid")
    readiness = record["readiness"]
    if (set(readiness) != {"runtime_host_verified", "native_binding_verified", "session_initialized",
                          "safe_to_login", "independent_backend_ready", "production_cutover_ready"}
            or any(type(value) is not bool for value in readiness.values())
            or any(readiness[key] for key in ("session_initialized", "safe_to_login",
                                              "independent_backend_ready", "production_cutover_ready"))):
        raise runtime.ProbeError("inspector_output_invalid")
    if record["error"] not in (None, "environment_refused", "discovery_timeout", "discovery_limit",
                               "discovery_invalid", "socket_unavailable", "socket_timeout", "socket_closed",
                               "socket_error", "message_limit", "protocol_invalid", "evaluation_failed",
                               "runtime_mismatch", "binding_load_failed", "binding_invalid",
                               "internal_error", "output_limit"):
        raise runtime.ProbeError("inspector_output_invalid")
    host, binding = record["runtime"], record["binding"]
    if (set(host) != {"versions", "exec_path_matches"}
            or type(host["exec_path_matches"]) is not bool
            or not isinstance(host["versions"], dict)
            or set(host["versions"]) - {"node", "electron", "modules", "napi", "chrome", "v8", "uv"}
            or any(not isinstance(value, str) or not re.fullmatch(r"[0-9A-Za-z.+_-]{1,64}", value)
                   for value in host["versions"].values())):
        raise runtime.ProbeError("inspector_output_invalid")
    candidates = {"NodeIQQNTWrapperEngine", "NodeIKernelLoginService", "NodeIKernelWrapperSession",
                  "NodeIQQNTWrapperSession", "NodeIQQNTStartupSessionWrapper"}
    if (set(binding) != {"load_attempted", "loaded", "exports", "truncated", "candidates"}
            or any(type(binding[key]) is not bool for key in ("load_attempted", "loaded", "truncated"))
            or not isinstance(binding["exports"], list) or len(binding["exports"]) > 64
            or not isinstance(binding["candidates"], dict) or set(binding["candidates"]) != candidates
            or any(type(value) is not bool for value in binding["candidates"].values())):
        raise runtime.ProbeError("inspector_output_invalid")
    names = set()
    for item in binding["exports"]:
        if (not isinstance(item, dict) or set(item) != {"name", "type", "kind"}
                or not isinstance(item["name"], str)
                or not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]{0,95}", item["name"])
                or item["name"] in names or item["kind"] not in ("data", "accessor")
                or item["type"] not in ("undefined", "object", "boolean", "number", "bigint",
                                        "string", "symbol", "function", "accessor")
                or (item["kind"] == "accessor") != (item["type"] == "accessor")):
            raise runtime.ProbeError("inspector_output_invalid")
        names.add(item["name"])
    host_verified = readiness["runtime_host_verified"]
    binding_verified = readiness["native_binding_verified"]
    if (host_verified != host["exec_path_matches"]
            or (host_verified and not {"node", "electron", "modules", "napi"}.issubset(host["versions"]))
            or binding_verified != binding["loaded"]
            or (binding_verified and (not host_verified or not binding["load_attempted"]))
            or (binding["load_attempted"] and not host_verified)
            or record["ok"] != binding_verified
            or (record["ok"] and record["error"] is not None)
            or (not record["ok"] and record["error"] is None)):
        raise runtime.ProbeError("inspector_output_invalid")
    return record


def observe_native_host(command, env, output, errors, timeout, report, *, source, parent_netns):
    """Run exact QQ plus documented inspector flag; inspect without calling exports."""
    if command != [application.QQ_EXE] or "ELECTRON_RUN_AS_NODE" in env or timeout != QQ_TIMEOUT:
        raise runtime.ProbeError("native_host_invocation_contract_failed")
    # Keep isolation/display/service evidence already assembled by the shared gate.
    report.update({key: value for key, value in base_report().items() if key not in report})
    report.update(mode="isolated_native_host_control", invocation=base_report()["invocation"])
    report["inspector_source_sha256"] = prepare_inspector(source, parent_netns)
    report["private_loopback_up"] = True
    inspector_env = dict(env, QQ_ISOLATED_NATIVE_INSPECTOR="1",
                         QQ_INSPECTOR_PARENT_NETNS=parent_netns, NODE_NO_WARNINGS="1")
    observation = {"samples": 0, "unavailable_samples": 0, "baseline_root_children": None,
                   "baseline_qq_pid_properties": None, "maximum_root_children": None,
                   "qq_processes": [], "qq_window_pid_identities": [],
                   "titles_requested": False, "scope": "private_pid_and_x_namespaces"}
    report["identity_observation"] = observation
    application.merge_sample(observation, application.unprivileged_sample(env), baseline=True)
    start = time.monotonic()
    process = subprocess.Popen(QQ_COMMAND, env=env, stdin=subprocess.DEVNULL,
                               stdout=output, stderr=errors, close_fds=True,
                               preexec_fn=runtime.drop_runtime_privileges)
    report.update(application_invoked=True, runtime_executed=True)
    report["isolation"].update(unprivileged=True, no_new_privileges=True,
                               effective_capabilities_zero=True)
    try:
        with tempfile.TemporaryFile() as inspector_output:
            report["inspector_invoked"] = True
            try:
                result = subprocess.run(["/usr/bin/node", INSPECTOR_PATH], env=inspector_env,
                                        stdin=subprocess.DEVNULL, stdout=inspector_output,
                                        stderr=subprocess.DEVNULL, close_fds=True,
                                        preexec_fn=runtime.drop_runtime_privileges,
                                        timeout=INSPECTOR_TIMEOUT, check=False)
                inspector_output.seek(0)
                record = parse_inspector(inspector_output.read(INSPECTOR_LIMIT + 1))
                report["inspector_record"] = record
                report["inspector_exit_code"] = result.returncode
            except subprocess.TimeoutExpired:
                report["errors"] = ["inspector_timeout"]
            except runtime.ProbeError as exc:
                report["errors"] = [str(exc)]
        while process.poll() is None and time.monotonic() - start < QQ_TIMEOUT:
            remaining = QQ_TIMEOUT - (time.monotonic() - start)
            application.merge_sample(observation, application.unprivileged_sample(env, timeout=min(0.75, remaining)))
            remaining = QQ_TIMEOUT - (time.monotonic() - start)
            if remaining > 0:
                time.sleep(min(1, remaining))
        report.update(experiment_completed=True, elapsed_seconds=round(time.monotonic() - start, 2),
                      completion="watchdog_30s" if process.poll() is None else "application_exited")
    finally:
        # This process and all services live inside PID1's disposable namespace.
        if process.poll() is None:
            process.kill()
        process.wait(timeout=1)
    report["qq_gui_identity_verified"] = bool(observation["qq_window_pid_identities"])
    record = report["inspector_record"] or {}
    binding = record.get("binding", {})
    loaded = isinstance(binding, dict) and binding.get("loaded") is True
    readiness = record.get("readiness", {})
    host = record.get("runtime", {})
    report["native_host_binding_load_verified"] = bool(
        loaded and binding.get("load_attempted") is True and record.get("ok") is True
        and record.get("error") is None and host.get("exec_path_matches") is True
        and readiness.get("runtime_host_verified") is True
        and readiness.get("native_binding_verified") is True
        and report.get("inspector_exit_code") == 0)
    report["status"] = "observed" if report["native_host_binding_load_verified"] else "failed"
    if not report["native_host_binding_load_verified"] and not report["errors"]:
        report["errors"] = ["native_host_binding_load_unverified"]
    report["interpretation"] = "Load-only native host evidence; no native exports/getters invoked. No login, session, transport or independent backend readiness is established."


def child_main(args):
    """Read fixed inspector source before pivot and reuse the full shared jail gate."""
    source = INSPECTOR_SOURCE.read_bytes()
    parent_ids = json.loads(args.parent_ns)
    def observer(command, env, output, errors, timeout, report):
        """Bind only authored bytes and trusted outer namespace metadata."""
        return observe_native_host(command, env, output, errors, timeout, report,
                                   source=source, parent_netns=parent_ids["net"])
    return runtime.child_probe(runtime.validate_root(args.qq_root), Path(args.jail), args.nonce,
                               parent_ids, args.mount_tool, filesystem_mode="pivot-root",
                               pivot_tool=args.pivot_tool, umount_tool=args.umount_tool,
                               private_display=True, extended_startup=True,
                               private_runtime_services=True, application_observer=observer)


def observe(qq_root, *, execute=False):
    """Stage imported helpers without changing QQ, then apply an outer group watchdog."""
    report = base_report()
    try:
        if sys.platform != "linux":
            raise runtime.ProbeError("linux_required")
        root = runtime.validate_root(qq_root)
        if not execute:
            return report
        if os.geteuid() != 0:
            raise runtime.ProbeError("root_required_for_isolation")
        if not INSPECTOR_SOURCE.is_file() or INSPECTOR_SOURCE.is_symlink():
            raise runtime.ProbeError("inspector_source_missing")
        tools = {name: shutil.which(name, path="/usr/sbin:/usr/bin:/sbin:/bin")
                 for name in ("unshare", "mount", "pivot_root", "umount")}
        if any(not value for value in tools.values()) or not Path(IP_TOOL).is_file():
            raise runtime.ProbeError("isolation_tools_missing")
        parent_ids = runtime.namespace_ids()
        parent_ids["ipc"] = os.readlink("/proc/self/ns/ipc")
        with tempfile.TemporaryDirectory(prefix="qq-native-host-control-") as temporary:
            stage = Path(temporary)
            jail = stage / "root"
            jail.mkdir()
            for source in (Path(__file__).resolve(), Path(application.__file__).resolve(),
                           Path(runtime.__file__).resolve(), INSPECTOR_SOURCE):
                shutil.copyfile(source, stage / source.name)
            command = [tools["unshare"], "--ipc", "--mount", "--net", "--pid", "--fork",
                       "--kill-child", "--mount-proc", sys.executable,
                       str(stage / Path(__file__).name), "--child", "--qq-root", str(root),
                       "--jail", str(jail), "--nonce", secrets.token_hex(16),
                       "--parent-ns", json.dumps(parent_ids), "--mount-tool", tools["mount"],
                       "--pivot-tool", tools["pivot_root"], "--umount-tool", tools["umount"]]
            environment = runtime.minimal_environment()
            environment.pop("ELECTRON_RUN_AS_NODE", None)
            process = subprocess.Popen(command, env=environment, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       start_new_session=True, close_fds=True)
            try:
                raw, _ = process.communicate(timeout=OUTER_TIMEOUT)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=2)
            if process.returncode not in (0, 1) or len(raw) > runtime.HELPER_OUTPUT_LIMIT:
                raise runtime.ProbeError("native_host_helper_failed")
            parsed = json.loads(raw)
            if not isinstance(parsed, dict) or parsed.get("mode") != report["mode"]:
                raise runtime.ProbeError("native_host_helper_invalid_report")
            report = parsed
    except runtime.ProbeError as exc:
        report.update(status="refused", errors=[str(exc)])
    except subprocess.TimeoutExpired:
        report.update(status="failed", errors=["native_host_outer_timeout"])
    except (OSError, ValueError, subprocess.SubprocessError):
        report.update(status="failed", errors=["native_host_control_failed"])
    return report


def main(argv=None):
    """Require explicit execution consent; internal flags are fixed staging metadata."""
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
        report.update(status="failed", errors=["native_host_child_failed"])
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] in ("not_executed", "observed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
