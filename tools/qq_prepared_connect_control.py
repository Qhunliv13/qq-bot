"""One fixed prepared-host offline connect experiment using shared isolation only."""

import argparse
import json
import re

try:
    from tools import qq_ephemeral_host_entry as control
    from tools import qq_native_connect_control as connect
except ModuleNotFoundError as exc:
    if exc.name != "tools":
        raise
    import qq_ephemeral_host_entry as control
    import qq_native_connect_control as connect


# Fixed paths and complete stage prefixes cannot be supplied through CLI inputs.
PROGRESS_PATH = "/tmp/qq-prepared-connect-progress.json"
STAGES = ("runtime", "before_binding_load", "after_binding_load", "before_prepare",
          "after_prepare", "before_initialize", "after_initialize", "before_connect",
          "after_connect", "tick_1s", "tick_5s", "tick_8s", "before_dispose", "after_dispose")
HOST_KEYS = {"phase", "engine_available", "login_available", "startup_available", "session_available",
             "failure_code", "process_cleanup_required", "cleanup_required"}
ERROR_STAGES = {"runtime_mismatch": ("runtime",), "binding_load_failed": ("before_binding_load",),
                "metadata_invalid": ("after_binding_load",), "prepare_failed": ("after_binding_load", "before_prepare"),
                "controller_invalid": ("after_prepare",), "initialize_failed": ("before_initialize",),
                "listener_contract_unexpected": ("after_initialize",),
                "connect_observation_failed": ("before_connect", "after_connect", "tick_1s", "tick_5s", "tick_8s", "after_dispose"),
                "dispose_failed": ("before_dispose",), None: ("after_dispose",)}


def invalid():
    """Use one fixed public error instead of reflecting untrusted native data."""
    raise control.runtime.ProbeError("prepared_connect_record_invalid")


def validate_host(value, stage):
    """Require exact module snapshots at each externally observed preparation stage."""
    index = STAGES.index(stage)
    if index < STAGES.index("before_prepare"):
        if value is not None:
            invalid()
        return
    if (not isinstance(value, dict) or set(value) != HOST_KEYS
            or any(type(value[key]) is not bool for key in HOST_KEYS - {"phase", "failure_code", "cleanup_required"})):
        invalid()
    prepared = index >= STAGES.index("after_prepare")
    disposed = stage == "after_dispose"
    if (value["phase"] != ("disposed" if disposed else "prepared" if prepared else "new")
            or value["failure_code"] is not None
            or value["cleanup_required"] != ("process_exit" if prepared else "none")
            or value["process_cleanup_required"] != prepared
            or any(value[key] != (prepared and not disposed) for key in
                   ("engine_available", "login_available", "startup_available", "session_available"))):
        invalid()


def parse_progress(raw, nonce):
    """Validate a bounded nonce-bound stage prefix with honest monotonic deadlines."""
    if (not raw or len(raw) > control.LIMIT or raw.count(b"\n") != 1 or not raw.endswith(b"\n")
            or not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", nonce)):
        invalid()
    value = json.loads(raw, object_pairs_hook=control.unique_object)
    if (not isinstance(value, dict) or set(value) != {"schema_version", "nonce", "stage", "history", "host", "login"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["nonce"] != nonce or value["stage"] not in STAGES or not isinstance(value["history"], list)):
        invalid()
    history = value["history"]
    index = STAGES.index(value["stage"])
    if len(history) != index + 1:
        invalid()
    previous = 0
    for number, item in enumerate(history):
        if (not isinstance(item, dict) or set(item) != {"stage", "elapsed_ms"}
                or item["stage"] != STAGES[number] or type(item["elapsed_ms"]) is not int
                or not previous <= item["elapsed_ms"] <= 60000):
            invalid()
        previous = item["elapsed_ms"]
        if item["stage"] in ("tick_1s", "tick_5s", "tick_8s"):
            deadline = {"tick_1s": 1000, "tick_5s": 5000, "tick_8s": 8000}[item["stage"]]
            if previous - history[STAGES.index("after_connect")]["elapsed_ms"] < deadline:
                invalid()
    validate_host(value["host"], value["stage"])
    snapshot = value["login"]
    if index < STAGES.index("before_initialize"):
        if snapshot is not None:
            invalid()
    else:
        context = "disposed" if value["stage"] == "after_dispose" else "connect" if index >= STAGES.index("after_connect") else "initialize"
        control.validate_login_snapshot(snapshot, context=context, require_diagnostics=True)
        if snapshot["listener_lifetime"] != "process" or snapshot["native_listener_removed"]:
            invalid()
        if value["stage"] == "before_initialize":
            if (snapshot["phase"] != "new" or snapshot["disposed"] or snapshot["failure_code"] is not None
                    or snapshot["initialization_step"] is not None or snapshot["failed_stage"] is not None
                    or snapshot["registration_call_returned"] or snapshot["listener_id_type"] != "not_attempted"
                    or snapshot["listener_handle_available"] or snapshot["cleanup_required"] != "none"):
                invalid()
        elif index < STAGES.index("after_connect"):
            if (snapshot["phase"] != "initialized" or snapshot["disposed"] or snapshot["failure_code"] is not None
                    or snapshot["initialization_step"] != "initialized" or snapshot["failed_stage"] is not None):
                invalid()
        if index >= STAGES.index("before_connect") and (snapshot["listener_id_type"] != "undefined"
                or snapshot["listener_handle_available"] or snapshot["cleanup_required"] != "process_exit"):
            invalid()
        if index >= STAGES.index("after_connect") and snapshot["connect_result_type"] == "not_attempted":
            invalid()
    return value


def parse_record(raw, nonce):
    """Compose existing connect validation with strict prepared-host progress evidence."""
    prefix = ("QQ_PREPARED_CONNECT_" + nonce + ":").encode("ascii")
    if not raw or len(raw) > control.LIMIT or not raw.startswith(prefix) or raw.count(b"\n") != 1 or not raw.endswith(b"\n"):
        invalid()
    value = json.loads(raw[len(prefix):], object_pairs_hook=control.unique_object)
    if (not isinstance(value, dict)
            or set(value) != {"schema_version", "nonce", "ok", "error", "runtime", "binding", "initialization", "connection", "progress"}
            or not isinstance(value["error"], (str, type(None))) or value["error"] not in ERROR_STAGES):
        invalid()
    progress = parse_progress((json.dumps(value["progress"]) + "\n").encode(), nonce)
    stage = progress["stage"]
    if stage not in ERROR_STAGES[value["error"]]:
        invalid()
    # Preserve the original fixed error but map extra preconditions to shared schema.
    common = {key: item for key, item in value.items() if key != "progress"}
    if value["error"] == "prepare_failed":
        common["error"] = "metadata_invalid"
    elif value["error"] == "listener_contract_unexpected":
        common["error"] = "connect_observation_failed"
    connect.parse_record(("QQ_NATIVE_CONNECT_" + nonce + ":" + json.dumps(common) + "\n").encode(), nonce)
    index = STAGES.index(stage)
    if (value["binding"]["load_attempted"] != (index >= STAGES.index("before_binding_load"))
            or value["binding"]["loaded"] != (index >= STAGES.index("after_binding_load"))
            or value["initialization"]["attempted"] != (index >= STAGES.index("before_initialize"))
            or value["initialization"]["initialized"] != (index >= STAGES.index("after_initialize"))
            or value["connection"]["attempted"] != (index >= STAGES.index("before_connect"))
            or value["connection"]["wait_completed"] != (index >= STAGES.index("before_dispose"))
            or value["initialization"]["disposed"] != (stage == "after_dispose")
            or value["ok"] and stage != "after_dispose"):
        invalid()
    if value["ok"] and progress["login"] != value["connection"]["disposed_snapshot"]:
        invalid()
    return value


def project_progress(report, progress):
    """Report completed stages even when no final record survives the watchdog."""
    index = STAGES.index(progress["stage"]) if progress else -1
    report.update(native_host_prepared_verified=index >= STAGES.index("after_prepare"),
                  bounded_progress_verified=index >= STAGES.index("tick_8s"))


def project_report(report, observed, returncode):
    """Separate prepared/connect observations from callback/login/session readiness."""
    connect.project_report(report, observed, returncode)
    project_progress(report, observed["progress"])
    # The existing projection cannot mistake this explicit process-only disposal for removal.
    report["native_listener_disposed"] = False


def observe(qq_root, *, execute=False):
    """Delegate one fixed mode with no account/source/isolation override inputs."""
    return control.observe(qq_root, execute=execute, control_mode="prepared_connect_offline")


def main(argv=None):
    """Require explicit execution consent; default only inspects pristine software."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qq-root", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    report = observe(args.qq_root, execute=args.execute)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] in ("not_executed", "observed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
