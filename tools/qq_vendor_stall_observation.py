"""Shared bounded external stall facts; no QQ JS, memory, titles, or actions."""

import argparse
import itertools
import json
import os
from pathlib import Path
import re
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
# Inspect at most 256 numeric descriptor entries plus one truncation sentinel.
MAX_FD = 256
DETAILED_RESOURCES = False
# Only the fixed waits mode opts in; eight first-observed symbols bound each sample.
WAIT_SYMBOLS = False
MAX_SYMBOLS = 8
WAIT_CATEGORIES = ("symbol", "numeric_zero", "numeric_nonzero", "empty", "invalid_shape", "denied", "unavailable")
MAIN_PID = None
MAIN_START = None
WINDOW_SAMPLER = None
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


def fd_resources(root):
    """Count bounded numeric names only; never follow links or read descriptor targets."""
    result = {"visibility": "unavailable", "count": None, "maxfd": None, "truncated": False}
    try:
        # scandir streams entries (unlike listdir); the extra entry only detects truncation.
        with os.scandir(root / "fd") as entries:
            names = [entry.name for entry in itertools.islice(entries, MAX_FD + 1)]
        numbers = [int(name) for name in names[:MAX_FD] if name.isascii() and name.isdigit()]
        result.update(visibility="readable", count=len(numbers), maxfd=max(numbers, default=None),
                      truncated=len(names) > MAX_FD)
    except PermissionError:
        # A denied directory is unknown, never a measured zero descriptors.
        result["visibility"] = "denied"
    except OSError:
        pass
    return result


def project_wait(raw):
    """Expose only bounded ASCII public symbols or fixed formats, never addresses/text."""
    if len(raw) > 128:
        return {"category": "invalid_shape"}
    value = raw.strip(b" \t\r\n\v\f")
    if not value:
        return {"category": "empty"}
    # Numeric-looking all-alpha hex must be masked before applying the symbol regex.
    numeric = (value == b"0" or re.fullmatch(rb"0[xX][0-9a-fA-F]+", value)
               or re.fullmatch(rb"[0-9a-fA-F]{8,}", value))
    if numeric:
        digits = value[2:] if value[:2].lower() == b"0x" else value
        return {"category": "numeric_zero" if not digits.strip(b"0") else "numeric_nonzero"}
    if re.fullmatch(rb"[A-Za-z_][A-Za-z0-9_]{0,95}", value):
        return {"category": "symbol", "symbol": value.decode("ascii")}
    return {"category": "invalid_shape"}


def read_wait(path):
    """Read at most 128 bytes, conservatively reject a full buffer to prevent truncation."""
    try:
        with path.open("rb") as stream:
            raw = stream.read(128)
        # A valid symbol is <=96 bytes; a full read cannot establish complete shape.
        return {"category": "invalid_shape"} if len(raw) == 128 else project_wait(raw)
    except PermissionError:
        return {"category": "denied"}
    except OSError:
        return {"category": "unavailable"}


def aggregate_wait(aggregate, projected):
    """Retain eight first-observed symbol keys and count all overflow observations."""
    aggregate["categories"][projected["category"]] += 1
    if projected["category"] != "symbol":
        return
    key = "symbol:" + projected["symbol"]
    symbols = aggregate["symbols"]
    if key in symbols or len(symbols) < MAX_SYMBOLS:
        symbols[key] = symbols.get(key, 0) + 1
    else:
        aggregate["overflow_count"] += 1


def resource_threads(root, *, waits=None):
    """Aggregate visibility without IDs/symbols, retaining read failures despite stat races."""
    threads = []
    counts = dict.fromkeys(("sampled", "wchan_zero", "wchan_empty", "wchan_read_denied",
                            "wchan_unavailable", "wchan_nonzero", "stat_unavailable"), 0)
    try:
        for entry in itertools.islice((root / "task").iterdir(), MAX_THREADS):
            if not entry.name.isdigit():
                continue
            counts["sampled"] += 1
            category = "unknown"
            try:
                if waits is not None:
                    projected = read_wait(entry / "wchan")
                    aggregate_wait(waits, projected)
                    if projected["category"] == "denied":
                        raise PermissionError
                    if projected["category"] == "unavailable":
                        raise OSError
                    # Existing coarse fields remain descriptive, not guessed wait causes.
                    raw = (projected.get("symbol", "") if projected["category"] in ("symbol", "empty")
                           else "0" if projected["category"] == "numeric_zero" else "other")
                else:
                    raw = bounded_text(entry / "wchan", 128)
                value = raw.strip()
                # Per-thread booleans are immediately aggregated, never persisted with TIDs.
                zero, empty = value == "0", value == ""
                counts["wchan_zero"] += zero
                counts["wchan_empty"] += empty
                counts["wchan_nonzero"] += not (zero or empty)
                category = coarse_wchan(raw)
            except PermissionError:
                counts["wchan_read_denied"] += 1
            except (OSError, ValueError):
                counts["wchan_unavailable"] += 1
            try:
                stat = parse_stat(bounded_text(entry / "stat"))
                again = parse_stat(bounded_text(entry / "stat"))
                if again["start_ticks"] != stat["start_ticks"]:
                    counts["stat_unavailable"] += 1
                    continue
                threads.append({"state": stat["state"], "wait_category": category})
            except (OSError, ValueError, IndexError):
                counts["stat_unavailable"] += 1
    except OSError:
        return threads, counts, False
    return threads, counts, True


def trusted_sample():
    """Run via the existing bounded UID65534 sampler, restricted to the main QQ PID."""
    result = {"main": None, "windows": None, "identity_verified": False}
    before = application.process_identity(MAIN_PID)
    if (before is None or before["uid"] != [65534] * 4 or not before["cap_eff_zero"]
            or not before["no_new_privileges"] or not before["user_namespace_matches_supervisor"]
            or (MAIN_START is not None and before["start_ticks"] != MAIN_START)):
        return result
    root = Path("/proc") / str(MAIN_PID)
    try:
        raw_stat = bounded_text(root / "stat")
        main = parse_stat(raw_stat)
    except (OSError, ValueError, IndexError):
        return result
    threads = []
    if DETAILED_RESOURCES:
        waits = ({"categories": dict.fromkeys(WAIT_CATEGORIES, 0), "symbols": {}, "overflow_count": 0}
                 if WAIT_SYMBOLS else None)
        threads, visibility, task_readable = resource_threads(root, waits=waits)
        if WAIT_SYMBOLS:
            # Both main and task observations are discarded if the outer identity guard fails.
            main["main_wait"] = read_wait(root / "wchan")
            waits["symbols"] = dict(sorted(waits["symbols"].items()))
            main["task_waits"] = waits
        # tail starts at Linux stat field 3: flags field 9 is tail[6], not tail[9].
        try:
            main["flags"] = int(raw_stat[raw_stat.rfind(")") + 2:].split()[6])
        except (ValueError, IndexError):
            main["flags"] = None
        main.update(fd=fd_resources(root), wchan_visibility=visibility, task_directory_readable=task_readable)
    else:
        # Old modes retain their original record schema and read-failure behavior.
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
    if WAIT_SYMBOLS:
        # New mode aggregates existing coarse facts too: 30x64 records plus symbols
        # would otherwise exceed the unchanged helper cap. Old schemas are untouched.
        main["thread_summary"] = {"states": dict.fromkeys("RSDZTtXxKWPIN", 0),
                                  "wait_categories": dict.fromkeys(("unknown", "futex", "poll", "wait", "read", "other"), 0)}
        for thread in main.pop("threads"):
            main["thread_summary"]["states"][thread["state"]] += 1
            main["thread_summary"]["wait_categories"][thread["wait_category"]] += 1
    x = None
    try:
        if WINDOW_SAMPLER is not None:
            # A fixed preimported reader extends metadata only, not the jail/waiter.
            result["windows"] = WINDOW_SAMPLER(before)
        else:
            x = application.PrivateXIdentity()
            windows = x.snapshot()
            matching = [item for item in windows["qq_pid_properties"]
                        if item["identity"]["pid"] == MAIN_PID
                        and item["identity"]["start_ticks"] == before["start_ticks"]]
            # Existing stall mode remains count-only with no role property queries.
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


def observe_wait(process, env, timeout=30, report=None, *, window_sampler=None, detailed_resources=False,
                 wait_symbols=False):
    """Observe fixed prepared/vendor waits with <=30 external samples; retain deadline."""
    global MAIN_PID, MAIN_START, WINDOW_SAMPLER, DETAILED_RESOURCES, WAIT_SYMBOLS
    if type(wait_symbols) is not bool or (wait_symbols and not detailed_resources):
        raise control.runtime.ProbeError("wait_symbols_invalid")
    if type(detailed_resources) is not bool:
        raise control.runtime.ProbeError("resource_detail_invalid")
    if window_sampler is not None:
        try:
            from tools import qq_private_window_roles
        except ModuleNotFoundError as exc:
            if exc.name != "tools":
                raise
            import qq_private_window_roles
        if window_sampler is not qq_private_window_roles.sample_roles:
            raise control.runtime.ProbeError("window_sampler_invalid")
    if timeout != 30 or report is None:
        raise control.runtime.ProbeError("stall_wait_bounds_invalid")
    MAIN_PID, MAIN_START, WINDOW_SAMPLER = process.pid, None, window_sampler
    DETAILED_RESOURCES = detailed_resources
    WAIT_SYMBOLS = wait_symbols
    records = []
    start = time.monotonic()
    original = application.sample_identity
    # Reuse the existing privilege/fork/timeout implementation, not another runner.
    application.sample_identity = trusted_sample
    report["stall_observation"] = {"samples": records, "maximum_samples": MAX_SAMPLES,
                                   "maximum_threads_per_sample": MAX_THREADS,
                                   "sample_interval_seconds": 1, "private_pid_scope_only": True,
                                   "window_roles_queried": window_sampler is not None}
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
        MAIN_PID, MAIN_START, WINDOW_SAMPLER = None, None, None
        DETAILED_RESOURCES = False
        WAIT_SYMBOLS = False


def detailed_categories(bounded):
    """Project fixed tokens per bounded stderr line; absence is not proof of no error."""
    categories = dict.fromkeys(("system_bus_socket_missing", "private_session_bus_connect_failure",
                               "dbus_scope_unknown", "resource_emfile", "resource_efbig",
                               "resource_enospc", "profile_create_or_open_failed", "sandbox_fatal"), False)
    for line in bounded.lower().splitlines():
        # Path boundaries exclude lookalikes/suffixed paths; never join separate lines.
        system = bool(re.search(rb"(?<![a-z0-9_./-])/run/dbus/system_bus_socket(?![a-z0-9_./-])", line)
                      and re.search(rb"\b(?:enoent|no such file or directory)\b", line))
        private = bool(re.search(rb"(?<![a-z0-9_./-])/tmp/runtime/bus(?![a-z0-9_./-])", line)
                       and re.search(rb"\b(?:failed to connect|connection refused|connection failed|could not connect|unable to connect|enoent|no such file or directory)\b", line))
        categories["system_bus_socket_missing"] |= system
        categories["private_session_bus_connect_failure"] |= private
        categories["dbus_scope_unknown"] |= bool(re.search(rb"\b(?:dbus|d-bus)\b", line)
                                                 and re.search(rb"\b(?:failed|failure|error|unavailable|refused|could not|unable to)\b", line)
                                                 and not (system or private))
        for category, tokens in (
                ("resource_emfile", rb"\b(?:emfile|too many open files)\b"),
                ("resource_efbig", rb"\b(?:efbig|file too large|file size limit exceeded)\b"),
                ("resource_enospc", rb"\b(?:enospc|no space left on device)\b"),
                ("profile_create_or_open_failed", rb"\b(?:failed to create profile|failed to open profile|cannot create profile|cannot open profile)\b"),
                ("sandbox_fatal", rb"\b(?:no usable sandbox|sandbox initialization failed)\b")):
            categories[category] |= bool(re.search(tokens, line))
        categories["sandbox_fatal"] |= bool(re.search(rb"\bfatal\b", line) and re.search(rb"\bsandbox\b", line))
    return categories


def capture_categories(output, errors, report, detailed=False):
    """Classify existing private handles only; default report remains byte-compatible."""
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
    if detailed:
        capture = report["stall_capture"]
        capture["stderr_categories"].update(detailed_categories(bounded))
        capture.update(stdout_cap_reached=capture["stdout_size_bytes"] >= control.LIMIT,
                       stderr_cap_reached=capture["stderr_size_bytes"] >= control.LIMIT,
                       category_false_means_no_error=False, root_cause_proven=False)


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
