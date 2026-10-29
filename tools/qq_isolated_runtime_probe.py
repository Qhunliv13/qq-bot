"""Opt-in isolated QQ versions probe and fixed system-Node native-load control.

Default mode never imports native modules. Explicit --native-load-control loads only
wrapper/major with trusted system Node and reports descriptors, not QQ readiness.
No mode calls native exports or performs login. QQ may ignore RUN_AS_NODE and
enter its pristine application bootstrap; account-free filesystem and network
isolation remain mandatory. --private-display permits this only after pivot-root,
using trusted authenticated Xvfb, no host sockets and a new private /dev/shm.

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
from contextlib import contextmanager, ExitStack
import ctypes
import hashlib
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
import time

# Only runtime version strings, never arbitrary process properties, enter reports.
VERSION_KEYS = ("node", "electron", "chrome", "v8", "modules", "napi", "openssl", "uv")
OUTPUT_LIMIT = 1024 * 1024
# Raw diagnostics use a separate inherited descriptor, never helper JSON/PIPE.
PRIVATE_STDERR_LIMIT = 16 * 1024
HELPER_OUTPUT_LIMIT = 128 * 1024
# Static entry reads are bounded to the official tiny bootstrap, not the whole ASAR.
BOOTSTRAP_ENTRY_LIMIT = 4096
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


def inspect_bootstrap(qq_root):
    """Read the fixed pristine QQ ASAR entry offline; never evaluate its bytes.

    This is a format gate, not a runtime test: decodable text alone would not
    prove valid JS, supported Node APIs, native initialization, or QQ readiness.
    """
    report = base_report()
    report.update(mode="offline_qq_bootstrap_readiness", status="refused",
                  entry_format="unverified", entry_sha256=None, entry_size=None,
                  system_node_direct_entry_supported=False,
                  electron_bootstrap_verified=False)
    try:
        # Runtime helper.py is copied alone into a namespace. Import metadata
        # dependencies only for this offline mode, never during child bootstrap.
        try:
            from tools.qq_asar_inventory import inspect_archive
            from tools.qq_native_preflight import checked_file, read_bounded
        except ModuleNotFoundError as exc:
            if exc.name != "tools":
                raise
            from qq_asar_inventory import inspect_archive
            from qq_native_preflight import checked_file, read_bounded
        root = validate_root(qq_root)
        package = json.loads(read_bounded(checked_file(root, "resources/app/package.json"),
                                          64 * 1024).decode("utf-8"))
        # Pin the only Tencent package/version and declared entry under investigation.
        # No user-provided entry path or arbitrary JavaScript is ever loaded.
        if (not isinstance(package, dict) or package.get("name") != "QQ"
                or package.get("version") != "3.2.34-53644"
                or package.get("main") != "./application.asar/app_launcher/index.js"):
            raise ProbeError("unexpected_pristine_package_entry")
        archive = checked_file(root, "resources/app/application.asar")
        metadata = inspect_archive(archive)
        entries = [item for item in metadata["entries"]
                   if item["name"] == "app_launcher/index.js"]
        if (len(entries) != 1 or entries[0]["kind"] != "file"
                or entries[0]["unpacked"] or not 0 < entries[0]["size"] <= BOOTSTRAP_ENTRY_LIMIT):
            raise ProbeError("unsupported_pristine_entry_metadata")
        entry = entries[0]
        with archive.open("rb") as stream:
            stream.seek(metadata["payload_offset"] + entry["offset"])
            content = stream.read(entry["size"])
        if len(content) != entry["size"]:
            raise ProbeError("truncated_pristine_entry")
        report["entry_size"] = len(content)
        report["entry_sha256"] = hashlib.sha256(content).hexdigest()
        try:
            content.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            report["entry_format"] = "not_utf8_javascript_source"
        else:
            # UTF-8 is only a necessary condition for plain source, not JS syntax.
            report["entry_format"] = "utf8_text_syntax_unverified"
        report["status"] = "observed_static"
    except (OSError, ValueError, UnicodeError, RuntimeError, RecursionError, TypeError):
        # Archive/package paths or raw bytes are never surfaced on failure.
        report["errors"] = ["bootstrap_static_inspection_failed"]
    return report


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


def validate_private_display_mode(private_display, filesystem_mode, userns_control, native_load_control,
                                  private_runtime_services=False):
    """Permit private services only with the isolated QQ display and detached root."""
    if private_display and (filesystem_mode != "pivot-root" or userns_control or native_load_control):
        raise ProbeError("private_display_requires_pivot_runtime")
    if private_runtime_services and not private_display:
        raise ProbeError("private_runtime_services_require_private_display")


def private_bus_configuration():
    """Provide a minimal session bus with no service directories or activation."""
    return ("<!DOCTYPE busconfig PUBLIC '-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN' "
            "'http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd'>\n"
            "<busconfig><type>session</type><listen>unix:path=/tmp/runtime/bus</listen>"
            "<auth>EXTERNAL</auth><policy context='default'>"
            "<allow send_destination='*'/><allow eavesdrop='true'/>"
            "<allow own='*'/></policy></busconfig>\n")


@contextmanager
def private_runtime_services_session(report, env):
    """Start only a jailed, cap-zero private D-Bus daemon, never host session bus."""
    runtime = Path("/tmp/runtime")
    runtime.mkdir(mode=0o700)
    os.chown(runtime, 65534, 65534)
    configuration = runtime / "session.conf"
    configuration.write_text(private_bus_configuration(), encoding="ascii")
    configuration.chmod(0o600)
    os.chown(configuration, 65534, 65534)
    env = dict(env, DBUS_SESSION_BUS_ADDRESS="unix:path=/tmp/runtime/bus",
               XDG_RUNTIME_DIR="/tmp/runtime", FONTCONFIG_FILE="/etc/fonts/fonts.conf",
               FONTCONFIG_PATH="/etc/fonts")
    report["private_runtime_services"] = {"launch_attempted": True, "bus_ready": False,
                                          "fontconfig_readonly": True, "activation_disabled": True}
    with Path("/tmp/dbus-stderr").open("x+b") as errors:
        process = subprocess.Popen(
            ["/usr/bin/dbus-daemon", "--nofork", "--nopidfile",
             "--config-file=/tmp/runtime/session.conf"], env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=errors,
            preexec_fn=drop_runtime_privileges, close_fds=True)
        try:
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise ProbeError("private_bus_start_failed")
                try:
                    info = (runtime / "bus").lstat()
                except FileNotFoundError:
                    time.sleep(0.025)
                    continue
                if not stat.S_ISSOCK(info.st_mode) or info.st_uid != 65534:
                    raise ProbeError("private_bus_socket_invalid")
                report["private_runtime_services"].update(bus_ready=True, socket_owner_verified=True,
                                                           unprivileged=True, effective_capabilities_zero=True)
                yield env
                break
            else:
                raise ProbeError("private_bus_start_failed")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            errors.seek(0)
            report["private_runtime_services"]["daemon_stderr_present"] = bool(errors.read(PRIVATE_STDERR_LIMIT))


def private_window_count(env):
    """Count X root children only; window titles and X output never enter reports."""
    try:
        with Path("/tmp/xwindows").open("w+b") as output:
            result = subprocess.run(["/usr/bin/xwininfo", "-root", "-children", "-display", ":99"],
                                    env=env, timeout=1, check=False, stdin=subprocess.DEVNULL,
                                    stdout=output, stderr=subprocess.DEVNULL, close_fds=True,
                                    preexec_fn=drop_runtime_privileges)
            if result.returncode == 0:
                output.seek(0)
                data = output.read(32769)
                if len(data) <= 32768:
                    match = re.search(rb"\b([0-9]{1,4}) children:", data)
                    if match:
                        return int(match.group(1))
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def run_observed_qq(command, env, output, errors, timeout, report):
    """Watch a QQ child and count only anonymous X root children under a watchdog."""
    baseline = private_window_count(env)
    report["private_window_observation"] = {"baseline_children": baseline,
                                            "maximum_children": baseline, "sampled": baseline is not None}
    process = subprocess.Popen(command, env=env, stdin=subprocess.DEVNULL, stdout=output,
                               stderr=errors, preexec_fn=drop_runtime_privileges, close_fds=True)
    deadline = time.monotonic() + timeout
    try:
        while True:
            returncode = process.poll()
            if returncode is not None:
                return subprocess.CompletedProcess(command, returncode)
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(command, timeout)
            count = private_window_count(env)
            if count is not None:
                observed = report["private_window_observation"]
                observed["sampled"] = True
                observed["maximum_children"] = max(observed["maximum_children"] or 0, count)
            time.sleep(0.2)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=1)


def private_display_authority(cookie):
    """Encode one FamilyWild MIT cookie without exposing host X authority or names."""
    fields = (b"", b"99", b"MIT-MAGIC-COOKIE-1", cookie)
    return b"\xff\xff" + b"".join(len(field).to_bytes(2, "big") + field for field in fields)


@contextmanager
def private_display_session(report):
    """Start trusted Xvfb after pivot, with cap-zero UID and private cookie-only access.

    The only socket path is in this jail's blank tmpfs. No host DISPLAY, X socket,
    IPC namespace, xauth file or DBus socket is imported. stderr stays separate and
    RLIMIT_FSIZE bounds it; only fixed categories enter the public report.
    """
    directory = Path("/tmp/.X11-unix")
    directory.mkdir(mode=0o700)
    os.chown(directory, 65534, 65534)
    authority = Path("/tmp/home/.Xauthority")
    with authority.open("xb") as stream:
        stream.write(private_display_authority(secrets.token_bytes(16)))
    authority.chmod(0o600)
    os.chown(authority, 65534, 65534)
    env = minimal_environment()
    env.update(DISPLAY=":99", XAUTHORITY=str(authority))
    # Start only the fixed system binary; never search a QQ-controlled PATH.
    with Path("/tmp/xvfb-stderr").open("x+b") as errors:
        process = subprocess.Popen(
            ["/usr/bin/Xvfb", ":99", "-screen", "0", "1024x768x24",
             "-nolisten", "tcp", "-auth", str(authority)],
            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=errors, preexec_fn=drop_runtime_privileges, close_fds=True)
        report["private_display"] = {"launch_attempted": True, "ready": False,
                                     "authenticated": True, "tcp_disabled": True}
        try:
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    report["private_display"]["failure_category"] = "server_exited_before_ready"
                    raise ProbeError("private_display_start_failed")
                try:
                    info = Path("/tmp/.X11-unix/X99").lstat()
                except FileNotFoundError:
                    time.sleep(0.025)
                    continue
                # A ready signal must be a real Unix socket owned by our dropped UID.
                if not stat.S_ISSOCK(info.st_mode) or info.st_uid != 65534:
                    raise ProbeError("private_display_socket_invalid")
                report["private_display"].update(ready=True, socket_owner_verified=True,
                                                unprivileged=True, effective_capabilities_zero=True)
                yield env
                break
            else:
                report["private_display"]["failure_category"] = "server_socket_timeout"
                raise ProbeError("private_display_start_failed")
        finally:
            # PID1 death remains a second backstop for every Xvfb/QQ descendant.
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            errors.seek(0)
            data = errors.read(PRIVATE_STDERR_LIMIT)
            report["private_display"]["stderr_present"] = bool(data)
            report["private_display"]["stderr_prefix_bounded"] = True


def validate_synthetic_identity(synthetic_identity, control_mode, filesystem_mode,
                                private_display, private_runtime_services, extended_startup,
                                application_observer, userns_control, native_load_control):
    """Reject identity outside the two fixed fully isolated controls before any writes."""
    if type(synthetic_identity) is not bool or (synthetic_identity and (
            control_mode not in ("bus_identity_offline", "host_dispatch_identity_offline") or filesystem_mode != "pivot-root"
            or not private_display or not private_runtime_services or not extended_startup
            or not callable(application_observer) or userns_control or native_load_control)):
        raise ProbeError("synthetic_identity_requires_fixed_private_bus_control")


def create_synthetic_identity(jail):
    """Create only fixed records in a fresh canonical root-owned private staging jail."""
    jail = Path(jail)
    # Reject ancestor symlinks, populated roots and writable staging parents before mkdir.
    if (not jail.is_absolute() or jail.resolve(strict=True) != jail or jail.is_symlink()
            or not jail.is_dir() or any(jail.iterdir()) or os.geteuid() != 0
            or jail.stat().st_uid != 0 or jail.parent.stat().st_uid != 0
            or stat.S_IMODE(jail.parent.stat().st_mode) != 0o700):
        raise ProbeError("synthetic_identity_requires_fresh_private_root")
    records = (("passwd", b"qqprobe:x:65534:65534:isolated runtime:/tmp/home:/usr/sbin/nologin\n"),
               ("group", b"qqprobe:x:65534:\n"))
    etc = jail / "etc"
    etc.mkdir(mode=0o755)
    evidence = {}
    for name, content in records:
        # x forbids overwrite/symlink following; the verified root-only parent prevents races.
        with (etc / name).open("xb") as stream:
            stream.write(content)
            os.fchown(stream.fileno(), 0, 0)
            os.fchmod(stream.fileno(), 0o444)
            metadata = os.fstat(stream.fileno())
        evidence["/etc/" + name] = {"sha256": hashlib.sha256(content).hexdigest(),
                                   "root_owned": metadata.st_uid == metadata.st_gid == 0,
                                   "mode_0444": stat.S_IMODE(metadata.st_mode) == 0o444}
    return {"enabled": True, "host_identity_read": False, "records": evidence}


def child_probe(root, jail, nonce, parent_ids, mount, diagnostic_fd=None, *, userns_control=False,
                filesystem_mode="chroot", pivot_tool=None, umount_tool=None, native_load_control=False,
                private_display=False, extended_startup=False, private_runtime_services=False,
                application_observer=None, synthetic_identity=False, control_mode=None):
    """Build the shared isolation, then probe QQ or a trusted namespace control.

    application_observer is a trusted internal callable, never a CLI script or
    arbitrary command. It observes the pristine default app only under the full
    pivot/private-services/30-second boundary; normal probe behavior is unchanged.

    This helper is PID 1 in its new namespace. On exit the kernel kills remaining
    descendants. No QQ process starts unless all isolation setup has succeeded.
    """
    validate_synthetic_identity(synthetic_identity, control_mode, filesystem_mode,
                                private_display, private_runtime_services, extended_startup,
                                application_observer, userns_control, native_load_control)
    if filesystem_mode not in ("chroot", "pivot-root"):
        raise ProbeError("invalid_filesystem_mode")
    if filesystem_mode == "pivot-root":
        # Internal CLI arguments cannot nominate arbitrary root-executed helpers.
        for name, tool in (("pivot_root", pivot_tool), ("umount", umount_tool)):
            if not tool or not Path(tool).is_absolute() or tool != shutil.which(name, path="/usr/sbin:/usr/bin:/sbin:/bin"):
                raise ProbeError("pivot_tools_missing_or_untrusted")
    validate_private_display_mode(private_display, filesystem_mode, userns_control, native_load_control,
                                  private_runtime_services)
    if extended_startup and not private_display:
        raise ProbeError("extended_startup_requires_private_display")
    if application_observer is not None:
        # An app callback cannot weaken isolation or become an arbitrary command API.
        if (not callable(application_observer) or filesystem_mode != "pivot-root"
                or not private_display or not private_runtime_services or not extended_startup
                or userns_control or native_load_control):
            raise ProbeError("application_observer_requires_full_private_runtime")
    report = base_report(userns_control=userns_control, native_load_control=native_load_control)
    if userns_control and native_load_control:
        report.update(status="refused", errors=["mutually_exclusive_controls"])
        return report
    report["filesystem_mode"] = filesystem_mode
    if diagnostic_fd is not None:
        validate_diagnostic_fd(diagnostic_fd)
    observed = namespace_ids()
    expected_namespaces = set(NAMESPACES)
    if private_display:
        # X11 may allocate SysV IPC: do not share the host's kernel IPC namespace.
        observed["ipc"] = os.readlink("/proc/self/ns/ipc")
        expected_namespaces.add("ipc")
    if set(parent_ids) != expected_namespaces or any(observed[k] == parent_ids[k] for k in expected_namespaces):
        raise ProbeError("namespace_isolation_failed")
    import resource

    run_checked([mount, "--make-rprivate", "/"])
    # The parent creates an empty, private temporary directory, never a host root.
    if not jail.is_dir() or any(jail.iterdir()) or jail.is_symlink():
        raise ProbeError("jail_not_empty")
    if synthetic_identity:
        report["synthetic_identity"] = create_synthetic_identity(jail)
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
    if private_runtime_services:
        # Fontconfig's host configuration is software-only; do not bind /etc itself.
        fonts = Path("/etc/fonts")
        if not fonts.is_dir() or fonts.is_symlink() or not (fonts / "fonts.conf").is_file():
            raise ProbeError("private_fontconfig_unavailable")
        (jail / "etc" / "fonts").mkdir(parents=True)
        bind_readonly(fonts, jail / "etc/fonts", mount)
        if not Path("/usr/bin/dbus-daemon").is_file() or not Path("/usr/bin/xwininfo").is_file():
            raise ProbeError("private_service_tools_missing")
    for directory in ("tmp", "run"):
        run_checked([mount, "-t", "tmpfs", "-o", "nosuid,nodev,noexec,size=16m,mode=1777",
                     "tmpfs", str(jail / directory)])
    if private_display:
        # Chromium needs shared memory; use new tmpfs, never bind host IPC state.
        (jail / "dev" / "shm").mkdir()
        run_checked([mount, "-t", "tmpfs", "-o", "nosuid,nodev,noexec,size=16m,mode=1777",
                     "tmpfs", str(jail / "dev" / "shm")])
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
    with (jail / "tmp" / "stdout").open("w+b") as output, (jail / "tmp" / "stderr").open("w+b") as errors, ExitStack() as runtime_stack:
        enter_filesystem_root(jail, filesystem_mode, pivot_tool, umount_tool)
        entered_ids = namespace_ids()
        if private_display:
            entered_ids["ipc"] = os.readlink("/proc/self/ns/ipc")
        if entered_ids != observed:
            raise ProbeError("private_proc_identity_failed")
        # Keep this PID 1 supervisor root: changing its credentials would clear
        # unshare --kill-child's PDEATHSIG. Only QQ's child drops privileges.
        report["isolation"] = {"network": True, "mount": True, "pid": True,
                               "chroot": filesystem_mode == "chroot",
                               "pivotRoot": filesystem_mode == "pivot-root",
                               "oldroot_detached": filesystem_mode == "pivot-root"}
        if private_display:
            report["isolation"]["ipc"] = True
        # The longer opt-in observation remains CPU-bounded as well as wall-bounded.
        resource.setrlimit(resource.RLIMIT_CPU, (30 if extended_startup else 10,
                                               30 if extended_startup else 10))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_FSIZE, (OUTPUT_LIMIT, OUTPUT_LIMIT))
        resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
        runtime_env = minimal_environment()
        if private_display:
            try:
                runtime_env = runtime_stack.enter_context(private_display_session(report))
            except (ProbeError, OSError, subprocess.SubprocessError):
                report.update(status="failed", errors=["private_display_start_failed"])
                return report
        if private_runtime_services:
            try:
                runtime_env = runtime_stack.enter_context(private_runtime_services_session(report, runtime_env))
            except (ProbeError, OSError, subprocess.SubprocessError):
                report.update(status="failed", errors=["private_runtime_services_start_failed"])
                return report
        if application_observer is not None:
            # A packaged Electron launcher may ignore RUN_AS_NODE and /probe.js;
            # default-app observation therefore never supplies either of them.
            runtime_env.pop("ELECTRON_RUN_AS_NODE", None)
            report.update(mode="isolated_default_application_observation")
            application_observer(["/opt/QQ/qq"], runtime_env, output, errors, 30, report)
            return report
        # Same chroot, UID, NNP and cap-zero hook; only the trusted command differs.
        command = (["/usr/bin/unshare", "--user", "--map-root-user", "/usr/bin/true"]
                   if userns_control else (["/usr/bin/node", "/probe.js"] if native_load_control else ["/opt/QQ/qq", "/probe.js"]))
        executed_key = "control_executed" if userns_control else ("native_control_executed" if native_load_control else "runtime_executed")
        failure_prefix = "userns_control" if userns_control else ("native_control" if native_load_control else "runtime")
        report["control_launch_attempted" if userns_control else ("native_control_launch_attempted" if native_load_control else "runtime_launch_attempted")] = True
        try:
            result = (run_observed_qq(command, runtime_env, output, errors,
                                      30 if extended_startup else 10, report)
                      if private_runtime_services else subprocess.run(
                          command, env=runtime_env, stdin=subprocess.DEVNULL, stdout=output,
                          stderr=errors, timeout=30 if extended_startup else 10, check=False,
                          preexec_fn=drop_runtime_privileges, close_fds=True))
        except subprocess.TimeoutExpired:
            report.update(status="failed", errors=[failure_prefix + "_timeout"])
            report[executed_key] = True
            if extended_startup:
                # Timeouts prove only a bounded observation, never a bootstrap phase.
                report["startup_observation"] = "qq_invoked_watchdog_at_30s"
            if private_display:
                errors.seek(0)
                text = errors.read(OUTPUT_LIMIT).decode("ascii", errors="ignore")
                # Fixed exact categories only: timeout is not version or app readiness.
                report["runtime_diagnostics"] = {
                    "dbus_unavailable": "Failed to connect to the bus:" in text,
                    "fontconfig_unavailable": "Fontconfig error:" in text,
                    "shared_memory_unavailable": "Unable to access(W_OK|X_OK) /dev/shm" in text,
                    "crash_reporter_activity": "third_party/crashpad/" in text}
                output.seek(0)
                try:
                    parse_output(output.read(OUTPUT_LIMIT + 1), nonce)
                    report["runtime_record_present"] = True
                except ProbeError:
                    report["runtime_record_present"] = False
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
          filesystem_mode="chroot", native_load_control=False, private_display=False,
          extended_startup=False, private_runtime_services=False):
    """Validate by default; explicit pivot/display modes never change execution consent."""
    report = base_report(userns_control=userns_control, native_load_control=native_load_control)
    if userns_control and native_load_control:
        report.update(status="refused", errors=["mutually_exclusive_controls"])
        return report
    report["filesystem_mode"] = filesystem_mode
    if filesystem_mode not in ("chroot", "pivot-root"):
        report.update(status="refused", errors=["invalid_filesystem_mode"])
        return report
    try:
        validate_private_display_mode(private_display, filesystem_mode, userns_control, native_load_control,
                                      private_runtime_services)
        if extended_startup and not private_display:
            raise ProbeError("extended_startup_requires_private_display")
    except ProbeError as exc:
        report.update(status="refused", errors=[str(exc)])
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
        if private_display:
            parent_ids["ipc"] = os.readlink("/proc/self/ns/ipc")
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
            if private_display:
                args.insert(1, "--ipc")
                args.append("--private-display")
            if extended_startup:
                args.append("--extended-startup")
            if private_runtime_services:
                args.append("--private-runtime-services")
            private_kwargs = {}
            if diagnostic_fd is not None:
                args.extend(["--private-diagnostic-fd", str(diagnostic_fd)])
                private_kwargs["pass_fds"] = (diagnostic_fd,)
            result = subprocess.run(args, env=minimal_environment(),
                                    timeout=35 if extended_startup else 15,
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
            if extended_startup and child.get("startup_observation") == "qq_invoked_watchdog_at_30s":
                report["startup_observation"] = "qq_invoked_watchdog_at_30s"
            if private_display:
                if type(child.get("runtime_record_present")) is bool:
                    report["runtime_record_present"] = child["runtime_record_present"]
                diagnostics = child.get("runtime_diagnostics", {})
                if isinstance(diagnostics, dict):
                    report["runtime_diagnostics"] = {
                        key: diagnostics[key] for key in ("dbus_unavailable", "fontconfig_unavailable",
                                                         "shared_memory_unavailable", "crash_reporter_activity")
                        if type(diagnostics.get(key)) is bool}
            if private_display and isinstance(child.get("private_display"), dict):
                allowed = ("launch_attempted", "ready", "authenticated", "tcp_disabled",
                           "socket_owner_verified", "unprivileged", "effective_capabilities_zero",
                           "stderr_present", "stderr_prefix_bounded")
                report["private_display"] = {key: child["private_display"][key] for key in allowed
                                             if type(child["private_display"].get(key)) is bool}
                category = child["private_display"].get("failure_category")
                if category in ("server_exited_before_ready", "server_socket_timeout"):
                    report["private_display"]["failure_category"] = category
            if private_runtime_services:
                if isinstance(child.get("private_runtime_services"), dict):
                    allowed_services = ("launch_attempted", "bus_ready", "socket_owner_verified",
                                        "unprivileged", "effective_capabilities_zero",
                                        "fontconfig_readonly", "activation_disabled",
                                        "daemon_stderr_present")
                    report["private_runtime_services"] = {
                        key: child["private_runtime_services"][key] for key in allowed_services
                        if type(child["private_runtime_services"].get(key)) is bool}
                if isinstance(child.get("private_window_observation"), dict):
                    window = child["private_window_observation"]
                    if (type(window.get("sampled")) is bool and
                            all(value is None or type(value) is int and 0 <= value <= 9999
                                for value in (window.get("baseline_children"), window.get("maximum_children")))):
                        report["private_window_observation"] = {
                            key: window[key] for key in ("sampled", "baseline_children", "maximum_children")}
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
    controls.add_argument("--bootstrap-readiness", action="store_true",
                          help="Offline fixed ASAR entry format check; refuses --execute and never runs QQ")
    controls.add_argument("--native-load-control", action="store_true",
                          help="Load fixed pristine native modules with system Node; never calls exports or verifies QQ runtime")
    controls.add_argument("--userns-control", action="store_true",
                        help="Run trusted user-namespace control instead of QQ; still needs --execute")
    parser.add_argument("--private-display", action="store_true",
                        help="Private authenticated Xvfb inside pivot-root runtime only; no host display")
    parser.add_argument("--extended-startup", action="store_true",
                        help="Opt-in 30-second QQ observation, only with private authenticated display")
    parser.add_argument("--private-runtime-services", action="store_true",
                        help="Opt-in jailed no-activation session bus and read-only fonts; requires private display/pivot root")
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
    # Static inspection is a separate offline mode: reject all runtime options even
    # when the caller explicitly grants --execute (consent cannot alter this gate).
    if args.bootstrap_readiness:
        if (args.child or args.execute or args.private_display or args.extended_startup or args.private_runtime_services or args.private_diagnostics
                or args.filesystem_mode != "chroot" or args.report is not None):
            parser.error("bootstrap-readiness permits only --qq-root")
        report = inspect_bootstrap(args.qq_root)
        sys.stdout.write(json.dumps(report, sort_keys=True) + "\n")
        return 1  # Offline format evidence is never a readiness gate pass.
    if args.child:
        report = base_report(userns_control=args.userns_control, native_load_control=args.native_load_control)
        try:
            report = child_probe(validate_root(args.qq_root), args.jail, args.nonce,
                                 json.loads(args.parent_ns), args.mount_tool,
                                 diagnostic_fd=args.private_diagnostic_fd,
                                 userns_control=args.userns_control,
                                 filesystem_mode=args.filesystem_mode,
                                 pivot_tool=args.pivot_tool, umount_tool=args.umount_tool,
                                  native_load_control=args.native_load_control,
                                  private_display=args.private_display, extended_startup=args.extended_startup,
                                   private_runtime_services=args.private_runtime_services)
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
        if args.private_display:
            kwargs["private_display"] = True
        if args.extended_startup:
            kwargs["extended_startup"] = True
        if args.private_runtime_services:
            kwargs["private_runtime_services"] = True
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
