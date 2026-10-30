"""Fresh installation inputs must not inherit production identities or QQ routing."""
import ast
import asyncio
import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin import runtime_settings


@pytest.fixture(autouse=True)
def isolated_runtime_env(monkeypatch):
    """Never consume this machine's private runtime credentials or identity lists."""
    for key in ('HIGAN_SETTINGS_FILE', 'HIGAN_OWNER_QQS', 'HIGAN_MEMBER_WATCH_GROUPS',
                'HIGAN_ONEBOT_BASE', 'HIGAN_ONEBOT_TOKEN', 'HIGAN_VM_BRIDGE_BASE'):
        monkeypatch.delenv(key, raising=False)


def test_absent_settings_preserves_legacy(monkeypatch):
    """Existing deployment keeps its source and persona behavior when opt-in is absent."""
    monkeypatch.delenv('HIGAN_SETTINGS_FILE', raising=False)
    owner = str(10 ** 5 + 7)
    assert runtime_settings.configured_owners({owner}) == {owner}
    assert runtime_settings.configured_persona() is None
    assert runtime_settings.onebot_connection('http://legacy') == ('http://legacy', '')


def test_explicit_fresh_settings_do_not_inherit_owners_or_qq(tmp_path, monkeypatch):
    """An empty owner list and absent QQ connection are meaningful, not fallback values."""
    source = tmp_path / 'settings.json'
    source.write_text(json.dumps({'schema_version': 1, 'owner_qqs': [], 'persona': '原样\n人设'}), encoding='utf-8')
    monkeypatch.setenv('HIGAN_SETTINGS_FILE', str(source))
    assert runtime_settings.configured_owners({'legacy'}) == set()
    assert runtime_settings.configured_persona() == '原样\n人设'
    assert runtime_settings.onebot_connection('http://legacy') == ('', '')


def test_external_onebot_is_explicit(tmp_path, monkeypatch):
    """Connection details are operator input, never embedded NapCat code or account data."""
    source = tmp_path / 'settings.json'
    source.write_text(json.dumps({'schema_version': 1, 'owner_qqs': ['123'], 'persona': '',
                      'onebot': {'enabled': True, 'http_base': 'http://external:3000/', 'token': 'fixture'}}), encoding='utf-8')
    monkeypatch.setenv('HIGAN_SETTINGS_FILE', str(source))
    assert runtime_settings.configured_owners({'legacy'}) == {'123'}
    assert runtime_settings.onebot_connection('http://legacy') == ('http://external:3000', 'fixture')


def test_new_install_has_no_identity_or_watch_defaults():
    """Public defaults grant nobody owner authority and observe no personal group."""
    assert runtime_settings.configured_owners() == set()
    assert runtime_settings.configured_member_watch_groups() == set()
    assert runtime_settings.configured_tool_profile() == {'mode': 'full', 'initial_tools': []}


def test_explicit_private_environment_is_preserved(monkeypatch):
    """Synthetic operator IDs are generated in-test, not substituted public defaults."""
    owner, group = str(10 ** 5 + 7), str(10 ** 6 + 11)
    monkeypatch.setenv('HIGAN_OWNER_QQS', json.dumps([owner]))
    monkeypatch.setenv('HIGAN_MEMBER_WATCH_GROUPS', json.dumps([group]))
    monkeypatch.setenv('HIGAN_ONEBOT_BASE', 'https://bridge.invalid/custom/')
    monkeypatch.setenv('HIGAN_ONEBOT_TOKEN', 'fixture-only')
    assert runtime_settings.configured_owners() == {owner}
    assert runtime_settings.configured_member_watch_groups() == {group}
    assert runtime_settings.onebot_connection('http://legacy') == ('https://bridge.invalid/custom', 'fixture-only')


def test_private_settings_override_legacy_and_environment(tmp_path, monkeypatch):
    """An explicit empty file list revokes any inherited private environment grants."""
    owner, group = str(10 ** 5 + 7), str(10 ** 6 + 11)
    monkeypatch.setenv('HIGAN_OWNER_QQS', json.dumps([owner]))
    monkeypatch.setenv('HIGAN_MEMBER_WATCH_GROUPS', json.dumps([group]))
    source = tmp_path / 'settings.json'
    source.write_text(json.dumps({'schema_version': 1, 'owner_qqs': [], 'member_watch_groups': []}), encoding='utf-8')
    monkeypatch.setenv('HIGAN_SETTINGS_FILE', str(source))
    assert runtime_settings.configured_owners({owner}) == set()
    assert runtime_settings.configured_member_watch_groups() == set()
    source.write_text(json.dumps({'schema_version': 1, 'owner_qqs': [owner],
                                  'member_watch_groups': [group]}), encoding='utf-8')
    assert runtime_settings.configured_owners() == {owner}
    assert runtime_settings.configured_member_watch_groups() == {group}


@pytest.mark.parametrize('values', [None, {}, 'all', [True], [0], ['0'], ['-1'], [' '], ['１２３'], ['valid']])
@pytest.mark.parametrize('key,reader,error', [
    ('owner_qqs', runtime_settings.configured_owners, 'invalid_runtime_owners'),
    ('member_watch_groups', runtime_settings.configured_member_watch_groups, 'invalid_member_watch_groups'),
])
def test_invalid_private_ids_fail_closed(tmp_path, monkeypatch, values, key, reader, error):
    """Reject the whole malformed list instead of retaining only permissive fragments."""
    source = tmp_path / 'settings.json'
    source.write_text(json.dumps({'schema_version': 1, key: values}), encoding='utf-8')
    monkeypatch.setenv('HIGAN_SETTINGS_FILE', str(source))
    with pytest.raises(ValueError, match=error):
        reader()


@pytest.mark.parametrize('key,reader', [
    ('HIGAN_OWNER_QQS', runtime_settings.configured_owners),
    ('HIGAN_MEMBER_WATCH_GROUPS', runtime_settings.configured_member_watch_groups),
])
def test_invalid_environment_fails_closed(monkeypatch, key, reader):
    """Broken environment JSON must not fall back to broader legacy authorization."""
    monkeypatch.setenv(key, 'not-json')
    with pytest.raises(ValueError):
        reader()


@pytest.mark.parametrize('settings', [[], {}, {'schema_version': True}, {'schema_version': 2}])
def test_invalid_settings_schema_fails_closed(tmp_path, monkeypatch, settings):
    """A malformed selected settings file cannot silently activate legacy defaults."""
    source = tmp_path / 'settings.json'
    source.write_text(json.dumps(settings), encoding='utf-8')
    monkeypatch.setenv('HIGAN_SETTINGS_FILE', str(source))
    with pytest.raises(ValueError, match='invalid_runtime_settings'):
        runtime_settings.configured_owners()


@pytest.mark.parametrize('connection', [None, [], {'enabled': 'true'}, {'enabled': True},
    {'enabled': True, 'http_base': 'http://'},
    {'enabled': True, 'http_base': 'http://bridge.invalid:bad'},
    {'enabled': True, 'http_base': 'https://user:secret@bridge.invalid'},
    {'enabled': True, 'http_base': 'https://bridge.invalid?token=secret'},
    {'enabled': True, 'http_base': 'https://bridge.invalid', 'token': False}])
def test_invalid_connection_never_uses_legacy(tmp_path, monkeypatch, connection):
    """Malformed explicit OneBot settings are never redirected to the local QQ account."""
    source = tmp_path / 'settings.json'
    source.write_text(json.dumps({'schema_version': 1, 'onebot': connection}), encoding='utf-8')
    monkeypatch.setenv('HIGAN_SETTINGS_FILE', str(source))
    with pytest.raises(ValueError, match='invalid_onebot_connection'):
        runtime_settings.onebot_connection('http://legacy')


def test_vm_bridge_explicit_endpoint_and_invalid_fail_closed(monkeypatch):
    """Default VM capability remains available; explicit malformed origins do not connect."""
    assert runtime_settings.configured_vm_bridge() == 'http://127.0.0.1:18003'
    monkeypatch.setenv('HIGAN_VM_BRIDGE_BASE', 'https://vm.invalid/bridge/')
    assert runtime_settings.configured_vm_bridge() == 'https://vm.invalid/bridge'
    monkeypatch.setenv('HIGAN_VM_BRIDGE_BASE', 'file:///private')
    with pytest.raises(ValueError, match='invalid_vm_bridge_connection'):
        runtime_settings.configured_vm_bridge()


def _main_methods(*names, **extra):
    """Compile only actual entry methods without importing AstrBot or any private data."""
    source = Path(__file__).parents[1] / 'plugin/main.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'HiganPlugin')
    methods = [node for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
               and node.name in names]
    assert {method.name for method in methods} == set(names)
    for method in methods:
        method.decorator_list = []
    namespace = {'__package__': 'plugin', 'asyncio': asyncio, 'json': json, 'os': os, 'Path': Path,
                 're': __import__('re'), 'logger': logging.getLogger(__name__),
                 'ONEBOT_BASE': 'http://legacy', 'MEMBER_WATCH_INTERVAL': 1, **extra}
    exec(compile(ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[])), str(source), 'exec'), namespace)
    return namespace


def test_message_file_lookup_reuses_configured_transport():
    """get_msg uses the same selected endpoint/auth transport, not a second implicit port."""
    calls = []
    def transport(action, params):
        """Capture OneBot lookup without any network or filesystem calls."""
        calls.append((action, params))
        return {'data': {'message': [{'type': 'file', 'data': {'url': 'https://file.invalid/item'}}]}}
    method = _main_methods('_file_url_of_message')['_file_url_of_message']
    assert asyncio.run(method(SimpleNamespace(_onebot_http=transport), '#42')) == 'https://file.invalid/item'
    assert calls == [('get_msg', {'message_id': 42})]


def test_no_configured_watch_groups_never_poll(monkeypatch):
    """An empty group allowlist does not enumerate or inspect a private account's groups."""
    calls = []
    async def sleep(_delay):
        """Allow one empty iteration then stop the background loop deterministically."""
        calls.append('sleep')
        if len(calls) == 2:
            raise asyncio.CancelledError()
    async def forbidden(*_args):
        """Any attempted OneBot access would broaden the empty watch allowlist."""
        raise AssertionError('unexpected group polling')
    monkeypatch.setattr(asyncio, 'sleep', sleep)
    method = _main_methods('_watch_members', MEMBER_WATCH_GROUPS=set())['_watch_members']
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(method(SimpleNamespace(_onebot=forbidden)))
    assert calls == ['sleep', 'sleep']


@pytest.mark.parametrize('name', ['vm_recall', 'vm_send_shot'])
@pytest.mark.parametrize('args', [{}, {'group_id': 0}, {'group_id': True}, {'group_id': 'bad'}])
def test_vm_delivery_without_explicit_destination_is_denied(name, args):
    """Direct VM calls cannot route into a historical personal group when target is absent."""
    method = _main_methods('_vm_tool')['_vm_tool']
    assert asyncio.run(method(SimpleNamespace(), name, args)) == {
        'ok': False, 'error': 'explicit_destination_required'}


class _JsonResponse:
    """Supply a context-managed local JSON response without opening a socket."""
    def __enter__(self):
        """Expose the local HTTP stand-in to the transport method."""
        return self

    def __exit__(self, *_args):
        """Release the fixture without suppressing transport exceptions."""
        return False

    def read(self):
        """Return a minimal native OneBot/VM success response."""
        return b'{"status":"ok","ok":true,"data":{}}'


@pytest.mark.parametrize('method_name', ['_onebot_http', '_onebot'])
def test_onebot_transports_honor_selected_endpoint_and_token(monkeypatch, method_name):
    """Both native transports use the explicit private endpoint/auth without a second port."""
    import urllib.request
    calls = []
    def urlopen(request, timeout):
        """Capture request metadata locally rather than sending a credential over the network."""
        calls.append((request, timeout))
        return _JsonResponse()
    monkeypatch.setattr(urllib.request, 'urlopen', urlopen)
    monkeypatch.setenv('HIGAN_ONEBOT_BASE', 'https://qq.invalid/bridge/')
    monkeypatch.setenv('HIGAN_ONEBOT_TOKEN', 'fixture-only')
    method = _main_methods(method_name)[method_name]
    if method_name == '_onebot_http':
        result = method(SimpleNamespace(), 'get_msg', {'message_id': 42})
    else:
        result = asyncio.run(method('get_msg', {'message_id': 42}))
    assert result['status'] == 'ok'
    request, _timeout = calls[0]
    assert request.full_url == 'https://qq.invalid/bridge/get_msg'
    assert request.get_header('Authorization') == 'Bearer fixture-only'
    assert json.loads(request.data) == {'message_id': 42}


@pytest.mark.parametrize('name,kind', [('vm_recall', 'group'), ('vm_send_shot', 'group'),
                                       ('vm_send_shot', 'private')])
def test_vm_delivery_explicit_override_remains_available(monkeypatch, name, kind):
    """Removing a personal fallback never deletes valid explicit group/private delivery."""
    import urllib.request
    calls = []
    def urlopen(request, timeout):
        """Capture the selected bridge and payload without invoking a real VM."""
        calls.append(request)
        return _JsonResponse()
    monkeypatch.setattr(urllib.request, 'urlopen', urlopen)
    monkeypatch.setenv('HIGAN_VM_BRIDGE_BASE', 'https://vm.invalid/bridge/')
    target = str(10 ** 6 + 11)
    key = 'user_id' if kind == 'private' else 'group_id'
    args = {'kind': kind, key: target}
    method = _main_methods('_vm_tool')['_vm_tool']
    assert asyncio.run(method(SimpleNamespace(), name, args))['ok'] is True
    assert calls[0].full_url.startswith('https://vm.invalid/bridge/vm/qq/')
    payload = json.loads(calls[0].data)
    assert str(payload[key]) == target
    if kind == 'private':
        assert payload['group_id'] == 0
