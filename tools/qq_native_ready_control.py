"""One fixed after-Electron-ready offline diagnostic; reuse shared jail and parsers."""

import argparse
import json

try:
    from tools import qq_native_connect_control as connect
except ModuleNotFoundError as exc:
    if exc.name != "tools":
        raise
    import qq_native_connect_control as connect

control = connect.control
PROGRESS_PATH = "/tmp/qq-native-ready-progress.json"
STAGES = ("before_when_ready", "after_when_ready", "before_initialize", "before_connect", "after_connect", "after_wait")


def parse_progress(raw, nonce):
    """Extend fixed stages explicitly without weakening the earlier connect parser."""
    if not raw or len(raw) > control.LIMIT or raw.count(b"\n") != 1 or not raw.endswith(b"\n"):
        raise control.runtime.ProbeError("ready_progress_invalid")
    value = json.loads(raw, object_pairs_hook=control.unique_object)
    if (not isinstance(value, dict)
            or set(value) != {"schema_version", "nonce", "stage", "snapshot", "app_is_ready", "pure_timer_fired"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["nonce"] != nonce or value["stage"] not in STAGES
            or any(type(value[key]) is not bool for key in ("app_is_ready", "pure_timer_fired"))
            or (value["stage"] != "before_when_ready" and not value["app_is_ready"])):
        raise control.runtime.ProbeError("ready_progress_invalid")
    # Pre-controller lifecycle evidence never fabricates an initialized snapshot.
    if value["stage"] in STAGES[:3]:
        if value["snapshot"] is not None:
            raise control.runtime.ProbeError("ready_progress_invalid")
    else:
        legacy = {key: value[key] for key in ("schema_version", "nonce", "stage", "snapshot")}
        connect.parse_progress((json.dumps(legacy) + "\n").encode(), nonce)
    return value


def parse_record(raw, nonce):
    """Validate readiness facts then reuse exact connect/init validation envelopes."""
    prefix = ("QQ_NATIVE_READY_" + nonce + ":").encode("ascii")
    if not raw or len(raw) > control.LIMIT or not raw.startswith(prefix) or raw.count(b"\n") != 1 or not raw.endswith(b"\n"):
        raise control.runtime.ProbeError("ready_record_invalid")
    record = json.loads(raw[len(prefix):], object_pairs_hook=control.unique_object)
    expected = {"schema_version", "nonce", "ok", "error", "runtime", "binding", "initialization", "connection", "readiness"}
    if not isinstance(record, dict) or set(record) != expected:
        raise control.runtime.ProbeError("ready_record_invalid")
    ready = record["readiness"]
    if (not isinstance(ready, dict) or set(ready) != {"when_ready_resolved", "app_is_ready", "pure_timer_fired"}
            or any(type(value) is not bool for value in ready.values())
            or (ready["when_ready_resolved"] and not ready["app_is_ready"])):
        raise control.runtime.ProbeError("ready_record_invalid")
    envelope = {key: value for key, value in record.items() if key != "readiness"}
    if record["error"] == "ready_wait_failed":
        if record["ok"] or ready["when_ready_resolved"]:
            raise control.runtime.ProbeError("ready_record_invalid")
        # Parser-only envelope: this never projects a synthetic result as verification.
        envelope["error"] = "runtime_mismatch"
    connect.parse_record(("QQ_NATIVE_CONNECT_" + nonce + ":" + json.dumps(envelope) + "\n").encode(), nonce)
    if record["binding"]["load_attempted"] and not (ready["when_ready_resolved"] and ready["app_is_ready"]):
        raise control.runtime.ProbeError("ready_record_invalid")
    return record


def parse_fatal(raw, nonce):
    """Accept only fixed fatal event names without raw exception data."""
    if not raw or len(raw) > 1024 or raw.count(b"\n") != 1 or not raw.endswith(b"\n"):
        raise control.runtime.ProbeError("ready_fatal_invalid")
    value = json.loads(raw, object_pairs_hook=control.unique_object)
    if (not isinstance(value, dict) or set(value) != {"schema_version", "nonce", "category"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["nonce"] != nonce or value["category"] not in ("uncaught_exception", "unhandled_rejection")):
        raise control.runtime.ProbeError("ready_fatal_invalid")
    return value


def project_report(report, observed, returncode):
    """Keep actual app readiness independent of transport, native ABI and login."""
    connect.project_report(report, observed, returncode)
    report["electron_readiness"] = observed["readiness"]
    report["electron_ready_verified"] = observed["readiness"]["when_ready_resolved"] and observed["readiness"]["app_is_ready"]


def observe(qq_root, *, execute=False):
    """Select only the fixed ready-first diagnostic with existing 30/45s bounds."""
    return control.observe(qq_root, execute=execute, control_mode="connect_ready_offline")


def main(argv=None):
    """Require explicit consent and software root; accept no identity or overrides."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qq-root", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    report = observe(args.qq_root, execute=args.execute)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] in ("not_executed", "observed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
