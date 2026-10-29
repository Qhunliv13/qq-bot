"""Observe the exact vendor outer bootstrap once in the shared offline fresh jail."""

import argparse
import json
import re

try:
    from tools import qq_ephemeral_host_entry as control
except ModuleNotFoundError as exc:
    if exc.name != "tools":
        raise
    import qq_ephemeral_host_entry as control

# These bytes were read from the actual pristine outer launcher, not guessed.
VENDOR_ENTRY = b"require('../major.node').load('internal_index', module);"
PROGRESS_PATH = "/tmp/qq-vendor-bootstrap-progress.json"
FATAL_PATH = "/tmp/qq-vendor-bootstrap-fatal.json"
STAGES = {"before_vendor_bootstrap", "after_vendor_bootstrap", "ready_callback", "timer0", "timer5s", "timer8s"}
BOOL_KEYS = {"vendor_bootstrap_returned", "ready_callback_fired", "app_is_ready", "timer0_fired", "timer5s_fired", "timer8s_fired"}


def validate_vendor_entry(raw):
    """Refuse before pivot unless the original ASCII source is the exact proven call."""
    if raw != VENDOR_ENTRY or raw.decode("ascii") != VENDOR_ENTRY.decode("ascii"):
        raise control.runtime.ProbeError("vendor_outer_entry_mismatch")
    return raw


def parse_progress(raw, nonce):
    """Accept a nonce-bound strict lifecycle schema, without arbitrary vendor data."""
    if not raw or len(raw) > control.LIMIT or raw.count(b"\n") != 1 or not raw.endswith(b"\n"):
        raise control.runtime.ProbeError("vendor_progress_invalid")
    value = json.loads(raw, object_pairs_hook=control.unique_object)
    if (not isinstance(value, dict) or set(value) != {"schema_version", "nonce", "stage", "runtime"} | BOOL_KEYS
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["nonce"] != nonce or value["stage"] not in STAGES
            or any(type(value[key]) is not bool for key in BOOL_KEYS)):
        raise control.runtime.ProbeError("vendor_progress_invalid")
    host = value["runtime"]
    if (not isinstance(host, dict) or set(host) != {"versions", "exec_path_matches"}
            or type(host["exec_path_matches"]) is not bool or not isinstance(host["versions"], dict)
            or set(host["versions"]) - control.VERSION_KEYS
            or not {"node", "electron", "modules", "napi"}.issubset(host["versions"])
            or any(not isinstance(item, str) or not re.fullmatch(r"[0-9A-Za-z.+_-]{1,64}", item) for item in host["versions"].values())
            or (value["ready_callback_fired"] and not value["app_is_ready"])
            or (value["stage"] == "before_vendor_bootstrap" and any(value[key] for key in BOOL_KEYS - {"app_is_ready"}))
            or (value["stage"] == "after_vendor_bootstrap" and not value["vendor_bootstrap_returned"])
            or (value["stage"] == "ready_callback" and not value["ready_callback_fired"])
            or any(value["stage"] == stage and not value[key] for stage, key in
                   (("timer0", "timer0_fired"), ("timer5s", "timer5s_fired"), ("timer8s", "timer8s_fired")))):
        raise control.runtime.ProbeError("vendor_progress_invalid")
    return value


def parse_record(raw, nonce):
    """Require the fixed 8-second completion record, not a fabricated ready claim."""
    prefix = ("QQ_VENDOR_BOOTSTRAP_" + nonce + ":").encode("ascii")
    if not raw.startswith(prefix):
        raise control.runtime.ProbeError("vendor_record_invalid")
    value = parse_progress(raw[len(prefix):], nonce)
    if value["stage"] != "timer8s":
        raise control.runtime.ProbeError("vendor_record_invalid")
    return value


def parse_fatal(raw, nonce):
    """Expose only two fixed fatal categories and never exception details."""
    if not raw or len(raw) > 1024 or raw.count(b"\n") != 1 or not raw.endswith(b"\n"):
        raise control.runtime.ProbeError("vendor_fatal_invalid")
    value = json.loads(raw, object_pairs_hook=control.unique_object)
    if (not isinstance(value, dict) or set(value) != {"schema_version", "nonce", "category"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["nonce"] != nonce or value["category"] not in ("uncaught_exception", "unhandled_rejection")):
        raise control.runtime.ProbeError("vendor_fatal_invalid")
    return value


def project_progress(report, observed):
    """Retain real ready-event proof even if later vendor work stalls before 8 seconds."""
    if observed is not None:
        report.update(vendor_bootstrap_returned=observed["vendor_bootstrap_returned"],
                      electron_ready_verified=observed["ready_callback_fired"] and observed["app_is_ready"],
                      runtime_versions_verified=observed["runtime"]["exec_path_matches"],
                      versions=observed["runtime"]["versions"])


def project_report(report, observed, returncode):
    """Separate lifecycle observation from Electron readiness and all login milestones."""
    complete = observed["timer8s_fired"] and returncode == 0
    report.update(vendor_bootstrap_observation_complete=complete,
                  vendor_bootstrap_returned=observed["vendor_bootstrap_returned"],
                  electron_ready_verified=observed["ready_callback_fired"] and observed["app_is_ready"],
                  runtime_versions_verified=observed["runtime"]["exec_path_matches"],
                  versions=observed["runtime"]["versions"],
                  status="observed" if complete else "failed",
                  errors=[] if complete else ["vendor_observation_incomplete"])


def observe(qq_root, *, execute=False):
    """Use exactly one fixed mode; shared controls own every isolation/security gate."""
    return control.observe(qq_root, execute=execute, control_mode="vendor_bootstrap")


def main(argv=None):
    """Require an explicit root and consent without exposing script or identity options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qq-root", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    report = observe(args.qq_root, execute=args.execute)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] in ("not_executed", "observed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
