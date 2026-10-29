"""Harmless child-only resize experiment; never loads QQ or changes its sandbox."""
import argparse
import errno
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

# Independent disposable Python children, not QQ or its supervisor, receive these limits.
LIMITS = (16384, 65536)
TARGET_BYTES = 32768
ARTIFACT = Path(__file__).resolve().parents[1] / "docs" / "qq-fsize-mechanism-evidence.json"


def resize(fd):
    """Return only fixed errno categories and size, never file contents or paths."""
    try:
        os.ftruncate(fd, TARGET_BYTES)
        outcome = "resized"
    except OSError as exc:
        outcome = "efbig" if exc.errno == errno.EFBIG else "other_os_error"
    return {"outcome": outcome, "size": os.fstat(fd).st_size}


def child(kind, limit):
    """Set a fresh child's own limit and ignore SIGXFSZ solely to observe errno."""
    import resource
    if sys.platform != "linux" or kind not in ("tmpfs", "memfd") or limit not in LIMITS:
        raise ValueError("invalid_child_configuration")
    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
    # Both files are anonymous, local to this child and closed before reporting.
    if kind == "memfd":
        if not hasattr(os, "memfd_create"):
            return {"outcome": "unsupported", "size": None}
        fd = os.memfd_create("bounded-resize-test", os.MFD_CLOEXEC)
        try:
            return resize(fd)
        finally:
            os.close(fd)
    with tempfile.TemporaryFile(dir="/dev/shm") as stream:
        return resize(stream.fileno())


def observe():
    """Compare the same resize in four fresh children with fixed arguments and bounds."""
    import resource
    before = resource.getrlimit(resource.RLIMIT_FSIZE)
    observations = []
    for kind in ("tmpfs", "memfd"):
        for limit in LIMITS:
            process = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--child", kind, "--limit", str(limit)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}, timeout=5, check=False,
            )
            if process.returncode != 0 or len(process.stdout) > 1024:
                raise ValueError("mechanism_child_failed")
            result = json.loads(process.stdout)
            if (set(result) != {"outcome", "size"}
                    or result["outcome"] not in ("resized", "efbig", "other_os_error", "unsupported")):
                raise ValueError("mechanism_result_invalid")
            observations.append(dict(kind=kind, child_limit_bytes=limit, **result))
    return {"schema_version": 1, "target_bytes": TARGET_BYTES, "observations": observations,
            "parent_limit_unchanged": resource.getrlimit(resource.RLIMIT_FSIZE) == before,
            "qq_executed": False, "qq_limits_changed": False,
            "child_sigxfsz_ignored_for_errno_only": True,
            "qq_root_cause_proven": False}


def main(argv=None):
    """Require explicit execution and reserve evidence once before any experiment."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--child", choices=("tmpfs", "memfd"), help=argparse.SUPPRESS)
    parser.add_argument("--limit", type=int, choices=LIMITS, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if sys.platform != "linux":
        parser.error("linux_required")
    if args.child:
        if args.execute or args.limit is None:
            parser.error("invalid_child_arguments")
        print(json.dumps(child(args.child, args.limit), sort_keys=True))
        return 0
    if not args.execute or args.limit is not None:
        parser.error("explicit_execute_required")
    with ARTIFACT.open("x", encoding="utf-8") as stream:
        report = observe()
        json.dump(report, stream, sort_keys=True, indent=2)
        stream.write("\n")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
