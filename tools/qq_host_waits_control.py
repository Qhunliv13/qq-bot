"""Fixed public wchan format/symbol observation of unchanged host identity capture."""
import argparse
import json
from pathlib import Path

try:
    from tools import qq_ephemeral_host_entry as control
except ModuleNotFoundError as exc:
    if exc.name != "tools":
        raise
    import qq_ephemeral_host_entry as control

# Exclusive reservation prevents an accidental second actual run replacing evidence.
ARTIFACT = Path(__file__).resolve().parents[1] / "docs" / "evidence" / "qq-host-waits-evidence.json"


def observe(qq_root, *, execute=False):
    """Reuse exact identity sources, capture and limits; add external waits only."""
    return control.observe(qq_root, execute=execute, control_mode="host_dispatch_waits_offline")


def main(argv=None):
    """Accept only root and explicit consent; reserve evidence before execution."""
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
