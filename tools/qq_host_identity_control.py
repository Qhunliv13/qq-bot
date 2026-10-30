"""Fixed identity-only contrast of the unchanged native-free host dispatch capture."""
import argparse
import json
from pathlib import Path

try:
    from tools import qq_ephemeral_host_entry as control
except ModuleNotFoundError as exc:
    if exc.name != "tools":
        raise
    import qq_ephemeral_host_entry as control

ARTIFACT = Path(__file__).resolve().parents[1] / "docs" / "evidence" / "qq-host-identity-evidence.json"


def observe(qq_root, *, execute=False):
    """Reuse the exact host capture, enabling only fixed synthetic UID/GID records."""
    return control.observe(qq_root, execute=execute, control_mode="host_dispatch_identity_offline")


def main(argv=None):
    """Reserve exclusive fixed evidence before execution; accept no identity overrides."""
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
