"""Focused fixed resource-sampler contracts, without launching QQ."""
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
import test_qq_host_identity_control as identity_tests
import test_qq_prepared_stall_integration as integration
from tools import qq_ephemeral_host_entry as shared
from tools import qq_host_dispatch_control as dispatch
from tools import qq_host_resources_control as resources
from tools import qq_vendor_stall_observation as stall
from tools import qq_isolated_runtime_probe as runtime

imports = integration.imports
observer = integration.observer
MODE = "host_dispatch_resources_offline"


def test_fixed_route_and_exclusive(tmp_path, monkeypatch):
    """Reuse exact identity source and reject evidence overwrite before any execution."""
    assert shared.fixed_sources(MODE) == shared.fixed_sources(identity_tests.MODE)
    invoke = mock.Mock(return_value={"status": "not_executed"})
    monkeypatch.setattr(shared, "observe", invoke)
    resources.observe("unused")
    invoke.assert_called_once_with("unused", execute=False, control_mode=MODE)
    artifact = tmp_path / "evidence.json"
    artifact.write_text("preserved", encoding="utf-8")
    monkeypatch.setattr(resources, "ARTIFACT", artifact)
    invoke.reset_mock()
    with pytest.raises(FileExistsError):
        resources.main(["--qq-root", "unused", "--execute"])
    invoke.assert_not_called()
    assert artifact.read_text() == "preserved"


def test_exact_waiter_capture_parser(monkeypatch, observer):
    """Only the new mode adds the strict resource boolean to the same internal waiter."""
    import sys
    monkeypatch.setitem(sys.modules, "qq_host_dispatch_control", dispatch)
    waiter, capture = mock.Mock(), mock.Mock()
    monkeypatch.setattr(stall, "observe_wait", waiter)
    monkeypatch.setattr(stall, "capture_categories", capture)
    out, err = io.BytesIO(), io.BytesIO()
    report = integration.invoke(MODE, out, err)
    waiter.assert_called_once_with(observer, {"QQ_WRAPPER_HOST_NONCE": "a" * 32}, timeout=30,
                                   report=report, detailed_resources=True)
    capture.assert_called_once_with(out, err, report, detailed=True)
    assert report["private_output_categories"] == {}
    assert shared.subprocess.Popen.call_args.args == (["/opt/QQ/qq"],)


def test_new_identity_and_copydeps(monkeypatch, tmp_path, imports):
    """The resources mode retains identical synthetic-record gates and staged helpers."""
    monkeypatch.setattr(identity_tests, "MODE", MODE)
    identity_tests.test_child_identity_only_new_fixed_mode(monkeypatch, tmp_path, imports, MODE)
    identity_tests.test_exact_identity_gate(MODE)
    identity_tests.test_same_preimports_and_copydeps(monkeypatch, tmp_path / "deps", imports)


def test_fd_numeric_and_denied(tmp_path, monkeypatch):
    """Count names, not links/targets; a denied directory is unknown, never zero."""
    (tmp_path / "fd").mkdir()
    for name in ("0", "9", "127", "not-numeric"):
        (tmp_path / "fd" / name).touch()
    assert stall.fd_resources(tmp_path) == {"visibility": "readable", "count": 3, "maxfd": 127, "truncated": False}
    monkeypatch.setattr(stall.os, "scandir", mock.Mock(side_effect=PermissionError("secret")))
    assert stall.fd_resources(tmp_path) == {"visibility": "denied", "count": None, "maxfd": None, "truncated": False}


def test_fd_enumeration_bound(monkeypatch):
    """Inspect only 256 entries and one sentinel even if the directory is much larger."""
    consumed = []
    def entries():
        """Yield a deliberately large synthetic directory to measure consumption."""
        for index in range(1000):
            consumed.append(index)
            yield SimpleNamespace(name=str(index))
    manager = mock.MagicMock()
    manager.__enter__.return_value = entries()
    monkeypatch.setattr(stall.os, "scandir", lambda _path: manager)
    assert stall.fd_resources(Path("unused")) == {"visibility": "readable", "count": 256, "maxfd": 255, "truncated": True}
    assert len(consumed) == 257


def test_wchan_failures_retained(tmp_path, monkeypatch):
    """Zero/empty/denied remain distinct, including a denied read followed by failed stat."""
    task = tmp_path / "task"
    task.mkdir()
    for name in ("1", "2", "3", "4", "5"):
        (task / name).mkdir()
    stat = "1 (hidden name) S " + " ".join(["0"] * 18 + ["42"])
    def read(path, limit=4096):
        """Supply bounded pseudo-file facts without accessing real proc."""
        if path.name == "wchan":
            if path.parent.name in ("3", "4"):
                raise PermissionError("must not leak")
            return {"1": "0", "2": "", "5": "futex_wait"}[path.parent.name]
        if path.parent.name == "4":
            raise OSError("must not leak")
        return stat
    monkeypatch.setattr(stall, "bounded_text", read)
    threads, counts, readable = stall.resource_threads(tmp_path)
    assert readable and len(threads) == 4
    assert counts == {"sampled": 5, "wchan_zero": 1, "wchan_empty": 1, "wchan_read_denied": 2,
                      "wchan_unavailable": 0, "wchan_nonzero": 1, "stat_unavailable": 1}
    assert {tuple(sorted(item)) for item in threads} == {("state", "wait_category")}
    assert any(item["wait_category"] == "unknown" for item in threads)
    assert stall.coarse_wchan("0") == stall.coarse_wchan("") == "other"


def test_aggregate_report_bound():
    """Worst fixed thread count plus aggregate resources stays within the unchanged cap."""
    main = {"threads": [{"state": "S", "wait_category": "unknown"}] * 64,
            "fd": {"visibility": "unavailable", "count": None, "maxfd": None, "truncated": False},
            "wchan_visibility": dict.fromkeys(("sampled", "wchan_zero", "wchan_empty", "wchan_read_denied",
                                               "wchan_unavailable", "wchan_nonzero", "stat_unavailable"), 64),
            "limits": {"open_files": {"soft": 128, "hard": 128}}}
    # Leave 20KiB for fixed report metadata and remaining numeric main fields.
    assert len(json.dumps([{"main": main}] * 30).encode()) + 20 * 1024 < runtime.HELPER_OUTPUT_LIMIT


def test_frozen_actual_evidence():
    """Validate the single real artifact without manufacturing or rerunning observations."""
    artifact = resources.ARTIFACT
    if not artifact.exists():
        pytest.skip("actual evidence requires parent GO")
    current = json.loads(artifact.read_text(encoding="utf-8"))
    baseline = json.loads(artifact.with_name("qq-host-identity-evidence.json").read_text(encoding="utf-8"))
    assert current["authored_source_sha256"] == baseline["authored_source_sha256"]
    assert current["synthetic_identity"] == baseline["synthetic_identity"]
    assert current["host_software_sha256_before"] == current["host_software_sha256_after"] == baseline["host_software_sha256_before"]
    assert len(json.dumps(current).encode()) < runtime.HELPER_OUTPUT_LIMIT
    samples = current["stall_observation"]["samples"]
    assert len(samples) == 30 and all(item["identity_verified"] for item in samples)
    assert all(item["main"]["fd"]["visibility"] == "readable" and not item["main"]["fd"]["truncated"] for item in samples)
    assert [item["main"]["fd"]["count"] for item in samples] == [4] + [53] * 29
    assert [item["main"]["fd"]["maxfd"] for item in samples] == [3] + [52] * 29
    assert all(item["main"]["limits"]["open_files"] == {"soft": 128, "hard": 128} for item in samples)
    assert sum(item["main"]["wchan_visibility"]["wchan_nonzero"] for item in samples) == 687
    assert sum(item["main"]["wchan_visibility"]["wchan_zero"] for item in samples) == 10
    assert not current["stall_observation"]["summary"]["root_cause_proven"]
    assert not current["safe_to_login"] and not current["native_binding_verified"]


def test_strict_bool():
    """Do not turn the internal detail switch into truthy configuration injection."""
    with pytest.raises(runtime.ProbeError):
        stall.observe_wait(None, {}, report={}, detailed_resources=1)
