"""Synthetic offline evidence for observation-first wire locality and tradeoffs."""
import copy
import json
import os.path
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugin.conversation_context import build_context  # noqa: E402


def _old_wire(wire):
    """Rebuild the immediately previous history-first layout without altering data."""
    parsed = json.loads(wire)
    order = ('version', 'source', 'conversation', 'observations', 'other_scopes',
             'task', 'attachments', 'timing', 'trigger', 'activity',
             'conversation_state', 'deliveries')
    return json.dumps({key: parsed[key] for key in order if key in parsed},
                      ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def _prefix(a, b):
    """Measure actual UTF-8 bytes, not provider token cache hits."""
    return len(os.path.commonprefix((a.encode('utf-8'), b.encode('utf-8'))))


def _fixture():
    """Use synthetic large values only; never retain actual production messages."""
    return {'scope': {'id': 'group-a'}, 'messages': [{'id': 'current-1'}],
            'history': [{'id': 'history-1', 'body': 'H' * 240000}],
            'clock': {'now': 1}, 'observations': [
                {'id': f'obs-{index:03}', 'timestamp': index, 'question': 'Q' * 900,
                 'arguments': {'flag': False, 'value': None},
                 'result': {'body': 'R' * 900}, 'status': 'success', 'tool_name': 'fixture'}
                for index in range(100)],
            'conversation_state': {'raw': False}, 'deliveries': []}


@pytest.mark.parametrize('scenario', ['history_append', 'history_rollover',
                                     'observation_append', 'observation_rollover', 'scope_switch'])
def test_large_prefix_benefits_and_counterexamples(scenario):
    """Keep parsed content identical while explicitly testing both regression cases."""
    before = _fixture()
    after = copy.deepcopy(before)
    after['messages'] = [{'id': 'current-2'}]
    after['clock'] = {'now': 2}
    if scenario == 'history_append':
        after['history'].append({'id': 'history-2', 'body': 'new'})
    elif scenario == 'history_rollover':
        after['history'] = [{'id': 'history-2', 'body': 'new'}]
    elif scenario == 'observation_append':
        after['observations'].append({'id': 'obs-100', 'result': 'new'})
    elif scenario == 'observation_rollover':
        after['observations'] = after['observations'][1:] + [{'id': 'obs-100', 'result': 'new'}]
    else:
        after['scope'] = {'id': 'group-b'}
        after['observations'][0]['id'] = 'different-scope'
    originals = copy.deepcopy((before, after))
    wires = [build_context(**item) for item in (before, after)]
    old_wires = [_old_wire(wire) for wire in wires]
    for wire, old in zip(wires, old_wires):
        assert json.loads(wire) == json.loads(old)
        assert len(wire.encode('utf-8')) == len(old.encode('utf-8'))
        assert list(json.loads(wire))[:4] == ['version', 'source', 'observations', 'conversation']
    new_bytes, old_bytes = _prefix(*wires), _prefix(*old_wires)
    if scenario == 'history_append':
        assert new_bytes > 400000
        assert old_bytes < 250000
    elif scenario == 'history_rollover':
        assert new_bytes > 180000
        assert old_bytes < 200
    elif scenario == 'observation_append':
        assert new_bytes > 180000
        assert new_bytes < old_bytes  # Unchanged history formerly preceded observations.
    else:
        assert new_bytes < 200
        assert old_bytes > 240000  # A changed observation head sacrifices history prefix.
    assert (before, after) == originals


@pytest.mark.parametrize('observations', [None, False, [], {}, [{'id': 'id', 'raw': False}]])
def test_false_like_and_private_history_values_preserved(observations):
    """Only None gets the existing default; private history remains a supplied list."""
    inputs = {'scope': {'kind': 'private'}, 'history': [{'id': 'h', 'raw': None}],
              'messages': [], 'clock': None, 'observations': observations}
    original = copy.deepcopy(inputs)
    parsed = json.loads(build_context(**inputs))
    assert parsed['observations'] == ([] if observations is None else observations)
    assert parsed['conversation']['history'] == inputs['history']
    assert parsed['version'] == 1
    assert inputs == original
