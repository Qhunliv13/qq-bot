"""Pure tests for explicit positive current-batch reply targeting."""
from plugin.reply_selection import decode_reply


def test_structured_selection_quotes_first_nonfinal_target():
    result = decode_reply('{"reply_to":["101"],"text":"答复"}', [{"id":"101"},{"id":"102"}])
    assert result["reply_to"] == ["101"]
    assert result["quote_id"] == "101"
    assert result["text"] == "答复"


def test_structured_selection_of_final_target_needs_no_quote():
    result = decode_reply('{"reply_to":["102"],"text":"答复"}', [{"id":"101"},{"id":"102"}])
    assert result["reply_to"] == ["102"]
    assert result["quote_id"] is None


def test_multiple_positive_targets_are_one_reply_and_quote_first_nonfinal():
    result = decode_reply('{"reply_to":[101,102],"text":"合并回答"}', [{"id":101},{"id":102},{"id":103}])
    assert result["reply_to"] == ["101", "102"]
    assert result["quote_id"] == "101"


def test_empty_selection_is_silence():
    result = decode_reply('{"reply_to":[],"text":""}', [{"id":"101"}])
    assert result["text"] == ""
    assert result["reply_to"] == []
    assert result["error"] is None


def test_unknown_target_fails_closed():
    result = decode_reply('{"reply_to":["999"],"text":"答复"}', [{"id":"101"}])
    assert result["text"] == ""
    assert result["error"] == "invalid_reply_targets"


def test_plain_text_does_not_guess_current_target():
    result = decode_reply("普通回答", [{"id":"101"}, {"id":"102"}])
    assert result["reply_to"] == []
    assert result["quote_id"] is None
