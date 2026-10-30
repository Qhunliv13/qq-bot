"""Exercise real bootstrap initialization retries and listener conflicts without Docker."""
import builtins
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import socket

import pytest

from tools import setup_inputs


@pytest.fixture
def bootstrap(tmp_path, monkeypatch):
    """Import actual installer and give it a disposable checkout marker only."""
    spec = importlib.util.spec_from_file_location('first_run_bootstrap', Path(__file__).resolve().parents[1] / 'tools/bootstrap.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    repo = tmp_path / 'repo'
    repo.mkdir()
    monkeypatch.setattr(module, 'REPO_ROOT', repo)
    monkeypatch.setenv('FIRST_RUN_KEY', 'offline-only-key')
    for key in ('BOT_WEB_PORT', 'BOT_ONEBOT_PORT'):
        monkeypatch.delenv(key, raising=False)
    return module


@contextmanager
def busy_port():
    """Hold a real loopback listener while setup validates or selects defaults."""
    with socket.socket() as handle:
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            handle.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        handle.bind(('127.0.0.1', 0))
        handle.listen(1)
        yield handle.getsockname()[1]


def flags(data):
    """Supply model fields explicitly to keep port tests noninteractive and offline."""
    return ['init', '--data-dir', str(data), '--non-interactive', '--model-base',
            'https://provider.invalid/v1', '--model', 'operator-model', '--api-key-env', 'FIRST_RUN_KEY']


def test_wrong_prompt_field_retries_without_discarding_valid_inputs(bootstrap, tmp_path, monkeypatch, capsys):
    """A pasted full endpoint or mistyped owner list does not restart the entire wizard."""
    data = tmp_path / 'private'
    ordinary = iter(['https://provider.invalid/v1/chat/completions', 'https://provider.invalid/v1',
                     'my-model', 'not-a-qq', ''])
    keys = iter(['', 'valid-hidden-key'])
    monkeypatch.setattr(builtins, 'input', lambda prompt: next(ordinary))
    monkeypatch.setattr(setup_inputs.getpass, 'getpass', lambda prompt: next(keys))
    assert bootstrap.main(['init', '--data-dir', str(data)]) == 0
    settings = json.loads((data / 'settings.json').read_text(encoding='utf-8'))
    assert settings['model'] == {'base_url': 'https://provider.invalid/v1', 'model': 'my-model', 'api_key': 'valid-hidden-key'}
    assert settings['owner_qqs'] == []
    output = capsys.readouterr().out
    assert 'valid-hidden-key' not in output
    assert '/chat/completions' in output


def test_busy_default_port_is_replaced_and_persisted(bootstrap, tmp_path, monkeypatch):
    """Only fresh implicit defaults may move, leaving the existing listener running."""
    data = tmp_path / 'private'
    original = bootstrap.docker_environment
    with busy_port() as occupied:
        def pretend_default(root, settings=None):
            """Choose the occupied temporary port as this test's implicit default."""
            value = original(root, settings)
            if settings == {}:
                value['BOT_WEB_PORT'] = str(occupied)
                if value['BOT_ONEBOT_PORT'] == str(occupied):
                    value['BOT_ONEBOT_PORT'] = '6198'
            return value
        monkeypatch.setattr(bootstrap, 'docker_environment', pretend_default)
        assert bootstrap.main(flags(data)) == 0
        settings = json.loads((data / 'settings.json').read_text(encoding='utf-8'))
        assert settings['deployment']['web_port'] != occupied
        assert bootstrap.docker_environment(data)['BOT_WEB_PORT'] == str(settings['deployment']['web_port'])


def test_explicit_busy_port_fails_before_any_private_state(bootstrap, tmp_path, monkeypatch):
    """Automation receives a clear failure rather than a changed promised endpoint."""
    data = tmp_path / 'private'
    with busy_port() as occupied:
        monkeypatch.setenv('BOT_WEB_PORT', str(occupied))
        if occupied == 6199:
            monkeypatch.setenv('BOT_ONEBOT_PORT', '6198')
        assert bootstrap.main(flags(data)) == 2
        assert not data.exists()


def test_rerun_preserves_original_ports_without_probing_or_reallocating(bootstrap, tmp_path, monkeypatch):
    """Reopening setup never silently changes existing URLs or running data."""
    data = tmp_path / 'private'
    assert bootstrap.main(flags(data)) == 0
    original = (data / 'settings.json').read_bytes()
    def forbidden(*args, **kwargs):
        """Reinitialization must return before new socket allocation."""
        raise AssertionError('existing installation was probed')
    monkeypatch.setattr(bootstrap, 'first_install_ports', forbidden)
    assert bootstrap.main(['init', '--data-dir', str(data), '--non-interactive']) == 0
    assert (data / 'settings.json').read_bytes() == original
