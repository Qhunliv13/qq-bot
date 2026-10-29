"""Fixed one-call offline connect observation; isolation lives only in shared control."""

import argparse
import json

try:
    from tools import qq_ephemeral_host_entry as control
except ModuleNotFoundError as exc:
    if exc.name != "tools":
        raise
    import qq_ephemeral_host_entry as control


CALLBACK_NAMES = {"onLoginConnected", "onLoginConnecting", "onLoginDisConnected",
                  "onQRCodeGetPicture", "onQRCodeLoginSucceed", "onLoginFailed", "onQRCodeSessionFailed"}


def parse_record(raw, nonce):
    """Validate nonce-bound fixed status only, sharing initialize/snapshot validation."""
    prefix = ("QQ_NATIVE_CONNECT_" + nonce + ":").encode("ascii")
    if not raw or len(raw) > control.LIMIT or not raw.startswith(prefix) or raw.count(b"\n") != 1 or not raw.endswith(b"\n"):
        raise control.runtime.ProbeError("connect_record_invalid")
    record = json.loads(raw[len(prefix):], object_pairs_hook=control.unique_object)
    if (not isinstance(record, dict)
            or set(record) != {"schema_version", "nonce", "ok", "error", "runtime", "binding", "initialization", "connection"}
            or record["error"] not in (None, "runtime_mismatch", "binding_load_failed", "metadata_invalid", "controller_invalid", "initialize_failed", "connect_observation_failed", "dispose_failed")):
        raise control.runtime.ProbeError("connect_record_invalid")
    connection = record["connection"]
    if (not isinstance(connection, dict)
            or set(connection) != {"attempted", "call_threw", "wait_completed", "wait_limit_ms", "snapshot", "disposed_snapshot"}
            or any(type(connection[key]) is not bool for key in ("attempted", "call_threw", "wait_completed"))
            or type(connection["wait_limit_ms"]) is not int or connection["wait_limit_ms"] != 8000):
        raise control.runtime.ProbeError("connect_record_invalid")
    # Reuse the exact shared initialize parser, not another version of its checks.
    init_record = {key: value for key, value in record.items() if key != "connection"}
    if record["error"] in ("connect_observation_failed", "dispose_failed"):
        init_record["error"] = None
        init_record["ok"] = True
        init_record["initialization"] = dict(record["initialization"], disposed=True)
    encoded = ("QQ_NATIVE_INIT_" + nonce + ":" + json.dumps(init_record) + "\n").encode()
    control.parse_initialize_record(encoded, nonce)
    for key, context in (("snapshot", "connect"), ("disposed_snapshot", "disposed")):
        snapshot = connection[key]
        if snapshot is not None:
            control.validate_login_snapshot(snapshot, context=context, require_diagnostics=True)
    snapshot, disposed = connection["snapshot"], connection["disposed_snapshot"]
    if (record["ok"] != (record["error"] is None)
            or (connection["attempted"] and not record["initialization"]["initialized"])
            or (connection["wait_completed"] and not connection["attempted"])
            or (snapshot is not None and not connection["wait_completed"])
            or (connection["call_threw"] and not connection["attempted"])
            or (record["ok"] and not (connection["attempted"] and connection["wait_completed"] and snapshot and disposed))
            or (disposed is not None and (snapshot is None or not record["initialization"]["disposed"]))
            or (snapshot is not None and snapshot["connect_result_type"] == "not_attempted")
            or (snapshot is not None and snapshot["connect_result_type"] in ("threw", "undefined", "other") and not connection["call_threw"])
            or (disposed is not None and any(disposed[key] != snapshot[key] for key in ("connect_call_returned", "connect_result_type", "native_callback_counts")))):
        raise control.runtime.ProbeError("connect_record_invalid")
    return record


def parse_progress(raw, nonce):
    """Accept only fixed checkpoint enums and actual safe snapshots, never raw output."""
    if not raw or len(raw) > control.LIMIT or raw.count(b"\n") != 1 or not raw.endswith(b"\n"):
        raise control.runtime.ProbeError("connect_progress_invalid")
    value = json.loads(raw, object_pairs_hook=control.unique_object)
    if (not isinstance(value, dict) or set(value) != {"schema_version", "nonce", "stage", "snapshot"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1 or value["nonce"] != nonce
            or value["stage"] not in ("before_initialize", "before_connect", "after_connect", "after_wait")):
        raise control.runtime.ProbeError("connect_progress_invalid")
    if value["stage"] == "before_initialize":
        if value["snapshot"] is not None:
            raise control.runtime.ProbeError("connect_progress_invalid")
    else:
        control.validate_login_snapshot(value["snapshot"], context="initialize" if value["stage"] == "before_connect" else "connect", require_diagnostics=True)
    return value


def verify_offline_network(report):
    """Refuse unless the fresh private network has only a DOWN loopback and no routes."""
    import fcntl
    from pathlib import Path
    import socket
    import struct
    # SIOCGIFFLAGS observes the actual namespace interface, never changes its state.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as descriptor:
        flags = struct.unpack_from("H", fcntl.ioctl(descriptor, 0x8913, struct.pack("256s", b"lo")), 16)[0]
    routes = Path("/proc/net/route").read_text(encoding="ascii").splitlines()
    ipv6 = Path("/proc/net/ipv6_route").read_text(encoding="ascii").splitlines()
    # Linux exposes harmless reject routes for ::/0 and ::1 on DOWN loopback;
    # route flags 0x200 reject forwarding and do not provide any host connectivity.
    ipv6_safe = all(line.split()[9] == "lo" and int(line.split()[8], 16) & 0x200 for line in ipv6)
    if (socket.if_nameindex() != [(1, "lo")] or flags & 1
            or any(line.split()[0] != "Iface" for line in routes) or not ipv6_safe):
        raise control.runtime.ProbeError("offline_network_gate_failed")
    report["offline_network"] = {"only_loopback": True, "loopback_down": True,
                                 "ipv4_routes_absent": True, "ipv6_forward_routes_absent": True}


def project_report(report, observed, returncode):
    """Report actual observation separately from transport/login readiness and ABI."""
    connection = observed["connection"]
    snapshot = connection["snapshot"]
    disposed = connection["disposed_snapshot"]
    verified = observed["ok"] and returncode == 0
    report.update(native_host_binding_load_verified=observed["binding"]["loaded"],
                  native_binding_verified=observed["binding"]["loaded"],
                  native_initialize_verified=observed["initialization"]["initialized"],
                  native_connect_observation_verified=verified,
                  native_listener_registered=bool(snapshot and snapshot["listener_registration_verified"]),
                  native_listener_disposed=bool(disposed and disposed["native_listener_removed"]),
                  native_cleanup_by_process_exit=bool(returncode is not None and disposed and disposed["cleanup_required"] == "process_exit"),
                  process_retired=returncode is not None,
                  runtime_versions_verified=observed["runtime"]["exec_path_matches"],
                  versions=observed["runtime"]["versions"], status="observed" if verified else "failed",
                  errors=[] if verified else [observed["error"] or "connect_observation_unverified"])


def observe(qq_root, *, execute=False):
    """Authorize only the fixed offline enum; no source or security override exists."""
    return control.observe(qq_root, execute=execute, control_mode="connect_offline")


def main(argv=None):
    """Require explicit execution and software root; never accept account inputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qq-root", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    report = observe(args.qq_root, execute=args.execute)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] in ("not_executed", "observed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
