"""Test observation semantics without launching QQ or touching account state."""

import inspect
import io
from pathlib import Path
from unittest import mock

import pytest

from tools import qq_application_observation as app
from tools import qq_isolated_runtime_probe as runtime


def test_base_never_claims_readiness():
    """Application evidence must not turn into backend/login/transport completion."""
    report = app.base_report()
    assert report["invocation"]["argv"] == ["/opt/QQ/qq"]
    assert report["invocation"]["electron_run_as_node_present"] is False
    for key in ("safe_to_login", "native_binding_verified", "native_login_verified",
                "full_sendrecv_verified", "application_readiness_verified", "independent_backend_ready"):
        assert report[key] is False


def test_shared_callback_requires_full_boundary():
    """The internal app interface must fail before any mutation on weak isolation."""
    with pytest.raises(runtime.ProbeError, match="application_observer_requires_full_private_runtime"):
        runtime.child_probe(Path("/not-used"), Path("/not-used"), "nonce", {}, "/usr/bin/mount",
                            application_observer=app.observe_application)


def test_invalid_invocation_rejected_before_launch():
    """Never accept script, RUN_AS_NODE or a relaxed observation timeout."""
    for command, environment, timeout in (([app.QQ_EXE, "/probe.js"], {}, 30),
                                          ([app.QQ_EXE], {"ELECTRON_RUN_AS_NODE": "1"}, 30),
                                          ([app.QQ_EXE], {}, 31)):
        with pytest.raises(runtime.ProbeError, match="application_invocation_contract_failed"):
            app.observe_application(command, environment, None, None, timeout, {})


def test_merge_baseline_and_pid_attribution_are_distinct():
    """Anonymous root children alone must never count as QQ-owned evidence."""
    observation = {"samples": 0, "unavailable_samples": 0, "baseline_root_children": None,
                   "baseline_qq_pid_properties": None, "maximum_root_children": None,
                   "qq_processes": [], "qq_window_pid_identities": []}
    app.merge_sample(observation, {"processes": [], "windows": {
        "root_children": 2, "qq_pid_properties": []}}, baseline=True)
    app.merge_sample(observation, {"processes": [], "windows": {
        "root_children": 3, "qq_pid_properties": []}})
    assert observation["baseline_root_children"] == 2
    assert observation["maximum_root_children"] == 3
    assert observation["qq_window_pid_identities"] == []


def test_sampler_queries_no_titles_or_commandlines():
    """Only public X PID/tree APIs and bounded proc identity are used."""
    source = inspect.getsource(app.PrivateXIdentity)
    assert "XFetchName" not in source and "_NET_WM_NAME" not in source
    assert "_NET_WM_PID" in source
    assert "cmdline" not in inspect.getsource(app.process_snapshot)


def test_default_validation_does_not_execute():
    """Execution consent remains explicit even for a canonical software root."""
    with mock.patch.object(app.sys, "platform", "linux"), mock.patch.object(
            runtime, "validate_root", return_value=Path("/software/QQ")), mock.patch.object(
            app.subprocess, "run") as run:
        assert app.observe("/software/QQ")["status"] == "not_executed"
    run.assert_not_called()


def test_process_identity_rejects_bad_pid():
    """Never traverse arbitrary proc strings, PID1, or boolean numeric aliases."""
    for value in ("../self", True, 1, -1, 4194304):
        assert app.process_identity(value) is None


def test_watchdog_is_completed_observation_not_failure():
    """A GUI staying alive for the budget must not become nonce/runtime failure."""
    process = mock.Mock(pid=123)
    process.poll.return_value = None
    sample = {"processes": [], "windows": {"root_children": 0, "qq_pid_properties": []}}
    report = {"isolation": {}}
    with mock.patch.object(app, "unprivileged_sample", return_value=sample), mock.patch.object(
            app.subprocess, "Popen", return_value=process), mock.patch.object(
            app.time, "monotonic", side_effect=[0, 31, 31]):
        app.observe_application([app.QQ_EXE], {}, io.BytesIO(), io.BytesIO(), 30, report)
    assert report["completion"] == "watchdog_30s"
    assert report["experiment_completed"] is True
    assert report["status"] == "observed"
    assert report["errors"] == []
    assert report["application_readiness_verified"] is False
    assert report["application_pid"] == 123
    process.kill.assert_called_once()


def test_source_reuses_isolation_and_default_command():
    """Guard against adding a second isolation implementation or script launcher."""
    source = inspect.getsource(app.child_main)
    assert "runtime.child_probe" in source
    assert "application_observer=observe_application" in source
    assert "private_runtime_services=True" in source
    source = inspect.getsource(runtime.child_probe)
    assert 'runtime_env.pop("ELECTRON_RUN_AS_NODE", None)' in source
    assert 'application_observer(["/opt/QQ/qq"]' in source
