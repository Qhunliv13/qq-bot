"""Native lightweight Windows chat: standard library only, no WSL/Docker/AstrBot."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot_core.http_host import ChatCompletionHost, validate_base_url  # noqa: E402
from tools.lite import _configure_utf8, _interactive, _positive_turns, _SafeParser  # noqa: E402
from tools.lite_credentials import protect, unprotect  # noqa: E402
from tools.setup_inputs import _read_hidden_key, validate_model_field  # noqa: E402

MARKER = 'qhunliv-native-lite'


def default_data_dir():
    """Keep native lightweight settings separate from full installs and the checkout."""
    local = os.environ.get('LOCALAPPDATA') if os.name == 'nt' else None
    return (Path(local) if local else Path.home() / '.local' / 'share') / 'QhunlivBot' / 'Lite'


def data_directory(raw):
    """Resolve a private selected directory without adopting code or foreign settings."""
    data = Path(raw).expanduser().resolve()
    if data == ROOT or ROOT in data.parents or data in ROOT.parents:
        raise ValueError('Choose a data directory outside this repository')
    if any(ord(character) < 32 for character in str(data)):
        raise ValueError('Invalid data directory')
    return data


def load_config(data):
    """Read only the selected native-lite settings; never infer a full/production install."""
    path = data / 'settings.json'
    if not path.exists():
        if data.exists() and any(data.iterdir()):
            raise ValueError('Selected directory is not a native-lite installation; choose a new empty directory')
        return None
    if path.is_symlink() or path.stat().st_size > 1024 * 1024:
        raise ValueError('Invalid native-lite settings')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('application') != MARKER or type(value.get('schema_version')) is not int or value['schema_version'] != 1:
        raise ValueError('Selected settings do not belong to native lite')
    validate_base_url(value.get('base_url'))
    validate_model_field('base_url', value.get('base_url'))
    validate_model_field('model', value.get('model'))
    if not isinstance(value.get('system'), str):
        raise ValueError('Invalid native-lite persona')
    if value.get('credential_storage') not in ('windows-dpapi-current-user', 'environment'):
        raise ValueError('Unsupported credential storage')
    if value['credential_storage'] == 'environment':
        name = value.get('api_key_env')
        if name is not None and (not isinstance(name, str) or not name.isidentifier() or not name.isascii()):
            raise ValueError('Invalid credential environment name')
    elif not isinstance(value.get('protected_api_key'), str):
        raise ValueError('Invalid protected credential')
    return value


def save_config(data, value, previous):
    """Commit private settings atomically, refusing a concurrent or foreign replacement."""
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != 'nt':
        data.chmod(0o700)
    target = data / 'settings.json'
    descriptor, temporary = tempfile.mkstemp(prefix='.lite-', suffix='.tmp', dir=data)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        if previous is None:
            # Windows rename is atomic and refuses an existing destination, even
            # on filesystems without hard links. POSIX rename would overwrite it.
            if os.name == 'nt':
                os.rename(temporary, target)
            else:
                os.link(temporary, target)
        else:
            if load_config(data) != previous:
                raise ValueError('Settings changed concurrently; reopen configuration')
            os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _prompt_field(label, validator, current=None, hidden=False):
    """Retry one interactive field without echoing a credential or discarding valid fields."""
    while True:
        raw = _read_hidden_key() if hidden else input(label + (' [Enter keeps current]' if current is not None else '') + ': ').strip()
        if current is not None and raw == '':
            return current
        try:
            validator(raw)
            return raw
        except ValueError:
            print('Invalid value; please retry this field. / 输入无效，请只重填此项。')


def _check_base(value):
    """Share common input checks and the existing HTTPS/loopback-only transport policy."""
    validate_model_field('base_url', value)
    validate_base_url(value)


def configure(data, args, previous=None):
    """Collect explicit native inputs and store only encrypted Windows keys or an env name."""
    previous = previous or None
    if args.api_key_env is not None and (not args.api_key_env.isidentifier() or not args.api_key_env.isascii()):
        raise ValueError('Use a valid environment variable NAME, never a literal key')
    supplied = {'base_url': args.model_base, 'model': args.model}
    for name, candidate in supplied.items():
        if candidate is not None:
            (_check_base if name == 'base_url' else lambda item: validate_model_field('model', item))(candidate)
    base = args.model_base or (previous.get('base_url') if previous else None)
    model = args.model or (previous.get('model') if previous else None)
    if not args.non_interactive:
        base = _prompt_field('模型 Base URL / Model Base URL', _check_base, base) if args.model_base is None else base
        model = _prompt_field('模型 ID / Model ID', lambda item: validate_model_field('model', item), model) if args.model is None else model
    if not base or not model:
        raise ValueError('First setup needs a model base URL and model ID')
    _check_base(base)
    system = (Path(args.persona_file).read_text(encoding='utf-8') if args.persona_file
              else previous.get('system', '') if previous else '')
    # Explicit environment mode supports unattended setup and never stores a key.
    if args.api_key_env:
        key = os.environ.get(args.api_key_env, '')
        ChatCompletionHost(base, model, key)
        secret = {'credential_storage': 'environment', 'api_key_env': args.api_key_env}
    elif args.non_interactive:
        if previous:
            secret = {name: previous[name] for name in ('credential_storage', 'api_key_env', 'protected_api_key') if name in previous}
        elif urlsplit(base).scheme == 'http':
            secret = {'credential_storage': 'environment', 'api_key_env': None}
        else:
            raise ValueError('Noninteractive HTTPS setup requires --api-key-env naming an existing variable')
    else:
        # Enter retains the credential; pre-commit validation below verifies it
        # locally against the new transport without making any network request.
        if previous:
            print('API Key: Enter keeps the existing protected/environment credential; type a new key to replace it.')
        key = _read_hidden_key()
        if not key and previous:
            secret = {name: previous[name] for name in ('credential_storage', 'api_key_env', 'protected_api_key') if name in previous}
        else:
            while True:
                try:
                    if key:
                        validate_model_field('api_key', key)
                    ChatCompletionHost(base, model, key)
                    break
                except ValueError:
                    print('A valid hidden API Key is required for HTTPS; local HTTP may leave it empty.')
                    key = _read_hidden_key()
            if os.name == 'nt':
                secret = {'credential_storage': 'windows-dpapi-current-user', 'protected_api_key': protect(key)}
            elif key:
                raise ValueError('Outside Windows use --api-key-env; plaintext credentials are not saved')
            else:
                secret = {'credential_storage': 'environment', 'api_key_env': None}
    value = {'application': MARKER, 'schema_version': 1, 'base_url': base,
             'model': model, 'system': system, **secret}
    # Validate the retained key against the new transport too. Switching a keyless
    # local service to HTTPS must not commit settings that cannot even start.
    configured_host(value)
    save_config(data, value, previous)
    print('Native-lite settings saved outside the checkout. No model request was sent.')
    return value


def configured_host(value):
    """Resolve a credential only in process memory for the existing text-only HTTP host."""
    if value['credential_storage'] == 'windows-dpapi-current-user':
        key = unprotect(value['protected_api_key'])
    else:
        name = value.get('api_key_env')
        key = os.environ.get(name, '') if name else ''
    if key:
        validate_model_field('api_key', key)
    return ChatCompletionHost(value['base_url'], value['model'], key)


def _port(value):
    """Accept only decimal ports, with zero meaning a fresh OS-selected loopback port."""
    if not value.isascii() or not value.isdigit() or not 0 <= int(value) <= 65535:
        raise argparse.ArgumentTypeError('port must be 0-65535')
    return int(value)


def main(argv=None):
    """Launch browser or terminal using one portable reply core and secret-free errors."""
    _configure_utf8()
    parser = _SafeParser(description=__doc__)
    parser.add_argument('command', nargs='?', choices=('web', 'chat', 'info', 'configure'), default='web')
    parser.add_argument('--data-dir', default=str(default_data_dir()))
    parser.add_argument('--model-base')
    parser.add_argument('--model')
    parser.add_argument('--api-key-env')
    parser.add_argument('--persona-file')
    parser.add_argument('--non-interactive', action='store_true')
    parser.add_argument('--history-turns', type=_positive_turns, default=12)
    parser.add_argument('--port', type=_port, default=0)
    parser.add_argument('--no-browser', action='store_true')
    try:
        args = parser.parse_args(argv)
        if args.history_turns > 1000:
            raise ValueError('History turns must be 1-1000')
        data = data_directory(args.data_dir)
        value = load_config(data)
        if args.command == 'info':
            if value is None:
                raise ValueError('Native lite is not configured; launch web or configure first')
            print('Native Windows Lite / 原生轻量版')
            print('Settings: ' + str(data / 'settings.json'))
            print('Credential storage: ' + value['credential_storage'])
            print('No WSL, Docker, pip, AstrBot, QQ or persistent chat history. No model request sent.')
            return 0
        configuration_flags = (args.model_base, args.model, args.api_key_env, args.persona_file)
        if args.command == 'configure' or value is None:
            value = configure(data, args, value)
        elif any(item is not None for item in configuration_flags):
            raise ValueError('Existing settings are kept; use configure explicitly to change them')
        if args.command == 'configure':
            return 0
        host = configured_host(value)
        print('Lite: text chat only; no tools, QQ, persistent memory or background tasks. / 仅文字聊天。')
        print('Conversation is held only in memory. Close this process to discard it. / 关闭进程后不保留聊天记录。')
        if args.command == 'web':
            from tools.lite_web import run_web
            run_web(host, value['system'], args.history_turns, args.port, not args.no_browser)
        else:
            print('Type /exit to quit. / 输入 /exit 退出。')
            _interactive(host, value['system'], args.history_turns)
        return 0
    except (EOFError, KeyboardInterrupt):
        return 0
    except Exception:
        print('Native lite could not start or complete this request. Check Python 3.10+, your selected native settings and model access; use configure to repair settings. No secrets were printed.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
