"""Fixed bounded public exit metadata observation; unchanged native host control."""
import argparse
import json
from pathlib import Path

try:
    from tools import qq_ephemeral_host_entry as control
except ModuleNotFoundError as exc:
    if exc.name != "tools":
        raise
    import qq_ephemeral_host_entry as control

# Exclusive creation prevents replacing the one authorized actual observation.
ARTIFACT = Path(__file__).resolve().parents[1] / "docs" / "qq-host-exit-evidence.json"


def observe(qq_root, *, execute=False):
    """Reuse exact waits control sources and isolation, adding only exit measurement."""
    return control.observe(qq_root, execute=execute, control_mode="host_dispatch_exit_offline")


def main(argv=None):
    """Require explicit consent and reserve the fixed evidence file exclusively."""
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
