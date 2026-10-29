"""Exercise optional light schemas through the unchanged real QQ HTTP/tool loop."""
import asyncio
import json

import pytest

from plugin.runtime_settings import configured_tool_profile
from test_production_integration import Event, _call, _current_turn, _provider, _visible

pytest_plugins = ('test_production_integration',)


def test_absent_settings_keeps_full_deployment(monkeypatch):
    """The existing deployment must not silently switch profiles after upgrade."""
    monkeypatch.delenv('HIGAN_SETTINGS_FILE', raising=False)
    assert configured_tool_profile() == {'mode': 'full', 'initial_tools': []}


@pytest.mark.parametrize('profile', [None, [], {'mode': 'minimal'}, {'mode': 'light', 'initial_tools': 'vm_exec'}, {'extra': True}])
def test_invalid_profile_never_silently_selects_light(monkeypatch, tmp_path, profile):
    """Reject unclear settings instead of guessing a feature selection."""
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps({'schema_version': 1, 'tool_profile': profile}), encoding='utf-8')
    monkeypatch.setenv('HIGAN_SETTINGS_FILE', str(path))
    with pytest.raises(ValueError, match='invalid_tool_profile'):
        configured_tool_profile()


def test_light_describes_then_uses_existing_dispatch(production, monkeypatch, tmp_path):
    """Discover exact schemas without dropping context or duplicating dispatch."""
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps({'schema_version': 1, 'tool_profile': {'mode': 'light'}}), encoding='utf-8')
    monkeypatch.setenv('HIGAN_SETTINGS_FILE', str(path))
    bot, event = production(), Event('Inspect available tools')
    _current_turn(bot, event)
    all_schemas = bot._tool_schemas()
    lookup = {item['function']['name']: item for item in all_schemas}
    requests = _provider(monkeypatch, [
        ('private tool planning', [_call('tool_describe', {'names': ['self_status']})]),
        ('', [_call('self_status')]),
        ('Public status answer.', []),
    ])
    answer = asyncio.run(bot._decide_tools_first(event, '42', event.text, False))
    assert _visible(answer, requests) == 'Public status answer.'
    first = requests[0]['tools']
    first_names = [item['function']['name'] for item in first]
    assert 'tool_describe' in first_names and 'tool_search' in first_names
    assert 'self_status' not in first_names
    second = requests[1]['tools']
    assert second[:len(first)] == first
    assert next(item for item in second if item['function']['name'] == 'self_status') == lookup['self_status']
    assert requests[2]['tools'] == second
    assert requests[0]['messages'][1] == requests[1]['messages'][1]
    assert len(second) < len(all_schemas)
    assert [row['tool_name'] for row in bot._tool_evidence().recent('42')] == ['tool_describe', 'self_status']


def test_full_default_preserves_complete_catalog(production, monkeypatch):
    """Existing all-on deployments keep exactly the same full tool definitions."""
    monkeypatch.delenv('HIGAN_SETTINGS_FILE', raising=False)
    bot, event = production(), Event('A normal complete-mode request')
    _current_turn(bot, event)
    expected = bot._tool_schemas()
    requests = _provider(monkeypatch, [('Public answer.', [])])
    asyncio.run(bot._decide_tools_first(event, '42', event.text, False))
    assert requests[0]['tools'] == expected
    assert 'Tool schema exposure is light' not in requests[0]['messages'][0]['content']
