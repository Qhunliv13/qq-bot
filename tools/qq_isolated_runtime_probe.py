"""Opt-in isolated QQ versions probe and fixed system-Node native-load control.

Default mode never imports native modules. Explicit --native-load-control loads only
wrapper/major with trusted system Node and reports descriptors, not QQ readiness.
No mode calls exports, initializes QQ services, loads QQ main or performs login.

The caller must supply a pristine extracted software-only QQ directory. System
library directories are exposed read-only; no host account directories are bound.
Default mode validates the candidate but executes nothing. Real execution needs
Linux root, util-linux unshare/mount, and explicit --execute consent.
--userns-control substitutes trusted system unshare/true under identical isolation;
it is a namespace prerequisite control, never QQ/runtime/native verification.
Optional --private-diagnostics creates a new owner-only file outside this repository
with at most 16 KiB of exact stderr. Treat this raw diagnostic as sensitive: do not
share it. It never enters public JSON, even for this account-free runtime probe.
"""

import argparse
import ctypes
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile

# Only runtime version strings, never arbitrary process properties, enter reports.
VERSION_KEYS = ("node", "electron", "chrome", "v8", "modules", "napi", "openssl", "uv")
OUTPUT_LIMIT = 1024 * 1024
# Raw diagnostics use a separate inherited descriptor, never helper JSON/PIPE.
PRIVATE_STDERR_LIMIT = 16 * 1024
HELPER_OUTPUT_LIMIT = 128 * 1024
SYSTEM_DIRS = ("/usr", "/lib", "/lib64", "/bin")
NAMESPACES = ("net", "mnt", "pid")


class ProbeError(ValueError):
    """Carry fixed redacted failure codes rather than host paths or raw output."""


def base_report(*, userns_control=False, native_load_control=False):
    """Keep runtime evidence distinct from native compatibility or release consent."""
    report = {"mode": "isolated_runtime_versions", "status": "not_executed",
            "runtime_executed": False, "runtime_versions_verified": False,
            "versions": {}, "errors": [], "private_diagnostics_written": False,
            "production_cutover": False, "production_cutover_authorized": False,
            "native_binding_verified": False, "independent_backend_ready": False,
            "safe_to_login": False, "abi_compatibility": "unverified"}
    if userns_control:
        # Trusted namespace control supplies no QQ or version evidence.
        report.update(mode="isolated_userns_control", control_executed=False,
                      user_namespace_creation_verified=False)
    if native_load_control:
        report.update(mode="system_node_native_load_control", native_control_executed=False,
                      native_load_control_verified=False, system_node_versions={}, native_candidates=[])
    return report


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


def native_load_script(nonce):
    """Load only fixed software modules; inspect data descriptors without invoking exports."""
    marker = json.dumps("QQ_NATIVE_" + nonce + ":")
    return """'use strict';
const candidates = [];
for (const candidate of ['wrapper', 'major']) {
  try {
    const mod = require('/opt/QQ/resources/app/' + candidate + '.node');
    const keys = Object.getOwnPropertyNames(mod);
    const exports = keys.slice(0, 32).map(name => {
      const d = Object.getOwnPropertyDescriptor(mod, name);
      return {name, type: Object.prototype.hasOwnProperty.call(d, 'value') ? typeof d.value : 'accessor'};
    });
    candidates.push({candidate, loaded:true, exports, exportsTruncated:keys.length > 32});
  } catch (e) {
    const text = String(e.message);
    candidates.push({candidate, loaded:false, dlopenFailed:e.code === 'ERR_DLOPEN_FAILED',
      undefinedSymbol:text.includes('undefined symbol:'),
      missingQqMagic:text.includes('undefined symbol: qq_magic_napi_register'),
      missingSharedLibrary:text.includes('cannot open shared object file'),
      moduleVersionMismatch:text.includes('NODE_MODULE_VERSION'),
      notSelfRegister:text.includes('Module did not self-register')});
  }
}
const versions = {};
for (const key of ['node', 'modules', 'napi']) versions[key] = process.versions[key];
process.stdout.write(""" + marker + """ + JSON.stringify({versions, candidates}) + String.fromCharCode(10));
"""


def parse_native_output(data, nonce):
    """Accept one nonce record of bounded export descriptors and fixed failure booleans."""
    if len(data) > OUTPUT_LIMIT:
        raise ProbeError("output_limit")
    marker = ("QQ_NATIVE_" + nonce + ":").encode("ascii")
    records = [line[len(marker):] for line in data.splitlines() if line.startswith(marker)]
    if len(records) != 1:
        raise ProbeError("native_record_missing_or_ambiguous")
    try:
        value = json.loads(records[0])
        if not isinstance(value, dict) or set(value) != {"versions", "candidates"}:
            raise ValueError
        versions = value["versions"]
        if not isinstance(versions, dict) or set(versions) != {"node", "modules", "napi"}:
            raise ValueError
        if any(not isinstance(v, str) or not re.fullmatch(r"[0-9A-Za-z.+_-]{1,80}", v) for v in versions.values()):
            raise ValueError
        candidates = value["candidates"]
        if not isinstance(candidates, list) or len(candidates) != 2:
            raise ValueError
        failure_keys = {"dlopenFailed", "undefinedSymbol", "missingQqMagic", "missingSharedLibrary", "moduleVersionMismatch", "notSelfRegister"}
        for expected, item in zip(("wrapper", "major"), candidates):
            if not isinstance(item, dict) or item.get("candidate") != expected or type(item.get("loaded")) is not bool:
                raise ValueError
            if item["loaded"]:
                if set(item) != {"candidate", "loaded", "exports", "exportsTruncated"} or type(item["exportsTruncated"]) is not bool:
                    raise ValueError
                exports = item["exports"]
                if not isinstance(exports, list) or len(exports) > 32:
                    raise ValueError
                for export in exports:
                    if not isinstance(export, dict) or set(export) != {"name", "type"}:
                        raise ValueError
                    if not isinstance(export["name"], str) or not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]{0,79}", export["name"]):
                        raise ValueError
                    if export["type"] not in ("function", "object", "string", "number", "boolean", "undefined", "symbol", "bigint", "accessor"):
                        raise ValueError
            elif set(item) != {"candidate", "loaded"} | failure_keys or any(type(item[k]) is not bool for k in failure_keys):
                raise ValueError
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ProbeError("invalid_native_record") from exc
    return {"system_node_versions": versions, "native_candidates": candidates}


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
    # Exact observed Chromium fatal, not an inferred explanation for other traps.
    if "The SUID sandbox helper binary was found, but is not configured correctly." in text:
        return {"runtime_failure_category": "suid_sandbox_helper_configuration_failed"}
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


def validate_diagnostic_fd(fd):
    """Accept only an owner-only regular output file, never standard streams."""
    if fd < 3:
        raise ProbeError("invalid_private_diagnostic_fd")
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
        raise ProbeError("invalid_private_diagnostic_fd")


def open_private_diagnostics(destination):
    """Create once outside the repository; never follow or overwrite a final link."""
    path = Path(destination)
    repository = Path(__file__).resolve().parents[1]
    resolved = path.resolve()
    if not path.is_absolute() or resolved == repository or repository in resolved.parents:
        raise ProbeError("private_diagnostics_outside_repository_required")
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        # A restrictive umask must not turn a valid private file into mode 000.
        os.fchmod(fd, 0o600)
        validate_diagnostic_fd(fd)
    except Exception:
        os.close(fd)
        raise
    return fd


def copy_private_stderr(errors, fd):
    """Copy an exact bounded prefix via the pinned fd, without logging any bytes."""
    validate_diagnostic_fd(fd)
    errors.seek(0)
    data = errors.read(PRIVATE_STDERR_LIMIT)
    remaining = memoryview(data)
    while remaining:
        # os.write may complete partially; never silently claim a full copy.
        written = os.write(fd, remaining)
        if written <= 0:
            raise ProbeError("private_diagnostic_write_failed")
        remaining = remaining[written:]
    return bool(data)


def enter_filesystem_root(jail, filesystem_mode, pivot_tool=None, umount_tool=None):
    """Replace the root once; pivot mode must detach its freshly created old root.

    The util-linux pivot_root syscall updates processes sharing the mount namespace
    whose root refers to the old root. Verify this supervisor's root before exec;
    never continue with an attached old root or an unchanged filesystem root.
    """
    if filesystem_mode == "chroot":
        os.chroot(jail)
        os.chdir("/")
        return
    if filesystem_mode != "pivot-root":
        raise ProbeError("invalid_filesystem_mode")
    expected = jail.stat()
    os.chdir(jail)
    run_checked([pivot_tool, ".", ".old-root"])
    os.chdir("/")
    actual = os.stat("/")
    if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
        raise ProbeError("filesystem_root_replacement_failed")
    run_checked([umount_tool, "--lazy", "--", "/.old-root"])
    # Only the empty put_old directory created by this invocation is removed.
    # EBUSY/nonempty/missing errors fail closed before privilege drop or exec.
    os.rmdir("/.old-root")
    if Path("/.old-root").exists():
        raise ProbeError("old_root_detach_failed")


def child_probe(root, jail, nonce, parent_ids, mount, diagnostic_fd=None, *, userns_control=False,
                filesystem_mode="chroot", pivot_tool=None, umount_tool=None, native_load_control=False):
    """Build the shared isolation, then probe QQ or a trusted namespace control.

    This helper is PID 1 in its new namespace. On exit the kernel kills remaining
    descendants. No QQ process starts unless all isolation setup has succeeded.
    """
    if filesystem_mode not in ("chroot", "pivot-root"):
        raise ProbeError("invalid_filesystem_mode")
    if filesystem_mode == "pivot-root":
        # Internal CLI arguments cannot nominate arbitrary root-executed helpers.
        for name, tool in (("pivot_root", pivot_tool), ("umount", umount_tool)):
            if not tool or not Path(tool).is_absolute() or tool != shutil.which(name, path="/usr/sbin:/usr/bin:/sbin:/bin"):
                raise ProbeError("pivot_tools_missing_or_untrusted")
    report = base_report(userns_control=userns_control, native_load_control=native_load_control)
    if userns_control and native_load_control:
        report.update(status="refused", errors=["mutually_exclusive_controls"])
        return report
    report["filesystem_mode"] = filesystem_mode
    if diagnostic_fd is not None:
        validate_diagnostic_fd(diagnostic_fd)
    observed = namespace_ids()
    if set(parent_ids) != set(NAMESPACES) or any(observed[k] == parent_ids[k] for k in NAMESPACES):
        raise ProbeError("namespace_isolation_failed")
    import resource

    run_checked([mount, "--make-rprivate", "/"])
    # The parent creates an empty, private temporary directory, never a host root.
    if not jail.is_dir() or any(jail.iterdir()) or jail.is_symlink():
        raise ProbeError("jail_not_empty")
    jail.chmod(0o755)
    if filesystem_mode == "pivot-root":
        # Establish the new root mount before any nested mount; put_old is new.
        run_checked([mount, "--bind", str(jail), str(jail)])
        (jail / ".old-root").mkdir()
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
    (jail / "probe.js").write_text(native_load_script(nonce) if native_load_control else node_script(nonce), encoding="utf-8")
    # File capture + RLIMIT_FSIZE bounds output before parsing, unlike PIPE capture.
    with (jail / "tmp" / "stdout").open("w+b") as output, (jail / "tmp" / "stderr").open("w+b") as errors:
        enter_filesystem_root(jail, filesystem_mode, pivot_tool, umount_tool)
        if namespace_ids() != observed:
            raise ProbeError("private_proc_identity_failed")
        # Keep this PID 1 supervisor root: changing its credentials would clear
        # unshare --kill-child's PDEATHSIG. Only QQ's child drops privileges.
        report["isolation"] = {"network": True, "mount": True, "pid": True,
                               "chroot": filesystem_mode == "chroot",
                               "pivotRoot": filesystem_mode == "pivot-root",
                               "oldroot_detached": filesystem_mode == "pivot-root"}
        resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_FSIZE, (OUTPUT_LIMIT, OUTPUT_LIMIT))
        resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
        # Same chroot, UID, NNP and cap-zero hook; only the trusted command differs.
        command = (["/usr/bin/unshare", "--user", "--map-root-user", "/usr/bin/true"]
                   if userns_control else (["/usr/bin/node", "/probe.js"] if native_load_control else ["/opt/QQ/qq", "/probe.js"]))
        executed_key = "control_executed" if userns_control else ("native_control_executed" if native_load_control else "runtime_executed")
        failure_prefix = "userns_control" if userns_control else ("native_control" if native_load_control else "runtime")
        report["control_launch_attempted" if userns_control else ("native_control_launch_attempted" if native_load_control else "runtime_launch_attempted")] = True
        try:
            result = subprocess.run(command, env=minimal_environment(),
                                    stdin=subprocess.DEVNULL, stdout=output,
                                    stderr=errors, timeout=10, check=False,
                                    preexec_fn=drop_runtime_privileges, close_fds=True)
        except subprocess.TimeoutExpired:
            report.update(status="failed", errors=[failure_prefix + "_timeout"])
            report[executed_key] = True
            return report
        except (OSError, subprocess.SubprocessError):
            report.update(status="failed", errors=[failure_prefix + "_launch_failed"])
            return report
        finally:
            if diagnostic_fd is not None:
                try:
                    report["private_diagnostics_written"] = copy_private_stderr(errors, diagnostic_fd)
                except (OSError, ProbeError):
                    # Preserve the original runtime failure; disclose no raw data.
                    report["errors"].append("private_diagnostic_write_failed")
        report[executed_key] = True
        report["isolation"].update(unprivileged=True, no_new_privileges=True,
                                   effective_capabilities_zero=True)
        if userns_control:
            # Success requires the real unshare + mapping + true chain, not a flag.
            errors.seek(0)
            denied = b"unshare failed: Operation not permitted" in errors.read(OUTPUT_LIMIT + 1)
            report.update(status="observed" if result.returncode == 0 else "failed",
                          control_status="control_succeeded" if result.returncode == 0 else "control_failed",
                          control_exit_code=result.returncode,
                          user_namespace_creation_verified=result.returncode == 0,
                          errors=[] if result.returncode == 0 else ["userns_control_failed"])
            if result.returncode != 0 and denied:
                report["control_failure_category"] = "user_namespace_creation_denied"
            return report
        if native_load_control:
            report["native_control_exit_code"] = result.returncode
            if result.returncode != 0:
                report.update(status="failed", errors=["native_control_nonzero_exit"])
                return report
            output.seek(0)
            try:
                report.update(parse_native_output(output.read(OUTPUT_LIMIT + 1), nonce))
            except ProbeError as exc:
                report.update(status="failed", errors=[str(exc)])
                return report
            report.update(status="observed", native_load_control_verified=True)
            return report
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


def probe(qq_root, execute=False, private_diagnostics=None, *, userns_control=False,
          filesystem_mode="chroot", native_load_control=False):
    """Validate by default; explicit pivot mode never changes execution consent."""
    report = base_report(userns_control=userns_control, native_load_control=native_load_control)
    if userns_control and native_load_control:
        report.update(status="refused", errors=["mutually_exclusive_controls"])
        return report
    report["filesystem_mode"] = filesystem_mode
    if filesystem_mode not in ("chroot", "pivot-root"):
        report.update(status="refused", errors=["invalid_filesystem_mode"])
        return report
    diagnostic_fd = None
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
        pivot_tool, umount_tool = None, None
        if filesystem_mode == "pivot-root":
            # Resolve on the host before entry, never from the QQ software tree.
            pivot_tool = shutil.which("pivot_root", path="/usr/sbin:/usr/bin:/sbin:/bin")
            umount_tool = shutil.which("umount", path="/usr/sbin:/usr/bin:/sbin:/bin")
            if any(not tool or not Path(tool).is_absolute()
                   for tool in (pivot_tool, umount_tool)):
                raise ProbeError("pivot_tools_missing_or_untrusted")
        if private_diagnostics is not None:
            diagnostic_fd = open_private_diagnostics(private_diagnostics)
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
            if filesystem_mode == "pivot-root":
                args.extend(["--filesystem-mode", filesystem_mode,
                             "--pivot-tool", pivot_tool, "--umount-tool", umount_tool])
            if userns_control:
                args.append("--userns-control")
            if native_load_control:
                args.append("--native-load-control")
            private_kwargs = {}
            if diagnostic_fd is not None:
                args.extend(["--private-diagnostic-fd", str(diagnostic_fd)])
                private_kwargs["pass_fds"] = (diagnostic_fd,)
            result = subprocess.run(args, env=minimal_environment(), timeout=15,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, check=False, start_new_session=True,
                                    close_fds=True, **private_kwargs)
            if result.returncode not in (0, 1):
                raise ProbeError("isolation_helper_failed")
            if len(result.stdout) > HELPER_OUTPUT_LIMIT:
                raise ProbeError("helper_output_limit")
            child = json.loads(result.stdout)
            # Only allow fixed helper fields; never surface unparsed process output.
            for key in ("status", "runtime_executed", "runtime_launch_attempted", "runtime_versions_verified", "versions", "errors", "platform", "arch", "isolation", "runtime_exit_code", "runtime_failure_category", "missing_library", "control_executed", "control_launch_attempted", "control_status", "control_exit_code", "control_failure_category", "user_namespace_creation_verified"):
                if key in child:
                    report[key] = child[key]
            if native_load_control:
                for key in ("native_control_executed", "native_control_launch_attempted", "native_control_exit_code", "native_load_control_verified", "system_node_versions", "native_candidates"):
                    if key in child:
                        report[key] = child[key]
                # System Node evidence never becomes QQ Electron/runtime/ABI readiness.
                report.update(runtime_executed=False, runtime_versions_verified=False, versions={})
            # A helper cannot invent consent or publish the descriptor/path/bytes.
            report["private_diagnostics_written"] = (
                diagnostic_fd is not None and child.get("private_diagnostics_written") is True)
    except subprocess.TimeoutExpired:
        report.update(status="failed", errors=["isolation_timeout"])
    except ProbeError as exc:
        report.update(status="refused", errors=[str(exc)])
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        report.update(status="failed", errors=["probe_failed"])
    finally:
        if diagnostic_fd is not None:
            os.close(diagnostic_fd)
    return report


def main(argv=None):
    """Expose opt-in execution and an optional redacted JSON report destination."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qq-root", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--filesystem-mode", choices=("chroot", "pivot-root"), default="chroot",
                        help="Opt-in detached pivot root; default keeps existing chroot isolation")
    parser.add_argument("--pivot-tool", help=argparse.SUPPRESS)
    parser.add_argument("--umount-tool", help=argparse.SUPPRESS)
    controls = parser.add_mutually_exclusive_group()
    controls.add_argument("--native-load-control", action="store_true",
                          help="Load fixed pristine native modules with system Node; never calls exports or verifies QQ runtime")
    controls.add_argument("--userns-control", action="store_true",
                        help="Run trusted user-namespace control instead of QQ; still needs --execute")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--private-diagnostics", type=Path,
                        help="New outside-repository owner-only raw stderr file (16 KiB max); sensitive, do not share")
    parser.add_argument("--private-diagnostic-fd", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--jail", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--nonce", help=argparse.SUPPRESS)
    parser.add_argument("--parent-ns", help=argparse.SUPPRESS)
    parser.add_argument("--mount-tool", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.child:
        report = base_report(userns_control=args.userns_control, native_load_control=args.native_load_control)
        try:
            report = child_probe(validate_root(args.qq_root), args.jail, args.nonce,
                                 json.loads(args.parent_ns), args.mount_tool,
                                 diagnostic_fd=args.private_diagnostic_fd,
                                 userns_control=args.userns_control,
                                 filesystem_mode=args.filesystem_mode,
                                 pivot_tool=args.pivot_tool, umount_tool=args.umount_tool,
                                  native_load_control=args.native_load_control)
        except Exception:
            # Child errors are intentionally fixed and redacted; no host traceback.
            report.update(status="failed", errors=["isolation_setup_or_probe_failed"])
    else:
        kwargs = {"execute": args.execute}
        if args.filesystem_mode != "chroot":
            kwargs["filesystem_mode"] = args.filesystem_mode
        if args.userns_control:
            kwargs["userns_control"] = True
        if args.native_load_control:
            kwargs["native_load_control"] = True
        if args.private_diagnostics is not None:
            kwargs["private_diagnostics"] = args.private_diagnostics
        report = probe(args.qq_root, **kwargs)
    text = json.dumps(report, sort_keys=True) + "\n"
    if args.report:
        args.report.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0 if report["status"] in ("observed", "not_executed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
