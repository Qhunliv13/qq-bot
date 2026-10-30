"""Regression tests for duplicate nickname text after native At components."""
import asyncio

from test_production_integration import Event, At


def test_at_component_removes_only_authoritative_repeated_member_prefix(production):
    """A real member name immediately repeated after [at:id] appears once."""
    plugin, event = production(), Event()
    async def lookup(action, **kwargs):
        """Return a deterministic synthetic member response asynchronously."""
        return _member_info(action, kwargs, '主人')
    event.bot.api.call_action = lookup
    result = asyncio.run(plugin._send(event, '42', '[at:12345]主人，已收到。', decoded=True))
    assert result == '，已收到。'
    assert len(event.sent) == 1
    chain = event.sent[0].chain
    assert [part.qq for part in chain if isinstance(part, At)] == ['12345']
    assert [part.text for part in chain if hasattr(part, 'text')] == ['，已收到。']


def test_at_component_does_not_strip_same_word_without_exact_authoritative_prefix(production):
    """Unrelated prose and stale member lookups remain intact."""
    plugin, event = production(), Event()
    async def lookup(action, **kwargs):
        """Return a deterministic synthetic member response asynchronously."""
        return _member_info(action, kwargs, '主人')
    event.bot.api.call_action = lookup
    result = asyncio.run(plugin._send(event, '42', '[at:12345]已收到主人消息。', decoded=True))
    assert result == '已收到主人消息。'
    assert [part.text for part in event.sent[0].chain if hasattr(part, 'text')] == ['已收到主人消息。']


def test_at_component_lookup_failure_keeps_original_text(production):
    """A failed optional display lookup cannot remove user-visible response text."""
    plugin, event = production(), Event()

    async def fail(action, **kwargs):
        """Simulate an unavailable member-info action without network access."""
        raise RuntimeError('offline')

    event.bot.api.call_action = fail
    result = asyncio.run(plugin._send(event, '42', '[at:12345]主人，已收到。', decoded=True))
    assert result == '主人，已收到。'


def _member_info(action, kwargs, name):
    """Return a deterministic synthetic OneBot member response."""
    assert action == 'get_group_member_info'
    assert kwargs['group_id'] == 42 and kwargs['user_id'] == 12345
    return {'card': name, 'nickname': 'fallback'}
