"""Safety and strict-output tests for the independent ephemeral host control."""

import copy
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import qq_ephemeral_host_entry as control


NONCE = "a" * 32


def package():
    """Supply only the observed Tencent identity plus preserved unrelated keys."""
    return {"name": "QQ", "version": "3.2.34-53644", "productName": "QQ", "platform": "linux",
            "buildVersion": "53644", "eleArch": "x64", "author": {"name": "Tencent"},
            "main": "./application.asar/app_launcher/index.js", "arbitrary_metadata": {"keep": True}}


def record():
    """Provide a valid descriptor-only load result without any QQ native call."""
    names = sorted(control.CANDIDATES)
    return {"schema_version": 1, "nonce": NONCE, "ok": True, "error": None,
            "runtime": {"versions": {"node": "22.0.0", "electron": "39.0.0", "modules": "140", "napi": "10"},
                        "exec_path_matches": True},
            "binding": {"load_attempted": True, "loaded": True, "truncated": False,
                        "exports": [{"name": name, "type": "function", "kind": "data"} for name in names],
                        "candidates": {name: True for name in names}}}


def encoded(value):
    """Encode the one fixed nonce marker accepted by the parser."""
    return ("QQ_WRAPPER_HOST_" + NONCE + ":" + json.dumps(value) + "\n").encode()


def test_manifest_preserves_every_key_except_main():
    """The private override cannot silently replace unrelated package metadata."""
    original = package()
    result = json.loads(control.validate_manifest(json.dumps(original).encode()))
    assert result.pop("main") == control.PACKAGE_MAIN
    assert control.PACKAGE_MAIN == "./app_launcher/index.js"
    original.pop("main")
    assert result == original


@pytest.mark.parametrize("key,value", [("name", "other"), ("version", "0"), ("main", "index.js"),
                                       ("platform", "win32"), ("eleArch", "arm64")])
def test_manifest_identity_refuses_other_packages(key, value):
    """No alternate package or arbitrary entry may pass the identity pin."""
    value_package = package()
    value_package[key] = value
    with pytest.raises(control.runtime.ProbeError):
        control.validate_manifest(json.dumps(value_package).encode())


def test_record_valid_and_accessor_safe():
    """Data types and accessors are descriptions only, never executable values."""
    value = record()
    value["binding"]["exports"].append({"name": "safeAccessor", "type": "accessor", "kind": "accessor"})
    assert control.parse_record(encoded(value), NONCE) == value


@pytest.mark.parametrize("change", ["nonce", "extra", "bool_schema", "version_key", "candidate", "getter", "duplicate", "inconsistent"])
def test_record_strict_schema(change):
    """Reject spoofed envelopes and inconsistent descriptor/type/readiness claims."""
    value = copy.deepcopy(record())
    if change == "nonce":
        value["nonce"] = "b" * 32
    elif change == "extra":
        value["session_initialized"] = True
    elif change == "bool_schema":
        value["schema_version"] = True
    elif change == "version_key":
        value["runtime"]["versions"]["secret"] = "no"
    elif change == "candidate":
        value["binding"]["candidates"]["NodeIKernelWrapperSession"] = False
    elif change == "getter":
        value["binding"]["exports"][0]["kind"] = "accessor"
    elif change == "duplicate":
        value["binding"]["exports"].append(value["binding"]["exports"][0])
    else:
        value["binding"]["load_attempted"] = False
    with pytest.raises(control.runtime.ProbeError):
        control.parse_record(encoded(value), NONCE)


@pytest.mark.parametrize("suffix", [b"extra", b"\n", b"x" * 16384])
def test_record_rejects_extra_and_oversize_output(suffix):
    """One nonce-marked record is the entire allowed output surface."""
    with pytest.raises((control.runtime.ProbeError, ValueError)):
        control.parse_record(encoded(record()) + suffix, NONCE)


def test_mount_gate_refuses_before_any_write(monkeypatch):
    """Even root cannot mount overrides without the verified private PID1 gate."""
    monkeypatch.setattr(control.os, "getpid", lambda: 77)
    monkeypatch.setattr(control.runtime, "namespace_ids", lambda: {"net": "n", "mnt": "m", "pid": "p"})
    monkeypatch.setattr(control.os, "readlink", lambda _path: "i")
    with pytest.raises(control.runtime.ProbeError, match="ephemeral_mount_gate_failed"):
        control.stage_control(b"fixed", b"fixed", "/usr/bin/mount", {}, {})


def test_observer_refuses_options_and_run_as_node():
    """No inspector, arbitrary options, or RUN_AS_NODE can reach the executable."""
    for command, env in ((["/opt/QQ/qq", "--inspect"], {}), (["/opt/QQ/qq"], {"ELECTRON_RUN_AS_NODE": "1"})):
        with pytest.raises(control.runtime.ProbeError, match="ephemeral_invocation_invalid"):
            control.observe_host(command, env, None, None, 30, {}, source=b"x", control=b"x",
                                 mount="mount", parent_ids={}, nonce=NONCE)


def test_shared_callback_full_isolation(monkeypatch, tmp_path):
    """The trusted callback must request every existing shared isolation feature."""
    root = tmp_path / "QQ"
    manifest = root / "resources/app/package.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps(package()), encoding="utf-8")
    monkeypatch.setattr(control.runtime, "validate_root", lambda _root: root)
    calls = {}
    def child_probe(*args, **kwargs):
        """Capture callback gates without executing software in a unit test."""
        calls.update(kwargs)
        return {"status": "test"}
    monkeypatch.setattr(control.runtime, "child_probe", child_probe)
    args = SimpleNamespace(qq_root=str(root), parent_ns="{}", jail=str(tmp_path / "jail"),
                           nonce=NONCE, mount_tool="mount", pivot_tool="pivot_root", umount_tool="umount")
    assert control.child_main(args) == {"status": "test"}
    assert calls["filesystem_mode"] == "pivot-root"
    assert calls["private_display"] and calls["extended_startup"] and calls["private_runtime_services"]
    assert callable(calls["application_observer"])


def test_fixed_js_has_only_builtin_dlopen_and_public_exit():
    """Source review guard disallows loader/proxy imports and native invocation."""
    source = Path(control.SOURCE).read_text(encoding="utf-8")
    assert "process.dlopen(mod, '/opt/QQ/resources/app/wrapper.node')" in source
    assert "require('electron').app.exit(process.exitCode)" in source
    assert "Object.getOwnPropertyDescriptor(mod.exports, name)" in source
    assert "Object.getOwnPropertyNames(mod.exports)" in source
    assert "descriptor.value(" not in source
    assert "mod.exports." not in source
    for banned in ("NapCat", "--no-sandbox", "NODE_OPTIONS", "process.argv", "getSession(", "connect("):
        assert banned not in source


def test_duplicate_json_keys_refused():
    """Duplicate keys must not create a second interpretation of trusted JSON."""
    raw = encoded(record()).replace(b'"ok": true', b'"ok": false, "ok": true')
    with pytest.raises(control.runtime.ProbeError, match="duplicate_json_key"):
        control.parse_record(raw, NONCE)


def test_diagnostic_categories_are_fixed_booleans():
    """Private exception messages never appear in the public report."""
    result = control.diagnostic_categories(io.BytesIO(b"unknown option --secret"),
                                          io.BytesIO(b"Cannot find module private-secret\nEACCES"))
    assert result["stdout"]["categories"]["unknown_arg"]
    assert result["stderr"]["categories"]["cannot_find_module"]
    assert result["stderr"]["categories"]["permission_denied"]
    assert "secret" not in json.dumps(result)


def test_diagnostic_prefix_is_bounded():
    """Only the first 16KiB may influence diagnostic classification."""
    result = control.diagnostic_categories(io.BytesIO(b"x" * control.LIMIT + b"unknown option"), io.BytesIO())
    assert result["stdout"]["prefix_truncated"]
    assert not result["stdout"]["categories"]["unknown_arg"]


def test_readiness_defaults_never_escalate():
    """Binding load is not session, login, transport, or production readiness."""
    value = control.base_report()
    for key in ("session_initialized", "native_login_verified", "safe_to_login", "independent_backend_ready",
                "production_cutover", "native_host_binding_load_verified", "full_sendrecv_verified"):
        assert value[key] is False
