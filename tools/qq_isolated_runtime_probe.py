"""Opt-in LinuxQQ process.versions probe, never an application/native/login probe.

The caller must supply a pristine extracted software-only QQ directory. System
library directories are exposed read-only; no host account directories are bound.
Default mode validates the candidate but executes nothing. Real execution needs
Linux root, util-linux unshare/mount, and explicit --execute consent.
"""

import argparse
import ctypes
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile

# Only runtime version strings, never arbitrary process properties, enter reports.
VERSION_KEYS = ("node", "electron", "chrome", "v8", "modules", "napi", "openssl", "uv")
OUTPUT_LIMIT = 1024 * 1024
SYSTEM_DIRS = ("/usr", "/lib", "/lib64", "/bin")
NAMESPACES = ("net", "mnt", "pid")


class ProbeError(ValueError):
    """Carry fixed redacted failure codes rather than host paths or raw output."""


def base_report():
    """Keep runtime evidence distinct from native compatibility or release consent."""
    return {"mode": "isolated_runtime_versions", "status": "not_executed",
            "runtime_executed": False, "runtime_versions_verified": False,
            "versions": {}, "errors": [],
            "production_cutover": False, "production_cutover_authorized": False,
            "native_binding_verified": False, "independent_backend_ready": False,
            "safe_to_login": False, "abi_compatibility": "unverified"}


def minimal_environment():
    """Construct a new environment without inherited tokens, proxies, or loaders."""
    return {"PATH": "/usr/bin:/bin", "HOME": "/tmp/home",
            "XDG_CONFIG_HOME": "/tmp/config", "XDG_CACHE_HOME": "/tmp/cache",
            "ELECTRON_RUN_AS_NODE": "1", "LANG": "C.UTF-8"}


def validate_root(candidate):
    """Accept only explicit canonical software directories with a regular QQ ELF.

    No directory walking or account-state reads occur. Pristine package provenance
    remains the caller's responsibility; a directory cannot prove its own origin.
    """
    supplied = Path(candidate)
    root = supplied.resolve(strict=True)
    if not supplied.is_absolute() or supplied != root or not root.is_dir():
        raise ProbeError("noncanonical_software_root")
    # Reject broad system/account roots; an extracted candidate may live under /tmp.
    if root == Path("/") or root in map(Path, SYSTEM_DIRS) or root.parts[1:2] in (
            ("home",), ("root",), ("run",), ("proc",), ("dev",), ("sys",)):
        raise ProbeError("unsafe_software_root")
    exe = root / "qq"
    if exe.is_symlink() or not exe.is_file() or not os.access(exe, os.X_OK):
        raise ProbeError("missing_qq_executable")
    with exe.open("rb") as stream:
        if stream.read(4) != b"\x7fELF":
            raise ProbeError("qq_not_elf")
    return root


def namespace_ids():
    """Read kernel namespace identities for mandatory parent/child comparison."""
    return {name: os.readlink("/proc/self/ns/" + name) for name in NAMESPACES}


def node_script(nonce):
    """Build a file-based Node script that cannot import QQ main or native modules."""
    keys = json.dumps(VERSION_KEYS)
    marker = json.dumps("QQ_RUNTIME_" + nonce + ":")
    return ("'use strict';\nconst versions = {};\nfor (const key of " + keys + ") {\n"
            "  if (typeof process.versions[key] === 'string') versions[key] = process.versions[key];\n"
            "}\nprocess.stdout.write(" + marker + "+JSON.stringify({versions,platform:process.platform,arch:process.arch})+'\\n');\n")


def parse_output(data, nonce):
    """Accept exactly one nonce-marked record and strictly whitelist its contents."""
    if len(data) > OUTPUT_LIMIT:
        raise ProbeError("output_limit")
    marker = ("QQ_RUNTIME_" + nonce + ":").encode("ascii")
    records = [line[len(marker):] for line in data.splitlines() if line.startswith(marker)]
    if len(records) != 1:
        raise ProbeError("runtime_record_missing_or_ambiguous")
    try:
        value = json.loads(records[0])
        if not isinstance(value, dict) or set(value) != {"versions", "platform", "arch"}:
            raise ValueError
        versions = value["versions"]
        if not isinstance(versions, dict) or not {"node", "electron"} <= set(versions):
            raise ValueError
        if not set(versions) <= set(VERSION_KEYS):
            raise ValueError
        if any(not isinstance(v, str) or not re.fullmatch(r"[0-9A-Za-z.+_-]{1,80}", v)
               for v in versions.values()):
            raise ValueError
        if value["platform"] != "linux" or value["arch"] not in ("x64", "arm64", "ia32", "arm"):
            raise ValueError
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ProbeError("invalid_runtime_record") from exc
    return value


def classify_runtime_stderr(data):
    """Return fixed loader/sandbox categories, never raw logs or filesystem paths."""
    text = data[:OUTPUT_LIMIT].decode("ascii", errors="ignore")
    missing = re.search(r"error while loading shared libraries: ([A-Za-z0-9_.+-]{1,120}):", text)
    if missing:
        return {"runtime_failure_category": "missing_shared_library",
                "missing_library": missing.group(1)}
    if "No usable sandbox!" in text or "Failed to move to new namespace" in text:
        return {"runtime_failure_category": "sandbox_initialization_failed"}
    return {"runtime_failure_category": "unclassified_nonzero_exit"}


def run_checked(args):
    """Run setup argument lists quietly, failing closed without returning stderr."""
    subprocess.run(args, check=True, timeout=3, env=minimal_environment(),
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


def bind_readonly(source, target, mount):
    """Bind one explicit software/system/device path without recursive host mounts."""
    run_checked([mount, "--bind", str(source), str(target)])
    run_checked([mount, "-o", "remount,bind,ro,nosuid,nodev", str(target)])


def drop_runtime_privileges():
    """Run only in the QQ child; keep namespace PID 1's death signal intact.

    subprocess's exec handshake confirms this hook completed before returning.
    A failed verification prevents exec, and does not run QQ with root privilege.
    """
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(8, 0, 0, 0, 0) != 0 or libc.prctl(38, 1, 0, 0, 0) != 0:
        raise ProbeError("privilege_lock_failed")
    os.setgroups([])
    os.setgid(65534)
    os.setuid(65534)
    # Credential changes clear PDEATHSIG; restore it to the still-root PID 1.
    death_signal = ctypes.c_int()
    if (libc.prctl(1, 9, 0, 0, 0) != 0
            or libc.prctl(2, ctypes.byref(death_signal), 0, 0, 0) != 0
            or death_signal.value != 9 or os.getppid() != 1):
        raise ProbeError("runtime_supervisor_missing")
    status = Path("/proc/self/status").read_text(encoding="ascii")
    fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
    if (os.geteuid() != 65534 or os.getegid() != 65534
            or int(fields["CapEff"].strip(), 16) != 0
            or fields["NoNewPrivs"].strip() != "1"):
        raise ProbeError("privilege_verification_failed")


def child_probe(root, jail, nonce, parent_ids, mount):
    """Build an empty chroot in private namespaces, drop privilege, then probe QQ.

    This helper is PID 1 in its new namespace. On exit the kernel kills remaining
    descendants. No QQ process starts unless all isolation setup has succeeded.
    """
    report = base_report()
    observed = namespace_ids()
    if set(parent_ids) != set(NAMESPACES) or any(observed[k] == parent_ids[k] for k in NAMESPACES):
        raise ProbeError("namespace_isolation_failed")
    import resource

    run_checked([mount, "--make-rprivate", "/"])
    # The parent creates an empty, private temporary directory, never a host root.
    if not jail.is_dir() or any(jail.iterdir()) or jail.is_symlink():
        raise ProbeError("jail_not_empty")
    jail.chmod(0o755)
    for directory in ("opt/QQ", "tmp", "run", "proc", "dev"):
        (jail / directory).mkdir(parents=True, exist_ok=True)
    bind_readonly(root, jail / "opt/QQ", mount)
    for source in SYSTEM_DIRS:
        if Path(source).is_dir():
            (jail / source[1:]).mkdir(exist_ok=True)
            bind_readonly(source, jail / source[1:], mount)
    for directory in ("tmp", "run"):
        run_checked([mount, "-t", "tmpfs", "-o", "nosuid,nodev,noexec,size=16m,mode=1777",
                     "tmpfs", str(jail / directory)])
    for directory in ("home", "config", "cache"):
        private = jail / "tmp" / directory
        private.mkdir(mode=0o700)
        os.chown(private, 65534, 65534)
    for device in ("null", "zero", "urandom", "random"):
        target = jail / "dev" / device
        target.touch()
        # Do not apply nodev to the four deliberately exposed minimal devices.
        run_checked([mount, "--bind", "/dev/" + device, str(target)])
        run_checked([mount, "-o", "remount,bind,ro,nosuid,noexec", str(target)])
    run_checked([mount, "-t", "proc", "-o", "nosuid,nodev,noexec", "proc", str(jail / "proc")])
    (jail / "probe.js").write_text(node_script(nonce), encoding="utf-8")
    # File capture + RLIMIT_FSIZE bounds output before parsing, unlike PIPE capture.
    with (jail / "tmp" / "stdout").open("w+b") as output, (jail / "tmp" / "stderr").open("w+b") as errors:
        os.chroot(jail)
        os.chdir("/")
        if namespace_ids() != observed:
            raise ProbeError("private_proc_identity_failed")
        # Keep this PID 1 supervisor root: changing its credentials would clear
        # unshare --kill-child's PDEATHSIG. Only QQ's child drops privileges.
        report["isolation"] = {"network": True, "mount": True, "pid": True, "chroot": True}
        resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_FSIZE, (OUTPUT_LIMIT, OUTPUT_LIMIT))
        resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
        report["runtime_launch_attempted"] = True
        try:
            result = subprocess.run(["/opt/QQ/qq", "/probe.js"], env=minimal_environment(),
                                    stdin=subprocess.DEVNULL, stdout=output,
                                    stderr=errors, timeout=10, check=False,
                                    preexec_fn=drop_runtime_privileges)
        except subprocess.TimeoutExpired:
            report.update(status="failed", runtime_executed=True, errors=["runtime_timeout"])
            return report
        except (OSError, subprocess.SubprocessError):
            report.update(status="failed", errors=["runtime_launch_failed"])
            return report
        report["runtime_executed"] = True
        report["isolation"].update(unprivileged=True, no_new_privileges=True,
                                   effective_capabilities_zero=True)
        report["runtime_exit_code"] = result.returncode
        if result.returncode != 0:
            errors.seek(0)
            report.update(classify_runtime_stderr(errors.read(OUTPUT_LIMIT + 1)))
            report.update(status="failed", errors=["runtime_nonzero_exit"])
            return report
        output.seek(0)
        try:
            report.update(parse_output(output.read(OUTPUT_LIMIT + 1), nonce))
        except ProbeError as exc:
            report.update(status="failed", errors=[str(exc)])
            return report
    report.update(status="observed", runtime_versions_verified=True)
    return report


def probe(qq_root, execute=False):
    """Validate without execution by default; explicit consent starts only isolation."""
    report = base_report()
    if sys.platform != "linux":
        report.update(status="refused", errors=["linux_required"])
        return report
    try:
        root = validate_root(qq_root)
        if not execute:
            return report
        if os.geteuid() != 0:
            raise ProbeError("root_required_for_isolation")
        unshare, mount = shutil.which("unshare"), shutil.which("mount")
        if not unshare or not mount:
            raise ProbeError("isolation_tools_missing")
        parent_ids = namespace_ids()
        nonce = secrets.token_hex(16)
        with tempfile.TemporaryDirectory(prefix="qq-isolated-probe-") as temporary:
            jail = Path(temporary) / "root"
            jail.mkdir()
            # Stage helper as a file; no multiline shell, eval, or shell=True.
            helper = Path(temporary) / "helper.py"
            shutil.copyfile(Path(__file__).resolve(), helper)
            args = [unshare, "--mount", "--net", "--pid", "--fork", "--kill-child",
                    "--mount-proc", sys.executable, str(helper), "--child",
                    "--qq-root", str(root), "--jail", str(jail), "--nonce", nonce,
                    "--parent-ns", json.dumps(parent_ids), "--mount-tool", mount]
            result = subprocess.run(args, env=minimal_environment(), timeout=15,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, check=False, start_new_session=True)
            if result.returncode not in (0, 1):
                raise ProbeError("isolation_helper_failed")
            if len(result.stdout) > OUTPUT_LIMIT:
                raise ProbeError("helper_output_limit")
            child = json.loads(result.stdout)
            # Only allow fixed helper fields; never surface unparsed process output.
            for key in ("status", "runtime_executed", "runtime_launch_attempted", "runtime_versions_verified", "versions", "errors", "platform", "arch", "isolation", "runtime_exit_code", "runtime_failure_category", "missing_library"):
                if key in child:
                    report[key] = child[key]
    except subprocess.TimeoutExpired:
        report.update(status="failed", errors=["isolation_timeout"])
    except ProbeError as exc:
        report.update(status="refused", errors=[str(exc)])
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        report.update(status="failed", errors=["probe_failed"])
    return report


def main(argv=None):
    """Expose opt-in execution and an optional redacted JSON report destination."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qq-root", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--jail", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--nonce", help=argparse.SUPPRESS)
    parser.add_argument("--parent-ns", help=argparse.SUPPRESS)
    parser.add_argument("--mount-tool", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.child:
        report = base_report()
        try:
            report = child_probe(validate_root(args.qq_root), args.jail, args.nonce,
                                 json.loads(args.parent_ns), args.mount_tool)
        except Exception:
            # Child errors are intentionally fixed and redacted; no host traceback.
            report.update(status="failed", errors=["isolation_setup_or_probe_failed"])
    else:
        report = probe(args.qq_root, execute=args.execute)
    text = json.dumps(report, sort_keys=True) + "\n"
    if args.report:
        args.report.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0 if report["status"] in ("observed", "not_executed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
