"""Fixed identity-only no-QQ contrast of the unchanged detailed bus client."""

import argparse
import json
from pathlib import Path

try:
    from tools import qq_bus_capture_control as capture
except ModuleNotFoundError as exc:
    if exc.name != "tools":
        raise
    import qq_bus_capture_control as capture


def observe_bus(command, env, output, errors, timeout, report):
    """Reuse exact baseline detailed observer, changing only the fixed report label."""
    capture.observe_bus(command, env, output, errors, timeout, report)
    report["mode"] = "isolated_bus_identity_control"


def observe(qq_root, *, execute=False):
    """Select the sole identity mode without accepting identity bytes or host variables."""
    return capture.bus.control.observe(qq_root, execute=execute, control_mode="bus_identity_offline")


def main(argv=None):
    """Require execution consent and exclusively create the fixed new evidence file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qq-root", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    artifact = Path(__file__).resolve().parents[1] / "docs" / "qq-bus-identity-evidence.json"
    if args.execute:
        # Fail before execution if an artifact or symlink already occupies this name.
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
