"""Exit metadata bounds, old schema compatibility and fixed mode routing."""
import io
import json
from unittest import mock

import pytest
import test_qq_host_resources_control as resource_tests
import test_qq_host_waits_control as wait_tests
from tools import qq_ephemeral_host_entry as shared
from tools import qq_host_exit_control as exits
from tools import qq_vendor_stall_observation as stall
from tools import qq_isolated_runtime_probe as runtime

observer = resource_tests.observer
imports = resource_tests.imports
MODE = "host_dispatch_exit_offline"


@pytest.mark.parametrize("raw,expected", [
    (b"CoreDumping:\t1\n", 1), (b"CoreDumping:\t0\n", 0), (b"Name: secret\n", None),
    (b"CoreDumping: true\n", None), (b"CoreDumping: 01\n", None),
    (b"CoreDumping: 1\nCoreDumping: 0\n", None),
    (b"CoreDumping: invalid\nCoreDumping: 1\n", None),
    (b"CoreDumping: 1\n" + b"x" * 16384, None),
    (b"CoreDumping: 1 secret\n", None), (b"CoreDumping: \xff\n", None),
])
def test_core_projection(raw, expected):
    """Missing, malformed, duplicated and oversized status never become false facts."""
    assert stall.project_core_dumping(raw) == expected


def test_status_read_bound(tmp_path):
    """A full16KiB buffer is rejected without a second or unbounded read."""
    stream = io.BytesIO(b"CoreDumping: 1\n" + b"x" * 32768)
    reader = mock.Mock(wraps=stream)
    with mock.patch.object(type(tmp_path), "open") as opened:
        opened.return_value.__enter__.return_value = reader
        assert stall.read_core_dumping(tmp_path) is None
    reader.read.assert_called_once_with(16384)


@pytest.mark.parametrize("value,expected", [("0", 0), ("5", 5), ("65535", 65535),
    ("65536", None), ("-1", None), ("true", None), ("1.0", None), ("", None)])
def test_field52(value, expected):
    """Field52 tail49 is independent of field38, with old schemas unchanged."""
    tail = ["0"] * 49 + ([value] if value else [])
    tail[0], tail[35] = "S", "31"
    raw = "1 (comm ) tricky) " + " ".join(tail)
    assert "exit_code" not in stall.parse_stat(raw)
    assert stall.parse_stat(raw, exit_status=True)["exit_code"] == expected
    with pytest.raises(ValueError):
        stall.parse_stat(raw, exit_status=1)


def test_aggregate_and_strict_options():
    """Eight distinct bounded statuses plus overflow, and only strict valid opt-in."""
    counts = {"counts": {}, "overflow_count": 0, "unavailable_count": 0}
    for value in range(64):
        stall.aggregate_exit(counts, value)
    stall.aggregate_exit(counts, 0)
    stall.aggregate_exit(counts, None)
    assert counts == {"counts": {str(i): 2 if i == 0 else 1 for i in range(8)},
                      "overflow_count": 56, "unavailable_count": 1}
    for options in ({"exit_status": 1}, {"exit_status": True},
                    {"exit_status": True, "wait_symbols": True},
                    {"exit_status": True, "wait_symbols": True, "detailed_resources": 1}):
        with pytest.raises(runtime.ProbeError):
            stall.observe_wait(None, {}, report={}, **options)


def test_exact_route_and_identity(tmp_path, monkeypatch, imports):
    """Reuse exclusive artifact behavior and identical private identity/source gates."""
    assert shared.fixed_sources(MODE) == shared.fixed_sources(wait_tests.MODE)
    monkeypatch.setattr(resource_tests, "MODE", MODE)
    monkeypatch.setattr(resource_tests, "resources", exits)
    with monkeypatch.context() as isolated:
        resource_tests.test_fixed_route_and_exclusive(tmp_path, isolated)
    resource_tests.test_new_identity_and_copydeps(monkeypatch, tmp_path, imports)


def test_exact_waiter(monkeypatch, observer):
    """New opt-in alone distinguishes the waiter; capture remains exactly identical."""
    import sys
    monkeypatch.setitem(sys.modules, "qq_host_dispatch_control", resource_tests.dispatch)
    waiter, capture = mock.Mock(), mock.Mock()
    monkeypatch.setattr(stall, "observe_wait", waiter)
    monkeypatch.setattr(stall, "capture_categories", capture)
    out, err = io.BytesIO(), io.BytesIO()
    report = resource_tests.integration.invoke(MODE, out, err)
    waiter.assert_called_once_with(observer, {"QQ_WRAPPER_HOST_NONCE": "a" * 32}, timeout=30,
                                   report=report, detailed_resources=True, wait_symbols=True, exit_status=True)
    capture.assert_called_once_with(out, err, report, detailed=True)


def test_frozen_actual_evidence():
    """Validate measured exit evidence and source identity without another execution."""
    if not exits.ARTIFACT.exists():
        pytest.skip("actual evidence requires parent GO")
    current = json.loads(exits.ARTIFACT.read_text(encoding="utf-8"))
    baseline = json.loads(wait_tests.waits.ARTIFACT.read_text(encoding="utf-8"))
    assert current["authored_source_sha256"] == baseline["authored_source_sha256"]
    assert current["synthetic_identity"] == baseline["synthetic_identity"]
    assert current["host_software_sha256_before"] == current["host_software_sha256_after"] == baseline["host_software_sha256_before"]
    samples = current["stall_observation"]["samples"]
    assert len(samples) == 30 and all(item["identity_verified"] for item in samples)
    assert [item["main"]["core_dumping"] for item in samples] == [0] + [1] * 29
    assert [item["main"]["exit_code"] for item in samples] == [0] + [5] * 29
    for item in samples:
        main = item["main"]
        counts = main["task_exit_codes"]
        assert len(counts["counts"]) <= 8
        assert sum(counts["counts"].values()) + counts["overflow_count"] + counts["unavailable_count"] == main["wchan_visibility"]["sampled"]
    assert current["completion"] == "watchdog_30s" and current["exit_code"] == -9
    assert len(exits.ARTIFACT.read_bytes()) < runtime.HELPER_OUTPUT_LIMIT
    assert not current["safe_to_login"] and not current["native_binding_verified"]


def test_full_report_bound():
    """Thirty maximal bounded waits/exit records fit unchanged128KiB report cap."""
    report = json.loads(wait_tests.waits.ARTIFACT.read_text(encoding="utf-8"))
    sample = next(item for item in report["stall_observation"]["samples"] if item["main"])
    main = sample["main"]
    main.update(core_dumping=1, exit_code=65535,
                task_exit_codes={"counts": {str(65535-i): 64 for i in range(8)},
                                 "overflow_count": 64, "unavailable_count": 64})
    main["main_wait"] = {"category": "symbol", "symbol": "z" * 96}
    main["task_waits"] = {"categories": dict.fromkeys(stall.WAIT_CATEGORIES, 64),
                          "symbols": {"symbol:" + str(i) + "z" * 95: 64 for i in range(8)},
                          "overflow_count": 64}
    report["stall_observation"]["samples"] = [sample] * 30
    assert len(json.dumps(report).encode()) < runtime.HELPER_OUTPUT_LIMIT
