"""First-time open-source users can recover access details without Docker or secret dumps."""
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def bootstrap(tmp_path, monkeypatch):
    """Keep all configuration in disposable paths and prohibit actual subprocesses."""
    path = Path(__file__).resolve().parents[1] / 'tools/bootstrap.py'
    spec = importlib.util.spec_from_file_location('bootstrap_usability', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    repo = tmp_path / 'repo'
    repo.mkdir()
    monkeypatch.setattr(module, 'REPO_ROOT', repo)
    monkeypatch.setenv('TEST_MODEL_KEY', 'private-fictitious-model-key')
    monkeypatch.delenv('BOT_WEB_PORT', raising=False)
    monkeypatch.delenv('BOT_ONEBOT_PORT', raising=False)
    def forbidden(*args, **kwargs):
        """Information and initial configuration must require no network, services or Docker."""
        raise AssertionError('unexpected subprocess')
    monkeypatch.setattr(module.subprocess, 'run', forbidden)
    return module


def initialize(bootstrap, data):
    """Create valid operator-defined model settings with no private personal identities."""
    assert bootstrap.main(['init', '--data-dir', str(data), '--non-interactive',
                           '--model-base', 'https://provider.invalid/v1', '--model', 'custom-model',
                           '--api-key-env', 'TEST_MODEL_KEY']) == 0
    return json.loads((data / 'settings.json').read_text(encoding='utf-8'))


def test_info_no_docker_no_password_no_key_and_no_mutation(bootstrap, tmp_path, capsys):
    """Install information remains useful even if Docker is missing or stopped."""
    data = tmp_path / 'private state'
    settings = initialize(bootstrap, data)
    original = {file.name: file.read_bytes() for file in data.iterdir() if file.is_file()}
    capsys.readouterr()
    assert bootstrap.main(['info', '--data-dir', str(data)]) == 0
    output = capsys.readouterr().out
    # Busy implicit defaults may select another free port; info must show the
    # actual saved installation rather than assuming 6185 is free on this host.
    assert str(data) in output and ('http://127.0.0.1:' + str(settings['deployment']['web_port'])) in output
    assert 'astrbot' in output and 'not a readiness check' in output
    assert 'initial setup values' in output and 'does not reset credentials' in output
    assert settings['model']['api_key'] not in output and settings['dashboard_password'] not in output
    assert original == {file.name: file.read_bytes() for file in data.iterdir() if file.is_file()}


def test_password_reveal_is_explicit_and_dashboard_only(bootstrap, tmp_path, capsys):
    """The operator can recover dashboard access without displaying model credentials."""
    data = tmp_path / 'private'
    settings = initialize(bootstrap, data)
    capsys.readouterr()
    assert bootstrap.main(['info', '--data-dir', str(data), '--show-password']) == 0
    output = capsys.readouterr().out
    assert settings['dashboard_password'] in output
    assert settings['model']['api_key'] not in output
    assert settings['model']['base_url'] not in output


def test_saved_ports_survive_new_shell_without_environment(bootstrap, tmp_path, monkeypatch, capsys):
    """A non-default first-run port must not be forgotten when the terminal closes."""
    data = tmp_path / 'portable'
    monkeypatch.setenv('BOT_WEB_PORT', '26185')
    monkeypatch.setenv('BOT_ONEBOT_PORT', '26199')
    settings = initialize(bootstrap, data)
    assert settings['deployment'] == {'web_port': 26185, 'onebot_port': 26199}
    monkeypatch.delenv('BOT_WEB_PORT')
    monkeypatch.delenv('BOT_ONEBOT_PORT')
    capsys.readouterr()
    assert bootstrap.main(['info', '--data-dir', str(data), '--json']) == 0
    output = json.loads(capsys.readouterr().out)
    assert output['dashboard_url'] == 'http://127.0.0.1:26185'
    assert output['readiness_checked'] is False
    assert 'password' not in repr(output) and settings['model']['api_key'] not in repr(output)
    assert bootstrap.docker_environment(data)['BOT_ONEBOT_PORT'] == '26199'
    original = (data / 'settings.json').read_bytes()
    monkeypatch.setenv('BOT_WEB_PORT', '36185')
    assert bootstrap.docker_environment(data)['BOT_WEB_PORT'] == '36185'
    assert (data / 'settings.json').read_bytes() == original


@pytest.mark.parametrize('flags', [['up', '--show-password'], ['info', '--json', '--show-password'], ['status', '--json']])
def test_secret_flags_cannot_leak_through_other_commands(bootstrap, flags):
    """Machine-readable or unrelated commands cannot reveal passwords accidentally."""
    with pytest.raises(SystemExit) as error:
        bootstrap.parse_args(flags)
    assert error.value.code == 2


def test_stop_scopes_ownership_and_preserves_data(bootstrap, tmp_path, monkeypatch):
    """Stopping is not down/remove/reset and may only affect this exact installation."""
    data = tmp_path / 'private'
    initialize(bootstrap, data)
    original = (data / 'settings.json').read_bytes()
    calls = []
    monkeypatch.setattr(bootstrap, 'check_project_ownership', lambda *args: calls.append('ownership'))
    monkeypatch.setattr(bootstrap, 'run_docker', lambda command, root: calls.append(command))
    assert bootstrap.main(['stop', '--data-dir', str(data)]) == 0
    assert calls[0] == 'ownership' and calls[1][-1] == 'stop'
    assert all(value not in calls[1] for value in ['down', 'rm', '--volumes'])
    assert (data / 'settings.json').read_bytes() == original


def test_stop_conflict_never_stops_another_project(bootstrap, tmp_path, monkeypatch):
    """Do not trade isolation for an easy-looking stop command."""
    data = tmp_path / 'private'
    initialize(bootstrap, data)
    def conflict(*args):
        """Represent another user's project with a matching name."""
        raise ValueError('runtime belongs to another installation')
    monkeypatch.setattr(bootstrap, 'check_project_ownership', conflict)
    assert bootstrap.main(['stop', '--data-dir', str(data)]) == 2


def test_missing_install_has_actionable_hint_without_creating_state(bootstrap, tmp_path, capsys):
    """A user should not have to interpret FileNotFoundError on their first info command."""
    data = tmp_path / 'absent'
    assert bootstrap.main(['info', '--data-dir', str(data)]) == 2
    assert 'Run up for first setup' in capsys.readouterr().err
    assert not data.exists()


def test_changed_dashboard_account_is_not_claimed_or_reset(bootstrap, tmp_path, capsys):
    """Initial setup values never masquerade as recovery of a later dashboard password."""
    data = tmp_path / 'changed-account'
    initialize(bootstrap, data)
    runtime = data / 'astrbot' / 'cmd_config.json'
    runtime.write_text(json.dumps({'dashboard': {'username': 'changed-user', 'password': 'runtime-hash'}}), encoding='utf-8')
    original = runtime.read_bytes()
    capsys.readouterr()
    assert bootstrap.main(['info', '--data-dir', str(data), '--show-password']) == 0
    output = capsys.readouterr().out
    assert 'initial setup' in output and 'dashboard changes may supersede them' in output
    assert 'does not reset credentials' in output
    assert 'runtime-hash' not in output and 'changed-user' not in output
    assert runtime.read_bytes() == original


def test_legacy_settings_keep_defaults_without_automatic_rewrite(bootstrap, tmp_path):
    """New portability fields remain optional so old isolated installs do not reset."""
    data = tmp_path / 'legacy-managed'
    settings = initialize(bootstrap, data)
    settings.pop('deployment')
    (data / 'settings.json').write_text(json.dumps(settings), encoding='utf-8')
    original = (data / 'settings.json').read_bytes()
    assert bootstrap.docker_environment(data)['BOT_WEB_PORT'] == '6185'
    assert (data / 'settings.json').read_bytes() == original
