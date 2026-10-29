"""Fixed offline host dispatch diagnostic, without native/application initialization."""
import argparse
import json
from pathlib import Path
import re

try:
    from tools import qq_ephemeral_host_entry as control
except ModuleNotFoundError as exc:
    if exc.name != "tools":
        raise
    import qq_ephemeral_host_entry as control

# Fixed names are not caller-configurable paths, modes or injected source.
PROGRESS_PATH = "/tmp/qq-host-dispatch-progress.json"
ARTIFACT = Path(__file__).resolve().parents[1] / "docs" / "qq-host-dispatch-evidence.json"
EVENTS = ("bootstrapEntered", "synchronousEnd", "nextTick", "promiseThen", "queueMicrotask",
          "setImmediate", "setTimeout0", "setTimeout1000", "whenReady", "onReady")
CALLBACKS = EVENTS[2:]


def invalid():
    """Reject with a fixed code, never reflect untrusted payloads."""
    raise control.runtime.ProbeError("host_dispatch_record_invalid")


def parse_progress(raw, nonce):
    """Validate arbitrary unique callback prefixes and derive every flag from history."""
    if (not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", nonce)
            or not raw or len(raw) > control.LIMIT or raw.count(b"\n") != 1 or not raw.endswith(b"\n")):
        invalid()
    value = json.loads(raw, object_pairs_hook=control.unique_object)
    if (not isinstance(value, dict) or set(value) != {"schema_version", "nonce", "runtime", "flags", "registrations", "history", "observation_complete"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["nonce"] != nonce or type(value["observation_complete"]) is not bool):
        invalid()
    host, flags, registrations, history = (value[key] for key in ("runtime", "flags", "registrations", "history"))
    if (not isinstance(host, dict) or set(host) != {"versions", "exec_path_matches"}
            or type(host["exec_path_matches"]) is not bool or not isinstance(host["versions"], dict)
            or set(host["versions"]) - control.VERSION_KEYS
            or any(not isinstance(item, str) or not re.fullmatch(r"[0-9A-Za-z.+_-]{1,64}", item) for item in host["versions"].values())
            or not isinstance(flags, dict) or set(flags) != set(EVENTS)
            or any(type(item) is not bool for item in flags.values())
            or not isinstance(registrations, dict) or set(registrations) != set(CALLBACKS)
            or any(item not in ("pending", "registered", "threw") for item in registrations.values())
            or not isinstance(history, list) or not 1 <= len(history) <= len(EVENTS)):
        invalid()
    seen, previous = set(), 0
    for index, item in enumerate(history):
        if (not isinstance(item, dict) or set(item) != {"event", "elapsed_ms"}
                or not isinstance(item["event"], str) or item["event"] not in EVENTS or item["event"] in seen
                or type(item["elapsed_ms"]) is not int or not previous <= item["elapsed_ms"] <= 60000
                or (index == 0 and item["event"] != "bootstrapEntered")
                or (item["event"] == "setTimeout1000" and item["elapsed_ms"] < 1000)):
            invalid()
        seen.add(item["event"])
        previous = item["elapsed_ms"]
    if (flags != {event: event in seen for event in EVENTS}
            or any(flags[event] and registrations[event] != "registered" for event in CALLBACKS)
            or (flags["synchronousEnd"] and "pending" in registrations.values())
            or (value["observation_complete"] and (not flags["setTimeout1000"] or not flags["synchronousEnd"]
                or history[-1]["event"] != "setTimeout1000"))):
        invalid()
    return value


def project_progress(report, progress):
    """Project dispatch facts without upgrading QQ, login or native readiness."""
    report.update(dispatch_progress=progress, host_dispatch_observation_complete=bool(progress and progress["observation_complete"]))
    if progress:
        report.update(versions=progress["runtime"]["versions"], runtime_versions_verified=bool(
            progress["runtime"]["exec_path_matches"] and {"node", "electron", "modules", "napi"}.issubset(progress["runtime"]["versions"])))


def project_report(report, progress, returncode):
    """A completed observation is not a successful QQ bootstrap or ready claim."""
    project_progress(report, progress)
    complete = bool(progress and progress["observation_complete"] and returncode == 0 and report["runtime_versions_verified"])
    report.update(status="observed" if complete else "failed", errors=[] if complete else ["host_dispatch_incomplete"])


def observe(qq_root, *, execute=False):
    """Delegate only this exact mode to the unchanged isolation implementation."""
    return control.observe(qq_root, execute=execute, control_mode="host_dispatch_offline")


def main(argv=None):
    """Reserve the exclusive fixed artifact before any actual execution."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qq-root", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if args.execute:
        with ARTIFACT.open("x", encoding="utf-8") as stream:
            report = observe(args.qq_root, execute=True)
            json.dump(report, stream, sort_keys=True, indent=2)
            stream.write("\n")
    else:
        report = observe(args.qq_root)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] in ("not_executed", "observed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
