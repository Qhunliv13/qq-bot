"""Prepared-native successor on the verified host profile, not a single-variable contrast."""
import argparse
import json
from pathlib import Path

try:
    from tools import qq_ephemeral_host_entry as control
except ModuleNotFoundError as exc:
    if exc.name != "tools":
        raise
    import qq_ephemeral_host_entry as control

# Exclusive public metadata only; native output must remain outside the repository.
ARTIFACT = Path(__file__).resolve().parents[1] / "docs" / "qq-prepared-fsize-evidence.json"


def observe(qq_root, *, execute=False, private_capture_path=None):
    """Reuse unchanged prepared entry/parser with fixed identity, logging and 1MiB profile."""
    return control.observe(qq_root, execute=execute, control_mode="prepared_connect_fsize_offline",
                           private_capture_path=private_capture_path)


def main(argv=None):
    """Require explicit execution/private consent and reserve evidence before launching."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qq-root", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--private-output")
    args = parser.parse_args(argv)
    if args.execute and not args.private_output:
        parser.error("--execute requires --private-output outside the repository")
    if args.execute:
        with ARTIFACT.open("x", encoding="utf-8") as stream:
            report = observe(args.qq_root, execute=True, private_capture_path=args.private_output)
            json.dump(report, stream, sort_keys=True, indent=2)
            stream.write("\n")
    else:
        report = observe(args.qq_root, private_capture_path=args.private_output)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] in ("not_executed", "observed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
