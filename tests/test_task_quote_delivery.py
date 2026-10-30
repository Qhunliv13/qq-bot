"""Scheduled final messages retain model-selected native quote metadata across context cleanup."""
import asyncio
import json
from types import SimpleNamespace

from plugin.message_batch import message_record
from test_production_integration import Event, _provider

pytest_plugins = ('test_production_integration',)


def test_scheduled_result_carries_only_current_attempt_authorized_quote(production, monkeypatch):
    """Returning text to the scheduler must not throw away its validated historical quote."""
    plugin = production()
    task = {'id': 'own', 'scope': '42', 'origin': 'offline:GroupMessage:42', 'goal': 'continue'}
    plugin._tasks_mod = lambda: SimpleNamespace(listing=lambda **kwargs: [dict(task, status='active')])
    historical = Event('earlier question', message_id='8101')
    plugin._hist().append('42', 'Earlier#8101: earlier question', record=message_record(historical, historical.text))
    _provider(monkeypatch, [(json.dumps({'reply_to': [], 'text': '[quote:8101]Scheduled result', 'delivery': 'new'}), [])])
    assert asyncio.run(plugin._run_task_once(task)) == 'Scheduled result'
    assert task['_final_quote_id'] == '8101'
    assert '42' not in plugin._turn_context
    # Reusing the task object cannot attach its old quote to an unrelated result.
    _provider(monkeypatch, [(json.dumps({'reply_to': [], 'text': 'Plain result', 'delivery': 'new'}), [])])
    assert asyncio.run(plugin._run_task_once(task)) == 'Plain result'
    assert '_final_quote_id' not in task
