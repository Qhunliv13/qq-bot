"""Compact fake-only routing contracts reusing existing isolation fixtures."""
import hashlib
import io
import json
import sys
from unittest import mock

import pytest
import test_qq_prepared_stall_integration as integration
from tools import qq_ephemeral_host_entry as shared
from tools import qq_host_dispatch_control as dispatch
from tools import qq_host_dispatch_roles_control as roles_control
from tools import qq_private_window_roles as roles

imports = integration.imports
observer = integration.observer
MODE = "host_dispatch_roles_offline"
HELPERS = ("qq_host_dispatch_control", "qq_native_connect_control",
           "qq_application_observation", "qq_vendor_stall_observation")


def test_fixed_sources_and_cli(tmp_path, monkeypatch):
    """Pin old source hash, fixed mode and exclusive artifact before any invocation."""
    assert shared.fixed_sources(MODE) == shared.fixed_sources("host_dispatch_offline")
    source = shared.fixed_sources(MODE)[shared.MAIN]
    previous = json.loads(dispatch.ARTIFACT.read_text(encoding="utf-8"))
    assert hashlib.sha256(source.read_bytes()).hexdigest() == previous["authored_source_sha256"]
    assert roles_control.ARTIFACT != dispatch.ARTIFACT
    assert roles_control.ARTIFACT.name == "qq-host-dispatch-roles-evidence.json"
    invoke = mock.Mock(return_value={"status": "not_executed"})
    monkeypatch.setattr(shared, "observe", invoke)
    roles_control.observe("unused")
    invoke.assert_called_once_with("unused", execute=False, control_mode=MODE)
    artifact = tmp_path / "existing.json"
    artifact.write_text("preserved", encoding="utf-8")
    monkeypatch.setattr(roles_control, "ARTIFACT", artifact)
    invoke.reset_mock()
    with pytest.raises(FileExistsError):
        roles_control.main(["--qq-root", "unused", "--execute"])
    invoke.assert_not_called()
    assert artifact.read_text(encoding="utf-8") == "preserved"
    with pytest.raises(SystemExit):
        roles_control.main(["--qq-root", "unused", "--control-mode", "wrapper"])
    with pytest.raises(shared.runtime.ProbeError):
        shared.fixed_sources("host_dispatch_roles")


@pytest.mark.parametrize("mode", ["host_dispatch_offline", MODE])
def test_route_same_parser_and_sampler(monkeypatch, observer, mode):
    """Only new mode adds the exact existing callback; no error-text capture added."""
    monkeypatch.setitem(sys.modules, "qq_host_dispatch_control", dispatch)
    waiter, capture = mock.Mock(), mock.Mock()
    monkeypatch.setattr(integration.stall, "observe_wait", waiter)
    monkeypatch.setattr(integration.stall, "capture_categories", capture)
    project = mock.Mock(wraps=dispatch.project_report)
    monkeypatch.setattr(dispatch, "project_report", project)
    report = integration.invoke(mode, io.BytesIO(), io.BytesIO())
    assert report["mode"] == "ephemeral_" + mode
    assert report["private_output_categories"] == {}
    assert report["status"] == "failed" and not report["native_binding_verified"]
    assert report["process_retired"]
    project.assert_called_once_with(report, None, -9)
    capture.assert_not_called()
    kwargs = waiter.call_args.kwargs
    if mode == MODE:
        assert kwargs["window_sampler"] is roles.sample_roles
    else:
        assert "window_sampler" not in kwargs


@pytest.mark.parametrize("mode", ["host_dispatch_offline", MODE])
def test_preimports_and_copydeps(monkeypatch, tmp_path, imports, mode):
    """Reuse actual setup fixtures, checking exact old set plus roles reader only."""
    monkeypatch.setitem(sys.modules, "qq_host_dispatch_control", dispatch)
    monkeypatch.setitem(integration.HELPERS, mode, HELPERS + (("qq_private_window_roles",) if mode == MODE else ()))
    integration.test_helpers_preimported_before_pivot(monkeypatch, tmp_path, imports, mode)
    integration.test_outer_copy_set_matches_preimports(monkeypatch, tmp_path / "outer", mode)


def test_failed_samples_remain_unknown(monkeypatch, observer):
    """A failed bounded sampler cannot prove window absence, dialog state or readiness."""
    monkeypatch.setitem(sys.modules, "qq_host_dispatch_control", dispatch)
    clock = [0.0]

    def sleep(seconds):
        """Advance fake clock, never perform a real native observation."""
        clock[0] += max(seconds, 0.001)

    monkeypatch.setattr(integration.stall.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(integration.stall.time, "sleep", sleep)
    monkeypatch.setattr(integration.stall.os, "sysconf", lambda _name: 100, raising=False)
    monkeypatch.setattr(integration.stall.application, "unprivileged_sample", lambda *_args, **_kwargs: None)
    report = integration.invoke(MODE, io.BytesIO(), io.BytesIO())
    observation = report["stall_observation"]
    assert observation["window_roles_queried"]
    assert len(observation["samples"]) == 30
    assert all(row["windows"] is None and not row["identity_verified"] for row in observation["samples"])
    assert observation["summary"]["classification"] == "insufficient_observation"
    assert not observation["summary"]["root_cause_proven"]
    assert report["completion"] == "watchdog_30s" and report["status"] == "failed"
    assert (integration.stall.MAIN_PID, integration.stall.MAIN_START, integration.stall.WINDOW_SAMPLER) == (None, None, None)


def test_frozen_roles_evidence():
    """Compare the one approved observation to prior evidence without another run."""
    report = json.loads(roles_control.ARTIFACT.read_text(encoding="utf-8"))
    previous = json.loads(dispatch.ARTIFACT.read_text(encoding="utf-8"))
    assert hashlib.sha256(dispatch.ARTIFACT.read_bytes()).hexdigest() == "828b84a651fc0099a98dbce400232fa5f954abac0c57b77d3fede2a1b484571e"
    assert report["authored_source_sha256"] == previous["authored_source_sha256"]
    assert report["host_software_sha256_before"] == report["host_software_sha256_after"] == previous["host_software_sha256_before"]
    assert report["host_software_unchanged"] and all(report["offline_network"].values())
    assert report["completion"] == "watchdog_30s" and report["exit_code"] == -9
    assert report["process_retired"] and report["private_output_categories"] == {}
    progress = report["dispatch_progress"]
    dispatch.parse_progress((json.dumps(progress) + "\n").encode(), progress["nonce"])
    assert progress["flags"] == previous["dispatch_progress"]["flags"]
    assert not progress["observation_complete"]
    rows = report["stall_observation"]["samples"]
    assert len(rows) == 30 and all(row["identity_verified"] for row in rows)
    assert rows[0]["windows"]["main_pid_correlated_windows"] == 0
    assert all(row["windows"]["root_children"] == 3 and row["windows"]["main_pid_correlated_windows"] == 1 for row in rows[1:])
    for row in rows:
        windows = row["windows"]
        assert windows["roles_queried"]
        assert not windows["main_owned_dialog_observed"]
        assert not windows["modal_state_proven"] and not windows["root_cause_proven"]
        for role in windows["roles"]:
            assert role["map_state"] == "unmapped" and role["type_unknown"]
            assert role["class_property_valid"] and role["class_contains_qq"]
            assert not any(role[key] for key in ("type_dialog", "type_normal", "type_utility", "transient_for_present", "transient_for_main_correlated_window", "class_contains_error", "class_contains_dialog"))
            assert not any(role["property_truncated"].values())
    assert not report["native_binding_verified"] and not report["safe_to_login"]
