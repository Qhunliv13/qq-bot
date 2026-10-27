"""Offline event outbox tests; no QQ events, credentials, or network are used."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import sqlite3

import pytest

from qq_transport.events import EventOutbox, EventPump


@pytest.fixture
def outbox(tmp_path):
    """Create a new isolated local outbox, never a bot or QQ database."""
    return EventOutbox(tmp_path / 'events.sqlite')


def event(account='100', body='雪🙂'):
    """Keep synthetic source timestamps, nested identifiers, and unknown fields intact."""
    return {'post_type': 'message', 'self_id': account, 'time': 0,
            'message_id': 'native-opaque-id', 'message': [{'type': 'text', 'data': {'text': body}}],
            'extra': {'unknown': [False, None, {'id': 123}]}}


def test_append_replay_conflict_and_detached_values(outbox):
    """Only identical source events share an ID; rereads never share mutable trees."""
    source = event()
    original = deepcopy(source)
    first = outbox.append('100', 'event-a', source)
    assert first['inserted'] is True
    assert outbox.append('100', 'event-a', source) == dict(first, inserted=False)
    with pytest.raises(ValueError, match='conflict'):
        outbox.append('100', 'event-a', event(body='different'))
    rows = outbox.pending('100', 'consumer')
    assert rows[0]['event'] == original == source
    rows[0]['event']['extra']['unknown'].append('mutated')
    assert outbox.pending('100', 'consumer')[0]['event'] == original


def test_account_order_ack_and_durable_reopen(outbox):
    """Interleaved accounts and consumers cannot skip or acknowledge foreign events."""
    first = outbox.append('100', 'event-1', event())['sequence']
    foreign = outbox.append('200', 'event-1', event('200'))['sequence']
    last = outbox.append('100', 'event-2', event(body='second'))['sequence']
    with pytest.raises(ValueError):
        outbox.acknowledge('100', 'consumer-a', last)
    with pytest.raises(ValueError):
        outbox.acknowledge('100', 'consumer-a', foreign)
    outbox.acknowledge('100', 'consumer-a', first)
    outbox.acknowledge('100', 'consumer-a', first)
    reopened = EventOutbox(outbox.path)
    assert [row['event_id'] for row in reopened.pending('100', 'consumer-a')] == ['event-2']
    assert len(reopened.pending('100', 'consumer-b')) == 2
    reopened.acknowledge('100', 'consumer-a', last)
    assert reopened.pending('100', 'consumer-a') == []
    with pytest.raises(ValueError):
        reopened.acknowledge('100', 'consumer-a', first)


@pytest.mark.parametrize('identity', [True, False, None, '200', 0, {}, []])
def test_mismatched_account_rejected(outbox, identity):
    """No display identity or truthy coercion can select another account."""
    value = event()
    value['self_id'] = identity
    with pytest.raises(ValueError):
        outbox.append('100', 'event', value)
    assert outbox.pending('100', 'consumer') == []


@pytest.mark.parametrize('limit', [True, 0, -1, 1001, '2'])
def test_invalid_limits_rejected(outbox, limit):
    """Bound work explicitly without silent numeric coercion."""
    with pytest.raises(ValueError):
        outbox.pending('100', 'consumer', limit)


@pytest.mark.parametrize('path', ['', ':memory:', None, 123, False])
def test_explicit_durable_path_required(path):
    """An empty or transient database cannot claim durable event preservation."""
    with pytest.raises(ValueError):
        EventOutbox(path)


def test_duplicate_concurrent_append_has_one_source_event(outbox):
    """SQLite uniqueness and immediate transactions prevent concurrent duplicate insertion."""
    def append(_):
        """Use a separate connection for each concurrent source submission."""
        return outbox.append('100', 'concurrent-event', event())
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(append, range(12)))
    assert sum(row['inserted'] for row in results) == 1
    assert len({row['sequence'] for row in results}) == 1


def test_pump_failure_then_ack_preserves_id_and_body(outbox):
    """Failed or ambiguous transport calls never erase pending source events."""
    outbox.append('100', 'source-id', event())
    received = []
    async def sink(identifier, body):
        """Simulate a transport which fails once and explicitly acknowledges once."""
        received.append((identifier, deepcopy(body)))
        body['message'] = []
        if len(received) == 1:
            raise OSError('synthetic connection interruption')
        return True
    async def scenario():
        """Serialize two attempts through the same production pump implementation."""
        pump = EventPump(outbox, '100', 'consumer', sink)
        assert (await pump.deliver_once())['state'] == 'unknown'
        assert len(outbox.pending('100', 'consumer')) == 1
        assert (await pump.deliver_once())['acknowledged'] is True
        assert (await pump.deliver_once())['state'] == 'empty'
    asyncio.run(scenario())
    assert received == [('source-id', event()), ('source-id', event())]


def test_truthy_sink_result_is_not_acknowledgement(outbox):
    """Only literal True acknowledges; strings, response dictionaries and None do not."""
    outbox.append('100', 'source-id', event())
    async def sink(identifier, body):
        """An arbitrary accepted-looking object is not the sink acknowledgement contract."""
        return {'accepted': True}
    assert asyncio.run(EventPump(outbox, '100', 'consumer', sink).deliver_once())['state'] == 'unacknowledged'
    assert len(outbox.pending('100', 'consumer')) == 1


def test_pump_cancellation_keeps_pending_source(outbox):
    """Cancellation propagates but cannot advance the durable consumer cursor."""
    outbox.append('100', 'source-id', event())
    async def sink(identifier, body):
        """Simulate an owned worker cancelled during its transport await."""
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(EventPump(outbox, '100', 'consumer', sink).deliver_once())
    assert len(outbox.pending('100', 'consumer')) == 1


def test_pump_ack_persistence_failure_requires_replay(outbox, monkeypatch):
    """A post-send crash can replay the same ID; do not promise exactly-once network delivery."""
    outbox.append('100', 'source-id', event())
    async def sink(identifier, body):
        """The remote side has already accepted before local acknowledgement fails."""
        return True
    def fail_ack(*args):
        """Simulate a disk failure at the durable acknowledgement boundary."""
        raise sqlite3.OperationalError('synthetic disk error')
    monkeypatch.setattr(outbox, 'acknowledge', fail_ack)
    with pytest.raises(sqlite3.OperationalError):
        asyncio.run(EventPump(outbox, '100', 'consumer', sink).deliver_once())
    assert len(outbox.pending('100', 'consumer')) == 1
