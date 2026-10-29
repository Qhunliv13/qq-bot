"""Fixed external resource contrast of unchanged native-free host identity capture."""
import argparse
import json
from pathlib import Path

try:
    from tools import qq_ephemeral_host_entry as control
except ModuleNotFoundError as exc:
    if exc.name != "tools":
        raise
    import qq_ephemeral_host_entry as control

# Exclusive evidence prevents an accidental second actual run from replacing the first.
ARTIFACT = Path(__file__).resolve().parents[1] / "docs" / "qq-host-resources-evidence.json"


def observe(qq_root, *, execute=False):
    """Reuse identity sources and gates; extend only fixed external resource sampling."""
    return control.observe(qq_root, execute=execute, control_mode="host_dispatch_resources_offline")


def main(argv=None):
    """Accept only fixed root and explicit consent; reserve evidence before execution."""
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
