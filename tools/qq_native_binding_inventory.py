"""Read only pristine QQ native ELF metadata; never load a binding or account state.

This independent inventory is deliberately not a runtime/ABI readiness test. Only
explicit software paths are read; native libraries and QQ JavaScript never execute.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

# These package-relative locations are software, not profile/account directories.
CANDIDATES = ("resources/app/wrapper.node", "resources/app/major.node")
MAX_BINARY = 256 * 1024 * 1024
MAX_SYMBOL_OUTPUT = 16 * 1024 * 1024
INTERESTING = re.compile(r"(?:napi_|node_module|qq_magic|QQMagic|NAPI_MODULE|Kernel|kernel|Session|session)")
# readelf --wide prevents symbol-name truncation; UND is never an exported API.
SYMBOL_LINE = re.compile(r"^\s*\d+:\s+\S+\s+\d+\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)")


def parse_symbols(text):
    """Separate defined global/weak symbols from undefined imports, without guessing ABI."""
    exports, imports = set(), set()
    total_exports = 0
    for line in text.splitlines():
        match = SYMBOL_LINE.match(line)
        if not match:
            continue  # Section headings and blank lines carry no symbol evidence.
        kind, binding, visibility, section, name = match.groups()
        if binding not in ("GLOBAL", "WEAK") or visibility not in ("DEFAULT", "PROTECTED"):
            continue  # Local/hidden symbols cannot establish public binding exports.
        if section != "UND":
            total_exports += 1
        if INTERESTING.search(name):
            (imports if section == "UND" else exports).add(name)
    return {"defined_dynamic_symbol_count": total_exports,
            "interesting_defined_symbols": sorted(exports)[:200],
            "interesting_undefined_symbols": sorted(imports)[:200],
            "interesting_symbols_truncated": len(exports) > 200 or len(imports) > 200}


def inspect_candidate(root, relative):
    """Hash one bounded regular ELF and ask trusted system readelf for dynamic symbols."""
    path = root / relative
    # Resolve intermediates too: no package symlink may escape into an account store.
    if path.is_symlink() or not path.is_file() or path.resolve() != path:
        raise ValueError("candidate_missing_or_symlink")
    size = path.stat().st_size
    if not 64 <= size <= MAX_BINARY:
        raise ValueError("candidate_size_refused")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        header = stream.read(64)
        if header[:6] != b"\x7fELF\x02\x01":
            raise ValueError("candidate_not_elf64_little_endian")
        digest.update(header)
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    # File capture bounds memory; RLIMIT_FSIZE prevents unbounded trusted-tool output.
    import resource

    def limit_output():
        """Bound readelf output before exec; no target binary is ever executed."""
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_SYMBOL_OUTPUT, MAX_SYMBOL_OUTPUT))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    with tempfile.TemporaryFile() as output:
        result = subprocess.run(["/usr/bin/readelf", "--dyn-syms", "--wide", str(path)],
                                stdout=output, stderr=subprocess.DEVNULL, timeout=15,
                                check=False, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
                                stdin=subprocess.DEVNULL, preexec_fn=limit_output)
        if result.returncode != 0 or output.tell() >= MAX_SYMBOL_OUTPUT:
            raise ValueError("readelf_failed_or_output_limit")
        output.seek(0)
        symbols = parse_symbols(output.read(MAX_SYMBOL_OUTPUT).decode("ascii", errors="replace"))
    return {"candidate": relative, "sha256": digest.hexdigest(), "size_bytes": size,
            "elf_machine": int.from_bytes(header[18:20], "little"), **symbols}


def inventory(qq_root):
    """Inventory two explicit software artifacts, keeping every readiness flag false."""
    report = {"mode": "static_native_binding_inventory", "status": "refused",
              "native_code_executed": False, "native_binding_verified": False,
              "abi_compatibility": "unverified", "independent_backend_ready": False,
              "safe_to_login": False, "production_cutover_authorized": False,
              "candidates": [], "errors": []}
    try:
        if sys.platform != "linux":
            raise ValueError("linux_required")
        supplied = Path(qq_root)
        root = supplied.resolve(strict=True)
        if not supplied.is_absolute() or supplied != root or not root.is_dir():
            raise ValueError("canonical_software_root_required")
        if root == Path("/") or root.parts[1:2] in (("home",), ("root",), ("proc",), ("run",), ("dev",), ("sys",)):
            raise ValueError("unsafe_software_root")
        for relative in CANDIDATES:
            report["candidates"].append(inspect_candidate(root, relative))
        report["status"] = "inventoried_not_runtime_verified"
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        # Never leak arbitrary filesystem/tool errors; only fixed intentional codes.
        report["errors"] = [str(exc) if type(exc) is ValueError else "inventory_failed"]
    return report


def main(argv=None):
    """Print reproducible static evidence; nonzero exit prevents confusing it with readiness."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qq-root", required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    report = inventory(args.qq_root)
    text = json.dumps(report, sort_keys=True, indent=2) + "\n"
    if args.report:
        # Exclusive creation prevents accidentally overwriting prior native evidence.
        with args.report.open("x", encoding="utf-8") as stream:
            stream.write(text)
    sys.stdout.write(text)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
