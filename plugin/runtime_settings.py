"""Single runtime configuration reader with identity-free fresh-install defaults."""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit


def load_settings():
    """Read only an operator-selected external settings file, never scan for credentials."""
    path = os.environ.get('HIGAN_SETTINGS_FILE')
    if not path:
        return None
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if (not isinstance(value, dict) or type(value.get('schema_version')) is not int
            or value.get('schema_version') != 1):
        raise ValueError('invalid_runtime_settings')
    return value


def configured_tool_profile():
    """Keep full capabilities by default; light changes schema exposure, not authority.

    Read once per model turn so editing external settings cannot change the
    profile halfway through a tool batch. No deployment configuration is written.
    """
    settings = load_settings()
    profile = settings.get('tool_profile', {}) if settings is not None else {}
    if not isinstance(profile, dict) or set(profile) - {'mode', 'initial_tools'}:
        raise ValueError('invalid_tool_profile')
    mode = profile.get('mode', 'full')
    initial = profile.get('initial_tools', [])
    if (mode not in ('full', 'light') or not isinstance(initial, list)
            or any(not isinstance(name, str) or not name for name in initial)):
        raise ValueError('invalid_tool_profile')
    return {'mode': mode, 'initial_tools': list(initial)}


def _configured_ids(values, error):
    """Validate an explicit identity list atomically; malformed input never broadens access."""
    if (not isinstance(values, list) or any(type(value) is not str or not value
            or not value.isascii() or not value.isdigit() or int(value) <= 0 for value in values)):
        raise ValueError(error)
    return set(values)


def configured_owners(legacy=()):
    """Honor explicit private owners, defaulting to nobody rather than bundled identities.

    The optional legacy argument remains for callers supplying their own private
    configuration; the public entry point never passes a personal fallback.
    """
    settings = load_settings()
    if settings is not None:
        return _configured_ids(settings.get('owner_qqs', []), 'invalid_runtime_owners')
    # The entrypoint already exports this JSON list; standalone deployments may
    # supply it explicitly without creating or modifying any private settings file.
    supplied = os.environ.get('HIGAN_OWNER_QQS')
    if supplied is not None:
        try:
            values = json.loads(supplied)
        except (TypeError, ValueError) as exc:
            raise ValueError('invalid_runtime_owners') from exc
        return _configured_ids(values, 'invalid_runtime_owners')
    return _configured_ids(list(legacy), 'invalid_runtime_owners')


def configured_member_watch_groups():
    """Watch only operator-selected groups; an empty list never means every group."""
    settings = load_settings()
    if settings is not None:
        values = settings.get('member_watch_groups', [])
    else:
        try:
            values = json.loads(os.environ.get('HIGAN_MEMBER_WATCH_GROUPS', '[]'))
        except (TypeError, ValueError) as exc:
            raise ValueError('invalid_member_watch_groups') from exc
    return _configured_ids(values, 'invalid_member_watch_groups')


def configured_persona():
    """Preserve the supplied persona verbatim; None keeps the existing database loader."""
    settings = load_settings()
    if settings is None:
        return None
    persona = settings.get('persona', '')
    if not isinstance(persona, str):
        raise ValueError('invalid_runtime_persona')
    return persona


def _http_endpoint(value, error):
    """Reject malformed service origins without logging URLs or embedded credentials."""
    try:
        if not isinstance(value, str) or any(char.isspace() for char in value):
            raise ValueError(error)
        parsed = urlsplit(value)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment):
            raise ValueError(error)
        # Accessing port also validates malformed/out-of-range explicit ports.
        parsed.port
    except (TypeError, ValueError) as exc:
        raise ValueError(error) from exc
    return value.rstrip('/')


def onebot_connection(default_base=''):
    """Use explicit settings/env; retain an existing caller's local transport fallback."""
    settings = load_settings()
    if settings is None:
        base = os.environ.get('HIGAN_ONEBOT_BASE', default_base)
        token = os.environ.get('HIGAN_ONEBOT_TOKEN', '')
        return (_http_endpoint(base, 'invalid_onebot_connection'), token) if base else ('', '')
    connection = settings.get('onebot', {})
    if not isinstance(connection, dict):
        raise ValueError('invalid_onebot_connection')
    enabled = connection.get('enabled', False)
    if type(enabled) is not bool:
        raise ValueError('invalid_onebot_connection')
    if not enabled:
        return '', ''
    base, token = connection.get('http_base', ''), connection.get('token', '')
    if not isinstance(token, str):
        raise ValueError('invalid_onebot_connection')
    return _http_endpoint(base, 'invalid_onebot_connection'), token


def configured_vm_bridge():
    """Keep local VM capabilities available while permitting a deployment-selected endpoint."""
    return _http_endpoint(os.environ.get('HIGAN_VM_BRIDGE_BASE', 'http://127.0.0.1:18003'),
                          'invalid_vm_bridge_connection')
