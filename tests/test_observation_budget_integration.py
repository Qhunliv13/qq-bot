"""The existing prompt budget bounds retrieval, not durable memory or tool retention."""
import asyncio
import json

from test_production_integration import _load_production, Event


def test_context_uses_existing_archive_prompt_budget_without_changing_archive(tmp_path):
    """A busy scope must not resend its full 100-record retention every turn."""
    plugin = _load_production(tmp_path)()
    archive = plugin._tool_evidence()
    for index in range(100):
        archive.append('42', 'q' * 800, 'synthetic', {'index': index}, {'body': 'x' * 3000},
                       observation_id='budget-' + str(index))
    before = archive.recent('42')
    event = Event('hello', group='42')
    envelope = json.loads(plugin._request_context(event, '42', 'hello', False))
    expected = json.loads(archive.prompt('42'))
    assert envelope['observations'] == expected['observations']
    assert envelope['observation_window']['omitted'] > 0
    assert envelope['observation_window']['detail_tool'] == 'tool_observations'
    assert len(json.dumps(expected, ensure_ascii=False, separators=(',', ':'))) <= archive.max_prompt_chars
    assert archive.recent('42') == before
    assert len(before) == 100
    # Every retained record remains available to an explicit query, not only the
    # recent projection included automatically with unrelated conversation.
    pages = []
    for offset in range(0, 100, 20):
        page = asyncio.run(plugin._dispatch_tool('tool_observations',
                           {'limit': 20, 'offset': offset}, '42', event))
        pages = page['observations'] + pages
    assert pages == before
