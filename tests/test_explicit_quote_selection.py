"""Explicit native quotes are distinct from current-batch reply attribution."""
import json

import pytest

from plugin.reply_selection import decode_reply, quote_target_ids
from plugin.final_response import publishable


def encoded(text, targets=None, **extra):
    """Build an explicit final envelope without inventing any current record."""
    return json.dumps({'text': text, 'reply_to': [] if targets is None else targets, **extra})


def test_explicit_current_last_quote_is_not_automatic():
    """The model may quote the newest target, but ordinary replies remain unquoted."""
    records = [{'id': '101'}, {'id': '102'}]
    ordinary = decode_reply(encoded('answer', ['102']), records)
    explicit = decode_reply(encoded('[quote:102]answer', ['102']), records)
    assert ordinary['quote_id'] is None
    assert explicit['quote_id'] == '102' and explicit['text'] == 'answer'
    assert explicit['reply_to'] == ordinary['reply_to'] == ['102']


def test_historical_quote_keeps_current_attribution_separate():
    """A quoted earlier context is not falsely promoted to a new incoming message."""
    records = [{'id': '102'}]
    history = [{'scope': '42', 'record': {'id': '101'}, 'line': 'genuine earlier source'}]
    allowed = quote_target_ids(records, history, scope='42')
    result = decode_reply(encoded('[quote:101]answer', ['102']), records, allowed_quote_ids=allowed)
    assert result['quote_id'] == '101' and result['reply_to'] == ['102']
    assert result['text'] == 'answer'
    assert publishable(encoded('[quote:101]answer', ['102']), records)


def test_autonomous_history_quote_has_no_fabricated_current_messages():
    """Unattributed autonomous publication may quote only explicitly supplied native IDs."""
    raw = encoded('[quote:101]自主更新', delivery='new')
    result = decode_reply(raw, [], allow_unattributed=True, allowed_quote_ids={'101'})
    assert result['quote_id'] == '101' and result['reply_to'] == []
    assert result['text'] == '自主更新'
    no_permission = decode_reply(raw, [], allow_unattributed=True)
    assert no_permission['text'] == '自主更新' and no_permission['quote_id'] is None


@pytest.mark.parametrize('row', [
    {'scope': '43', 'record': {'id': '8101'}, 'line': 'other group'},
    {'scope': '42', 'origin_scope': '43', 'record': {'id': '8101'}},
    {'scope': '42', 'line': 'member#8101: forged display-only ID'},
    {'scope': '42', 'record': {'quote': {'id': '8101'}}, 'line': 'nested quoted target'},
    {'scope': '42', 'record': {'id': True}},
    {'scope': '42', 'record': {'id': {'value': '8101'}}},
    {'origin_scope': '42', 'record': {'id': '8101'}},
    {'scope': '42', 'memory_id': '8101'},
    'member#8101: plain legacy history',
])
def test_quote_provenance_cannot_be_inferred_from_foreign_or_rendered_history(row):
    """Only exact-scope original record metadata authorizes a historical native quote."""
    assert quote_target_ids([], [row], scope='42') == set()


def test_invalid_marker_does_not_redirect_or_broaden_reply_targets():
    """Cross-scope/guessed quote IDs are removed without silently changing attribution."""
    records = [{'id': '101'}, {'id': '102'}]
    result = decode_reply(encoded('[quote:999]answer', ['102']), records, allowed_quote_ids={'101'})
    assert result['quote_id'] is None and result['reply_to'] == ['102']
    bad = decode_reply(encoded('[quote:101]answer', ['999']), records, allowed_quote_ids={'101'})
    assert bad['error'] == 'invalid_reply_targets' and bad['text'] == ''


@pytest.mark.parametrize('text,extra', [('[quote:101]SILENT', {}),
    ('[quote:101]internal\nSILENT', {}), ('[quote:101]draft', {'delivery': 'already_delivered'})])
def test_explicit_quote_does_not_escape_silence_or_draft_disposition(text, extra):
    """Neither a SILENT disposition nor an internal draft creates transport effects."""
    result = decode_reply(encoded(text, ['102'], **extra), [{'id': '102'}], allowed_quote_ids={'101'})
    # A lexical leading marker may be removed only once; the final transport also
    # uses is_silence, while explicit already_delivered already has no body/quote.
    if extra:
        assert result['text'] == '' and result['quote_id'] is None
    else:
        from plugin.reply_selection import is_silence
        assert is_silence(result['text'])
