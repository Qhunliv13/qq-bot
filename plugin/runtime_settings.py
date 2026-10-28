"""Explicit fresh-install settings; legacy production behavior stays unchanged when absent."""
import json
import os
from pathlib import Path


def load_settings():
    """Read only an operator-selected external settings file, never scan for credentials."""
    path = os.environ.get('HIGAN_SETTINGS_FILE')
    if not path:
        return None
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('schema_version') != 1:
        raise ValueError('invalid_runtime_settings')
    return value


def configured_owners(legacy):
    """An explicit empty owner list denies QQ administration instead of inheriting IDs."""
    settings = load_settings()
    if settings is None:
        return set(legacy)
    values = settings.get('owner_qqs', [])
    if (not isinstance(values, list) or any(type(value) is not str or not value
            or not value.isascii() or not value.isdigit() or int(value) <= 0 for value in values)):
        raise ValueError('invalid_runtime_owners')
    return set(values)


def configured_persona():
    """Preserve the supplied persona verbatim; None keeps the existing database loader."""
    settings = load_settings()
    if settings is None:
        return None
    persona = settings.get('persona', '')
    if not isinstance(persona, str):
        raise ValueError('invalid_runtime_persona')
    return persona


def onebot_connection(default_base):
    """Keep QQ optional in fresh installs; never connect to an implicit legacy account."""
    settings = load_settings()
    if settings is None:
        return default_base, ''
    connection = settings.get('onebot', {})
    if not isinstance(connection, dict) or connection.get('enabled') is not True:
        return '', ''
    base, token = connection.get('http_base', ''), connection.get('token', '')
    if not isinstance(base, str) or not base.startswith(('https://', 'http://')) or not isinstance(token, str):
        raise ValueError('invalid_onebot_connection')
    return base.rstrip('/'), token
