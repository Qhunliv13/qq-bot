"""Fixed no-QQ private-bus admission control using the sole existing jail runner.

AUTH, explicit Hello, and GetId use THREE separate connections. No claim about
same-connection progression or QQ readiness follows from these observations.
"""

import argparse
from contextlib import nullcontext
from functools import partial
import tempfile
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time

try:
    from tools import qq_application_observation as application
    from tools import qq_ephemeral_host_entry as control
    from tools import qq_native_connect_control as offline
except ModuleNotFoundError as exc:
    if exc.name != "tools":
        raise
    import qq_application_observation as application
    import qq_ephemeral_host_entry as control
    import qq_native_connect_control as offline

# Fixed public client and private address; neither is configurable by callers.
CLIENT = "/usr/bin/dbus-send"
ADDRESS = "unix:path=/tmp/runtime/bus"
AUTH = b"\0AUTH EXTERNAL 3635353334\r\n"
CALLBACK_LIMIT = 16 * 1024


def client_identity():
    """Snapshot only the client's own privilege fields before any bus operation."""
    fields = dict(line.split(":", 1) for line in
                  Path("/proc/self/status").read_text(encoding="ascii").splitlines() if ":" in line)
    identity = {"uid": [int(value) for value in fields["Uid"].split()],
                "gid": [int(value) for value in fields["Gid"].split()],
                "cap_eff_zero": int(fields["CapEff"].strip(), 16) == 0,
                "no_new_privileges": fields["NoNewPrivs"].strip() == "1"}
    identity["verified"] = (identity["uid"] == [65534] * 4
                            and identity["gid"] == [65534] * 4
                            and identity["cap_eff_zero"] and identity["no_new_privileges"])
    return identity


def auth_probe():
    """Bound the sole raw AUTH socket to one second and 256 bytes, discard GUID."""
    deadline = time.monotonic() + 1
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(max(0.001, deadline - time.monotonic()))
            connection.connect("/tmp/runtime/bus")
            connection.settimeout(max(0.001, deadline - time.monotonic()))
            connection.sendall(AUTH)
            response = bytearray()
            while len(response) < 256:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return "timeout"
                connection.settimeout(remaining)
                data = connection.recv(1)
                if not data:
                    return "connection_closed"
                response.extend(data)
                if response.endswith(b"\r\n"):
                    # Never retain or serialize daemon GUID or rejected response text.
                    if re.fullmatch(rb"OK [0-9a-fA-F]{32}\r\n", response):
                        return "auth_ok"
                    return "auth_not_ok"
            return "response_too_long"
    except (TimeoutError, socket.timeout):
        return "timeout"
    except OSError:
        return "socket_error"


def classify_stderr(raw, failed):
    """Project only fixed booleans from 4096 bytes; strip quoted arbitrary excerpts."""
    bounded = raw[:4096].lower()
    # Quoted payloads/options are not diagnostic wording, preventing false matches.
    text = re.sub(rb'''"[^"\n]*"|'[^'\n]*'|`[^`\n]*`''', b"", bounded)
    patterns = {
        "client_usage_error": rb"\b(?:unknown option|unrecognized option|usage)\b",
        "connection_closed": rb"\b(?:connection (?:(?:was|is) )?closed|disconnected)\b",
        "no_reply_timeout": rb"\b(?:no reply|did not receive a reply|noreply|timed out|timeout)\b",
        "access_denied": rb"\b(?:accessdenied|access denied|permission denied)\b",
        "auth_failure": rb"\b(?:authfailed|authentication failed|authentication failure|authentication rejected)\b",
        "unknown_method": rb"\b(?:unknownmethod|unknown method)\b",
        "service_unknown": rb"\b(?:serviceunknown|service unknown)\b",
        "unix_identity_lookup_failure": rb"\b(?:could not get password database information for uid|could not get uid for username|could not get user database information|failed to (?:look up|lookup) (?:uid|user|username)|unknown username|user(?:name)? lookup failed)\b",
        "daemon_unavailable": rb"\b(?:failed to connect|could not connect|connection refused|unable to autolaunch)\b",
        "library_load_failure": rb"\berror (?:while )?loading shared libraries\b",
        "machine_id_unavailable": rb"\bmachine[-_ ]?id\b.*\b(?:failed|missing|not found|no such file)\b|\b(?:failed|missing)\b.*\bmachine[-_ ]?id\b",
        "message_generic_error": rb"\b(?:error|failed|failure)\b",
    }
    categories = {key: bool(re.search(pattern, text)) for key, pattern in patterns.items()}
    categories["unknown_failure"] = bool(failed and not any(categories.values()))
    return {"present": bool(raw), "prefix_truncated": len(raw) > 4096,
            "categories": categories}


def method_probe(method, detailed=False):
    """Keep DEVNULL defaults; fixed capture uses inherited 16KB RLIMIT and no stdout."""
    if method not in ("Hello", "GetId"):
        raise ValueError("fixed_method_required")
    # --peer avoids automatic Hello; --bus performs its own Hello before GetId.
    transport = "--peer=" if method == "Hello" else "--bus="
    command = [CLIENT, transport + ADDRESS, "--print-reply", "--reply-timeout=1000",
               "--dest=org.freedesktop.DBus", "/org/freedesktop/DBus",
               "org.freedesktop.DBus." + method]
    # The detailed sampler narrows its inherited hard RLIMIT_FSIZE to <=16384.
    with tempfile.TemporaryFile() if detailed else nullcontext(subprocess.DEVNULL) as errors:
        try:
            result = subprocess.run(command, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=errors,
                                    timeout=1, check=False, close_fds=True)
            observed = {"category": "returned_zero" if result.returncode == 0 else "nonzero_exit",
                        "returned_zero": result.returncode == 0, "exit_code": result.returncode}
        except subprocess.TimeoutExpired:
            observed = {"category": "timeout", "returned_zero": False, "exit_code": None}
        except OSError:
            observed = {"category": "launch_failed", "returned_zero": False, "exit_code": None}
        if detailed:
            errors.seek(0)
            observed["stderr"] = classify_stderr(errors.read(4097), not observed["returned_zero"])
        return observed


def sample_bus(detailed=False):
    """Run only fixed local clients after independently verifying dropped privileges."""
    result = {"identity": None, "auth": "not_attempted", "hello": None, "get_id": None,
              "completed": False, "same_connection_stage_proof": False}
    try:
        result["identity"] = client_identity()
    except (OSError, ValueError, KeyError):
        result["error"] = "client_identity_unavailable"
        return result
    if not result["identity"]["verified"]:
        result["error"] = "client_identity_rejected"
        return result
    if not Path(CLIENT).is_file() or not os.access(CLIENT, os.X_OK):
        result["error"] = "dbus_send_missing"
        return result
    if detailed:
        # Narrow the existing 1MB hard limit only in this disposable sampler child.
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
        cap = min([CALLBACK_LIMIT] + [value for value in (soft, hard) if value >= 0])
        resource.setrlimit(resource.RLIMIT_FSIZE, (cap, cap))
    start = time.monotonic()
    result["auth"] = auth_probe()
    # These independent controls still run on AUTH failure, not a coupled proof.
    probe = partial(method_probe, detailed=True) if detailed else method_probe
    result["hello"] = probe("Hello")
    result["get_id"] = probe("GetId")
    result.update(completed=True, elapsed_seconds=round(time.monotonic() - start, 3))
    return result


def observe_bus(command, env, output, errors, timeout, report, *, detailed=False):
    """Replace the trusted sampler only during the existing four-second fork hook."""
    if command != ["/opt/QQ/qq"] or timeout != 30 or "ELECTRON_RUN_AS_NODE" in env:
        raise control.runtime.ProbeError("bus_callback_contract_invalid")
    offline.verify_offline_network(report)
    # This runs in private PID1 only; restore globals even when the child fails.
    original, original_limit = application.sample_identity, application.SUMMARY_LIMIT
    try:
        application.sample_identity = partial(sample_bus, detailed=True) if detailed else sample_bus
        application.SUMMARY_LIMIT = CALLBACK_LIMIT
        sample = application.unprivileged_sample(env, timeout=4)
    finally:
        application.sample_identity, application.SUMMARY_LIMIT = original, original_limit
    report.update(mode="isolated_bus_admission_capture_control" if detailed else "isolated_bus_admission_control", application_invoked=False,
                  runtime_executed=False, same_connection_stage_proof=False,
                  invocation={"argv": [], "electron_run_as_node_present": False},
                  bus_admission=sample, experiment_completed=bool(sample and sample.get("completed")),
                  status="observed" if sample and sample.get("completed") else "failed",
                  errors=[] if sample and sample.get("completed") else ["bus_client_incomplete"],
                  bounds_seconds={"client": 4, "stage": 1, "outer": control.OUTER_TIMEOUT})


def observe(qq_root, *, execute=False):
    """Reuse the managed outer runner without entry overlays or any QQ launch."""
    return control.observe(qq_root, execute=execute, control_mode="bus_admission_offline")


def main(argv=None):
    """Require explicit root and execution consent; expose no commands or addresses."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qq-root", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    # Exclusive evidence creation is intentional: reruns cannot overwrite history.
    artifact = Path(__file__).resolve().parents[1] / "docs" / "evidence" / "qq-bus-admission-evidence.json"
    if args.execute:
        with artifact.open("x", encoding="utf-8") as stream:
            report = observe(args.qq_root, execute=True)
            json.dump(report, stream, sort_keys=True, indent=2)
            stream.write("\n")
    else:
        report = observe(args.qq_root)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] in ("not_executed", "observed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
