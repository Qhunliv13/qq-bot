"""Offline, fail-closed QQ software inventory; never load or execute the installation.

Only allowlisted installation files are read. Static evidence cannot verify a
native binding, ABI, independent backend, login safety, or production cutover.
"""

import argparse
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import struct


# Bounds prevent unexpectedly large metadata/bootstrap reads; native reads are 64 bytes.
PACKAGE_LIMIT = 1024 * 1024
BOOTSTRAP_LIMIT = 256 * 1024
VERSION_PATTERN = re.compile(r"[0-9A-Za-z.+_-]{1,80}\Z")
# ELF e_machine values from the ELF specification, not host architecture guesses.
ELF_MACHINES = {3: "x86", 40: "arm", 62: "x86_64", 183: "aarch64", 243: "riscv"}


class InspectionError(ValueError):
    """Carry only fixed, non-sensitive error codes to the JSON boundary."""


def checked_file(root, relative):
    """Resolve a regular software file strictly, rejecting traversal and symlinks.

    Rejecting even in-root symlinks prevents an installation link from targeting
    account state. No directory enumeration or account directory reads occur.
    """
    path = Path(relative)
    if (path.is_absolute() or PurePosixPath(str(relative)).is_absolute()
            or PureWindowsPath(str(relative)).is_absolute() or ".." in path.parts):
        raise InspectionError("unsafe_path")
    current = root
    for component in path.parts:
        current = current / component
        if current.is_symlink():
            raise InspectionError("unsafe_path")
    try:
        resolved = current.resolve(strict=True)
        resolved.relative_to(root)
        if not stat.S_ISREG(resolved.stat().st_mode):
            raise InspectionError("not_regular_file")
    except FileNotFoundError as exc:
        raise InspectionError("missing_file") from exc
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, InspectionError):
            raise
        raise InspectionError("unsafe_or_unreadable_file") from exc
    return resolved


def read_bounded(path, limit):
    """Read at most the permitted size plus a sentinel, never echoing bytes."""
    if path.stat().st_size > limit:
        raise InspectionError("file_too_large")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise InspectionError("file_too_large")
    return data


def inspect_native(root, relative):
    """Inventory only a 64-byte header; ELF architecture is not ABI evidence."""
    result = {"path": relative, "present": False, "binary_format": "unknown",
              "abi_compatibility": "unverified", "header_bytes_read": 0}
    try:
        path = checked_file(root, relative)
        result.update(present=True, size_bytes=path.stat().st_size)
        with path.open("rb") as stream:
            header = stream.read(64)
        result["header_bytes_read"] = len(header)
        # ELF identification, class, byte order, and version must be coherent.
        if (len(header) >= 20 and header[:4] == b"\x7fELF"
                and header[4] in (1, 2) and header[5] in (1, 2) and header[6] == 1):
            required = 52 if header[4] == 1 else 64
            if len(header) < required:
                result["error"] = "truncated_elf_header"
            else:
                order = "<" if header[5] == 1 else ">"
                machine = struct.unpack(order + "H", header[18:20])[0]
                result.update(binary_format="ELF", elf_class=32 if header[4] == 1 else 64,
                              byte_order="little" if header[5] == 1 else "big",
                              elf_machine=machine, machine=ELF_MACHINES.get(machine, "unknown"))
        elif header[:2] == b"MZ":
            # MZ alone proves DOS, not PE: never label a guessed signature as PE.
            result["format_hint"] = "DOS_or_PE_unsupported"
        else:
            result["error"] = "unrecognized_header"
    except InspectionError as exc:
        result["error"] = str(exc)
    except OSError:
        result["error"] = "unreadable_file"
    return result


def inspect_installation(qq_root):
    """Return a redacted allowlist report without imports, network, or mutations."""
    report = {"mode": "offline_static_inspection", "native_binding_verified": False,
              "independent_backend_ready": False, "independence": False,
              "safe_to_login": False, "production_cutover_authorized": False,
              "abi_compatibility": "unverified", "observed_napcat_bootstrap_reference": False,
              "bootstrap_coverage": "unknown", "backend_independence_status": "unverified",
              "supported_native_version": "unverified", "errors": []}
    try:
        # Root resolution is explicit and strict; no default live installation is touched.
        supplied = Path(qq_root)
        if supplied.is_symlink():
            raise InspectionError("invalid_root")
        root = supplied.resolve(strict=True)
        if not root.is_dir():
            raise InspectionError("invalid_root")
    except (OSError, RuntimeError, ValueError, TypeError):
        report["errors"].append("invalid_root")
        return report
    package_relative = "resources/app/package.json"
    report["package"] = {"path": package_relative}
    try:
        package_path = checked_file(root, package_relative)
        package = json.loads(read_bounded(package_path, PACKAGE_LIMIT).decode("utf-8"))
        if not isinstance(package, dict):
            raise InspectionError("invalid_metadata")
        for field in ("version", "buildVersion"):
            value = package.get(field)
            if isinstance(value, str) and VERSION_PATTERN.fullmatch(value):
                report["package"][field] = value
            elif value is not None:
                report["errors"].append("invalid_" + field)
        # Dependency values and arbitrary metadata keys/values are never reported.
        name = package.get("name")
        marker = isinstance(name, str) and "napcat" in name.casefold()
        for field in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
            dependencies = package.get(field)
            if isinstance(dependencies, dict):
                marker = marker or any("napcat" in key.casefold() for key in dependencies)
        report["observed_napcat_bootstrap_reference"] = marker
        if marker:
            report["backend_independence_status"] = "observed_napcat_bootstrap_reference"
        main = package.get("main")
        if not isinstance(main, str) or not main or len(main) > 256:
            raise InspectionError("invalid_main")
        # Main must be software JS below app, not a metadata-directed account file.
        if ("\\" in main or ":" in main or PurePosixPath(main).is_absolute()
                or Path(main).is_absolute()
                or ".." in Path(main).parts
                or not re.fullmatch(r"[A-Za-z0-9_./-]+\.js", main)):
            raise InspectionError("unsafe_main")
        marker = marker or "napcat" in main.casefold()
        report["observed_napcat_bootstrap_reference"] = marker
        report["package"]["main"] = (Path("resources/app") / main).as_posix()
        try:
            main_path = checked_file(root, Path("resources/app") / main)
            bootstrap = read_bounded(main_path, BOOTSTRAP_LIMIT).decode("utf-8")
            report["bootstrap_coverage"] = "entry_literal_only_framework_uninspected"
            marker = marker or "napcat" in bootstrap.casefold()
        except (InspectionError, UnicodeError, OSError):
            # Missing QQ framework and unknown loaders never imply zero references.
            report["errors"].append("bootstrap_uninspected")
        report["observed_napcat_bootstrap_reference"] = marker
        if marker:
            report["backend_independence_status"] = "observed_napcat_bootstrap_reference"
    except InspectionError as exc:
        report["errors"].append(str(exc))
    except (UnicodeError, ValueError, RecursionError):
        report["errors"].append("invalid_metadata")
    except OSError:
        report["errors"].append("unreadable_metadata")
    report["native_files"] = {
        "wrapper": inspect_native(root, "resources/app/wrapper.node"),
        "major": inspect_native(root, "resources/app/major.node"),
    }
    return report


def main(argv=None):
    """Print a safe JSON report; static-only preflight always exits non-ready (1)."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qq-root", required=True, help="Explicit QQ software installation root")
    arguments = parser.parse_args(argv)
    print(json.dumps(inspect_installation(arguments.qq_root), sort_keys=True))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
