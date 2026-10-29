"""Wait format/privacy and exact fixed-mode contracts without launching QQ."""
import io
import json
from unittest import mock

import pytest
import test_qq_host_resources_control as resource_tests
from tools import qq_ephemeral_host_entry as shared
from tools import qq_host_waits_control as waits
from tools import qq_vendor_stall_observation as stall
from tools import qq_isolated_runtime_probe as runtime

observer = resource_tests.observer
imports = resource_tests.imports
MODE = "host_dispatch_waits_offline"


@pytest.mark.parametrize("raw,category,symbol", [
    (b" futex_wait_queue\n", "symbol", "futex_wait_queue"),
    (b"do_freezer_trap", "symbol", "do_freezer_trap"),
    (b"0", "numeric_zero", None), (b"0x0000", "numeric_zero", None),
    (b"0xffffffff81234567", "numeric_nonzero", None),
    (b"ffffffff", "numeric_nonzero", None), (b"00000000", "numeric_zero", None),
    (b"12345678", "numeric_nonzero", None), (b" \n", "empty", None),
    (b"symbol+0x12", "invalid_shape", None), (b"secret/path", "invalid_shape", None),
    (b"x\x00", "invalid_shape", None), (b"\xff", "invalid_shape", None),
    (b"z" * 97, "invalid_shape", None), (b"z" * 96, "symbol", "z" * 96),
])
def test_projection(raw, category, symbol):
    """Only a whole bounded ASCII symbol survives; addresses and arbitrary text do not."""
    expected = {"category": category}
    if symbol is not None:
        expected["symbol"] = symbol
    assert stall.project_wait(raw) == expected


def test_first_eight_symbols_and_overflow():
    """Bound retained first-observed keys while counting subsequent observations."""
    counts = {"categories": dict.fromkeys(stall.WAIT_CATEGORIES, 0), "symbols": {}, "overflow_count": 0}
    for index in range(64):
        stall.aggregate_wait(counts, {"category": "symbol", "symbol": "wait_" + str(index)})
    stall.aggregate_wait(counts, {"category": "symbol", "symbol": "wait_0"})
    assert len(counts["symbols"]) == 8 and counts["overflow_count"] == 56
    assert counts["symbols"]["symbol:wait_0"] == 2
    assert counts["categories"]["symbol"] == 65


def test_read_bound_and_failure(tmp_path, monkeypatch):
    """Read no more than 128 bytes and never expose exception text."""
    path = tmp_path / "wchan"
    path.write_bytes(b"z" * 128)
    assert stall.read_wait(path) == {"category": "invalid_shape"}
    monkeypatch.setattr(type(path), "open", mock.Mock(side_effect=PermissionError("hidden")))
    assert stall.read_wait(path) == {"category": "denied"}


def test_route_exclusive_and_sources(tmp_path, monkeypatch):
    """The fixed source map and exclusive artifact behavior match prior resources."""
    assert shared.fixed_sources(MODE) == shared.fixed_sources(resource_tests.MODE)
    monkeypatch.setattr(resource_tests, "MODE", MODE)
    monkeypatch.setattr(resource_tests, "resources", waits)
    resource_tests.test_fixed_route_and_exclusive(tmp_path, monkeypatch)


def test_exact_waiter_capture(monkeypatch, observer):
    """Only the new fixed mode explicitly opts into symbol sampling."""
    import sys
    monkeypatch.setitem(sys.modules, "qq_host_dispatch_control", resource_tests.dispatch)
    waiter, capture = mock.Mock(), mock.Mock()
    monkeypatch.setattr(stall, "observe_wait", waiter)
    monkeypatch.setattr(stall, "capture_categories", capture)
    out, err = io.BytesIO(), io.BytesIO()
    report = resource_tests.integration.invoke(MODE, out, err)
    waiter.assert_called_once_with(observer, {"QQ_WRAPPER_HOST_NONCE": "a" * 32}, timeout=30,
                                   report=report, detailed_resources=True, wait_symbols=True)
    capture.assert_called_once_with(out, err, report, detailed=True)
    assert report["private_output_categories"] == {}


def test_identity_and_copydeps(monkeypatch, tmp_path, imports):
    """Retain exact private identity gates and the same preimported helpers."""
    monkeypatch.setattr(resource_tests, "MODE", MODE)
    resource_tests.test_new_identity_and_copydeps(monkeypatch, tmp_path, imports)


def test_strict_opt_in():
    """Reject truthy switches and symbol mode without resource-detail opt-in."""
    for options in ({"wait_symbols": 1}, {"wait_symbols": True}):
        with pytest.raises(runtime.ProbeError):
            stall.observe_wait(None, {}, report={}, **options)


def test_frozen_actual_evidence():
    """Check identity/source invariants without rerunning or inventing observations."""
    if not waits.ARTIFACT.exists():
        pytest.skip("actual evidence requires parent GO")
    current = json.loads(waits.ARTIFACT.read_text(encoding="utf-8"))
    baseline = json.loads(resource_tests.resources.ARTIFACT.read_text(encoding="utf-8"))
    assert current["authored_source_sha256"] == baseline["authored_source_sha256"]
    assert current["synthetic_identity"] == baseline["synthetic_identity"]
    assert current["host_software_sha256_before"] == current["host_software_sha256_after"] == baseline["host_software_sha256_before"]
    assert len(json.dumps(current).encode()) < runtime.HELPER_OUTPUT_LIMIT
    for sample in current["stall_observation"]["samples"]:
        assert sample["identity_verified"]
        main = sample["main"]
        assert main["main_wait"]["category"] in stall.WAIT_CATEGORIES
        assert "threads" not in main
        assert len(main["task_waits"]["symbols"]) <= 8
        assert sum(main["task_waits"]["categories"].values()) == main["wchan_visibility"]["sampled"]
    assert not current["safe_to_login"] and not current["native_binding_verified"]


def test_serialized_worst_case():
    """Thirty maximal records fit unchanged 128KiB helper cap including metadata."""
    baseline = json.loads(resource_tests.resources.ARTIFACT.read_text(encoding="utf-8"))
    for sample in baseline["stall_observation"]["samples"]:
        main = sample["main"]
        main.pop("threads")
        main["thread_summary"] = {"states": dict.fromkeys("RSDZTtXxKWPIN", 64),
                                  "wait_categories": dict.fromkeys(("unknown", "futex", "poll", "wait", "read", "other"), 64)}
        main["main_wait"] = {"category": "symbol", "symbol": "z" * 96}
        main["task_waits"] = {"categories": dict.fromkeys(stall.WAIT_CATEGORIES, 64),
                              "symbols": {"symbol:" + str(i) + "z" * 95: 64 for i in range(8)},
                              "overflow_count": 64}
    assert len(json.dumps(baseline).encode()) < runtime.HELPER_OUTPUT_LIMIT
