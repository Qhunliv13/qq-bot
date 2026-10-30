"""Native setup, Windows current-user credentials, and existing lightweight chat integration."""
from contextlib import contextmanager
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from tools import lite_native as native
from tools.lite_credentials import protect, unprotect


def args(**kwargs):
    """Supply only explicitly selected options to the native setup layer."""
    defaults = dict(model_base='http://127.0.0.1:18080/v1', model='synthetic-only',
                    api_key_env=None, persona_file=None, non_interactive=True)
    return SimpleNamespace(**(defaults | kwargs))


def test_empty_native_config_then_idempotent_info(tmp_path, capsys):
    """Native info has no model/network side effects and never reads another install."""
    data = tmp_path / '私有 配置'
    assert native.load_config(data) is None
    native.configure(data, args())
    before = (data / 'settings.json').read_bytes()
    assert native.main(['info', '--data-dir', str(data)]) == 0
    assert (data / 'settings.json').read_bytes() == before
    assert 'No model request' in capsys.readouterr().out


def test_foreign_and_in_checkout_data_fail_without_writes(tmp_path):
    """Never adopt a full deployment or create private state inside source code."""
    (tmp_path / 'existing.txt').write_text('unrelated', encoding='utf-8')
    with pytest.raises(ValueError):
        native.load_config(tmp_path)
    for path in (native.ROOT, native.ROOT / 'private', native.ROOT.parent):
        with pytest.raises(ValueError):
            native.data_directory(str(path))


@pytest.mark.parametrize('address', [
    'http://remote.example/v1', 'https://model.example/v1/chat/completions',
    'https://secret@model.example/v1', 'https://model.example/v1?key=hidden',
])
def test_invalid_explicit_model_base_fails_before_io(tmp_path, address):
    """Transport policy and common first-run mistakes do not create state."""
    with pytest.raises(ValueError):
        native.configure(tmp_path / 'new', args(model_base=address))
    assert not (tmp_path / 'new').exists()


def test_environment_reference_never_saves_plaintext_key(tmp_path, monkeypatch, capsys):
    """Automation stores a variable name rather than any literal environment credential."""
    monkeypatch.setenv('LITE_TEST_CREDENTIAL', 'synthetic-key-not-saved')
    value = native.configure(tmp_path, args(model_base='https://model.example/v1', api_key_env='LITE_TEST_CREDENTIAL'))
    raw = (tmp_path / 'settings.json').read_text(encoding='utf-8')
    assert 'synthetic-key-not-saved' not in raw
    assert value['credential_storage'] == 'environment'
    assert native.configured_host(value).api_key == 'synthetic-key-not-saved'
    native.main(['info', '--data-dir', str(tmp_path)])
    output = capsys.readouterr()
    assert 'synthetic-key-not-saved' not in output.out + output.err


def test_invalid_environment_name_fails_before_prompting(tmp_path, monkeypatch):
    """Mistyped explicit automation options cannot trigger hidden interactive key reads."""
    def unexpected_input(*unused):
        """Fail if validation happens only after reading interactive values."""
        raise AssertionError('unexpected prompt')
    monkeypatch.setattr('builtins.input', unexpected_input)
    with pytest.raises(ValueError):
        native.configure(tmp_path / 'new', args(model_base=None, model=None, api_key_env='not a name', non_interactive=False))
    assert not (tmp_path / 'new').exists()


@pytest.mark.parametrize('key', ['   ', 'key\x00broken', 'key\x85broken'])
def test_invalid_environment_credential_is_never_saved(tmp_path, monkeypatch, key):
    """Opaque credentials keep common control/empty validation before private writes."""
    monkeypatch.setenv('LITE_TEST_CREDENTIAL', key) if '\x00' not in key else None
    if '\x00' in key:
        monkeypatch.setattr(native.os, 'environ', dict(os.environ) | {'LITE_TEST_CREDENTIAL': key})
    with pytest.raises(ValueError):
        native.configure(tmp_path / 'new', args(model_base='https://model.example/v1', api_key_env='LITE_TEST_CREDENTIAL'))
    assert not (tmp_path / 'new').exists()


def test_noninteractive_https_without_secret_variable_is_not_initialized(tmp_path):
    """Missing unattended credentials fail without prompting, network or partial files."""
    with pytest.raises(ValueError):
        native.configure(tmp_path / 'new', args(model_base='https://model.example/v1'))
    assert not (tmp_path / 'new').exists()


def test_interactive_retries_only_bad_field_and_retains_default_persona(tmp_path, monkeypatch):
    """Correct model input survives an earlier bad URL; no author persona is inserted."""
    values = iter(['bad address', 'http://127.0.0.1:18080/v1', 'my-model'])
    monkeypatch.setattr('builtins.input', lambda label: next(values))
    monkeypatch.setattr(native, '_read_hidden_key', lambda: '')
    value = native.configure(tmp_path, args(model_base=None, model=None, non_interactive=False))
    assert value['system'] == '' and value['model'] == 'my-model'
    assert native.configured_host(value).api_key == ''


@pytest.mark.skipif(os.name != 'nt', reason='Actual current-user Windows DPAPI')
def test_current_user_dpapi_encrypts_and_roundtrips_unicode():
    """Exercise real Windows crypto, not a mock that accidentally permits plaintext."""
    secret = 'synthetic-密钥-only'
    ciphertext = protect(secret)
    assert secret not in ciphertext
    assert unprotect(ciphertext) == secret
    with pytest.raises(ValueError):
        unprotect('not-valid-base64%%')


@pytest.mark.skipif(os.name != 'nt', reason='Actual Windows encrypted native installation')
def test_hidden_key_is_saved_encrypted_and_kept_on_reconfigure(tmp_path, monkeypatch, capsys):
    """Enter retains existing protected settings without exposing or replacing the key."""
    monkeypatch.setattr(native, '_read_hidden_key', lambda: 'synthetic-hidden-only')
    initial = native.configure(tmp_path, args(non_interactive=False))
    assert initial['credential_storage'] == 'windows-dpapi-current-user'
    assert native.configured_host(initial).api_key == 'synthetic-hidden-only'
    assert 'synthetic-hidden-only' not in (tmp_path / 'settings.json').read_text(encoding='utf-8')
    monkeypatch.setattr(native, '_read_hidden_key', lambda: '')
    monkeypatch.setattr('builtins.input', lambda label: '')
    updated = native.configure(tmp_path, args(model_base=None, model=None, non_interactive=False), initial)
    assert updated == initial
    assert 'synthetic-hidden-only' not in capsys.readouterr().out


def test_configuration_changes_need_explicit_command(tmp_path, capsys):
    """Relaunch never silently overwrites a configured native instance."""
    native.configure(tmp_path, args())
    original = (tmp_path / 'settings.json').read_bytes()
    assert native.main(['chat', '--data-dir', str(tmp_path), '--model', 'replacement']) == 2
    assert (tmp_path / 'settings.json').read_bytes() == original
    assert 'replacement' not in capsys.readouterr().err


def test_atomic_first_setup_rejects_concurrent_creation(tmp_path):
    """A stale first-run cannot replace a fully committed competing installation."""
    initial = native.configure(tmp_path, args())
    with pytest.raises(FileExistsError):
        native.save_config(tmp_path, initial | {'model': 'another'}, None)
    assert native.load_config(tmp_path) == initial
    assert not list(tmp_path.glob('.lite-*.tmp'))


@contextmanager
def provider():
    """Start one real loopback synthetic provider, with no external credential/network use."""
    messages = []

    class Handler(BaseHTTPRequestHandler):
        """Reply with synthetic public text while retaining native incoming role messages."""

        def log_message(self, *unused):
            """Do not log test prompts or credentials."""

        def do_POST(self):
            """Serve the existing ChatCompletionHost's actual JSON request shape."""
            assert self.path == '/v1/chat/completions'
            value = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            messages.append(value['messages'])
            body = json.dumps({'choices': [{'message': {'role': 'assistant', 'content': '合成回复🙂'}}]}, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:' + str(server.server_port) + '/v1', messages
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_native_cli_real_python_process_is_stdlib_only_and_keeps_multiturn(tmp_path):
    """Python -S disables site packages and still runs real two-turn Unicode text chat."""
    with provider() as (base, messages):
        initialized = subprocess.run([sys.executable, '-S', '-B', str(native.ROOT / 'tools/lite_native.py'),
                                      'configure', '--data-dir', str(tmp_path), '--non-interactive',
                                      '--model-base', base, '--model', 'synthetic-only'],
                                     capture_output=True, text=True, encoding='utf-8', timeout=10)
        assert initialized.returncode == 0, initialized.stderr
        before = (tmp_path / 'settings.json').read_bytes()
        chat = subprocess.run([sys.executable, '-S', '-B', str(native.ROOT / 'tools/lite_native.py'),
                               'chat', '--data-dir', str(tmp_path)], input='你好🙂\n第二轮\n/exit\n',
                              capture_output=True, text=True, encoding='utf-8', timeout=15, cwd=tmp_path)
        assert chat.returncode == 0, chat.stderr
        assert chat.stdout.count('合成回复🙂') == 2
        assert messages[0][-1]['content'] == '你好🙂'
        assert [item['role'] for item in messages[1]] == ['system', 'user', 'assistant', 'user']
        assert (tmp_path / 'settings.json').read_bytes() == before
        assert sorted(path.name for path in tmp_path.iterdir()) == ['settings.json']


def test_web_start_uses_same_host_and_native_configuration(tmp_path, monkeypatch):
    """Browser launch receives the portable provider, not a duplicate model implementation."""
    from tools import lite_web
    native.configure(tmp_path, args())
    calls = []
    monkeypatch.setattr(lite_web, 'run_web', lambda *items: calls.append(items))
    assert native.main(['web', '--data-dir', str(tmp_path), '--no-browser', '--port', '0']) == 0
    host, system, limit, port, browser = calls[0]
    assert isinstance(host, native.ChatCompletionHost)
    assert system == '' and limit == 12 and port == 0 and browser is False


def test_hidden_input_cancellation_writes_no_settings(tmp_path, monkeypatch):
    """Cancelling first-run setup cannot leave half-initialized native state."""
    def cancel():
        """Represent the user cancelling the hidden key prompt."""
        raise KeyboardInterrupt
    monkeypatch.setattr(native, '_read_hidden_key', cancel)
    with pytest.raises(KeyboardInterrupt):
        native.configure(tmp_path / 'new', args(non_interactive=False))
    assert not (tmp_path / 'new').exists()
