"""Mock-only regression checks: never execute QQ or create real namespaces."""

import io
import json
import os
import stat
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest

from tools import qq_isolated_runtime_probe as probe


@pytest.fixture
def software(tmp_path):
    """Create inert ELF-identifying bytes, not a runnable QQ executable."""
    root = tmp_path / "QQ"
    root.mkdir()
    exe = root / "qq"
    exe.write_bytes(b"\x7fELF" + b"\0" * 60)
    exe.chmod(0o755)
    return root.resolve()


def linux(monkeypatch):
    """Permit platform-logic tests on Windows without running Linux commands."""
    monkeypatch.setattr(probe.sys, "platform", "linux")


def test_default_never_executes(monkeypatch, software):
    """Default validation must not invoke even unshare or mount."""
    linux(monkeypatch)
    runner = Mock(side_effect=AssertionError("execution forbidden"))
    monkeypatch.setattr(probe.subprocess, "run", runner)
    report = probe.probe(software)
    assert report["status"] == "not_executed"
    assert report["runtime_executed"] is False
    assert report["production_cutover"] is False
    runner.assert_not_called()


def test_nonlinux_refuses_before_read(monkeypatch):
    """A non-Linux process never reads or executes the supplied installation."""
    monkeypatch.setattr(probe.sys, "platform", "win32")
    monkeypatch.setattr(probe, "validate_root", Mock(side_effect=AssertionError))
    assert probe.probe("not-read", True)["errors"] == ["linux_required"]


def test_environment_does_not_inherit(monkeypatch):
    """Tokens, proxy settings and dynamic-loader overrides never cross the boundary."""
    for key in ("QAPI_TOKEN", "HTTPS_PROXY", "LD_PRELOAD", "NODE_OPTIONS"):
        monkeypatch.setenv(key, "secret")
    env = probe.minimal_environment()
    assert set(env) == {"PATH", "HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
                        "ELECTRON_RUN_AS_NODE", "LANG"}
    assert env["ELECTRON_RUN_AS_NODE"] == "1"
    assert env["HOME"] == "/tmp/home"


def test_script_is_runtime_only():
    """The saved script reads runtime properties without imports or app entrypoints."""
    script = probe.node_script("nonce")
    assert "process.versions" in script
    assert "QQ_RUNTIME_nonce:" in script
    for forbidden in ("require(", "import ", "native.node", "app.asar", "process.env", "cwd("):
        assert forbidden not in script


def runtime_record(nonce="nonce", **changes):
    """Build one synthetic allowlisted record solely for parser unit tests."""
    value = {"versions": {"node": "22.0.0", "electron": "35.0.0", "modules": "133"},
             "platform": "linux", "arch": "x64"}
    value.update(changes)
    return ("QQ_RUNTIME_" + nonce + ":" + json.dumps(value) + "\n").encode()


def test_parser_ignores_unmarked_logs():
    """Unmarked text may exist but is never surfaced in the redacted result."""
    result = probe.parse_output(b"untrusted raw log\n" + runtime_record(), "nonce")
    assert result["versions"]["electron"] == "35.0.0"
    assert "untrusted" not in json.dumps(result)


@pytest.mark.parametrize("data", [b"no marker", runtime_record("other"),
                                  runtime_record() * 2, b"x" * (probe.OUTPUT_LIMIT + 1),
                                  runtime_record(versions={"node": "22", "electron": "35", "token": "x"}),
                                  runtime_record(versions={"node": "22"}),
                                  runtime_record(versions={"node": "22", "electron": 35}),
                                  runtime_record(platform="win32")],
                         ids=["missing", "wrong_nonce", "duplicate", "oversize", "extra_key",
                              "missing_electron", "not_string", "wrong_platform"])
def test_parser_rejects_ambiguous_or_nonwhitelisted(data):
    """Malformed, duplicate, oversized, or unexpected properties cannot become evidence."""
    with pytest.raises(probe.ProbeError):
        probe.parse_output(data, "nonce")


def test_explicit_execution_builds_isolation_args(monkeypatch, software):
    """Mock the helper, checking real isolation flags without running its commands."""
    linux(monkeypatch)
    monkeypatch.setattr(probe.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(probe.shutil, "which", lambda name: "/usr/bin/" + name)
    parent = {"net": "net:[1]", "mnt": "mnt:[2]", "pid": "pid:[3]"}
    monkeypatch.setattr(probe, "namespace_ids", lambda: parent)
    child = probe.base_report()
    child.update(status="observed", runtime_executed=True,
                 versions={"node": "22", "electron": "35"})
    runner = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(child).encode()))
    monkeypatch.setattr(probe.subprocess, "run", runner)
    report = probe.probe(software, execute=True)
    args = runner.call_args.args[0]
    kwargs = runner.call_args.kwargs
    for flag in ("--mount", "--net", "--pid", "--fork", "--kill-child", "--mount-proc"):
        assert flag in args
    assert args[0] == "/usr/bin/unshare"
    assert "--no-sandbox" not in args
    assert kwargs["env"] == probe.minimal_environment()
    assert kwargs["timeout"] == 15
    assert kwargs["start_new_session"] is True
    assert "shell" not in kwargs
    assert json.loads(args[args.index("--parent-ns") + 1]) == parent
    assert report["versions"] == child["versions"]
    assert report["production_cutover"] is False
    assert report["native_binding_verified"] is False


@pytest.mark.parametrize("failure,code", [
    (subprocess.TimeoutExpired(["unshare"], 15), "isolation_timeout"),
    (OSError("sensitive path or token"), "probe_failed"),
])
def test_execution_errors_are_redacted(monkeypatch, software, failure, code):
    """Failures cannot leak exception messages or accidentally authorize a release."""
    linux(monkeypatch)
    monkeypatch.setattr(probe.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(probe.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(probe, "namespace_ids", lambda: {k: k for k in probe.NAMESPACES})
    monkeypatch.setattr(probe.subprocess, "run", Mock(side_effect=failure))
    report = probe.probe(software, True)
    assert report["errors"] == [code]
    assert report["production_cutover"] is False
    assert "sensitive" not in json.dumps(report)


def test_same_namespace_refuses_before_mount(monkeypatch, software, tmp_path):
    """A mocked or broken namespace launcher cannot reach mount or QQ execution."""
    ids = {key: key for key in probe.NAMESPACES}
    monkeypatch.setattr(probe, "namespace_ids", lambda: ids)
    runner = Mock(side_effect=AssertionError("must not mount"))
    monkeypatch.setattr(probe, "run_checked", runner)
    with pytest.raises(probe.ProbeError, match="namespace_isolation_failed"):
        probe.child_probe(software, tmp_path, "nonce", ids, "/usr/bin/mount")
    runner.assert_not_called()


def test_readonly_bind_uses_only_argument_lists(monkeypatch):
    """Explicit nonrecursive read-only binds never expose a host root implicitly."""
    runner = Mock()
    monkeypatch.setattr(probe, "run_checked", runner)
    probe.bind_readonly(Path("/usr"), Path("/jail/usr"), "/usr/bin/mount")
    assert runner.call_args_list[0].args[0] == ["/usr/bin/mount", "--bind", str(Path("/usr")), str(Path("/jail/usr"))]
    assert runner.call_args_list[1].args[0] == ["/usr/bin/mount", "-o", "remount,bind,ro,nosuid,nodev", str(Path("/jail/usr"))]


def test_nonelf_rejected(software):
    """Software entrypoint validation rejects an inert non-ELF candidate."""
    (software / "qq").write_bytes(b"not an ELF")
    with pytest.raises(probe.ProbeError, match="qq_not_elf"):
        probe.validate_root(software)


def test_cli_report_default_is_nonexecuting(monkeypatch, tmp_path, capsys):
    """The default CLI writes only the fixed report and never opts into execution."""
    destination = tmp_path / "report.json"
    fake = Mock(return_value=probe.base_report())
    monkeypatch.setattr(probe, "probe", fake)
    assert probe.main(["--qq-root", "/explicit/QQ", "--report", str(destination)]) == 0
    fake.assert_called_once_with("/explicit/QQ", execute=False)
    assert json.loads(destination.read_text(encoding="utf-8"))["production_cutover"] is False
    assert json.loads(capsys.readouterr().out)["status"] == "not_executed"


def test_child_failure_report_preserves_execution(monkeypatch, software):
    """Exit code one must not erase a helper's truthful attempted runtime evidence."""
    linux(monkeypatch)
    monkeypatch.setattr(probe.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(probe.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(probe, "namespace_ids", lambda: {k: k for k in probe.NAMESPACES})
    child = probe.base_report()
    child.update(status="failed", runtime_executed=True, errors=["runtime_nonzero_exit"])
    runner = Mock(return_value=subprocess.CompletedProcess([], 1, json.dumps(child).encode()))
    monkeypatch.setattr(probe.subprocess, "run", runner)
    report = probe.probe(software, True)
    assert report["runtime_executed"] is True
    assert report["runtime_versions_verified"] is False
    assert report["errors"] == ["runtime_nonzero_exit"]
    assert report["production_cutover"] is False


def test_stderr_classification_redacts_paths_and_tokens():
    """Only a SONAME or fixed category survives private bounded stderr capture."""
    data = b"/private/qq: error while loading shared libraries: libasound.so.2: secret token"
    assert probe.classify_runtime_stderr(data) == {
        "runtime_failure_category": "missing_shared_library", "missing_library": "libasound.so.2"}
    assert probe.classify_runtime_stderr(b"secret /home/account") == {
        "runtime_failure_category": "unclassified_nonzero_exit"}
    assert probe.classify_runtime_stderr(b"No usable sandbox! /private") == {
        "runtime_failure_category": "sandbox_initialization_failed"}


def test_observed_suid_sandbox_fatal_is_classified_without_raw_details():
    """Match the observed fatal only; neither paths nor arbitrary traps become evidence."""
    data = (b"FATAL:setuid_sandbox_host.cc(166) The SUID sandbox helper binary was found, "
            b"but is not configured correctly. /private/secret/chrome-sandbox mode 4755")
    assert probe.classify_runtime_stderr(data) == {
        "runtime_failure_category": "suid_sandbox_helper_configuration_failed"}
    assert probe.classify_runtime_stderr(b"SIGTRAP setuid_sandbox_host.cc /private") == {
        "runtime_failure_category": "unclassified_nonzero_exit"}


def test_drop_privileges_requires_live_pid_one(monkeypatch):
    """Mock every privilege setter; a missing PID1 supervisor must prevent exec."""
    libc = Mock()
    libc.prctl.return_value = 0
    monkeypatch.setattr(probe.ctypes, "CDLL", lambda *args, **kwargs: libc)
    for name in ("setgroups", "setgid", "setuid"):
        monkeypatch.setattr(probe.os, name, Mock(), raising=False)
    monkeypatch.setattr(probe.os, "getppid", lambda: 99)
    with pytest.raises(probe.ProbeError, match="runtime_supervisor_missing"):
        probe.drop_runtime_privileges()
    probe.os.setgroups.assert_called_once_with([])
    probe.os.setgid.assert_called_once_with(65534)
    probe.os.setuid.assert_called_once_with(65534)


def test_private_diagnostics_default_creates_nothing(monkeypatch, software, tmp_path):
    """Supplying a destination is not execution consent and cannot create a file."""
    linux(monkeypatch)
    opener = Mock(side_effect=AssertionError("must not open"))
    monkeypatch.setattr(probe, "open_private_diagnostics", opener)
    report = probe.probe(software, private_diagnostics=tmp_path / "private")
    assert report["private_diagnostics_written"] is False
    opener.assert_not_called()


def test_private_descriptor_is_only_forwarded_to_helper(monkeypatch, software, tmp_path):
    """Mock execution validates fd transport and drops all child raw/private fields."""
    linux(monkeypatch)
    monkeypatch.setattr(probe.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(probe.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(probe, "namespace_ids", lambda: {k: k for k in probe.NAMESPACES})
    monkeypatch.setattr(probe, "open_private_diagnostics", Mock(return_value=91))
    closer = Mock()
    monkeypatch.setattr(probe.os, "close", closer)
    child = probe.base_report()
    child.update(status="failed", private_diagnostics_written=True,
                 _private_stderr_b64="secret", private_path="secret")
    runner = Mock(return_value=subprocess.CompletedProcess([], 1, json.dumps(child).encode()))
    monkeypatch.setattr(probe.subprocess, "run", runner)
    destination = tmp_path / "private-sensitive-name"
    report = probe.probe(software, True, destination)
    args = runner.call_args.args[0]
    assert args[-2:] == ["--private-diagnostic-fd", "91"]
    assert runner.call_args.kwargs["pass_fds"] == (91,)
    assert runner.call_args.kwargs["close_fds"] is True
    assert str(destination) not in args
    assert report["private_diagnostics_written"] is True
    assert "secret" not in json.dumps(report)
    assert "private-sensitive-name" not in json.dumps(report)
    closer.assert_called_once_with(91)


def test_private_descriptor_closed_on_helper_timeout(monkeypatch, software):
    """An outer helper timeout still closes the one parent-owned descriptor."""
    linux(monkeypatch)
    monkeypatch.setattr(probe.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(probe.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(probe, "namespace_ids", lambda: {k: k for k in probe.NAMESPACES})
    monkeypatch.setattr(probe, "open_private_diagnostics", Mock(return_value=91))
    closer = Mock()
    monkeypatch.setattr(probe.os, "close", closer)
    monkeypatch.setattr(probe.subprocess, "run", Mock(side_effect=subprocess.TimeoutExpired([], 15)))
    assert probe.probe(software, True, "/tmp/private")["errors"] == ["isolation_timeout"]
    closer.assert_called_once_with(91)


def test_private_copy_is_exact_bounded_and_handles_partial_writes(monkeypatch):
    """Synthetic stderr never runs QQ; only its exact 16-KiB prefix is copied."""
    monkeypatch.setattr(probe, "validate_diagnostic_fd", Mock())
    captured = bytearray()

    def partial_write(fd, data):
        """Model legal short writes without touching any real file descriptor."""
        assert fd == 91
        captured.extend(data[:13])
        return min(13, len(data))

    monkeypatch.setattr(probe.os, "write", partial_write)
    data = b"\xff\x00raw stderr\n" * 2000
    assert probe.copy_private_stderr(io.BytesIO(data), 91) is True
    assert bytes(captured) == data[:probe.PRIVATE_STDERR_LIMIT]


@pytest.mark.parametrize("fd,mode", [(0, stat.S_IFREG | 0o600),
                                      (91, stat.S_IFIFO | 0o600),
                                      (91, stat.S_IFREG | 0o644)])
def test_private_descriptor_rejects_standard_special_or_public(monkeypatch, fd, mode):
    """Even internal fd input cannot select a pipe, stdio, or public-mode file."""
    monkeypatch.setattr(probe.os, "fstat", Mock(return_value=Mock(st_mode=mode)))
    with pytest.raises(probe.ProbeError, match="invalid_private_diagnostic_fd"):
        probe.validate_diagnostic_fd(fd)


@pytest.mark.skipif(os.name != "posix", reason="Unix exclusive flags/mode semantics")
def test_private_open_exclusive_mode_and_symlink(tmp_path):
    """Use only empty temporary files, never QQ, accounts, logs, or namespaces."""
    destination = tmp_path / "private"
    fd = probe.open_private_diagnostics(destination)
    try:
        assert stat.S_IMODE(os.fstat(fd).st_mode) == 0o600
    finally:
        os.close(fd)
    with pytest.raises(FileExistsError):
        probe.open_private_diagnostics(destination)
    link = tmp_path / "link"
    link.symlink_to(destination)
    with pytest.raises(FileExistsError):
        probe.open_private_diagnostics(link)
    assert destination.read_bytes() == b""
