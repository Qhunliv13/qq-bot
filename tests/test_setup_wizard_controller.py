"""Offline full-install wizard lifecycle tests with private disposable state only."""

from __future__ import annotations

from contextlib import contextmanager
import json
import subprocess
import threading

import pytest


_MODEL = {"base_url": "https://provider.invalid/v1", "model": "test-model",
          "api_key": "fictitious-private-model-key"}


@pytest.fixture
def core(tmp_path, monkeypatch):
    """Reuse the real exclusive writer while forbidding external runtime processes."""
    from tools import bootstrap

    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(bootstrap, "REPO_ROOT", repo)
    monkeypatch.delenv("BOT_WEB_PORT", raising=False)
    monkeypatch.delenv("BOT_ONEBOT_PORT", raising=False)

    @contextmanager
    def ports(*args, **kwargs):
        """Avoid reserving real fixed host ports in parallel offline tests."""
        yield {"web_port": 26185, "onebot_port": 26199}

    def forbidden(*args, **kwargs):
        """A controller unit test must never reach Docker, model or QQ subprocesses."""
        raise AssertionError("unexpected real subprocess")

    monkeypatch.setattr(bootstrap, "first_install_ports", ports)
    monkeypatch.setattr(bootstrap.subprocess, "run", forbidden)
    return bootstrap


def _controller(core, tmp_path):
    """Resolve a new external root for one owned, synthetic installation."""
    from tools.setup_wizard import Controller
    return Controller(core, tmp_path / "private state", "test-wizard")


def _save(controller):
    """Create one complete fresh-install input with deliberately fictitious secrets."""
    return controller.action("save", {"model": dict(_MODEL), "owner_qqs": [],
                                      "persona": "自定义人格，保持原样。"})


def _wait(controller):
    """Bound an asynchronous test using worker completion rather than polling sleeps."""
    controller.thread.join(timeout=3)
    assert not controller.thread.is_alive()
    assert controller.snapshot()["busy"] is False


def test_new_session_has_no_side_effects_and_no_claimed_readiness(core, tmp_path):
    """Opening setup must not create state, call a model, or claim QQ readiness."""
    controller = _controller(core, tmp_path)
    value = controller.snapshot()
    assert value["initialized"] is False
    assert value["busy"] is False
    assert value["runtime"] == "unverified"
    assert value["model"] == "unverified"
    assert value["qq"] == "unverified"
    assert not controller.data.exists()


def test_save_uses_shared_state_contract_without_leaking_inputs(core, tmp_path):
    """Wizard state is recoverable by bootstrap and never becomes a second writer."""
    controller = _controller(core, tmp_path)
    assert _save(controller)["ok"] is True
    settings = core.load_install(controller.data, controller.name)
    assert settings["model"] == _MODEL
    assert settings["owner_qqs"] == []
    assert settings["persona"] == "自定义人格，保持原样。"
    assert len(settings["dashboard_password"]) >= 16
    state = controller.snapshot()
    assert state["initialized"] is True
    assert state["dashboard_url"] == "http://127.0.0.1:26185"
    public = json.dumps(state, ensure_ascii=False)
    assert _MODEL["api_key"] not in public
    assert settings["dashboard_password"] not in public
    assert "自定义人格" not in public
    assert state["model"] == "unverified"
    assert state["qq"] == "unverified"


def test_existing_state_is_never_overwritten_by_repeated_save(core, tmp_path):
    """An existing managed install is a management target, not new setup input."""
    controller = _controller(core, tmp_path)
    _save(controller)
    before = {path.name: path.read_bytes() for path in controller.data.iterdir() if path.is_file()}
    from tools.setup_wizard import Controller
    reopened = Controller(core, controller.data, controller.name)
    assert reopened.snapshot()["initialized"] is True
    with pytest.raises(ValueError):
        reopened.action("save", {"model": {**_MODEL, "api_key": "different"},
                                 "owner_qqs": ["123456"], "persona": "different"})
    assert before == {path.name: path.read_bytes() for path in controller.data.iterdir() if path.is_file()}


def test_unmanaged_nonempty_state_is_refused_without_adoption(core, tmp_path):
    """Never infer production data ownership from a user-selected path."""
    data = tmp_path / "foreign state"
    data.mkdir()
    sentinel = data / "existing-private-file.txt"
    sentinel.write_text("private untouched", encoding="utf-8")
    from tools.setup_wizard import Controller
    with pytest.raises(ValueError):
        Controller(core, data, "test-wizard")
    assert sentinel.read_text(encoding="utf-8") == "private untouched"
    assert not (data / core.MARKER).exists()


def test_marker_name_drift_is_refused(core, tmp_path):
    """Even initialized state cannot be silently adopted under another project name."""
    controller = _controller(core, tmp_path)
    _save(controller)
    from tools.setup_wizard import Controller
    with pytest.raises(ValueError):
        Controller(core, controller.data, "foreign-project")


@pytest.mark.parametrize("payload", [
    {"model": {**_MODEL, "api_key": ""}},
    {"model": {**_MODEL, "base_url": "https://provider.invalid/v1/chat/completions"}},
    {"model": _MODEL, "owner_qqs": ["not-a-qq"]},
    {"model": _MODEL, "owner_qqs": "123456"},
    {"model": _MODEL, "persona": 1},
    {"model": _MODEL, "persona": "x" * 16001},
    {"model": {**_MODEL, "api_key": "bad\ud800"}},
    {"model": {**_MODEL, "model": "bad\ud800"}},
    {"model": {**_MODEL, "base_url": "https://provider.invalid/\ud800"}},
    {"model": _MODEL, "persona": "bad\ud800"},
    {"model": _MODEL, "unexpected": True},
])
def test_bad_save_rejects_before_creating_any_state(core, tmp_path, payload):
    """All first-run fields validate atomically rather than leaving partial secrets."""
    controller = _controller(core, tmp_path)
    with pytest.raises(ValueError):
        controller.action("save", payload)
    assert not controller.data.exists()
    assert controller.snapshot()["initialized"] is False


@pytest.mark.parametrize("action,payload", [
    ("credentials", {"confirm": True}), ("start", {}),
    ("stop", {"confirm": True}), ("chat", {"prompt": "Hello", "confirm": True}),
])
def test_privileged_actions_require_a_managed_install(core, tmp_path, action, payload):
    """An empty configuration cannot disclose credentials or operate another runtime."""
    controller = _controller(core, tmp_path)
    with pytest.raises(ValueError):
        controller.action(action, payload)
    assert not controller.data.exists()


def test_credentials_require_explicit_confirmation_and_reveal_only_initial_login(core, tmp_path):
    """Password retrieval is deliberate and never exports an entire settings file."""
    controller = _controller(core, tmp_path)
    _save(controller)
    with pytest.raises(ValueError):
        controller.action("credentials", {})
    value = controller.action("credentials", {"confirm": True})
    settings = core.load_install(controller.data, controller.name)
    assert value["password"] == settings["dashboard_password"]
    assert value["username"] == "astrbot"
    assert _MODEL["api_key"] not in json.dumps(value)
    assert "初始" in value["message"]
    assert "password" not in controller.snapshot()


def test_async_check_is_serialized_read_only_and_never_claims_model_ready(core, tmp_path, monkeypatch):
    """Repeated clicks cannot enqueue duplicate environment/start/model operations."""
    entered = threading.Event()
    release = threading.Event()
    calls = []
    controller = _controller(core, tmp_path)

    def check(data, *, quiet=False):
        """Hold one check until the competing command has been rejected."""
        calls.append((data, quiet))
        entered.set()
        assert release.wait(timeout=3)

    monkeypatch.setattr(core, "check_engine", check)
    try:
        assert controller.action("check", {})["ok"] is True
        assert entered.wait(timeout=3)
        assert controller.snapshot()["busy"] is True
        for action in ("check", "start", "save"):
            with pytest.raises(ValueError):
                controller.action(action, {})
    finally:
        release.set()
        _wait(controller)
    state = controller.snapshot()
    assert state["environment"] == "ready"
    assert state["model"] == state["qq"] == "unverified"
    assert not controller.data.exists()
    assert calls == [(controller.data, True)]


def test_worker_failure_is_sanitized_without_retry(core, tmp_path, monkeypatch, capsys):
    """Native errors may contain secrets; expose only fixed stage-specific advice."""
    controller = _controller(core, tmp_path)
    calls = []

    def check(data, *, quiet=False):
        """Simulate an internal command failure carrying a noncredential sentinel."""
        calls.append(data)
        raise RuntimeError("PRIVATE-EXCEPTION key=fictitious-never-expose")

    monkeypatch.setattr(core, "check_engine", check)
    controller.action("check", {})
    _wait(controller)
    state = controller.snapshot()
    assert state["environment"] == "failed"
    assert calls == [controller.data]
    public = json.dumps(state) + capsys.readouterr().out + capsys.readouterr().err
    assert "PRIVATE-EXCEPTION" not in public
    assert "fictitious-never-expose" not in public


def test_start_reuses_same_instance_bootstrap_then_health(core, tmp_path, monkeypatch):
    """Runtime readiness remains separate from unverified model and QQ connectivity."""
    controller = _controller(core, tmp_path)
    _save(controller)
    calls = []
    monkeypatch.setattr(controller, "_command", lambda action, **kwargs: calls.append(action))
    controller.action("start", {})
    _wait(controller)
    assert calls == ["up", "health"]
    state = controller.snapshot()
    assert state["environment"] == state["runtime"] == "ready"
    assert state["model"] == state["qq"] == "unverified"


@pytest.mark.parametrize("native_mode", [False, True])
def test_stop_is_deliberate_preserves_state_and_uses_shared_command(core, tmp_path, monkeypatch, native_mode):
    """Stopping preserves data; native acceptance must not falsely report process exit."""
    if native_mode:
        monkeypatch.setattr(core, "DEPLOYMENT_MODE", "windows-native", raising=False)
    controller = _controller(core, tmp_path)
    _save(controller)
    before = (controller.data / "settings.json").read_bytes()
    with pytest.raises(ValueError):
        controller.action("stop", {})
    calls = []
    monkeypatch.setattr(controller, "_command", lambda action, **kwargs: calls.append(action))
    controller.action("stop", {"confirm": True})
    _wait(controller)
    assert calls == ["stop"]
    assert before == (controller.data / "settings.json").read_bytes()
    assert controller.snapshot()["runtime"] != "ready"
    if native_mode:
        assert "已接受停止请求" in controller.snapshot()["message"]
        assert "Bot 已停止" not in controller.snapshot()["message"]


def test_model_test_requires_consent_and_ready_runtime_then_runs_once(core, tmp_path, monkeypatch):
    """A charged full-runtime chat is never a silent health check or automatic retry."""
    controller = _controller(core, tmp_path)
    _save(controller)
    with pytest.raises(ValueError):
        controller.action("chat", {"prompt": "Hello", "confirm": True})
    controller.state["runtime"] = "ready"
    with pytest.raises(ValueError):
        controller.action("chat", {"prompt": "Hello"})
    calls = []

    def local(prompt):
        """Use a deterministic local response rather than any alternate model loop."""
        calls.append(prompt)
        return "确定性测试回复"

    monkeypatch.setattr(controller, "_local", local)
    controller.action("chat", {"prompt": "Hello", "confirm": True})
    _wait(controller)
    state = controller.snapshot()
    assert calls == ["Hello"]
    assert state["model"] == "ready"
    assert state["qq"] == "unverified"
    assert state["answer"] == "确定性测试回复"


def test_native_chat_sends_prompt_via_stdin_and_masks_saved_secrets(core, tmp_path, monkeypatch):
    """Model tests reuse bootstrap chat and never expose credentials through argv."""
    controller = _controller(core, tmp_path)
    _save(controller)
    settings = core.load_install(controller.data, controller.name)
    calls = []
    answer = "Hello " + _MODEL["api_key"] + " / " + settings["dashboard_password"]

    def run(command, **kwargs):
        """Return one native-client-shaped response without launching a subprocess."""
        calls.append((command, kwargs))
        output = {"ok": True, "outputs": [{"components": [{"text": answer}]}]}
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(output).encode("utf-8"))

    monkeypatch.setattr(subprocess, "run", run)
    result = controller._local("private prompt")
    command, kwargs = calls[0]
    assert "chat" in command
    assert "--data-dir" in command and str(controller.data) in command
    assert "private prompt" not in command
    assert kwargs["input"] == b"private prompt"
    assert kwargs["shell"] is False
    assert _MODEL["api_key"] not in result
    assert settings["dashboard_password"] not in result
    assert len(calls) == 1
