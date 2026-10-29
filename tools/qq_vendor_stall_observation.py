"""Bounded external vendor-stall facts; no QQ JS, memory, titles, or actions."""

import argparse
import itertools
import json
import os
from pathlib import Path
import subprocess
import time

try:
    from tools import qq_ephemeral_host_entry as control
    from tools import qq_application_observation as application
except ModuleNotFoundError as exc:
    if exc.name != "tools":
        raise
    import qq_ephemeral_host_entry as control
    import qq_application_observation as application

# Fixed budgets match the authorized common runner; never relax child limits.
MAX_SAMPLES = 30
MAX_THREADS = 64
MAIN_PID = None
MAIN_START = None
ERROR_TOKENS = {
    "too_many_open_files": (b"too many open files", b"emfile", b"enfile"),
    "file_size_limit": (b"file size limit", b"file too large", b"efbig"),
    "dbus_unavailable": (b"failed to connect to the bus", b"dbus connection failed", b"dbus unavailable"),
    "fontconfig_unavailable": (b"fontconfig error", b"fontconfig warning", b"cannot load default config file"),
    "sandbox": (b"sandbox",),
    "sharedmemory": (b"shared memory", b"shared_memory", b"/dev/shm"),
}


def bounded_text(path, limit=4096):
    """Read only a fixed proc pseudo-file with a strict allocation bound."""
    with path.open("r", encoding="ascii", errors="replace") as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError("proc_record_oversized")
    return value


def parse_stat(raw):
    """Discard comm, projecting only state, numeric CPU/thread counts and identity."""
    tail = raw[raw.rfind(")") + 2:].split()
    state = tail[0]
    if state not in "RSDZTtXxKWPIN":
        raise ValueError("proc_state_invalid")
    return {"state": state, "utime_ticks": int(tail[11]), "stime_ticks": int(tail[12]),
            "num_threads": int(tail[17]), "start_ticks": int(tail[19])}


def coarse_wchan(raw):
    """Map ephemeral kernel symbols to an allowlist; never retain raw symbols."""
    value = raw.strip().lower()
    if "futex" in value:
        return "futex"
    if any(token in value for token in ("poll", "ep_", "epoll", "select")):
        return "poll"
    if "wait" in value:
        return "wait"
    if "read" in value or "recv" in value:
        return "read"
    return "other"


def numeric_limits(root):
    """Project only three inherited numeric resource limits, never arbitrary text."""
    labels = {"Max cpu time": "cpu_seconds", "Max file size": "file_size_bytes", "Max open files": "open_files"}
    result = {}
    for line in bounded_text(root / "limits", 16384).splitlines():
        for label, key in labels.items():
            if line.startswith(label):
                fields = line[len(label):].split()
                if len(fields) >= 2 and all(value.isdigit() for value in fields[:2]):
                    result[key] = {"soft": int(fields[0]), "hard": int(fields[1])}
    return result


def trusted_sample():
    """Run via the existing bounded UID65534 sampler, restricted to the main QQ PID."""
    result = {"main": None, "windows": None, "identity_verified": False}
    before = application.process_identity(MAIN_PID)
    if (before is None or before["uid"] != [65534] * 4 or not before["cap_eff_zero"]
            or not before["no_new_privileges"] or not before["user_namespace_matches_supervisor"]
            or (MAIN_START is not None and before["start_ticks"] != MAIN_START)):
        return result
    root = Path("/proc") / str(MAIN_PID)
    main = parse_stat(bounded_text(root / "stat"))
    threads = []
    # Only the first fixed 64 directory entries are inspected, not an unbounded list.
    for entry in itertools.islice((root / "task").iterdir(), MAX_THREADS):
        if not entry.name.isdigit():
            continue
        try:
            stat = parse_stat(bounded_text(entry / "stat"))
            category = coarse_wchan(bounded_text(entry / "wchan", 128))
            again = parse_stat(bounded_text(entry / "stat"))
            if again["start_ticks"] == stat["start_ticks"]:
                threads.append({"state": stat["state"], "wait_category": category})
        except (OSError, ValueError, IndexError):
            continue
    main.update(threads=threads, threads_truncated=main["num_threads"] > MAX_THREADS,
                limits=numeric_limits(root))
    x = None
    try:
        x = application.PrivateXIdentity()
        windows = x.snapshot()
        matching = [item for item in windows["qq_pid_properties"]
                    if item["identity"]["pid"] == MAIN_PID
                    and item["identity"]["start_ticks"] == before["start_ticks"]]
        # No additional role properties are queried: count-only is deliberately honest.
        result["windows"] = {"root_children": windows["root_children"],
                             "visited_windows": windows["visited_windows"],
                             "main_pid_correlated_windows": len(matching),
                             "dialog_presence": None, "roles_queried": False}
    except (OSError, ValueError, control.runtime.ProbeError):
        pass
    finally:
        if x is not None:
            x.close()
    after = application.process_identity(MAIN_PID)
    if (after is None or after != before or main["start_ticks"] != before["start_ticks"]):
        return {"main": None, "windows": None, "identity_verified": False}
    free = os.statvfs("/tmp")
    result.update(main=main, identity_verified=True,
                  tmp_free_bytes=free.f_bavail * free.f_frsize)
    return result


def summarize(records, ticks_per_second):
    """Describe sampled work/wait, not deadlock, modal state, or root-cause proof."""
    valid = [item for item in records if item.get("identity_verified") and item.get("main")]
    result = {"classification": "insufficient_observation", "valid_samples": len(valid),
              "ticks_per_second": ticks_per_second, "first_cpu_ticks": None,
              "last_cpu_ticks": None, "cpu_tick_delta": None,
              "root_cause_proven": False}
    if len(valid) < 2:
        return result
    first, last = valid[0]["main"], valid[-1]["main"]
    delta = last["utime_ticks"] + last["stime_ticks"] - first["utime_ticks"] - first["stime_ticks"]
    elapsed = valid[-1]["elapsed_seconds"] - valid[0]["elapsed_seconds"]
    result.update(first_cpu_ticks={key: first[key] for key in ("utime_ticks", "stime_ticks")},
                  last_cpu_ticks={key: last[key] for key in ("utime_ticks", "stime_ticks")}, cpu_tick_delta=delta)
    if delta < 0 or elapsed <= 0:
        return result
    sleeping = sum(item["main"]["state"] in ("S", "D", "I") for item in valid)
    # Half of one CPU over the observation is a descriptive busy threshold only.
    result["classification"] = ("busy_cpu_observed" if delta >= ticks_per_second * elapsed * 0.5 else
                                "predominantly_waiting_observed" if sleeping * 2 >= len(valid) else
                                "mixed_or_low_cpu_observed")
    return result


def observe_wait(process, env, timeout=30, report=None):
    """Replace only vendor_stall wait with <=30 one-second external bounded samples."""
    global MAIN_PID, MAIN_START
    if timeout != 30 or report is None:
        raise control.runtime.ProbeError("stall_wait_bounds_invalid")
    MAIN_PID, MAIN_START = process.pid, None
    records = []
    start = time.monotonic()
    original = application.sample_identity
    # Reuse the existing privilege/fork/timeout implementation, not another runner.
    application.sample_identity = trusted_sample
    report["stall_observation"] = {"samples": records, "maximum_samples": MAX_SAMPLES,
                                   "maximum_threads_per_sample": MAX_THREADS,
                                   "sample_interval_seconds": 1, "private_pid_scope_only": True,
                                   "window_roles_queried": False}
    try:
        for index in range(MAX_SAMPLES):
            target = start + index
            while process.poll() is None and time.monotonic() < target:
                time.sleep(min(0.05, max(0, target - time.monotonic())))
            if process.poll() is not None or time.monotonic() >= start + timeout:
                break
            item = application.unprivileged_sample(env, timeout=min(0.75, start + timeout - time.monotonic()))
            if not isinstance(item, dict):
                item = {"main": None, "windows": None, "identity_verified": False}
            item["elapsed_seconds"] = round(time.monotonic() - start, 3)
            if item.get("identity_verified") and MAIN_START is None:
                MAIN_START = item["main"]["start_ticks"]
            records.append(item)
        while process.poll() is None and time.monotonic() < start + timeout:
            time.sleep(min(0.05, max(0, start + timeout - time.monotonic())))
        if process.poll() is None:
            raise subprocess.TimeoutExpired(process.args, timeout)
        return process.returncode
    finally:
        application.sample_identity = original
        report["stall_observation"]["summary"] = summarize(records, os.sysconf("SC_CLK_TCK"))
        MAIN_PID, MAIN_START = None, None


def capture_categories(output, errors, report):
    """Before cleanup classify bounded stderr only and fstat actual capture sizes."""
    output.flush()
    errors.flush()
    errors.seek(0)
    raw = errors.read(control.LIMIT + 1)
    bounded = raw[:control.LIMIT].lower()
    report["stall_capture"] = {
        "stderr_categories": {key: any(token in bounded for token in tokens) for key, tokens in ERROR_TOKENS.items()},
        "stderr_prefix_truncated": len(raw) > control.LIMIT,
        "stdout_size_bytes": os.fstat(output.fileno()).st_size,
        "stderr_size_bytes": os.fstat(errors.fileno()).st_size,
        "resource_bounds_unchanged": {"file_size_bytes": 16384, "open_files": 128, "cpu_seconds": 30},
    }


def observe(qq_root, *, execute=False):
    """Use the single shared isolated runner with exactly the original vendor JS."""
    return control.observe(qq_root, execute=execute, control_mode="vendor_stall")


def main(argv=None):
    """Expose only root and explicit execution consent, never custom script/flags."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qq-root", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    report = observe(args.qq_root, execute=args.execute)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] in ("not_executed", "observed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
