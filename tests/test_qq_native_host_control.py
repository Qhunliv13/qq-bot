"""Safety contracts for the fixed native host callback; no native code in unit tests."""

import argparse
import json
from pathlib import Path
from unittest import mock

import pytest

from tools import qq_native_host_control as control


def record():
    """Build the companion's pessimistic fixed output envelope."""
    return {"schema_version": 1, "ok": False, "error": "discovery_timeout",
            "runtime": {"versions": {}, "exec_path_matches": False},
            "binding": {"load_attempted": False, "loaded": False, "exports": [],
                        "truncated": False, "candidates": {
                            "NodeIQQNTWrapperEngine": False, "NodeIKernelLoginService": False,
                            "NodeIKernelWrapperSession": False, "NodeIQQNTWrapperSession": False,
                            "NodeIQQNTStartupSessionWrapper": False}},
            "readiness": {"runtime_host_verified": False, "native_binding_verified": False,
                          "session_initialized": False, "safe_to_login": False,
                          "independent_backend_ready": False, "production_cutover_ready": False}}


def test_base_report_cannot_claim_readiness():
    """Successful load would still not permit account or backend operations."""
    report = control.base_report()
    for key in ("native_host_binding_load_verified", "native_binding_verified", "safe_to_login",
                "independent_backend_ready", "native_login_verified", "full_sendrecv_verified",
                "production_cutover", "production_cutover_authorized"):
        assert report[key] is False
    assert report["invocation"]["argv"] == ["/opt/QQ/qq", "--inspect=127.0.0.1:9229"]
    assert control.OUTER_TIMEOUT == 45
    assert control.QQ_TIMEOUT == 30


def test_fixed_failure_record_roundtrip():
    """Only the single fixed marker and bounded JSON can leave the jail."""
    value = record()
    assert control.parse_inspector(b"QQ_NATIVE_INSPECTOR:" + json.dumps(value).encode()) == value


@pytest.mark.parametrize("raw", [b"", b"{}", b"noise\nQQ_NATIVE_INSPECTOR:{}",
                                    b"QQ_NATIVE_INSPECTOR:[]", b"x" * 16385,
                                    b"QQ_NATIVE_INSPECTOR:{}\n{}"])
def test_reject_invalid_inspector_output(raw):
    """Malformed, extra or excessive inspector output never enters evidence."""
    with pytest.raises(control.runtime.ProbeError, match="inspector_output_invalid"):
        control.parse_inspector(raw)


@pytest.mark.parametrize("key", ["safe_to_login", "session_initialized", "independent_backend_ready",
                                "production_cutover_ready"])
def test_reject_readiness_escalation(key):
    """The native-load control must never grow into readiness certification."""
    value = record()
    value["readiness"][key] = True
    with pytest.raises(control.runtime.ProbeError):
        control.parse_inspector(b"QQ_NATIVE_INSPECTOR:" + json.dumps(value).encode())


def test_reject_raw_failure_strings():
    """Errors must remain fixed categories, never exception/path/account text."""
    value = record()
    value["error"] = "secret path or remote error"
    with pytest.raises(control.runtime.ProbeError):
        control.parse_inspector(b"QQ_NATIVE_INSPECTOR:" + json.dumps(value).encode())


@pytest.mark.parametrize("field", ["host", "binding", "ok"])
def test_reject_incoherent_readiness(field):
    """Flags alone cannot certify the host or native loading without matching facts."""
    value = record()
    if field == "host":
        value["readiness"]["runtime_host_verified"] = True
    elif field == "binding":
        value["binding"]["loaded"] = True
    else:
        value["ok"] = True
    with pytest.raises(control.runtime.ProbeError):
        control.parse_inspector(b"QQ_NATIVE_INSPECTOR:" + json.dumps(value).encode())


def test_success_is_only_coherent_load_evidence():
    """A good record still has all account/backend readiness flags false."""
    value = record()
    value.update(ok=True, error=None)
    value["runtime"].update(exec_path_matches=True,
                            versions={"node": "22.20.0", "electron": "39.0.0", "modules": "140", "napi": "10"})
    value["binding"].update(loaded=True, load_attempted=True)
    value["readiness"].update(runtime_host_verified=True, native_binding_verified=True)
    assert control.parse_inspector(b"QQ_NATIVE_INSPECTOR:" + json.dumps(value).encode()) == value


def test_private_network_rejects_parent_namespace():
    """Loopback is never raised if namespace identity matches the original host."""
    with mock.patch.object(control.os, "readlink", return_value="net:[12]"):
        with pytest.raises(control.runtime.ProbeError, match="private_network_identity_failed"):
            control.validate_private_network("net:[12]")


def test_private_network_rejects_external_interface():
    """A non-loopback interface blocks all subsequent trusted ip execution."""
    with mock.patch.object(control.os, "readlink", return_value="net:[13]"), mock.patch.object(
            Path, "read_text", return_value="header\nheader\n lo: data\n eth0: data\n"):
        with pytest.raises(control.runtime.ProbeError, match="private_network_interfaces_failed"):
            control.validate_private_network("net:[12]")


def test_private_network_rejects_external_route():
    """A discovered route outside lo refuses the experiment without changing it."""
    with mock.patch.object(control.os, "readlink", return_value="net:[13]"), mock.patch.object(
            Path, "read_text", side_effect=["header\nheader\n lo: data\n", "header\neth0 00000000\n"]):
        with pytest.raises(control.runtime.ProbeError, match="private_network_routes_failed"):
            control.validate_private_network("net:[12]")


def test_prepare_stages_exclusively_and_raises_only_loopback():
    """Fixed source is readonly before the sole trusted network mutation."""
    opened = mock.mock_open()
    with mock.patch.object(control, "validate_private_network") as network, mock.patch.object(
            Path, "open", opened), mock.patch.object(Path, "chmod") as chmod, mock.patch.object(
            control.subprocess, "run") as run:
        digest = control.prepare_inspector(b"fixed source", "net:[12]")
    opened.assert_called_once_with("xb")
    opened().write.assert_called_once_with(b"fixed source")
    chmod.assert_called_once_with(0o444)
    assert network.call_count == 2
    assert run.call_args.args[0] == ["/usr/sbin/ip", "link", "set", "dev", "lo", "up"]
    assert run.call_args.kwargs["timeout"] == 2
    assert len(digest) == 64


def test_callback_refuses_run_as_node():
    """The native host is the packaged QQ application, never RUN_AS_NODE."""
    with pytest.raises(control.runtime.ProbeError, match="native_host_invocation_contract_failed"):
        control.observe_native_host(["/opt/QQ/qq"], {"ELECTRON_RUN_AS_NODE": "1"},
                                    None, None, 30, {}, source=b"fixed", parent_netns="net:[12]")


def test_child_uses_full_existing_gate():
    """No second isolation implementation or weakened service gate is allowed."""
    args = argparse.Namespace(qq_root="/candidate", jail="/jail", nonce="nonce",
                              parent_ns=json.dumps({"net": "net:[12]"}), mount_tool="/bin/mount",
                              pivot_tool="/bin/pivot_root", umount_tool="/bin/umount")
    with mock.patch.object(Path, "read_bytes", return_value=b"fixed"), mock.patch.object(
            control.runtime, "validate_root", return_value=Path("/candidate")), mock.patch.object(
            control.runtime, "child_probe", return_value={"status": "observed"}) as child:
        assert control.child_main(args) == {"status": "observed"}
    kwargs = child.call_args.kwargs
    assert kwargs["filesystem_mode"] == "pivot-root"
    assert kwargs["private_display"] is True
    assert kwargs["private_runtime_services"] is True
    assert kwargs["extended_startup"] is True
    assert callable(kwargs["application_observer"])


def test_source_missing_fails_before_launch():
    """Missing companion code is evidence refusal, not guessed native success."""
    with mock.patch.object(control.sys, "platform", "linux"), mock.patch.object(
            control.runtime, "validate_root", return_value=Path("/candidate")), mock.patch.object(
            control.os, "geteuid", return_value=0, create=True), mock.patch.object(
            Path, "is_file", return_value=False), mock.patch.object(control.subprocess, "Popen") as launch:
        report = control.observe("/candidate", execute=True)
    assert report["errors"] == ["inspector_source_missing"]
    assert report["native_host_binding_load_verified"] is False
    launch.assert_not_called()
