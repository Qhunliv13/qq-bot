"""Fixed offline phase-timer control; never permits caller-selected native calls."""

import argparse
import json
from pathlib import Path
import re

try:
    from tools import qq_prepared_connect_control as prepared
except ModuleNotFoundError as exc:
    if exc.name != "tools":
        raise
    import qq_prepared_connect_control as prepared

control = prepared.control
# Four additional waits precede irreversible operations; the fifth is existing tick_1s.
WAITS = {"phase_baseline_1s": "runtime", "phase_loaded_1s": "after_binding_load",
         "phase_prepared_1s": "after_prepare", "phase_initialized_1s": "after_initialize"}
STAGES = tuple(part for stage in prepared.STAGES for part in
               (stage, *tuple(wait for wait, origin in WAITS.items() if origin == stage)))
PROGRESS_PATH = "/tmp/qq-prepared-phase-progress.json"
ARTIFACT = Path(__file__).resolve().parents[1] / "docs" / "qq-prepared-phase-evidence.json"


def invalid():
    """Reject malformed phase evidence with no reflection of native data."""
    raise control.runtime.ProbeError("prepared_phase_record_invalid")


def common_progress(value):
    """Remove only known extra checkpoints to reuse all existing snapshot checks."""
    return dict(value, stage=WAITS.get(value["stage"], value["stage"]),
                history=[item for item in value["history"] if item["stage"] not in WAITS])


def parse_progress(raw, nonce):
    """Require exact bounded stage prefixes and each full 1000ms phase delta."""
    if (not raw or len(raw) > control.LIMIT or raw.count(b"\n") != 1 or not raw.endswith(b"\n")
            or not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", nonce)):
        invalid()
    value = json.loads(raw, object_pairs_hook=control.unique_object)
    if (not isinstance(value, dict) or set(value) != {"schema_version", "nonce", "stage", "history", "host", "login"}
            or not isinstance(value["stage"], str) or value["stage"] not in STAGES
            or not isinstance(value["history"], list)):
        invalid()
    history = value["history"]
    if len(history) != STAGES.index(value["stage"]) + 1:
        invalid()
    previous = 0
    for index, item in enumerate(history):
        if (not isinstance(item, dict) or set(item) != {"stage", "elapsed_ms"}
                or item["stage"] != STAGES[index] or type(item["elapsed_ms"]) is not int
                or not previous <= item["elapsed_ms"] <= 60000):
            invalid()
        if item["stage"] in WAITS and item["elapsed_ms"] - previous < 1000:
            invalid()
        previous = item["elapsed_ms"]
    # Existing parser checks nonce/schema, host/login invariants and connect 1/5/8s.
    prepared.parse_progress((json.dumps(common_progress(value)) + "\n").encode(), nonce)
    return value


def parse_record(raw, nonce):
    """Validate the separate marker then compose the existing final-record contract."""
    if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", nonce):
        invalid()
    prefix = ("QQ_PREPARED_PHASE_" + nonce + ":").encode()
    if not raw or len(raw) > control.LIMIT or not raw.startswith(prefix) or raw.count(b"\n") != 1 or not raw.endswith(b"\n"):
        invalid()
    value = json.loads(raw[len(prefix):], object_pairs_hook=control.unique_object)
    if not isinstance(value, dict) or "progress" not in value:
        invalid()
    progress = parse_progress((json.dumps(value["progress"]) + "\n").encode(), nonce)
    common = dict(value, progress=common_progress(progress))
    prepared.parse_record(("QQ_PREPARED_CONNECT_" + nonce + ":" + json.dumps(common) + "\n").encode(), nonce)
    return value


def project_progress(report, progress):
    """Expose only completed dispatch waits, never runtime/native readiness guesses."""
    prepared.project_progress(report, common_progress(progress) if progress else None)
    completed = {item["stage"] for item in progress["history"]} if progress else set()
    report["phase_timers_verified"] = {name: stage in completed for name, stage in
        (("baseline", "phase_baseline_1s"), ("loaded", "phase_loaded_1s"),
         ("prepared", "phase_prepared_1s"), ("initialized", "phase_initialized_1s"), ("connected", "tick_1s"))}


def project_report(report, observed, returncode):
    """Keep all conservative native readiness semantics from the shared projection."""
    prepared.project_report(report, dict(observed, progress=common_progress(observed["progress"])), returncode)
    project_progress(report, observed["progress"])


def observe(qq_root, *, execute=False):
    """Use only the fixed shared runner with its unchanged isolation and deadlines."""
    return control.observe(qq_root, execute=execute, control_mode="prepared_phase_offline")


def main(argv=None):
    """Require explicit execute and reserve one immutable artifact before execution."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qq-root", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if args.execute:
        # Exclusive creation refuses a second invocation before any native work.
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
