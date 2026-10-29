"""Fixed initialize-only control; reuse the wrapper control's entire jail runner."""

import argparse
import json

try:
    from tools import qq_ephemeral_host_entry as control
except ModuleNotFoundError as exc:
    if exc.name != "tools":
        raise
    import qq_ephemeral_host_entry as control


def observe(qq_root, *, execute=False):
    """Authorize only the internally authored initialize mode, never custom source."""
    return control.observe(qq_root, execute=execute, control_mode="initialize")


def main(argv=None):
    """Expose software root and explicit execution only; no account or script input."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qq-root", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    report = observe(args.qq_root, execute=args.execute)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] in ("not_executed", "observed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
