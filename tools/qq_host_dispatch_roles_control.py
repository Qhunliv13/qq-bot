"""Fixed native-free offline dispatch with external private-X role metadata only."""
import argparse
import json
from pathlib import Path

try:
    from tools import qq_ephemeral_host_entry as control
except ModuleNotFoundError as exc:
    if exc.name != "tools":
        raise
    import qq_ephemeral_host_entry as control

# Separate exclusive evidence: never replace the earlier count-only observation.
ARTIFACT = Path(__file__).resolve().parents[1] / "docs" / "evidence" / "qq-host-dispatch-roles-evidence.json"


def observe(qq_root, *, execute=False):
    """Select only the fixed roles mode; retain the original dispatch source/parser."""
    return control.observe(qq_root, execute=execute, control_mode="host_dispatch_roles_offline")


def main(argv=None):
    """Reserve this mode's evidence before execution; expose no source/mode override."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qq-root", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if args.execute:
        # Exclusive creation fails before launch when evidence already exists.
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
