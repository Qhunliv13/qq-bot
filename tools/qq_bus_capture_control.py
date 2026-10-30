"""Fixed no-QQ offline bus diagnostic control; never publish captured bytes."""

import argparse
import json
from pathlib import Path

try:
    from tools import qq_bus_admission_control as bus
except ModuleNotFoundError as exc:
    if exc.name != "tools":
        raise
    import qq_bus_admission_control as bus


def observe_bus(command, env, output, errors, timeout, report):
    """Bind capture to the sole shared observer, without global capture settings."""
    return bus.observe_bus(command, env, output, errors, timeout, report, detailed=True)


def observe(qq_root, *, execute=False):
    """Select only the authored capture mode in the existing isolation runner."""
    return bus.control.observe(qq_root, execute=execute, control_mode="bus_admission_capture_offline")


def main(argv=None):
    """Require consent and exclusively create a new evidence artifact, never replace."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qq-root", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    artifact = Path(__file__).resolve().parents[1] / "docs" / "evidence" / "qq-bus-capture-evidence.json"
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
