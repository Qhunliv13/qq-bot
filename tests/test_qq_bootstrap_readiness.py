"""Offline bootstrap gate fixtures; never run QQ, Electron, Node or native exports."""

import json
import struct
from unittest.mock import Mock

import pytest

from tools import qq_isolated_runtime_probe as probe


def make_software(tmp_path, payload=b"\x9c\x60\x71\xd9", *, main="./application.asar/app_launcher/index.js", entry=None):
    """Build a tiny synthetic ASAR and inert ELF; avoid any real client runtime."""
    root = tmp_path / "QQ"
    app = root / "resources" / "app"
    app.mkdir(parents=True)
    (root / "qq").write_bytes(b"\x7fELF" + b"\0" * 60)
    (root / "qq").chmod(0o755)
    (app / "package.json").write_text(json.dumps({"name": "QQ", "version": "3.2.34-53644",
                                                   "main": main}), encoding="utf-8")
    entry = {"size": len(payload), "offset": "0"} if entry is None else entry
    header = json.dumps({"files": {"app_launcher": {"files": {"index.js": entry}}}}).encode()
    padding = bytes((-len(header)) % 4)
    prefix = struct.pack("<4I", 4, 8 + len(header) + len(padding),
                         4 + len(header) + len(padding), len(header))
    (app / "application.asar").write_bytes(prefix + header + padding + payload)
    return root.resolve()


def test_binary_entry_is_not_system_node_source(tmp_path, monkeypatch, capsys):
    """A real bounded entry read distinguishes opaque bytes from plain JS."""
    root = make_software(tmp_path)
    never = Mock(side_effect=AssertionError("runtime must not start"))
    monkeypatch.setattr(probe.subprocess, "run", never)
    assert probe.main(["--qq-root", str(root), "--bootstrap-readiness"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["entry_format"] == "not_utf8_javascript_source"
    assert report["entry_size"] == 4
    assert len(report["entry_sha256"]) == 64
    assert report["status"] == "observed_static"
    for flag in ("runtime_executed", "runtime_versions_verified", "native_binding_verified",
                 "independent_backend_ready", "safe_to_login", "production_cutover_authorized",
                 "system_node_direct_entry_supported", "electron_bootstrap_verified"):
        assert report[flag] is False
    never.assert_not_called()


def test_utf8_entry_still_does_not_assert_node_support(tmp_path):
    """Text may be JS-looking but cannot prove supported APIs or executable syntax."""
    root = make_software(tmp_path, b"console.log('test')")
    result = probe.inspect_bootstrap(root)
    assert result["entry_format"] == "utf8_text_syntax_unverified"
    assert result["system_node_direct_entry_supported"] is False


@pytest.mark.parametrize("change", ["wrong_main", "link", "unpacked", "oversize", "missing_archive", "symlink_archive"])
def test_missing_or_unsafe_package_entries_fail_closed(tmp_path, change):
    """Only the exact declared packed entry may be inspected, without symlink reads."""
    root = make_software(tmp_path, main="./elsewhere.js" if change == "wrong_main"
                         else "./application.asar/app_launcher/index.js",
                         entry={"link": "private"} if change == "link" else
                         ({"size": 4, "unpacked": True} if change == "unpacked" else
                          ({"size": probe.BOOTSTRAP_ENTRY_LIMIT + 1, "offset": "0"}
                           if change == "oversize" else None)))
    archive = root / "resources" / "app" / "application.asar"
    if change in ("missing_archive", "symlink_archive"):
        archive.unlink()
    if change == "symlink_archive":
        try:
            archive.symlink_to(root / "qq")
        except OSError as exc:
            if getattr(exc, "winerror", None) != 1314:
                raise
            pytest.skip("Windows symlink privilege unavailable")
    report = probe.inspect_bootstrap(root)
    assert report["status"] == "refused"
    assert report["errors"] == ["bootstrap_static_inspection_failed"]
    assert report["entry_sha256"] is None
    assert report["safe_to_login"] is False


@pytest.mark.parametrize("extras", [["--execute"], ["--private-display"],
                                    ["--filesystem-mode", "pivot-root"],
                                    ["--native-load-control"], ["--userns-control"]])
def test_offline_mode_rejects_runtime_options_before_read(monkeypatch, extras):
    """Do not let execution consent, GUI or native controls expand static scope."""
    read = Mock(side_effect=AssertionError("must refuse before reading"))
    monkeypatch.setattr(probe, "inspect_bootstrap", read)
    with pytest.raises(SystemExit) as caught:
        probe.main(["--qq-root", "/unused", "--bootstrap-readiness", *extras])
    assert caught.value.code == 2
    read.assert_not_called()
