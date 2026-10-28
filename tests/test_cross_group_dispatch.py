"""Cross-group recall reaches scoped archives without inventing bot identities."""
import asyncio
import json

import test_production_integration as integration

production = integration.production


def test_explicit_group_without_keyword_reads_outside_global_window(production, tmp_path):
    """A quiet old group remains retrievable after another group fills the window."""
    bot = production()
    recorder = bot._hist()
    root = tmp_path / 'history'
    root.mkdir(exist_ok=True)
    # Recorder paths are supplied by the existing production fixture, not globals.
    from pathlib import Path
    directory = Path(recorder.root)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / '72.jsonl').write_text(json.dumps({'ts': 1, 'line': 'old group activity',
        'record': {'id': 9, 'sender_id': '77', 'scope': '72'}}) + '\n', encoding='utf-8')
    result = asyncio.run(bot._dispatch_tool('history_search', {'scope': '72'}, '42', event=integration.Event()))
    assert result['ok'] and result['count'] == 1
    assert result['hits'][0]['scope'] == '72'
    assert result['coverage']['live_membership_verified'] is False
    denied = asyncio.run(bot._dispatch_tool('history_search', {'scope': 'private:77'}, '42', event=integration.Event()))
    assert denied == {'ok': False, 'error': 'history_scope_not_visible'}


def test_group_lookup_dispatches_authenticated_bridge_without_event_origin_change(production):
    """Member lists are observations, not a guessed count of bots or messages sent."""
    bot = production()
    calls = []
    def native(action, params):
        """Return native-shaped data at the one simulated network edge."""
        calls.append((action, params))
        data = {'group_id': 72, 'group_name': 'other group', 'member_count': 2} if action == 'get_group_info' else [
            {'user_id': 1, 'nickname': 'bot-like nickname'}, {'user_id': 2, 'nickname': 'person'}]
        return {'status': 'ok', 'retcode': 0, 'data': data}
    bot._onebot_http = native
    event = integration.Event()
    result = asyncio.run(bot._dispatch_tool('qq_group_inspect', {'group_id': '72'}, '42', event=event))
    assert result['ok'] and result['returned'] == 2
    assert result['bot_count'] is None and result['bot_classification_complete'] is False
    assert calls == [('get_group_info', {'group_id': 72, 'no_cache': True}),
                     ('get_group_member_list', {'group_id': 72, 'no_cache': True})]
    assert not event.sent
