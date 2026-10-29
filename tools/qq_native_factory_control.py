"""Fixed offline pre-initialize factory experiment; all isolation is shared."""

import argparse
import json

try:
    from tools import qq_ephemeral_host_entry as control
except ModuleNotFoundError as exc:
    if exc.name != "tools":
        raise
    import qq_ephemeral_host_entry as control


PROGRESS_PATH = "/tmp/qq-native-factory-progress.json"
STAGES = ("runtime", "before_binding_load", "after_binding_load", "before_engine_get",
          "after_engine_get", "before_login_get", "after_login_get", "before_startup_create",
          "after_startup_create", "before_session_get", "after_session_get",
          "before_initialize", "after_initialize", "before_dispose", "after_dispose")
FACTORY_ERRORS = {"factory_contract_invalid", "factory_engine_failed", "factory_login_failed",
                  "factory_startup_failed", "factory_session_failed"}


def validate_factories(value):
    """Accept fixed descriptors only and require monotonic factory completion."""
    keys = {"stage", "validated", "engine_type", "login_type", "startup_type", "session_type",
            "startup_factory_created", "session_factory_available"}
    if (not isinstance(value, dict) or set(value) != keys or value["stage"] not in STAGES
            or any(type(value[key]) is not bool for key in ("validated", "startup_factory_created", "session_factory_available"))
            or any(value[key] not in ("not_attempted", "object", "function", "other")
                   for key in ("engine_type", "login_type", "startup_type", "session_type"))):
        raise control.runtime.ProbeError("factory_descriptor_invalid")
    index = STAGES.index(value["stage"])
    for key, after in (("engine_type", "after_engine_get"), ("login_type", "after_login_get"),
                       ("startup_type", "after_startup_create"), ("session_type", "after_session_get")):
        # A before-call checkpoint must not claim that the blocking call returned.
        if (value[key] != "not_attempted") != (index >= STAGES.index(after)):
            raise control.runtime.ProbeError("factory_descriptor_invalid")
    if (value["startup_factory_created"] != (value["startup_type"] in ("object", "function"))
            or value["session_factory_available"] != (value["session_type"] in ("object", "function"))
            or value["validated"] != (index >= STAGES.index("before_engine_get"))
            or (index > STAGES.index("after_engine_get") and value["engine_type"] not in ("object", "function"))
            or (index > STAGES.index("after_login_get") and value["login_type"] not in ("object", "function"))
            or (index > STAGES.index("after_startup_create") and not value["startup_factory_created"])
            or (index > STAGES.index("after_session_get") and not value["session_factory_available"])):
        raise control.runtime.ProbeError("factory_descriptor_invalid")
    return value


def parse_progress(raw, nonce):
    """Validate one synchronous nonce-bound checkpoint without exposing native data."""
    if not raw or len(raw) > control.LIMIT or raw.count(b"\n") != 1 or not raw.endswith(b"\n"):
        raise control.runtime.ProbeError("factory_progress_invalid")
    value = json.loads(raw, object_pairs_hook=control.unique_object)
    if (not isinstance(value, dict) or set(value) != {"schema_version", "nonce", "stage", "factories"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or value["nonce"] != nonce):
        raise control.runtime.ProbeError("factory_progress_invalid")
    factories = validate_factories(value["factories"])
    if value["stage"] != factories["stage"]:
        raise control.runtime.ProbeError("factory_progress_invalid")
    return value


def parse_record(raw, nonce):
    """Reuse the strict initialize envelope and add only fixed factory coherence."""
    prefix = ("QQ_NATIVE_FACTORY_" + nonce + ":").encode("ascii")
    if not raw or len(raw) > control.LIMIT or not raw.startswith(prefix) or raw.count(b"\n") != 1 or not raw.endswith(b"\n"):
        raise control.runtime.ProbeError("factory_record_invalid")
    record = json.loads(raw[len(prefix):], object_pairs_hook=control.unique_object)
    if not isinstance(record, dict) or set(record) != {"schema_version", "nonce", "ok", "error", "runtime", "binding", "initialization", "factories"}:
        raise control.runtime.ProbeError("factory_record_invalid")
    factories = validate_factories(record["factories"])
    initialization = {key: value for key, value in record.items() if key != "factories"}
    if record["error"] in FACTORY_ERRORS:
        # Map only this extra pre-controller failure to the existing metadata-stage
        # envelope; preserve original error in the returned factory record.
        initialization["error"] = "metadata_invalid"
    encoded = ("QQ_NATIVE_INIT_" + nonce + ":" + json.dumps(initialization) + "\n").encode()
    control.parse_initialize_record(encoded, nonce)
    index = STAGES.index(factories["stage"])
    expected_errors = {"factory_contract_invalid": ("after_binding_load",),
                       "factory_engine_failed": ("before_engine_get", "after_engine_get"),
                       "factory_login_failed": ("before_login_get", "after_login_get"),
                       "factory_startup_failed": ("before_startup_create", "after_startup_create"),
                       "factory_session_failed": ("before_session_get", "after_session_get"),
                       "runtime_mismatch": ("runtime",), "binding_load_failed": ("before_binding_load",),
                       "metadata_invalid": ("after_session_get",), "controller_invalid": ("after_session_get",),
                       "initialize_failed": ("before_initialize",), "dispose_failed": ("before_dispose",),
                       None: ("after_dispose",)}
    if (record["error"] not in expected_errors or factories["stage"] not in expected_errors[record["error"]]
            or record["binding"]["load_attempted"] != (index >= STAGES.index("before_binding_load"))
            or record["binding"]["loaded"] != (index >= STAGES.index("after_binding_load"))
            or record["initialization"]["attempted"] != (index >= STAGES.index("before_initialize"))
            or record["initialization"]["initialized"] != (index >= STAGES.index("after_initialize"))
            or record["initialization"]["disposed"] != (factories["stage"] == "after_dispose")
            or record["ok"] and factories["stage"] != "after_dispose"):
        raise control.runtime.ProbeError("factory_record_invalid")
    return record


def project_report(report, observed, returncode):
    """Separate returned preparation factories from authenticated session readiness."""
    factories, init = observed["factories"], observed["initialization"]
    verified = observed["ok"] and returncode == 0
    report.update(native_host_binding_load_verified=observed["binding"]["loaded"],
                  native_binding_verified=observed["binding"]["loaded"],
                  startup_factory_created=factories["startup_factory_created"],
                  session_factory_available=factories["session_factory_available"],
                  native_initialize_verified=bool(verified and init["initialized"]),
                  native_listener_registered=init["listener_registered"],
                  native_listener_disposed=False, process_retired=returncode is not None,
                  native_cleanup_by_process_exit=bool(returncode is not None and init["disposed"]),
                  runtime_versions_verified=observed["runtime"]["exec_path_matches"],
                  versions=observed["runtime"]["versions"], status="observed" if verified else "failed",
                  errors=[] if verified else [observed["error"] or "factory_initialize_unverified"])


def observe(qq_root, *, execute=False):
    """Expose one fixed preparation mode; no account, source, or QQ options accepted."""
    return control.observe(qq_root, execute=execute, control_mode="initialize_factories")


def main(argv=None):
    """Require software root and explicit execution consent for this offline mode."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qq-root", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    report = observe(args.qq_root, execute=args.execute)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] in ("not_executed", "observed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
