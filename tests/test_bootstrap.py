"""Fresh bootstrap tests use private temporary state and mocked Docker only."""

import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess

import pytest


@pytest.fixture
def bootstrap(monkeypatch, tmp_path):
    """Import standalone CLI without touching a host runtime or user home."""
    spec = importlib.util.spec_from_file_location("bootstrap", Path(__file__).parents[1] / "tools" / "bootstrap.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(module, "REPO_ROOT", repo)
    monkeypatch.setenv("TEST_BOOTSTRAP_KEY", "secret-only-in-private-test-settings")
    monkeypatch.delenv("BOT_WEB_PORT", raising=False)
    monkeypatch.delenv("BOT_ONEBOT_PORT", raising=False)
    return module


def init_args(data, *extra):
    """Keep fixture model inputs explicit, fictitious and outside command secrets."""
    return ["init", "--data-dir", str(data), "--non-interactive", "--model-base",
            "https://example.invalid/v1", "--model", "user-model", "--api-key-env", "TEST_BOOTSTRAP_KEY", *extra]


def prepare_runtime(bootstrap):
    """Create dummy bundled paths so doctor remains entirely offline."""
    for relative in ("deploy/compose.yaml", "deploy/Dockerfile", "vendor/astrbot/LICENSE"):
        path = bootstrap.REPO_ROOT / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test fixture\n", encoding="utf-8")


def test_init_private_external_config_and_idempotence(bootstrap, tmp_path, capsys):
    """A rerun preserves every generated secret and runtime file byte-for-byte."""
    data = tmp_path / "private runtime"
    assert bootstrap.main(init_args(data, "--owner-qq", "123456", "--owner-qq", "123456")) == 0
    settings_path = data / "settings.json"
    original = settings_path.read_bytes()
    settings = json.loads(original)
    assert settings["model"] == {"base_url": "https://example.invalid/v1", "model": "user-model",
                                 "api_key": "secret-only-in-private-test-settings"}
    assert settings["owner_qqs"] == ["123456"]
    assert len(settings["dashboard_password"]) >= 24
    assert settings["persona"] == ""
    assert {path.name for path in data.iterdir()} == {
        "settings.json", "compose.env", bootstrap.MARKER, "astrbot", "higan", "config"}
    assert bootstrap.main(["init", "--data-dir", str(data), "--non-interactive"]) == 0
    assert settings_path.read_bytes() == original
    assert settings["model"]["api_key"] not in capsys.readouterr().out
    if os.name != "nt":
        assert settings_path.stat().st_mode & 0o777 == 0o600
        assert data.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("relative", ["", "data", "../"])
def test_repo_or_ancestor_state_refused(bootstrap, relative):
    """Repository contents and ancestors can never become runtime data mounts."""
    data = bootstrap.REPO_ROOT / relative
    assert bootstrap.main(init_args(data)) == 2
    assert not (data / "settings.json").exists()


def test_existing_unmanaged_data_untouched(bootstrap, tmp_path):
    """Legacy production directories are refused rather than migrated or adopted."""
    data = tmp_path / "legacy-higan"
    data.mkdir()
    original = data / "existing.sqlite"
    original.write_bytes(b"preserve-production")
    assert bootstrap.main(init_args(data)) == 2
    assert list(data.iterdir()) == [original]
    assert original.read_bytes() == b"preserve-production"


@pytest.mark.parametrize("flags", [
    ["--model-base", "https://user:secret@example.invalid/v1"],
    ["--model-base", "https://example.invalid/v1?token=secret"],
    ["--model-base", "file:///tmp/model"],
    ["--model-base", "https://example.invalid:bad/v1"],
    ["--model", "model\nINJECT=1"],
    ["--owner-qq", "１２３４５６"],
    ["--owner-qq", "12345,67890"],
    ["--runtime-name", "../production"],
    ["--api-key-env", "MISSING_TEST_KEY"],
])
def test_bad_inputs_fail_before_writes(bootstrap, tmp_path, flags):
    """No malformed settings or credentials are persisted even on partial input."""
    data = tmp_path / "fresh"
    assert bootstrap.main(init_args(data, *flags)) == 2
    assert not data.exists()


def test_missing_noninteractive_model_fails(bootstrap, tmp_path):
    """Noninteractive mode never claims a placeholder model is usable."""
    data = tmp_path / "fresh"
    assert bootstrap.main(["init", "--data-dir", str(data), "--non-interactive"]) == 2
    assert not data.exists()


def test_persona_file_utf8_and_no_hardcoded_owner(bootstrap, tmp_path):
    """Persona is supplied by the installer; unspecified owners remain empty."""
    persona = tmp_path / "persona.txt"
    persona.write_text("用户自己的角色设定", encoding="utf-8")
    data = tmp_path / "fresh"
    assert bootstrap.main(init_args(data, "--persona-file", str(persona))) == 0
    settings = json.loads((data / "settings.json").read_text(encoding="utf-8"))
    assert settings["persona"] == "用户自己的角色设定"
    assert settings["owner_qqs"] == []


def test_compose_argv_and_environment_are_explicit(bootstrap, tmp_path, monkeypatch):
    """Shell metacharacters and inherited data roots cannot alter execution."""
    data = (tmp_path / "data with spaces").resolve()
    command = bootstrap.compose_command(data, "custom-project", "up", "-d", "--build")
    assert command == ["docker", "compose", "-p", "custom-project", "-f",
                       str(bootstrap.REPO_ROOT / "deploy/compose.yaml"), "--env-file",
                       str(data / "compose.env"), "up", "-d", "--build"]
    monkeypatch.setenv("BOT_DATA_DIR", "/root/higan")
    assert bootstrap.docker_environment(data)["BOT_DATA_DIR"] == data.as_posix()
    assert bootstrap.runtime_name(None, data) != bootstrap.runtime_name(None, tmp_path / "other")


def test_runtime_name_drift_refused(bootstrap, tmp_path):
    """Changing a project name cannot silently adopt initialized state."""
    data = tmp_path / "fresh"
    assert bootstrap.main(init_args(data)) == 0
    assert bootstrap.main(["status", "--data-dir", str(data), "--runtime-name", "other-project"]) == 2


def test_modified_compose_env_refused(bootstrap, tmp_path):
    """A copied env file must not redirect fresh tooling to existing production."""
    data = tmp_path / "fresh"
    assert bootstrap.main(init_args(data)) == 0
    (data / "compose.env").write_text("BOT_DATA_DIR=/root/higan\n", encoding="utf-8")
    assert bootstrap.main(["status", "--data-dir", str(data)]) == 2


def test_doctor_checks_only_readonly_commands(bootstrap, tmp_path, monkeypatch):
    """Doctor never starts containers or makes model/QQ network requests."""
    data = tmp_path / "fresh"
    assert bootstrap.main(init_args(data)) == 0
    prepare_runtime(bootstrap)
    calls = []
    monkeypatch.setattr(bootstrap.shutil, "which", lambda executable: "/usr/bin/docker")
    monkeypatch.setattr(bootstrap, "run_docker", lambda command, data: calls.append(command))
    assert bootstrap.main(["doctor", "--data-dir", str(data)]) == 0
    assert calls[:2] == [["docker", "compose", "version"], ["docker", "info", "--format", "{{.ServerVersion}}"]]
    assert calls[-1][-2:] == ["config", "--quiet"]
    assert not any("up" in command or "stop" in command for command in calls)


@pytest.mark.parametrize("labels,allowed", [("", True), ("\n", False), ("different\n", False), ("MATCH", True)])
def test_existing_project_ownership_guard(bootstrap, tmp_path, monkeypatch, labels, allowed):
    """Only this exact external state can own preexisting Compose containers."""
    data = tmp_path / "fresh"
    stdout = data.as_posix() + "\n" if labels == "MATCH" else labels
    monkeypatch.setattr(bootstrap.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout=stdout))
    if allowed:
        bootstrap.check_project_ownership(data, "existing-project")
    else:
        with pytest.raises(ValueError, match="already used"):
            bootstrap.check_project_ownership(data, "existing-project")


def test_one_command_up_initializes_before_docker(bootstrap, tmp_path, monkeypatch):
    """First-run up shares init's real writer instead of requiring two commands."""
    data = tmp_path / "fresh"
    calls = []
    def preflight(*args):
        """Ensure private settings exist before any Docker capability is checked."""
        assert (data / "settings.json").is_file()
        calls.append("doctor")
    monkeypatch.setattr(bootstrap, "doctor", preflight)
    monkeypatch.setattr(bootstrap, "check_project_ownership", lambda *args: calls.append("ownership"))
    monkeypatch.setattr(bootstrap, "run_docker", lambda command, data: calls.append(command))
    args = init_args(data)
    args[0] = "up"
    assert bootstrap.main(args) == 0
    assert calls[:2] == ["doctor", "ownership"]
    assert calls[2][-6:] == ["up", "-d", "--build", "--wait", "--wait-timeout", "300"]


def test_unconfigured_noninteractive_up_never_touches_docker(bootstrap, tmp_path, monkeypatch):
    """Missing real model credentials fail before Docker or filesystem mutation."""
    data = tmp_path / "fresh"
    calls = []
    monkeypatch.setattr(bootstrap, "doctor", lambda *args: calls.append(args))
    assert bootstrap.main(["up", "--data-dir", str(data), "--non-interactive"]) == 2
    assert calls == []
    assert not data.exists()


def test_up_preflight_then_build_only_owned_project(bootstrap, tmp_path, monkeypatch):
    """Up checks ownership after doctor and never executes stop/down/pull/remove."""
    data = tmp_path / "fresh"
    assert bootstrap.main(init_args(data)) == 0
    calls = []
    monkeypatch.setattr(bootstrap, "doctor", lambda *args: calls.append("doctor"))
    monkeypatch.setattr(bootstrap, "check_project_ownership", lambda *args: calls.append("ownership"))
    monkeypatch.setattr(bootstrap, "run_docker", lambda command, data: calls.append(command))
    assert bootstrap.main(["up", "--data-dir", str(data)]) == 0
    assert calls[:2] == ["doctor", "ownership"]
    assert calls[2][-6:] == ["up", "-d", "--build", "--wait", "--wait-timeout", "300"]


def test_up_ownership_conflict_never_builds(bootstrap, tmp_path, monkeypatch):
    """A conflicting live project is an explicit failure, never an adoption."""
    data = tmp_path / "fresh"
    assert bootstrap.main(init_args(data)) == 0
    calls = []
    def conflict(*args):
        """Represent a project already owned by a separate administrator."""
        raise ValueError("runtime name already used")
    monkeypatch.setattr(bootstrap, "doctor", lambda *args: None)
    monkeypatch.setattr(bootstrap, "check_project_ownership", conflict)
    monkeypatch.setattr(bootstrap, "run_docker", lambda *args: calls.append(args))
    assert bootstrap.main(["up", "--data-dir", str(data)]) == 2
    assert calls == []


@pytest.mark.parametrize("verb,tail", [("status", ["ps"]), ("logs", ["logs", "--tail", "100", "--follow"])])
def test_readonly_commands_remain_project_scoped(bootstrap, tmp_path, monkeypatch, verb, tail):
    """Status and logs cannot accidentally enumerate or mutate other projects."""
    data = tmp_path / "fresh"
    assert bootstrap.main(init_args(data, "--runtime-name", "chosen-runtime")) == 0
    calls = []
    monkeypatch.setattr(bootstrap, "run_docker", lambda command, data: calls.append(command))
    monkeypatch.setattr(bootstrap, "check_project_ownership", lambda *args: calls.append("ownership"))
    assert bootstrap.main([verb, "--data-dir", str(data), "--runtime-name", "chosen-runtime"]) == 0
    assert calls[0] == "ownership"
    assert calls[1][2:4] == ["-p", "chosen-runtime"]
    assert calls[1][-len(tail):] == tail


@pytest.mark.parametrize("verb,flags,tail", [
    ("health", [], ["--health"]),
    ("chat", ["--prompt", "请检查状态; no shell"], ["--prompt", "请检查状态; no shell"]),
])
def test_same_instance_client_entrypoints(bootstrap, tmp_path, monkeypatch, verb, flags, tail):
    """Health/chat reuse the container's root-local client after ownership checks."""
    data = tmp_path / "fresh"
    assert bootstrap.main(init_args(data)) == 0
    calls = []
    monkeypatch.setattr(bootstrap, "check_project_ownership", lambda *args: calls.append("ownership"))
    monkeypatch.setattr(bootstrap, "run_docker", lambda command, data: calls.append(command))
    assert bootstrap.main([verb, "--data-dir", str(data), *flags]) == 0
    assert calls[0] == "ownership"
    assert calls[1][-len(tail)-5:-len(tail)] == ["exec", "-T", "bot", "python", "/opt/qhunliv/local_runtime_client.py"]
    assert calls[1][-len(tail):] == tail


def test_chat_reads_stdin_without_copying_conversation(bootstrap, tmp_path, monkeypatch):
    """Piped text is one request to existing runtime, not an alternate chat engine."""
    data = tmp_path / "fresh"
    assert bootstrap.main(init_args(data)) == 0
    monkeypatch.setattr(bootstrap.sys, "stdin", io.StringIO("用户请求\n第二行"))
    monkeypatch.setattr(bootstrap, "check_project_ownership", lambda *args: None)
    calls = []
    monkeypatch.setattr(bootstrap, "run_docker", lambda command, data: calls.append(command))
    assert bootstrap.main(["chat", "--data-dir", str(data)]) == 0
    assert calls[0][-2:] == ["--prompt", "用户请求\n第二行"]


def test_empty_chat_prompt_fails_before_docker(bootstrap, tmp_path, monkeypatch):
    """Whitespace never initiates an unintended model turn or Docker command."""
    data = tmp_path / "fresh"
    assert bootstrap.main(init_args(data)) == 0
    calls = []
    monkeypatch.setattr(bootstrap, "run_docker", lambda *args: calls.append(args))
    assert bootstrap.main(["chat", "--data-dir", str(data), "--prompt", "  "]) == 2
    assert calls == []


def test_prompt_only_supported_by_chat(bootstrap):
    """Other commands cannot silently ignore a user's requested conversation."""
    with pytest.raises(SystemExit) as error:
        bootstrap.parse_args(["health", "--prompt", "not a health check"])
    assert error.value.code == 2


def test_cli_failure_redacts_subprocess_output(bootstrap, tmp_path, monkeypatch, capsys):
    """A failed Docker command cannot leak stderr credentials through our errors."""
    data = tmp_path / "fresh"
    assert bootstrap.main(init_args(data)) == 0
    def fail(*args):
        """Simulate Docker errors containing confidential diagnostic output."""
        raise subprocess.CalledProcessError(1, ["docker"], stderr="secret-do-not-print")
    monkeypatch.setattr(bootstrap, "run_docker", fail)
    monkeypatch.setattr(bootstrap, "check_project_ownership", lambda *args: None)
    assert bootstrap.main(["status", "--data-dir", str(data)]) == 2
    assert "secret-do-not-print" not in capsys.readouterr().err


def test_global_flags_before_command(bootstrap, tmp_path):
    """The compact parser accepts shared options before or after the verb."""
    args = bootstrap.parse_args(["--data-dir", str(tmp_path), "status"])
    assert args.command == "status"
    assert args.data_dir == str(tmp_path)


@pytest.mark.parametrize("value", ["0", "3601", "-1", "+1", "1.5", " 30", "30 ", "３００", "", "1e2"])
def test_wait_timeout_rejects_nonexact_integers(bootstrap, value):
    """Timeout CLI errors are argparse failures, before initialization or Docker."""
    with pytest.raises(SystemExit) as error:
        bootstrap.parse_args(["up", "--wait-timeout", value])
    assert error.value.code == 2


@pytest.mark.parametrize("value", ["1", "300", "3600"])
def test_wait_timeout_accepts_bounds(bootstrap, value):
    """All supported timeout bounds are passed through as integers."""
    assert bootstrap.parse_args(["up", "--wait-timeout", value]).wait_timeout == int(value)
    assert bootstrap.parse_args(["up"]).wait_timeout == 300


@pytest.mark.parametrize("succeeds", [True, False])
def test_up_wait_success_only_reports_actual_endpoint(bootstrap, tmp_path, monkeypatch, capsys, succeeds):
    """A failed health wait never claims readiness; successful waits use the real port."""
    data = tmp_path / "fresh"
    assert bootstrap.main(init_args(data)) == 0
    capsys.readouterr()
    monkeypatch.setenv("BOT_WEB_PORT", "6290")
    monkeypatch.setattr(bootstrap, "doctor", lambda *args: None)
    monkeypatch.setattr(bootstrap, "check_project_ownership", lambda *args: None)
    calls = []
    def run(command, data):
        """Represent a healthy runtime or a Compose health timeout without Docker."""
        calls.append(command)
        if not succeeds:
            raise subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(bootstrap, "run_docker", run)
    assert bootstrap.main(["up", "--data-dir", str(data), "--wait-timeout", "42"]) == (0 if succeeds else 2)
    assert calls[0][-6:] == ["up", "-d", "--build", "--wait", "--wait-timeout", "42"]
    output = capsys.readouterr().out
    assert ("Local runtime ready" in output) == succeeds
    assert ("http://127.0.0.1:6290" in output) == succeeds
    assert "Started." not in output


@pytest.mark.parametrize("name", ["BOT_WEB_PORT", "BOT_ONEBOT_PORT"])
@pytest.mark.parametrize("value", ["", "0", "65536", "-1", "+80", " 80", "80 ", "８０", "80.0", "80:90"])
def test_invalid_ports_fail_before_docker(bootstrap, tmp_path, monkeypatch, name, value):
    """Doctor rejects malformed environment ports before even probing Docker."""
    data = tmp_path / "fresh"
    assert bootstrap.main(init_args(data)) == 0
    original = (data / "settings.json").read_bytes()
    prepare_runtime(bootstrap)
    monkeypatch.setenv(name, value)
    calls = []
    monkeypatch.setattr(bootstrap.shutil, "which", lambda *args: calls.append("which"))
    monkeypatch.setattr(bootstrap, "run_docker", lambda *args: calls.append("docker"))
    assert bootstrap.main(["doctor", "--data-dir", str(data)]) == 2
    assert calls == []
    assert (data / "settings.json").read_bytes() == original


@pytest.mark.parametrize("web,onebot", [("1", "65535"), ("65535", "1"), ("06185", "6199")])
def test_valid_ports_are_normalized(bootstrap, tmp_path, monkeypatch, web, onebot):
    """Port bounds are inclusive and Compose receives canonical decimal strings."""
    monkeypatch.setenv("BOT_WEB_PORT", web)
    monkeypatch.setenv("BOT_ONEBOT_PORT", onebot)
    environment = bootstrap.docker_environment(tmp_path)
    assert environment["BOT_WEB_PORT"] == str(int(web))
    assert environment["BOT_ONEBOT_PORT"] == str(int(onebot))


def test_duplicate_ports_rejected(bootstrap, tmp_path, monkeypatch):
    """Numerically identical loopback bindings cannot both be allocated."""
    monkeypatch.setenv("BOT_WEB_PORT", "06199")
    with pytest.raises(ValueError, match="distinct"):
        bootstrap.docker_environment(tmp_path)


def test_missing_docker_instructions_are_actionable(bootstrap, tmp_path, monkeypatch, capsys):
    """Missing prerequisites name both supported platforms and official install docs."""
    data = tmp_path / "fresh"
    assert bootstrap.main(init_args(data)) == 0
    prepare_runtime(bootstrap)
    monkeypatch.setattr(bootstrap.shutil, "which", lambda *args: None)
    assert bootstrap.main(["doctor", "--data-dir", str(data)]) == 2
    error = capsys.readouterr().err
    for text in ("Docker Desktop", "WSL2", "Linux containers", "Docker Engine", "Compose v2",
                 "https://docs.docker.com/desktop/setup/install/windows-install/",
                 "https://docs.docker.com/engine/install/", "rerun doctor"):
        assert text in error
    assert "secret-only-in-private-test-settings" not in error


@pytest.mark.parametrize("verb", ["status", "logs"])
def test_readonly_ownership_conflict_hides_other_project(bootstrap, tmp_path, monkeypatch, verb):
    """Unowned containers cannot expose status or private logs through our CLI."""
    data = tmp_path / "fresh"
    assert bootstrap.main(init_args(data)) == 0
    calls = []
    def conflict(*args):
        """Represent a project owned by another data directory."""
        raise ValueError("runtime name already used")
    monkeypatch.setattr(bootstrap, "check_project_ownership", conflict)
    monkeypatch.setattr(bootstrap, "run_docker", lambda *args: calls.append(args))
    assert bootstrap.main([verb, "--data-dir", str(data)]) == 2
    assert calls == []
