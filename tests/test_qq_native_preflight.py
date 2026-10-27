"""Isolated static preflight tests; fixtures never touch a real QQ installation."""

import ast
import builtins
import importlib.util
import io
import json
from pathlib import Path
import socket
import struct
import subprocess

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "tools" / "qq_native_preflight.py"
SPEC = importlib.util.spec_from_file_location("qq_native_preflight", SOURCE)
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


@pytest.fixture
def installation(tmp_path):
    """Build minimal software-only files in a disposable temporary directory."""
    app = tmp_path / "resources" / "app"
    app.mkdir(parents=True)
    (app / "package.json").write_text(json.dumps({"version": "3.2.1", "buildVersion": "12345",
                                               "main": "./index.js"}), encoding="utf-8")
    (app / "index.js").write_text("require('./QQframework.js');", encoding="utf-8")
    return tmp_path, app


def elf_header(machine=62, elf_class=2, endian=1):
    """Construct a synthetic ELF header without creating an executable payload."""
    header = bytearray(64)
    header[:7] = b"\x7fELF" + bytes((elf_class, endian, 1))
    header[18:20] = struct.pack("<H" if endian == 1 else ">H", machine)
    return bytes(header)


def assert_closed(report):
    """Assert every inventory remains explicitly unauthorized and unverified."""
    for field in ("native_binding_verified", "independent_backend_ready", "independence",
                  "safe_to_login", "production_cutover_authorized"):
        assert report[field] is False
    assert report["abi_compatibility"] == "unverified"


def test_static_inventory_and_exit(installation, capsys):
    """Even plausible Linux native headers cannot authorize login or cutover."""
    root, app = installation
    (app / "wrapper.node").write_bytes(elf_header() + b"untouched payload")
    before = {path.name: path.read_bytes() for path in app.iterdir()}
    assert preflight.main(["--qq-root", str(root)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert_closed(report)
    assert report["package"]["version"] == "3.2.1"
    assert report["package"]["buildVersion"] == "12345"
    assert report["package"]["main"] == "resources/app/index.js"
    assert report["native_files"]["wrapper"]["machine"] == "x86_64"
    assert report["native_files"]["major"]["present"] is False
    assert "framework_uninspected" in report["bootstrap_coverage"]
    assert before == {path.name: path.read_bytes() for path in app.iterdir()}


@pytest.mark.parametrize("marker", ["main", "name", "dependency", "literal"])
def test_napcat_evidence(installation, marker):
    """Detect marker evidence in casefolded names, dependency keys, or literals."""
    root, app = installation
    (app / "wrapper.node").write_bytes(elf_header())
    (app / "major.node").write_bytes(elf_header(183))
    metadata = {"main": "./index.js"}
    if marker == "main":
        metadata["main"] = "./loadNapCat.js"
    elif marker == "name":
        metadata["name"] = "NaPcAt-loader"
    elif marker == "dependency":
        metadata["dependencies"] = {"@vendor/NAPCAT": "secret-do-not-report"}
    else:
        (app / "index.js").write_text("require('NaPcAt');", encoding="utf-8")
    (app / "package.json").write_text(json.dumps(metadata), encoding="utf-8")
    report = preflight.inspect_installation(root)
    assert report["observed_napcat_bootstrap_reference"] is True
    assert report["backend_independence_status"] == "observed_napcat_bootstrap_reference"
    assert "secret-do-not-report" not in json.dumps(report)
    assert_closed(report)


@pytest.mark.parametrize("main", ["../../x", "/private/credential.js", "C:\\secret\\a.js",
                                  "./account.json", 123, None])
def test_unsafe_main_is_redacted(installation, main):
    """Reject traversal, absolute paths, and non-software entries without echo."""
    root, app = installation
    (app / "package.json").write_text(json.dumps({"main": main}), encoding="utf-8")
    report = preflight.inspect_installation(root)
    assert "main" not in report["package"]
    assert report["errors"]
    assert "credential" not in json.dumps(report)
    assert_closed(report)


@pytest.mark.parametrize("payload", [b"\xff", b'{"secret-token":', b"[]", b"null",
                                     b'{"main":{}, "version":123}',
                                     b"[" * 2000, b'{"number":' + b"1" * 5000 + b"}"])
def test_malformed_metadata_is_generic(installation, payload):
    """Malformed JSON and UTF-8 never expose input bytes or arbitrary keys."""
    root, app = installation
    (app / "package.json").write_bytes(payload)
    report = preflight.inspect_installation(root)
    assert report["errors"]
    assert "secret-token" not in json.dumps(report)
    assert_closed(report)


def test_metadata_allowlist(installation):
    """Only safe string version fields survive, never credentials or dependencies."""
    root, app = installation
    (app / "package.json").write_text(json.dumps({"version": "bad version / secret",
        "buildVersion": {"token": "secret"}, "main": "./index.js", "token": "secret",
        "dependencies": {"ordinary": "napcat secret"}}), encoding="utf-8")
    report = preflight.inspect_installation(root)
    assert report["observed_napcat_bootstrap_reference"] is False
    assert "secret" not in json.dumps(report)
    assert "version" not in report["package"]


@pytest.mark.parametrize("filename,limit", [("package.json", preflight.PACKAGE_LIMIT),
                                          ("index.js", preflight.BOOTSTRAP_LIMIT)])
def test_huge_files_not_read(installation, filename, limit):
    """Size checks reject oversized metadata/bootstrap before content reads."""
    root, app = installation
    with (app / filename).open("wb") as stream:
        stream.truncate(limit + 1)
    report = preflight.inspect_installation(root)
    assert report["errors"]
    assert_closed(report)


@pytest.mark.parametrize("machine,elf_class,endian,name", [(62, 2, 1, "x86_64"),
    (183, 2, 1, "aarch64"), (3, 1, 1, "x86"), (40, 1, 2, "arm"), (999, 2, 1, "unknown")])
def test_header_dimensions(installation, machine, elf_class, endian, name):
    """Decode ELF class and machine statically, including big endian headers."""
    root, app = installation
    (app / "wrapper.node").write_bytes(elf_header(machine, elf_class, endian))
    native = preflight.inspect_installation(root)["native_files"]["wrapper"]
    assert native["binary_format"] == "ELF"
    assert native["elf_class"] == (32 if elf_class == 1 else 64)
    assert native["machine"] == name
    assert native["abi_compatibility"] == "unverified"


@pytest.mark.parametrize("header", [b"fakeELF", b"MZ" + bytes(62), b"\x7fELF", elf_header()[:40]])
def test_fake_headers_never_verified(installation, header):
    """Short and non-ELF headers remain unknown, not runnable native evidence."""
    root, app = installation
    (app / "wrapper.node").write_bytes(header)
    report = preflight.inspect_installation(root)
    assert report["native_files"]["wrapper"]["binary_format"] == "unknown"
    assert_closed(report)


def test_native_reads_exactly_header(installation, monkeypatch):
    """A sparse 150 MiB native fixture is read in one 64-byte operation only."""
    root, app = installation
    native = app / "wrapper.node"
    with native.open("wb") as stream:
        stream.write(elf_header())
        stream.truncate(150 * 1024 * 1024)
    original = Path.open
    reads = []

    class Guard(io.BytesIO):
        """Record requested byte bounds without reading the sparse fixture body."""

        def read(self, size=-1):
            """Reject any attempt to hash or fully consume the native payload."""
            reads.append(size)
            assert size == 64
            return super().read(size)

    def guarded_open(path, *args, **kwargs):
        """Intercept only native reads; ordinary fixture metadata remains real."""
        if path == native:
            return Guard(elf_header())
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    assert preflight.inspect_installation(root)["native_files"]["wrapper"]["size_bytes"] == 150 * 1024 * 1024
    assert reads == [64]


@pytest.mark.parametrize("filename", ["package.json", "index.js", "wrapper.node"])
def test_symlink_escape_not_read(installation, tmp_path, filename):
    """Reject software symlinks rather than following them into account state."""
    root, app = installation
    outside = tmp_path.parent / (tmp_path.name + "-account-secret")
    outside.write_text("private bytes", encoding="utf-8")
    target = app / filename
    if target.exists():
        target.unlink()
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation unavailable; no ACL changes attempted")
    report = preflight.inspect_installation(root)
    assert "private bytes" not in json.dumps(report)
    assert_closed(report)
    assert outside.read_text(encoding="utf-8") == "private bytes"


def test_nonexistent_root_generic(tmp_path, capsys):
    """Nonexistent roots return redacted JSON and a deterministic failing CLI."""
    assert preflight.main(["--qq-root", str(tmp_path / "sensitive-account-root")]) == 1
    output = capsys.readouterr().out
    assert "sensitive-account-root" not in output
    assert json.loads(output)["errors"] == ["invalid_root"]


def test_import_and_inspection_no_execution(installation, monkeypatch):
    """Forbid process/network/native imports across module import and inspection."""
    root, _ = installation
    original_import = builtins.__import__

    def forbidden(*args, **kwargs):
        """Fail immediately if any process or socket operation is attempted."""
        raise AssertionError("Execution/network forbidden")

    def guarded_import(name, *args, **kwargs):
        """Reject native-loader imports while permitting standard-library imports."""
        assert not any(marker in name.casefold() for marker in ("napcat", "ctypes"))
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    module = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(module)
    assert_closed(module.inspect_installation(root))


def test_all_functions_documented_and_syntax():
    """Check syntax/docstrings without writing bytecode or installing dependencies."""
    for source in (SOURCE, Path(__file__)):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert ast.get_docstring(node), node.name
