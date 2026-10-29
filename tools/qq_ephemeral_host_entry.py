"""Fixed independent wrapper-load main, mounted only in a disposable QQ jail.

No production files, account state, native getters/functions, or login are used.
The shared runtime remains the only implementation of isolation and privileges.
"""

import argparse
import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import stat
import struct
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

# Fixed authored source and internal targets; CLI cannot nominate another script.
SOURCE = Path(__file__).with_name("qq_wrapper_host_entry.js")
MAIN = "/tmp/qq-wrapper-host-entry.js"
# Electron v39 init.ts joins packagePath/main; an existing relative target is required.
ENTRY = "/opt/QQ/resources/app/app_launcher/index.js"
PACKAGE_MAIN = "./app_launcher/index.js"
MANIFEST = "/opt/QQ/resources/app/package.json"
CONTROL = "/tmp/package.control.json"
RESULT = "/tmp/qq-wrapper-host-result.json"
LIMIT = 16 * 1024
QQ_TIMEOUT = 30
OUTER_TIMEOUT = 45
CANDIDATES = {"NodeIQQNTWrapperEngine", "NodeIKernelLoginService",
              "NodeIQQNTWrapperSession", "NodeIQQNTStartupSessionWrapper"}
VERSION_KEYS = {"node", "electron", "modules", "napi", "chrome", "v8", "uv"}


def validate_mode(control_mode):
    """Accept only explicit internally authored controls; reject script/path injection."""
    if control_mode not in ("wrapper", "initialize", "initialize_factories", "connect_offline", "connect_ready_offline", "prepared_connect_offline", "prepared_phase_offline", "host_dispatch_offline", "host_dispatch_roles_offline", "host_dispatch_capture_offline", "host_dispatch_identity_offline", "host_dispatch_resources_offline", "host_dispatch_waits_offline", "host_dispatch_exit_offline", "host_dispatch_fatal_offline", "vendor_bootstrap", "vendor_stall", "vendor_window_roles", "bus_admission_offline", "bus_admission_capture_offline", "bus_identity_offline"):
        raise runtime.ProbeError("control_mode_invalid")
    return control_mode


def fixed_sources(control_mode):
    """Resolve fixed trusted source names, including copied child-local modules."""
    validate_mode(control_mode)
    if control_mode in ("bus_admission_offline", "bus_admission_capture_offline", "bus_identity_offline"):
        # No QQ entry overlay or native source is needed for this no-QQ control.
        return {}
    if control_mode == "wrapper":
        return {MAIN: SOURCE}
    directory = Path(__file__).parent
    if control_mode in ("vendor_bootstrap", "vendor_stall", "vendor_window_roles"):
        return {MAIN: directory / "qq_vendor_bootstrap_entry.js"}
    if control_mode in ("host_dispatch_offline", "host_dispatch_roles_offline", "host_dispatch_capture_offline", "host_dispatch_identity_offline", "host_dispatch_resources_offline", "host_dispatch_waits_offline", "host_dispatch_exit_offline", "host_dispatch_fatal_offline"):
        return {MAIN: directory / "qq_host_dispatch_entry.js"}
    module_directory = directory.parent / "qq_transport"
    if not module_directory.is_dir():
        module_directory = directory
    sources = {MAIN: directory / ({"initialize_factories": "qq_native_factory_entry.js", "connect_offline": "qq_native_connect_entry.js", "connect_ready_offline": "qq_native_ready_entry.js", "prepared_connect_offline": "qq_prepared_connect_entry.js", "prepared_phase_offline": "qq_prepared_connect_entry.js"}.get(control_mode, "qq_native_init_entry.js")),
               "/tmp/native_login.js": module_directory / "native_login.js",
               "/tmp/native_metadata.js": module_directory / "native_metadata.js"}
    if control_mode in ("prepared_connect_offline", "prepared_phase_offline"):
        sources["/tmp/native_host.js"] = module_directory / "native_host.js"
    return sources


def base_report(control_mode="wrapper"):
    """Keep this load-only milestone explicitly separate from backend readiness."""
    validate_mode(control_mode)
    report = runtime.base_report()
    report.update(mode="ephemeral_wrapper_host_entry", application_invoked=False,
                  experiment_completed=False, native_host_binding_load_verified=False,
                  native_login_verified=False, session_initialized=False,
                  full_sendrecv_verified=False, host_record=None,
                  invocation={"argv": ["/opt/QQ/qq"], "electron_run_as_node_present": False},
                  bounds_seconds={"qq": QQ_TIMEOUT, "outer": OUTER_TIMEOUT})
    if control_mode in ("initialize", "initialize_factories", "connect_offline", "connect_ready_offline", "prepared_connect_offline", "prepared_phase_offline"):
        report.update(mode="ephemeral_native_init", native_initialize_verified=False,
                      native_listener_registered=False, native_listener_disposed=False)
    if control_mode == "initialize_factories":
        report.update(mode="ephemeral_native_initialize_factories", startup_factory_created=False,
                      session_factory_available=False)
    if control_mode == "connect_offline":
        report.update(mode="ephemeral_native_connect_offline", native_connect_observation_verified=False)
    if control_mode == "connect_ready_offline":
        report.update(mode="ephemeral_native_connect_ready_offline", native_connect_observation_verified=False, electron_ready_verified=False)
    if control_mode in ("prepared_connect_offline", "prepared_phase_offline"):
        report.update(mode="ephemeral_" + control_mode, native_connect_observation_verified=False,
                      native_host_prepared_verified=False, bounded_progress_verified=False)
    if control_mode in ("vendor_bootstrap", "vendor_stall", "vendor_window_roles"):
        report.update(mode={"vendor_stall": "ephemeral_vendor_stall", "vendor_window_roles": "ephemeral_vendor_window_roles"}.get(control_mode, "ephemeral_vendor_bootstrap"), electron_ready_verified=False, vendor_bootstrap_observation_complete=False)
    if control_mode in ("host_dispatch_offline", "host_dispatch_roles_offline", "host_dispatch_capture_offline", "host_dispatch_identity_offline", "host_dispatch_resources_offline", "host_dispatch_waits_offline", "host_dispatch_exit_offline", "host_dispatch_fatal_offline"):
        report.update(mode="ephemeral_" + control_mode, host_dispatch_observation_complete=False, dispatch_progress=None)
    if control_mode in ("bus_admission_offline", "bus_admission_capture_offline", "bus_identity_offline"):
        report.update(mode="isolated_bus_identity_control" if control_mode == "bus_identity_offline" else "isolated_bus_admission_capture_control" if control_mode == "bus_admission_capture_offline" else "isolated_bus_admission_control",
                      invocation={"argv": [], "electron_run_as_node_present": False},
                      bounds_seconds={"client": 4, "stage": 1, "outer": OUTER_TIMEOUT},
                      same_connection_stage_proof=False, bus_admission=None)
    return report


def digest(path):
    """Hash software bytes without interpreting or disclosing their contents."""
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def unique_object(pairs):
    """Reject duplicate JSON keys so strict records have only one interpretation."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise runtime.ProbeError("duplicate_json_key")
        result[key] = value
    return result


def validate_manifest(raw):
    """Pin the observed pristine Tencent package and preserve every unrelated key."""
    if len(raw) > 64 * 1024:
        raise runtime.ProbeError("manifest_invalid")
    package = json.loads(raw, object_pairs_hook=unique_object)
    if (not isinstance(package, dict) or package.get("name") != "QQ"
            or package.get("version") != "3.2.34-53644"
            or package.get("main") != "./application.asar/app_launcher/index.js"
            or package.get("productName") != "QQ" or package.get("platform") != "linux"
            or package.get("buildVersion") != "53644" or package.get("eleArch") != "x64"
            or not isinstance(package.get("author"), dict) or package["author"].get("name") != "Tencent"):
        raise runtime.ProbeError("unexpected_pristine_package_entry")
    control = dict(package)
    control["main"] = PACKAGE_MAIN
    return (json.dumps(control, ensure_ascii=True, separators=(",", ":")) + "\n").encode()


def parse_record(raw, nonce):
    """Accept exactly one nonce-bound strict bounded descriptor-only JSON record."""
    prefix = ("QQ_WRAPPER_HOST_" + nonce + ":").encode("ascii")
    if not raw or len(raw) > LIMIT or not raw.startswith(prefix) or raw.count(b"\n") != 1 or not raw.endswith(b"\n"):
        raise runtime.ProbeError("host_record_invalid")
    record = json.loads(raw[len(prefix):], object_pairs_hook=unique_object)
    if (not isinstance(record, dict) or set(record) != {"schema_version", "nonce", "ok", "error", "runtime", "binding"}
            or type(record["schema_version"]) is not int or record["schema_version"] != 1
            or record["nonce"] != nonce or type(record["ok"]) is not bool
            or record["error"] not in (None, "runtime_mismatch", "binding_load_failed", "binding_invalid")):
        raise runtime.ProbeError("host_record_invalid")
    host, binding = record["runtime"], record["binding"]
    if (not isinstance(host, dict) or set(host) != {"versions", "exec_path_matches"}
            or type(host["exec_path_matches"]) is not bool or not isinstance(host["versions"], dict)
            or set(host["versions"]) - VERSION_KEYS
            or any(not isinstance(value, str) or not re.fullmatch(r"[0-9A-Za-z.+_-]{1,64}", value)
                   for value in host["versions"].values())):
        raise runtime.ProbeError("host_record_invalid")
    if (not isinstance(binding, dict) or set(binding) != {"load_attempted", "loaded", "exports", "truncated", "candidates"}
            or any(type(binding[key]) is not bool for key in ("load_attempted", "loaded", "truncated"))
            or not isinstance(binding["exports"], list) or len(binding["exports"]) > 64
            or not isinstance(binding["candidates"], dict) or set(binding["candidates"]) != CANDIDATES
            or any(type(value) is not bool for value in binding["candidates"].values())):
        raise runtime.ProbeError("host_record_invalid")
    names = set()
    for item in binding["exports"]:
        if (not isinstance(item, dict) or set(item) != {"name", "type", "kind"}
                or not isinstance(item["name"], str) or not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]{0,95}", item["name"])
                or item["name"] in names or item["kind"] not in ("data", "accessor")
                or item["type"] not in ("undefined", "object", "boolean", "number", "bigint", "string", "symbol", "function", "accessor")
                or (item["kind"] == "accessor") != (item["type"] == "accessor")):
            raise runtime.ProbeError("host_record_invalid")
        names.add(item["name"])
    valid_host = host["exec_path_matches"] and {"node", "electron", "modules", "napi"}.issubset(host["versions"])
    if (record["ok"] != (record["error"] is None)
            or (binding["load_attempted"] and not valid_host)
            or (binding["loaded"] and not binding["load_attempted"])
            or (record["ok"] and not binding["loaded"])
            or (not binding["loaded"] and (binding["exports"] or binding["truncated"] or any(binding["candidates"].values())))
            or any(binding["candidates"][name] != (name in names) for name in CANDIDATES if not binding["truncated"])):
        raise runtime.ProbeError("host_record_invalid")
    return record


def validate_login_snapshot(snapshot, *, context="initialize", require_diagnostics=False):
    """Validate one shared safe projection, with context-specific lifecycle limits."""
    keys = {"phase", "disposed", "qr_available", "authenticated", "failure_code", "initialization_step", "failed_stage", "registration_call_returned", "listener_id_type", "listener_handle_available", "listener_registration_verified", "native_listener_removed", "cleanup_required"}
    diagnostics = {"connect_call_returned", "connect_result_type", "native_callback_counts", "listener_lifetime"}
    callbacks = {"onLoginConnected", "onLoginConnecting", "onLoginDisConnected", "onQRCodeGetPicture", "onQRCodeLoginSucceed", "onLoginFailed", "onQRCodeSessionFailed"}
    if not isinstance(snapshot, dict) or set(snapshot) not in (keys, keys | diagnostics) or (require_diagnostics and not diagnostics.issubset(snapshot)):
        raise runtime.ProbeError("login_snapshot_invalid")
    if (any(type(snapshot[key]) is not bool for key in ("disposed", "qr_available", "authenticated", "registration_call_returned", "listener_handle_available", "listener_registration_verified", "native_listener_removed"))
            or snapshot["qr_available"] or snapshot["authenticated"]
            or snapshot["cleanup_required"] not in ("none", "remove_listener", "process_exit")
            or snapshot["listener_id_type"] not in ("not_attempted", "threw", "number", "string", "undefined", "other")
            or snapshot["registration_call_returned"] != (snapshot["listener_id_type"] in ("number", "string", "undefined", "other"))
            or (snapshot["listener_handle_available"] and snapshot["listener_id_type"] != "number")
            or (snapshot["listener_id_type"] == "undefined" and (snapshot["listener_handle_available"] or snapshot["native_listener_removed"] or snapshot["cleanup_required"] != "process_exit"))):
        raise runtime.ProbeError("login_snapshot_invalid")
    if diagnostics.issubset(snapshot):
        counts = snapshot["native_callback_counts"]
        if (snapshot["listener_lifetime"] not in ("token", "process")
                or type(snapshot["connect_call_returned"]) is not bool
                or snapshot["connect_result_type"] not in ("not_attempted", "threw", "boolean", "undefined", "other")
                or snapshot["connect_call_returned"] != (snapshot["connect_result_type"] in ("boolean", "undefined", "other"))
                or not isinstance(counts, dict) or set(counts) != callbacks
                or any(type(value) is not int or not 0 <= value <= 2147483647 for value in counts.values())
                or snapshot["listener_registration_verified"] != any(counts.values())
                or (context == "initialize" and (snapshot["connect_result_type"] != "not_attempted" or any(counts.values())))):
            raise runtime.ProbeError("login_snapshot_invalid")
    if context in ("connect", "disposed"):
        if (snapshot["initialization_step"] != "initialized" or snapshot["failed_stage"] is not None
                or snapshot["phase"] not in ("connecting", "connected", "disconnected", "failed")
                or snapshot["disposed"] != (context == "disposed")
                or (context == "disposed" and snapshot["phase"] != "disconnected")
                or snapshot["failure_code"] not in (None, "CONNECT_FAILED", "CONNECT_REJECTED", "LOGIN_FAILED", "QR_SESSION_FAILED", "INVALID_EVENT", "REMOVE_LISTENER_FAILED")):
            raise runtime.ProbeError("login_snapshot_invalid")
    return snapshot


def parse_initialize_record(raw, nonce):
    """Project only fixed stage codes and actual safe controller status, never raw."""
    prefix = ("QQ_NATIVE_INIT_" + nonce + ":").encode("ascii")
    if not raw or len(raw) > LIMIT or not raw.startswith(prefix) or raw.count(b"\n") != 1 or not raw.endswith(b"\n"):
        raise runtime.ProbeError("init_record_invalid")
    record = json.loads(raw[len(prefix):], object_pairs_hook=unique_object)
    if (not isinstance(record, dict) or set(record) != {"schema_version", "nonce", "ok", "error", "runtime", "binding", "initialization"}
            or type(record["schema_version"]) is not int or record["schema_version"] != 1
            or record["nonce"] != nonce or type(record["ok"]) is not bool
            or record["error"] not in (None, "runtime_mismatch", "binding_load_failed", "metadata_invalid", "controller_invalid", "initialize_failed", "dispose_failed")):
        raise runtime.ProbeError("init_record_invalid")
    host, binding, init = record["runtime"], record["binding"], record["initialization"]
    if (not isinstance(host, dict) or set(host) != {"versions", "exec_path_matches"}
            or type(host["exec_path_matches"]) is not bool or not isinstance(host["versions"], dict)
            or set(host["versions"]) - VERSION_KEYS
            or any(not isinstance(value, str) or not re.fullmatch(r"[0-9A-Za-z.+_-]{1,64}", value) for value in host["versions"].values())
            or not isinstance(binding, dict) or set(binding) != {"load_attempted", "loaded"}
            or any(type(value) is not bool for value in binding.values())
            or not isinstance(init, dict) or set(init) != {"attempted", "initialized", "snapshot", "listener_registered", "disposed"}
            or any(type(init[key]) is not bool for key in ("attempted", "initialized", "listener_registered", "disposed"))):
        raise runtime.ProbeError("init_record_invalid")
    snapshot = init["snapshot"]
    steps = {"factory_engine", "factory_login", "validate_methods", "engine_config", "login_config", "register_listener", "initialized"}
    if snapshot is not None:
        validate_login_snapshot(snapshot)
        if (not isinstance(snapshot, dict)
                or any(type(snapshot[key]) is not bool for key in ("listener_handle_available", "listener_registration_verified", "native_listener_removed"))
                or snapshot["cleanup_required"] not in ("none", "remove_listener", "process_exit")
                or type(snapshot["registration_call_returned"]) is not bool
                or snapshot["listener_id_type"] not in ("not_attempted", "threw", "number", "string", "undefined", "other")
                or snapshot["registration_call_returned"] != (snapshot["listener_id_type"] in ("number", "string", "undefined", "other"))
                or (snapshot["phase"] == "initialized" and snapshot["listener_id_type"] not in ("number", "undefined"))
                or snapshot["native_listener_removed"]
                or (snapshot["listener_handle_available"] and snapshot["listener_id_type"] != "number")
                or (snapshot["listener_id_type"] == "undefined" and (snapshot["listener_handle_available"] or snapshot["listener_registration_verified"] or snapshot["cleanup_required"] != "process_exit"))
                or snapshot["phase"] not in ("initialized", "failed")
                or snapshot["initialization_step"] not in steps
                or any(snapshot[key] is not False for key in ("disposed", "qr_available", "authenticated"))
                or (snapshot["phase"] == "initialized" and (snapshot["failure_code"] is not None or snapshot["failed_stage"] is not None or snapshot["initialization_step"] != "initialized"))
                or (snapshot["phase"] == "failed" and (snapshot["failure_code"] != "INITIALIZE_FAILED" or snapshot["failed_stage"] != snapshot["initialization_step"]))):
            raise runtime.ProbeError("init_record_invalid")
    valid_host = host["exec_path_matches"] and {"node", "electron", "modules", "napi"}.issubset(host["versions"])
    if (record["ok"] != (record["error"] is None)
            or (binding["load_attempted"] and not valid_host)
            or (binding["loaded"] and not binding["load_attempted"])
            or (init["attempted"] and not binding["loaded"])
            or (snapshot is not None and not init["attempted"])
            or (record["error"] == "runtime_mismatch" and binding["load_attempted"])
            or (record["error"] == "binding_load_failed" and (not binding["load_attempted"] or binding["loaded"]))
            or (record["error"] in ("metadata_invalid", "controller_invalid") and (not binding["loaded"] or init["attempted"]))
            or (record["error"] == "initialize_failed" and (not init["attempted"] or init["initialized"]))
            or (record["error"] == "dispose_failed" and (not init["initialized"] or init["disposed"]))
            or init["initialized"] != (snapshot is not None and snapshot["phase"] == "initialized")
            or init["listener_registered"] != (snapshot is not None and snapshot["listener_registration_verified"])
            or (init["initialized"] and not init["attempted"])
            or (init["disposed"] and not init["initialized"])
            or (record["ok"] and not (init["initialized"] and init["disposed"]))):
        raise runtime.ProbeError("init_record_invalid")
    return record


def stage_control(source, control, mount, parent_ids, report, *, auxiliary=None, control_mode="wrapper"):
    """Mount the fixed override only after the shared verified pivot and detach."""
    isolation = report.get("isolation", {})
    current = runtime.namespace_ids()
    current["ipc"] = os.readlink("/proc/self/ns/ipc")
    if (os.getpid() != 1 or os.geteuid() != 0 or set(current) != set(parent_ids)
            or any(current[key] == parent_ids[key] for key in current)
            or not all(isolation.get(key) is True for key in ("network", "mount", "pid", "ipc", "pivotRoot", "oldroot_detached"))
            or Path("/.old-root").exists()):
        raise runtime.ProbeError("ephemeral_mount_gate_failed")
    if not source or len(source) > 64 * 1024 or validate_manifest(Path(MANIFEST).read_bytes()) != control:
        raise runtime.ProbeError("ephemeral_source_invalid")
    validate_mode(control_mode)
    if control_mode in ("host_dispatch_offline", "host_dispatch_roles_offline", "host_dispatch_capture_offline", "host_dispatch_identity_offline", "host_dispatch_resources_offline", "host_dispatch_waits_offline", "host_dispatch_exit_offline", "host_dispatch_fatal_offline") and auxiliary:
        raise runtime.ProbeError("ephemeral_auxiliary_invalid")
    if control_mode in ("prepared_connect_offline", "prepared_phase_offline") and not auxiliary:
        raise runtime.ProbeError("ephemeral_auxiliary_invalid")
    if auxiliary is not None and auxiliary:
        # The extra module is mandatory only for this exact fixed experiment mode.
        expected = {"/tmp/native_login.js", "/tmp/native_metadata.js", "/tmp/qq-native-pristine-package.json"}
        if control_mode in ("prepared_connect_offline", "prepared_phase_offline"):
            expected.add("/tmp/native_host.js")
        if (set(auxiliary) != expected
                or any(not isinstance(content, bytes) or not content or len(content) > 256 * 1024 for content in auxiliary.values())
                or validate_manifest(auxiliary["/tmp/qq-native-pristine-package.json"]) != control):
            raise runtime.ProbeError("ephemeral_auxiliary_invalid")
    # O_EXCL plus sticky private /tmp prevents nonroot substitution of trusted files.
    for target, content in ((MAIN, source), (CONTROL, control), *tuple((auxiliary or {}).items())):
        with Path(target).open("xb") as stream:
            stream.write(content)
        os.chown(target, 0, 0)
        Path(target).chmod(0o444)
        info = Path(target).stat()
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o444:
            raise runtime.ProbeError("ephemeral_file_permissions_failed")
        if control_mode in ("prepared_connect_offline", "prepared_phase_offline", "host_dispatch_offline", "host_dispatch_roles_offline", "host_dispatch_capture_offline", "host_dispatch_identity_offline", "host_dispatch_resources_offline", "host_dispatch_waits_offline", "host_dispatch_exit_offline", "host_dispatch_fatal_offline") and Path(target).read_bytes() != content:
            raise runtime.ProbeError("ephemeral_source_readback_failed")
    if auxiliary:
        # Fresh data only, owned by the eventual nobody runtime, never host state.
        private = Path("/tmp/config/qq-native-init")
        private.mkdir(mode=0o700)
        os.chown(private, 65534, 65534)
        report["staged_source_sha256"] = {name: hashlib.sha256(content).hexdigest()
                                            for name, content in auxiliary.items()}
    # Only the existing outer launcher can be overlaid; never create inside QQ.
    target = Path(ENTRY)
    if target.is_symlink() or not target.is_file():
        raise runtime.ProbeError("ephemeral_entry_target_invalid")
    for source_path, destination in ((MAIN, ENTRY), (CONTROL, MANIFEST)):
        runtime.run_checked([mount, "--bind", source_path, destination])
        runtime.run_checked([mount, "-o", "remount,bind,ro,nosuid,nodev,noexec", destination])
    if Path(MANIFEST).read_bytes() != control or target.read_bytes() != source:
        raise runtime.ProbeError("ephemeral_manifest_mount_failed")
    report.update(ephemeral_manifest_mounted=True, manifest_override_readonly=True,
                  entry_override_readonly=True, override_main=PACKAGE_MAIN,
                  authored_source_sha256=hashlib.sha256(source).hexdigest())


def child_limits():
    """Apply the narrower file bound and the shared NNP/UID/cap-zero gate."""
    import resource
    resource.setrlimit(resource.RLIMIT_FSIZE, (LIMIT, LIMIT))
    runtime.drop_runtime_privileges()


def start_read_watches():
    """Watch only the two fixed private control files, without reading contents."""
    libc = ctypes.CDLL(None, use_errno=True)
    libc.inotify_init1.argtypes = [ctypes.c_int]
    libc.inotify_init1.restype = ctypes.c_int
    libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
    libc.inotify_add_watch.restype = ctypes.c_int
    descriptor = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
    if descriptor < 0:
        raise runtime.ProbeError("private_read_watch_failed")
    watches = {}
    try:
        for label, path in (("manifest", MANIFEST), ("authored_main", ENTRY)):
            watch = libc.inotify_add_watch(descriptor, path.encode("ascii"), 0x21)
            if watch < 0:
                raise runtime.ProbeError("private_read_watch_failed")
            watches[watch] = label
    except Exception:
        os.close(descriptor)
        raise
    return descriptor, watches


def finish_read_watches(descriptor, watches):
    """Return bounded OPEN/ACCESS evidence, not contents or actor attribution."""
    report = {label: {"opened": False, "accessed": False} for label in watches.values()}
    report["overflow"] = False
    try:
        try:
            raw = os.read(descriptor, LIMIT)
        except BlockingIOError:
            raw = b""
        offset = 0
        while offset + 16 <= len(raw):
            watch, mask, _cookie, length = struct.unpack_from("iIII", raw, offset)
            offset += 16 + length
            if mask & 0x4000:
                report["overflow"] = True
            if watch in watches:
                report[watches[watch]]["opened"] |= bool(mask & 0x20)
                report[watches[watch]]["accessed"] |= bool(mask & 0x01)
    finally:
        os.close(descriptor)
    return report


def diagnostic_categories(output, errors):
    """Classify bounded private output using fixed booleans; never expose raw text."""
    categories = {"cannot_find_module": (b"cannot find module",),
                  "module_not_found": (b"module_not_found",),
                  "permission_denied": (b"permission denied", b"eacces"),
                  "failed_to_load_main": (b"failed to load main", b"unable to load main"),
                  "js_error": (b"javascript error", b"uncaught exception", b"syntaxerror:", b"typeerror:", b"referenceerror:"),
                  "unknown_arg": (b"unknown argument", b"unrecognized option", b"unknown option")}
    streams = {}
    for name, stream in (("stdout", output), ("stderr", errors)):
        stream.flush()
        stream.seek(0)
        raw = stream.read(LIMIT + 1)
        bounded = raw[:LIMIT].lower()
        streams[name] = {"present": bool(raw), "prefix_truncated": len(raw) > LIMIT,
                         "categories": {key: any(token in bounded for token in tokens)
                                        for key, tokens in categories.items()}}
    return streams


def observe_host(command, env, output, errors, timeout, report, *, source, control, mount, parent_ids, nonce, control_mode="wrapper", auxiliary=None):
    """Launch only the pristine executable, without flags or native export calls."""
    if command != ["/opt/QQ/qq"] or timeout != QQ_TIMEOUT or "ELECTRON_RUN_AS_NODE" in env:
        raise runtime.ProbeError("ephemeral_invocation_invalid")
    validate_mode(control_mode)
    report.update({key: value for key, value in base_report(control_mode).items() if key not in report})
    report["mode"] = base_report(control_mode)["mode"]
    stage_control(source, control, mount, parent_ids, report, auxiliary=auxiliary, control_mode=control_mode)
    diagnostic = None
    if control_mode in ("host_dispatch_offline", "host_dispatch_roles_offline", "host_dispatch_capture_offline", "host_dispatch_identity_offline", "host_dispatch_resources_offline", "host_dispatch_waits_offline", "host_dispatch_exit_offline", "host_dispatch_fatal_offline"):
        import qq_native_connect_control
        import qq_host_dispatch_control
        diagnostic = qq_host_dispatch_control
        qq_native_connect_control.verify_offline_network(report)
    if control_mode in ("prepared_connect_offline", "prepared_phase_offline"):
        import qq_native_connect_control
        import qq_prepared_connect_control
        diagnostic = __import__("qq_prepared_phase_control") if control_mode == "prepared_phase_offline" else qq_prepared_connect_control
        qq_native_connect_control.verify_offline_network(report)
    if control_mode == "initialize_factories":
        import qq_native_connect_control
        import qq_native_factory_control
        diagnostic = qq_native_factory_control
        qq_native_connect_control.verify_offline_network(report)
    if control_mode in ("connect_offline", "connect_ready_offline"):
        import qq_native_connect_control
        diagnostic = __import__("qq_native_ready_control") if control_mode == "connect_ready_offline" else qq_native_connect_control
        qq_native_connect_control.verify_offline_network(report)
    if control_mode in ("vendor_bootstrap", "vendor_stall", "vendor_window_roles"):
        import qq_native_connect_control
        import qq_vendor_bootstrap_control
        diagnostic = qq_vendor_bootstrap_control
        qq_native_connect_control.verify_offline_network(report)
    child_env = dict(env, QQ_WRAPPER_HOST_NONCE=nonce)
    # Never inherit a caller flag: only the trusted fixed mode enables phase waits.
    child_env.pop("QQ_PREPARED_PHASE_CONTROL", None)
    if control_mode == "prepared_phase_offline":
        child_env["QQ_PREPARED_PHASE_CONTROL"] = "1"
    start = time.monotonic()
    watch_fd, watches = start_read_watches()
    process = None
    try:
        process = subprocess.Popen(command, env=child_env, stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=errors, close_fds=True, preexec_fn=child_limits)
        report.update(application_invoked=True, runtime_executed=True)
        report["isolation"].update(unprivileged=True, no_new_privileges=True, effective_capabilities_zero=True)
        try:
            if control_mode in ("prepared_connect_offline", "prepared_phase_offline", "host_dispatch_offline", "host_dispatch_roles_offline", "host_dispatch_capture_offline", "host_dispatch_identity_offline", "host_dispatch_resources_offline", "host_dispatch_waits_offline", "host_dispatch_exit_offline", "host_dispatch_fatal_offline", "vendor_stall", "vendor_window_roles"):
                # Prepared mode shares count-only external facts, not new JS timers.
                import qq_vendor_stall_observation
                if control_mode in ("vendor_window_roles", "host_dispatch_roles_offline"):
                    import qq_private_window_roles
                    qq_vendor_stall_observation.observe_wait(process, child_env, timeout=QQ_TIMEOUT, report=report,
                                                             window_sampler=qq_private_window_roles.sample_roles)
                elif control_mode in ("host_dispatch_exit_offline", "host_dispatch_fatal_offline"):
                    # Only exit mode adds bounded public exit metadata to the same waiter.
                    qq_vendor_stall_observation.observe_wait(process, child_env, timeout=QQ_TIMEOUT, report=report,
                                                             detailed_resources=True, wait_symbols=True, exit_status=True)
                elif control_mode == "host_dispatch_waits_offline":
                    # Explicit opt-in is confined to this fixed external observation mode.
                    qq_vendor_stall_observation.observe_wait(process, child_env, timeout=QQ_TIMEOUT, report=report,
                                                             detailed_resources=True, wait_symbols=True)
                elif control_mode == "host_dispatch_resources_offline":
                    # Only this fixed mode extends the external sampler, never QQ code.
                    qq_vendor_stall_observation.observe_wait(process, child_env, timeout=QQ_TIMEOUT, report=report,
                                                             detailed_resources=True)
                else:
                    qq_vendor_stall_observation.observe_wait(process, child_env, timeout=QQ_TIMEOUT, report=report)
            else:
                process.wait(timeout=QQ_TIMEOUT)
            report["completion"] = "application_exited"
        except subprocess.TimeoutExpired:
            report["completion"] = "watchdog_30s"
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)
        if control_mode in ("prepared_connect_offline", "prepared_phase_offline", "vendor_stall", "vendor_window_roles"):
            import qq_vendor_stall_observation
            qq_vendor_stall_observation.capture_categories(output, errors, report)
        if control_mode in ("host_dispatch_capture_offline", "host_dispatch_identity_offline", "host_dispatch_resources_offline", "host_dispatch_waits_offline", "host_dispatch_exit_offline", "host_dispatch_fatal_offline"):
            import qq_vendor_stall_observation
            qq_vendor_stall_observation.capture_categories(output, errors, report, detailed=True)
        if control_mode == "host_dispatch_fatal_offline":
            report["_private_fatal_capture"] = private_fatal_envelope(output, errors)
        report["private_control_reads"] = finish_read_watches(watch_fd, watches)
    report.update(experiment_completed=True, exit_code=process.returncode,
                  elapsed_seconds=round(time.monotonic() - start, 2),
                  private_output_categories={} if control_mode in ("host_dispatch_offline", "host_dispatch_roles_offline", "host_dispatch_capture_offline", "host_dispatch_identity_offline", "host_dispatch_resources_offline", "host_dispatch_waits_offline", "host_dispatch_exit_offline", "host_dispatch_fatal_offline") else diagnostic_categories(output, errors))
    if control_mode in ("host_dispatch_offline", "host_dispatch_roles_offline", "host_dispatch_capture_offline", "host_dispatch_identity_offline", "host_dispatch_resources_offline", "host_dispatch_waits_offline", "host_dispatch_exit_offline", "host_dispatch_fatal_offline"):
        # This mode has only one bounded progress file, not a native final record.
        report["process_retired"] = process.returncode is not None
        try:
            with Path(diagnostic.PROGRESS_PATH).open("rb") as stream:
                progress = diagnostic.parse_progress(stream.read(LIMIT + 1), nonce)
        except (OSError, ValueError, UnicodeError):
            progress = None
        diagnostic.project_report(report, progress, process.returncode)
        return
    if diagnostic is not None:
        # Retirement is an actual process observation even if native code never returns.
        report["process_retired"] = process.returncode is not None
        try:
            with Path(diagnostic.PROGRESS_PATH if control_mode in ("initialize_factories", "connect_ready_offline", "prepared_connect_offline", "prepared_phase_offline", "host_dispatch_offline", "host_dispatch_roles_offline", "host_dispatch_capture_offline", "host_dispatch_identity_offline", "host_dispatch_resources_offline", "host_dispatch_waits_offline", "host_dispatch_exit_offline", "host_dispatch_fatal_offline", "vendor_bootstrap", "vendor_stall", "vendor_window_roles") else "/tmp/qq-native-connect-progress.json").open("rb") as stream:
                report["factory_progress" if control_mode == "initialize_factories" else "vendor_progress" if control_mode in ("vendor_bootstrap", "vendor_stall", "vendor_window_roles") else "connect_progress"] = diagnostic.parse_progress(stream.read(LIMIT + 1), nonce)
        except (OSError, ValueError, UnicodeError):
            report["factory_progress" if control_mode == "initialize_factories" else "vendor_progress" if control_mode in ("vendor_bootstrap", "vendor_stall", "vendor_window_roles") else "connect_progress"] = None
    if control_mode in ("connect_ready_offline", "vendor_bootstrap", "vendor_stall", "vendor_window_roles"):
        try:
            with Path(diagnostic.FATAL_PATH if control_mode in ("vendor_bootstrap", "vendor_stall", "vendor_window_roles") else "/tmp/qq-native-ready-fatal.json").open("rb") as stream:
                report["vendor_fatal" if control_mode in ("vendor_bootstrap", "vendor_stall", "vendor_window_roles") else "ready_fatal"] = diagnostic.parse_fatal(stream.read(1025), nonce)
        except (OSError, ValueError, UnicodeError):
            report["vendor_fatal" if control_mode in ("vendor_bootstrap", "vendor_stall", "vendor_window_roles") else "ready_fatal"] = None
    if control_mode in ("vendor_bootstrap", "vendor_stall", "vendor_window_roles"):
        # A genuine ready event is proof even if later vendor work blocks final timers.
        diagnostic.project_progress(report, report.get("vendor_progress"))
    if control_mode in ("prepared_connect_offline", "prepared_phase_offline"):
        diagnostic.project_progress(report, report.get("connect_progress"))
    # Missing final output remains incomplete, not permission to patch or claim login.
    try:
        with Path(RESULT).open("rb") as stream:
            parser = parse_record if control_mode == "wrapper" else parse_initialize_record
            if diagnostic is not None:
                parser = diagnostic.parse_record
            report["host_record"] = parser(stream.read(LIMIT + 1), nonce)
    except (OSError, ValueError, UnicodeError):
        report.update(status="failed", errors=["custom_main_no_valid_record"])
        return
    if diagnostic is not None:
        diagnostic.project_report(report, report["host_record"], process.returncode)
        return
    if control_mode == "initialize":
        observed = report["host_record"]
        init = observed["initialization"]
        verified = observed["ok"] and process.returncode == 0
        report.update(native_host_binding_load_verified=observed["binding"]["loaded"],
                      native_binding_verified=observed["binding"]["loaded"],
                      native_initialize_verified=verified, native_listener_registered=init["listener_registered"],
                      native_listener_disposed=bool(init["disposed"] and init["snapshot"] and init["snapshot"]["listener_handle_available"]),
                      process_retired=process.returncode is not None,
                      native_cleanup_by_process_exit=bool(process.returncode is not None and init["snapshot"] and init["snapshot"]["cleanup_required"] == "process_exit"),
                      runtime_versions_verified=observed["runtime"]["exec_path_matches"],
                      versions=observed["runtime"]["versions"],
                      status="observed" if verified else "failed",
                      errors=[] if verified else [observed["error"] or "native_initialize_unverified"])
        return
    report["native_host_binding_load_verified"] = bool(report["host_record"]["ok"] and process.returncode == 0)
    report["native_binding_verified"] = report["native_host_binding_load_verified"]
    report["runtime_versions_verified"] = report["host_record"]["runtime"]["exec_path_matches"]
    report["versions"] = report["host_record"]["runtime"]["versions"]
    report["status"] = "observed" if report["native_host_binding_load_verified"] else "failed"
    report["errors"] = [] if report["native_host_binding_load_verified"] else ["native_binding_load_unverified"]


def child_main(args):
    """Capture fixed bytes before pivot, then use the unmodified shared runtime."""
    root = runtime.validate_root(args.qq_root)
    control_mode = validate_mode(getattr(args, "control_mode", "wrapper"))
    if control_mode in ("bus_admission_offline", "bus_admission_capture_offline", "bus_identity_offline"):
        # Import before pivot; bypass every QQ overlay and launch path entirely.
        diagnostic = __import__("qq_bus_identity_control" if control_mode == "bus_identity_offline" else "qq_bus_capture_control" if control_mode == "bus_admission_capture_offline" else "qq_bus_admission_control")
        return runtime.child_probe(root, Path(args.jail), args.nonce,
                                   json.loads(args.parent_ns), args.mount_tool,
                                   filesystem_mode="pivot-root", pivot_tool=args.pivot_tool,
                                   umount_tool=args.umount_tool, private_display=True,
                                   extended_startup=True, private_runtime_services=True,
                                   application_observer=diagnostic.observe_bus,
                                   synthetic_identity=control_mode == "bus_identity_offline",
                                   control_mode=control_mode)
    if control_mode in ("prepared_connect_offline", "prepared_phase_offline"):
        __import__("qq_native_connect_control")
        __import__("qq_prepared_connect_control")
        if control_mode == "prepared_phase_offline":
            __import__("qq_prepared_phase_control")
        # Reuse the trusted external sampler after pivot detaches its module path.
        __import__("qq_application_observation")
        __import__("qq_vendor_stall_observation")
    if control_mode == "initialize_factories":
        # Cache the fixed parser and existing offline gate before oldroot detach.
        __import__("qq_native_connect_control")
        __import__("qq_native_factory_control")
    if control_mode in ("connect_offline", "connect_ready_offline"):
        # Cache fixed validator before pivot detaches Python's staged module path.
        __import__("qq_native_connect_control")
        if control_mode == "connect_ready_offline":
            __import__("qq_native_ready_control")
    if control_mode in ("vendor_bootstrap", "vendor_stall", "vendor_window_roles"):
        # Import before pivot; validate the original outer bytes before overlay.
        __import__("qq_native_connect_control")
        vendor = __import__("qq_vendor_bootstrap_control")
        vendor.validate_vendor_entry((root / "resources/app/app_launcher/index.js").read_bytes())
        if control_mode in ("vendor_stall", "vendor_window_roles"):
            __import__("qq_application_observation")
            __import__("qq_vendor_stall_observation")
            if control_mode == "vendor_window_roles":
                __import__("qq_private_window_roles")
    if control_mode in ("host_dispatch_offline", "host_dispatch_roles_offline", "host_dispatch_capture_offline", "host_dispatch_identity_offline", "host_dispatch_resources_offline", "host_dispatch_waits_offline", "host_dispatch_exit_offline", "host_dispatch_fatal_offline"):
        # Cache only fixed helpers before pivot detaches the original source tree.
        __import__("qq_host_dispatch_control")
        __import__("qq_native_connect_control")
        __import__("qq_application_observation")
        __import__("qq_vendor_stall_observation")
        if control_mode == "host_dispatch_roles_offline":
            # The sole added dependency supplies the already allowlisted reader.
            __import__("qq_private_window_roles")
    sources = fixed_sources(control_mode)
    source = sources.pop(MAIN).read_bytes()
    auxiliary = {target: path.read_bytes() for target, path in sources.items()}
    pristine = (root / "resources/app/package.json").read_bytes()
    control = validate_manifest(pristine)
    if control_mode in ("initialize", "initialize_factories", "connect_offline", "connect_ready_offline", "prepared_connect_offline", "prepared_phase_offline"):
        auxiliary["/tmp/qq-native-pristine-package.json"] = pristine
    parent_ids = json.loads(args.parent_ns)
    def observer(command, env, output, errors, timeout, report):
        """Bind authored bytes and nonce to this one fixed callback."""
        observe_host(command, env, output, errors, timeout, report, source=source,
                     control=control, mount=args.mount_tool, parent_ids=parent_ids, nonce=args.nonce,
                     control_mode=control_mode, auxiliary=auxiliary)
    identity_options = ({"synthetic_identity": True, "control_mode": control_mode}
                        if control_mode in ("host_dispatch_identity_offline", "host_dispatch_resources_offline", "host_dispatch_waits_offline", "host_dispatch_exit_offline", "host_dispatch_fatal_offline") else {})
    return runtime.child_probe(root, Path(args.jail), args.nonce, parent_ids, args.mount_tool,
                               filesystem_mode="pivot-root", pivot_tool=args.pivot_tool,
                               umount_tool=args.umount_tool, private_display=True,
                               extended_startup=True, private_runtime_services=True,
                               application_observer=observer, **identity_options)


def private_fatal_envelope(output, errors):
    """Read each private prefix once before cleanup; never expose text in public fields."""
    envelope = {}
    for name, stream in (("stdout", output), ("stderr", errors)):
        stream.flush()
        stream.seek(0)
        envelope[name + "_base64"] = base64.b64encode(stream.read(LIMIT)).decode("ascii")
    return envelope


def persist_private_fatal(parsed, fd):
    """Strip first, validate exact bounded keys, and write only through the reserved fd."""
    envelope = parsed.pop("_private_fatal_capture", None)
    if not isinstance(envelope, dict) or set(envelope) != {"stdout_base64", "stderr_base64"}:
        raise runtime.ProbeError("private_capture_invalid")
    metadata = {}
    private_document = dict(envelope)
    for name in ("stdout", "stderr"):
        encoded = envelope[name + "_base64"]
        if not isinstance(encoded, str) or len(encoded) > 4 * ((LIMIT + 2) // 3):
            raise runtime.ProbeError("private_capture_invalid")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, UnicodeError):
            raise runtime.ProbeError("private_capture_invalid") from None
        if len(data) > LIMIT or base64.b64encode(data).decode("ascii") != encoded:
            raise runtime.ProbeError("private_capture_invalid")
        metadata[name] = {"captured_length": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        private_document[name] = data.decode("utf-8", errors="replace")
    runtime.validate_diagnostic_fd(fd)
    # This fixed schema contains no caller paths; bytes never enter the public report.
    remaining = memoryview((json.dumps(private_document, sort_keys=True) + "\n").encode("ascii"))
    while remaining:
        written = os.write(fd, remaining)
        if written <= 0:
            raise runtime.ProbeError("private_capture_write_failed")
        remaining = remaining[written:]
    os.fsync(fd)
    parsed["private_fatal_capture"] = metadata


def observe(qq_root, *, execute=False, control_mode="wrapper", private_capture_path=None):
    """Bound outer execution and prove all nominated host software stays identical."""
    report = base_report(control_mode)
    before = {}
    root = None
    private_fd = None
    try:
        if private_capture_path is not None and control_mode != "host_dispatch_fatal_offline":
            raise runtime.ProbeError("private_capture_requires_fixed_fatal_mode")
        if execute and control_mode == "host_dispatch_fatal_offline" and private_capture_path is None:
            raise runtime.ProbeError("private_capture_path_required")
        if sys.platform != "linux":
            raise runtime.ProbeError("linux_required")
        root = runtime.validate_root(qq_root)
        if control_mode not in ("bus_admission_offline", "bus_admission_capture_offline", "bus_identity_offline"):
            validate_manifest((root / "resources/app/package.json").read_bytes())
        names = ("qq", "resources/app/package.json", "resources/app/wrapper.node", "resources/app/application.asar",
                 "resources/app/app_launcher/index.js")
        before = {name: digest(root / name) for name in names}
        if not execute:
            return report
        if control_mode == "host_dispatch_fatal_offline":
            # Reserve before launching anything; never pass the private fd into QQ.
            private_fd = runtime.open_private_diagnostics(private_capture_path)
        sources = fixed_sources(control_mode)
        if os.geteuid() != 0 or any(path.is_symlink() or not path.is_file() for path in sources.values()):
            raise runtime.ProbeError("root_and_fixed_source_required")
        tools = {name: shutil.which(name, path="/usr/sbin:/usr/bin:/sbin:/bin")
                 for name in ("unshare", "mount", "pivot_root", "umount")}
        if any(not value for value in tools.values()):
            raise runtime.ProbeError("isolation_tools_missing")
        parent_ids = runtime.namespace_ids()
        parent_ids["ipc"] = os.readlink("/proc/self/ns/ipc")
        with tempfile.TemporaryDirectory(prefix="qq-ephemeral-host-entry-") as temporary:
            stage = Path(temporary)
            jail = stage / "root"
            jail.mkdir()
            helpers = tuple(Path(__file__).with_name(name) for name in
                            (("qq_host_dispatch_control.py", "qq_native_connect_control.py", "qq_application_observation.py", "qq_vendor_stall_observation.py") + (("qq_private_window_roles.py",) if control_mode == "host_dispatch_roles_offline" else ()) if control_mode in ("host_dispatch_offline", "host_dispatch_roles_offline", "host_dispatch_capture_offline", "host_dispatch_identity_offline", "host_dispatch_resources_offline", "host_dispatch_waits_offline", "host_dispatch_exit_offline", "host_dispatch_fatal_offline") else
                              ("qq_native_connect_control.py", "qq_vendor_bootstrap_control.py", "qq_application_observation.py", "qq_vendor_stall_observation.py", "qq_private_window_roles.py") if control_mode == "vendor_window_roles" else
                              ("qq_native_connect_control.py", "qq_vendor_bootstrap_control.py", "qq_application_observation.py", "qq_vendor_stall_observation.py") if control_mode == "vendor_stall" else
                              ("qq_native_connect_control.py", "qq_vendor_bootstrap_control.py") if control_mode == "vendor_bootstrap" else
                              (("qq_prepared_phase_control.py",) if control_mode == "prepared_phase_offline" else ()) + ("qq_native_connect_control.py", "qq_prepared_connect_control.py", "qq_application_observation.py", "qq_vendor_stall_observation.py") if control_mode in ("prepared_connect_offline", "prepared_phase_offline") else
                               ("qq_native_connect_control.py", "qq_native_ready_control.py") if control_mode == "connect_ready_offline" else
                             ("qq_native_connect_control.py", "qq_native_factory_control.py") if control_mode == "initialize_factories" else
                              ("qq_native_connect_control.py",) if control_mode == "connect_offline" else ()))
            if control_mode in ("bus_admission_offline", "bus_admission_capture_offline", "bus_identity_offline"):
                helpers = tuple(Path(__file__).with_name(name) for name in
                                ("qq_bus_admission_control.py", "qq_application_observation.py", "qq_native_connect_control.py") +
                                (("qq_bus_capture_control.py", "qq_bus_identity_control.py") if control_mode == "bus_identity_offline" else
                                  ("qq_bus_capture_control.py",) if control_mode == "bus_admission_capture_offline" else ()))
            for source in (Path(__file__).resolve(), Path(runtime.__file__).resolve(), *sources.values(), *helpers):
                shutil.copyfile(source, stage / source.name)
            command = [tools["unshare"], "--ipc", "--mount", "--net", "--pid", "--fork", "--kill-child",
                       "--mount-proc", "/usr/bin/python3", str(stage / Path(__file__).name), "--child",
                       "--qq-root", str(root), "--jail", str(jail), "--nonce", secrets.token_hex(16),
                       "--parent-ns", json.dumps(parent_ids), "--mount-tool", tools["mount"],
                       "--pivot-tool", tools["pivot_root"], "--umount-tool", tools["umount"],
                        "--control-mode", control_mode]
            environment = runtime.minimal_environment()
            environment.pop("ELECTRON_RUN_AS_NODE", None)
            # File-based bounded capture avoids unbounded PIPE allocations.
            with tempfile.TemporaryFile() as capture:
                process = subprocess.Popen(command, env=environment, stdin=subprocess.DEVNULL,
                                           stdout=capture, stderr=subprocess.DEVNULL,
                                           start_new_session=True, close_fds=True)
                try:
                    process.wait(timeout=OUTER_TIMEOUT)
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=2)
                capture.seek(0)
                raw = capture.read(runtime.HELPER_OUTPUT_LIMIT + 1)
            if process.returncode not in (0, 1) or len(raw) > runtime.HELPER_OUTPUT_LIMIT:
                raise runtime.ProbeError("ephemeral_child_failed")
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise runtime.ProbeError("ephemeral_child_report_invalid")
            if control_mode == "host_dispatch_fatal_offline":
                persist_private_fatal(parsed, private_fd)
            elif "_private_fatal_capture" in parsed:
                parsed.pop("_private_fatal_capture")
                raise runtime.ProbeError("private_capture_unexpected")
            if parsed.get("mode") != report["mode"]:
                raise runtime.ProbeError("ephemeral_child_report_invalid")
            report = parsed
    except runtime.ProbeError as exc:
        report.update(status="refused", errors=[str(exc)])
    except subprocess.TimeoutExpired:
        report.update(status="failed", errors=["ephemeral_outer_timeout"])
    except (OSError, ValueError, subprocess.SubprocessError):
        report.update(status="failed", errors=["ephemeral_control_failed"])
    finally:
        if private_fd is not None:
            os.close(private_fd)
        if root is not None and before:
            after = {name: digest(root / name) for name in before}
            report.update(host_software_sha256_before=before, host_software_sha256_after=after,
                          host_software_unchanged=before == after)
            if before != after:
                report.update(status="failed", native_host_binding_load_verified=False,
                              errors=["host_software_changed"])
    return report


def main(argv=None):
    """Expose execution consent and software root, never script/QQ option injection."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qq-root", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--control-mode", choices=("wrapper", "initialize", "initialize_factories", "connect_offline", "connect_ready_offline", "prepared_connect_offline", "prepared_phase_offline", "host_dispatch_offline", "host_dispatch_roles_offline", "host_dispatch_capture_offline", "host_dispatch_identity_offline", "host_dispatch_resources_offline", "host_dispatch_waits_offline", "host_dispatch_exit_offline", "host_dispatch_fatal_offline", "vendor_bootstrap", "vendor_stall", "vendor_window_roles", "bus_admission_offline", "bus_admission_capture_offline", "bus_identity_offline"), default="wrapper", help=argparse.SUPPRESS)
    for name in ("jail", "nonce", "parent-ns", "mount-tool", "pivot-tool", "umount-tool"):
        parser.add_argument("--" + name, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if not args.child and args.control_mode != "wrapper":
            parser.error("initialize mode is available only through the fixed trusted control")
        report = child_main(args) if args.child else observe(args.qq_root, execute=args.execute)
    except (OSError, ValueError, subprocess.SubprocessError):
        report = base_report(args.control_mode)
        report.update(status="failed", errors=["ephemeral_child_failed"])
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] in ("not_executed", "observed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
