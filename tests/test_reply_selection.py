"""Pure tests for current-batch targeting and strict silence protocol handling."""
import json

import pytest

from plugin.reply_selection import decode_reply, is_silence


def test_structured_selection_quotes_first_nonfinal_target():
    """An earlier selected message supplies the native quote destination."""
    result = decode_reply('{"reply_to":["101"],"text":"答复"}', [{"id":"101"},{"id":"102"}])
    assert result["reply_to"] == ["101"]
    assert result["quote_id"] == "101"
    assert result["text"] == "答复"


def test_structured_selection_of_final_target_needs_no_quote():
    """The final record does not acquire an unnecessary automatic quote."""
    result = decode_reply('{"reply_to":["102"],"text":"答复"}', [{"id":"101"},{"id":"102"}])
    assert result["reply_to"] == ["102"]
    assert result["quote_id"] is None


def test_multiple_positive_targets_are_one_reply_and_quote_first_nonfinal():
    """One response may positively select several messages without separate decisions."""
    result = decode_reply('{"reply_to":[101,102],"text":"合并回答"}', [{"id":101},{"id":102},{"id":103}])
    assert result["reply_to"] == ["101", "102"]
    assert result["quote_id"] == "101"


def test_empty_selection_is_silence():
    """No selected target and no prose is a valid silent completion."""
    result = decode_reply('{"reply_to":[],"text":""}', [{"id":"101"}])
    assert result["text"] == ""
    assert result["reply_to"] == []
    assert result["error"] is None


def test_unknown_target_fails_closed():
    """Fabricated attribution cannot be repaired by guessing another recipient."""
    result = decode_reply('{"reply_to":["999"],"text":"答复"}', [{"id":"101"}])
    assert result["text"] == ""
    assert result["error"] == "invalid_reply_targets"


def test_plain_text_does_not_guess_current_target():
    """Compatibility prose is not treated as evidence of a selected message."""
    result = decode_reply("普通回答", [{"id":"101"}, {"id":"102"}])
    assert result["reply_to"] == []
    assert result["quote_id"] is None


@pytest.mark.parametrize("value", [
    None, "", " \t\r\n", "SILENT", "silent", "SiLeNt", " silent \n",
    "SILENT.", "silent!", "Silent。", "silent！", "。!SILENT！..",
    '"SILENT"', ' "silent!" ', '" \\tSiLeNt。\\n "', '""',
])
def test_whole_output_silence_forms(value):
    """Only full control tokens or absent text terminate the legacy reply."""
    assert is_silence(value) is True
    result = decode_reply(value, [{"id": "101"}])
    assert result["text"] == ""
    assert result["error"] is None
    assert result["quote_id"] is None


@pytest.mark.parametrize("text", [
    "I prefer silent mode", "The word SILENT is a sentinel.",
    "SILENT mode", "not SILENT", "SILENT\nnormal reply", "SILENT SILENT",
    "SILENT?", "SILENT,", "SILENT... more", "SILENT .", "...", "！！",
    "`SILENT`", "```\nSILENT\n```", "```json\n\"SILENT\"\n```",
    "'SILENT'", '"SILENT" trailing', '"SILENT', '"SILENT".',
    '"I prefer silent mode"', '["SILENT"]', '{"action":"silent"}',
    '{"text":"SILENT"}', '"\\\"SILENT\\\""', "不回复", "（不回复）",
])
def test_nonprotocol_text_is_preserved(text):
    """Mentions, unsupported wrappers, and near matches must not erase prose."""
    assert is_silence(text) is False
    result = decode_reply(text, [{"id": "101"}])
    assert result["text"] == text
    assert result["structured"] is False
    assert result["error"] is None


@pytest.mark.parametrize("value", [False, True, 0, 1, [], {}, {"action": "silent"}])
def test_nonstring_values_are_not_control_tokens(value):
    """Do not coerce unknown action objects or arbitrary values into silence."""
    assert is_silence(value) is False


@pytest.mark.parametrize("text", [
    "SILENT", "silent", " SiLeNt! \n", "。SILENT！", '"SILENT"', "", " \t",
])
def test_valid_structured_silence_has_no_quote(text):
    """Validated targets may explicitly select silence without delivery metadata."""
    raw = json.dumps({"reply_to": [101, "102", 101], "text": text})
    assert is_silence(raw) is False
    result = decode_reply(raw, [{"id": 101}, {"id": "102"}, {"id": "103"}])
    assert result == {"text": "", "reply_to": ["101", "102"],
                      "quote_id": None, "structured": True, "error": None}


@pytest.mark.parametrize("targets", [
    ["999"], ["101", "999"], [True], [None], [[]], [{}], "101", None, [],
])
def test_structured_sentinel_does_not_bypass_target_validation(targets):
    """Invalid attribution must fail closed even when the text requests silence."""
    raw = json.dumps({"reply_to": targets, "text": "silent"})
    result = decode_reply(raw, [{"id": "101"}])
    assert result["structured"] is True
    assert result["error"] == "invalid_reply_targets"
    assert result["text"] == ""
    assert result["reply_to"] == []
    assert result["quote_id"] is None


@pytest.mark.parametrize("text", [
    "I prefer silent mode", "`SILENT`", "SILENT?", '"SILENT" trailing',
    "explain\n>SILENT", "explain\n`SILENT`", "```text\nSILENT",
    "explain\nSILENT\nordinary reply", "~~~\nSILENT\n~~~",
])
def test_structured_silence_mentions_remain_replies(text):
    """Whole-token recognition must not suppress ordinary validated responses."""
    raw = json.dumps({"reply_to": ["101"], "text": text})
    result = decode_reply(raw, [{"id": "101"}, {"id": "102"}])
    assert result["text"] == text
    assert result["reply_to"] == ["101"]
    assert result["quote_id"] == "101"
    assert result["error"] is None


@pytest.mark.parametrize("text", [None, True, 0, [], {"action": "silent"}])
def test_structured_nonstring_text_remains_invalid(text):
    """Only string response text participates in the structured protocol."""
    raw = json.dumps({"reply_to": ["101"], "text": text})
    result = decode_reply(raw, [{"id": "101"}])
    assert result["error"] == "invalid_reply_targets"
    assert result["text"] == ""


@pytest.mark.parametrize("records", [None, []])
def test_json_response_without_current_batch_stays_legacy(records):
    """Without current IDs, a JSON-shaped answer is not an actionable response."""
    raw = '{"reply_to":["101"],"text":"SILENT"}'
    result = decode_reply(raw, records)
    assert result["text"] == raw
    assert result["structured"] is False
    assert result["error"] is None


def test_fenced_structured_sentinel_is_not_guessed_as_protocol():
    """Unspecified fenced wrappers remain literal, not a broadened control schema."""
    raw = '```json\n{"reply_to":["101"],"text":"SILENT"}\n```'
    result = decode_reply(raw, [{"id": "101"}])
    assert result["text"] == raw
    assert result["structured"] is False


def test_legacy_quote_is_not_silently_removed_to_create_control_token():
    """A control word with transport markup is not the whole original output."""
    result = decode_reply("[quote:101] SILENT", [{"id": "101"}])
    assert result["text"] == "[quote:101] SILENT"
    assert result["quote_id"] == "101"


def test_structured_quote_removal_does_not_reclassify_control_text():
    """Validate the original text before stripping legacy attribution markers."""
    raw = '{"reply_to":["101"],"text":"[quote:999] SILENT"}'
    result = decode_reply(raw, [{"id": "101"}, {"id": "102"}])
    assert result["text"] == " SILENT"
    assert result["quote_id"] == "101"
    assert result["error"] is None


@pytest.mark.parametrize("text", [
    "（SILENT）", "(SILENT)", "（silent）", "(SiLeNt)",
    " \t（ SiLeNt! ）\n", "( silent。 )", "（SILENT）！", ".(SILENT).",
])
def test_one_balanced_parenthesis_pair_is_silence(text):
    """Observed full-token parenthetical outputs terminate plain and JSON replies."""
    for raw in (text, json.dumps(text)):
        assert is_silence(raw) is True
        result = decode_reply(raw, [{"id": "101"}])
        assert result["text"] == ""
        assert result["quote_id"] is None
        assert result["error"] is None
    raw = json.dumps({"reply_to": ["101"], "text": text})
    result = decode_reply(raw, [{"id": "101"}, {"id": "102"}])
    assert result == {"text": "", "reply_to": ["101"], "quote_id": None,
                      "structured": True, "error": None}


@pytest.mark.parametrize("text", [
    "(silent mode)", "（I prefer silent mode）", "I prefer (SILENT)",
    "(SILENT) is a sentinel", "(SILENT", "SILENT)", "（SILENT", "SILENT）",
    "(SILENT）", "（SILENT)", "((SILENT))", "（（SILENT））",
    "（(SILENT)）", "(（SILENT）)", "`（SILENT）`", "```\n(SILENT)\n```",
    "()", "（ ）", "(SILENT?)", "( SILENT . )",
])
def test_parenthesis_boundary_preserves_prose_and_nonprotocol_forms(text):
    """Only one matching pair around the whole token is protocol normalization."""
    for raw in (text, json.dumps(text)):
        assert is_silence(raw) is False
        assert decode_reply(raw, [{"id": "101"}])["text"] == raw
    raw = json.dumps({"reply_to": ["101"], "text": text})
    result = decode_reply(raw, [{"id": "101"}, {"id": "102"}])
    assert result["text"] == text
    assert result["quote_id"] == "101"
    assert result["error"] is None


@pytest.mark.parametrize("targets", [["999"], ["101", "999"], []])
def test_parenthetical_sentinel_keeps_target_validation_first(targets):
    """Observed punctuation variants never grant invalid targets permission to reply."""
    raw = json.dumps({"reply_to": targets, "text": "（SILENT）"})
    result = decode_reply(raw, [{"id": "101"}])
    assert result["text"] == ""
    assert result["quote_id"] is None
    assert result["error"] == "invalid_reply_targets"


@pytest.mark.parametrize("records", [None, []])
@pytest.mark.parametrize("text", [
    "", " \t\r\n", "SILENT", "silent", " SiLeNt! \n", "。SILENT！",
    '"SILENT"', '""', '" \\tSiLeNt。\\n "', "（SILENT）", "(silent)",
    " \t（ SiLeNt! ）\n", "( silent。 )", "（SILENT）！", ".(SILENT).",
])
def test_unattributed_empty_selection_accepts_silence_forms(records, text):
    """Explicit no-batch tasks consume structured silence without producing quotes."""
    raw = json.dumps({"reply_to": [], "text": text})
    assert is_silence(raw) is False
    result = decode_reply(raw, records, allow_unattributed=True)
    assert result == {"text": "", "reply_to": [], "quote_id": None,
                      "structured": True, "error": None}


@pytest.mark.parametrize("records", [None, []])
@pytest.mark.parametrize("text", ["自主任务完成", "I prefer silent mode", "SILENT?", "（SILENT?)"])
def test_unattributed_empty_selection_allows_message(records, text):
    """A no-current-record task may send literal prose without invented attribution."""
    raw = json.dumps({"reply_to": [], "text": text, "extra": "unchanged"})
    result = decode_reply(raw, records, allow_unattributed=True)
    assert result == {"text": text, "reply_to": [], "quote_id": None,
                      "structured": True, "error": None}


@pytest.mark.parametrize("records", [None, []])
@pytest.mark.parametrize("targets", [
    [""], ["999"], [101], [True], [None], [[]], [{}], ["", "999"],
    "", "101", None, {}, False, 0,
])
@pytest.mark.parametrize("text", ["", "SILENT", "（silent）", "task completed"])
def test_unattributed_targets_cannot_name_any_message(records, targets, text):
    """Empty/unknown IDs and wrong container types fail even for silence text."""
    raw = json.dumps({"reply_to": targets, "text": text})
    result = decode_reply(raw, records, allow_unattributed=True)
    assert result == {"text": "", "reply_to": [], "quote_id": None,
                      "structured": True, "error": "invalid_reply_targets"}


@pytest.mark.parametrize("records", [None, []])
@pytest.mark.parametrize("text", [None, True, 0, [], {"text": "SILENT"}])
def test_unattributed_text_must_be_string(records, text):
    """No-batch permission never widens the structured text schema."""
    raw = json.dumps({"reply_to": [], "text": text})
    result = decode_reply(raw, records, allow_unattributed=True)
    assert result["structured"] is True
    assert result["error"] == "invalid_reply_targets"
    assert result["text"] == ""
    assert result["quote_id"] is None


@pytest.mark.parametrize("records", [None, []])
@pytest.mark.parametrize("raw", [
    '{"action":"silent"}', '{"text":"SILENT"}', '{"reply_to":[]}',
    '[{"reply_to":[],"text":""}]',
    '```json\n{"reply_to":[],"text":""}\n```',
    'Here is my response: {"reply_to":[],"text":""}',
    json.dumps('{"reply_to":[],"text":""}'),
])
def test_unattributed_mode_preserves_nonprotocol_json_and_wrappers(records, raw):
    """Only a direct object containing both reserved keys opts into the schema."""
    result = decode_reply(raw, records, allow_unattributed=True)
    assert result == {"text": raw, "reply_to": [], "quote_id": None,
                      "structured": False, "error": None}


@pytest.mark.parametrize("records", [None, []])
@pytest.mark.parametrize("text", [
    '{"reply_to":[],"text":""}', '{"reply_to":[],"text":"SILENT"}',
    '{"reply_to":["999"],"text":"literal nested reply"}',
    json.dumps('{"reply_to":[],"text":""}'),
])
def test_unattributed_structured_text_is_unwrapped_once_only(records, text):
    """Literal JSON within structured text is content, never another reply envelope."""
    raw = json.dumps({"reply_to": [], "text": text})
    result = decode_reply(raw, records, allow_unattributed=True)
    assert result == {"text": text, "reply_to": [], "quote_id": None,
                      "structured": True, "error": None}


@pytest.mark.parametrize("records", [None, []])
@pytest.mark.parametrize("text, expected", [
    ("[quote:999]自主任务完成", "自主任务完成"),
    ("[quote:999] SILENT", " SILENT"),
    ("[quote:999]", ""),
])
def test_unattributed_structured_quote_cannot_fabricate_attribution(records, text, expected):
    """Strip legacy quote markers only after validating the original structured text."""
    raw = json.dumps({"reply_to": [], "text": text})
    result = decode_reply(raw, records, allow_unattributed=True)
    assert result == {"text": expected, "reply_to": [], "quote_id": None,
                      "structured": True, "error": None}
    legacy = decode_reply(text, records, allow_unattributed=True)
    assert legacy["text"] == text
    assert legacy["quote_id"] == "999"


@pytest.mark.parametrize("targets, text, expected_error", [
    ([], "", None), ([], "SILENT", "invalid_reply_targets"),
    ([], "ordinary reply", "invalid_reply_targets"),
    ([""], "", "invalid_reply_targets"), (["999"], "SILENT", "invalid_reply_targets"),
    (["101"], "reply", None), (["101"], "SILENT", None),
])
def test_unattributed_opt_in_does_not_change_current_batch_rules(targets, text, expected_error):
    """A real current batch always retains its existing required-target semantics."""
    raw = json.dumps({"reply_to": targets, "text": text})
    records = [{"id": "101"}, {"id": "102"}]
    original = decode_reply(raw, records)
    opted_in = decode_reply(raw, records, allow_unattributed=True)
    assert opted_in == original
    assert opted_in["error"] == expected_error


@pytest.mark.parametrize("records", [None, []])
@pytest.mark.parametrize("text", ["", "SILENT", "ordinary task message"])
def test_default_no_batch_empty_targets_stay_legacy(records, text):
    """Empty-target envelopes are still literal JSON unless permission is explicit."""
    raw = json.dumps({"reply_to": [], "text": text})
    result = decode_reply(raw, records)
    assert result == {"text": raw, "reply_to": [], "quote_id": None,
                      "structured": False, "error": None}


def test_unattributed_opt_in_is_keyword_only():
    """Callers must explicitly name the permission rather than pass an ambiguous bool."""
    with pytest.raises(TypeError):
        decode_reply('{"reply_to":[],"text":""}', [], True)


@pytest.mark.parametrize("text", [
    "explain\nSILENT", "(internal narration)\n\nSILENT",
    '"quoted narration"\nSILENT', '"unfinished narration\nSILENT',
    "arbitrary preceding paragraphs\n\nmore narration\nSiLeNt\n\n",
    "internal\r\n\t silent \t\r\n", "[quote:101] internal\nSILENT",
    "```python\nprint('example')\n```\nSILENT",
    "~~~text\nSILENT\n~~~\nSILENT", "````\n```\n````\nSILENT",
])
def test_terminal_silence_suppresses_all_narration(text):
    """Explicit final disposition suppresses arbitrary prefixes and quote effects."""
    for raw in (text, json.dumps(text)):
        assert is_silence(raw) is True
        assert decode_reply(raw, [{"id": "101"}]) == {
            "text": "", "reply_to": [], "quote_id": None,
            "structured": False, "error": None,
        }


@pytest.mark.parametrize("text", [
    "explain\nSILENT.", "explain\n(SILENT)", "explain\nnot SILENT",
    "explain\n>SILENT", "explain\n> SILENT", "explain\n`SILENT`",
    'explain\n"SILENT"', "explain\n'SILENT'", "explain\nSILENT SILENT",
    "explain\nSILENT\nordinary reply", "SILENT\nnormal reply",
    "```\nSILENT", "~~~\nSILENT", "prefix\n```python\nSILENT\n\n",
    "````\n```\nSILENT", "~~~\n```\nSILENT",
    "```\n``` not a closing fence\nSILENT",
    "   ```python\nSILENT", "\t~~~example\nSILENT",
    '```json\n"explain\\nSILENT"\n```',
    '{"text":"explain\\nSILENT"}', '"explain\\\\nSILENT"',
])
def test_terminal_silence_preserves_literal_examples_and_near_matches(text):
    """Final-line control is literal, unfenced, and not recursively JSON-decoded."""
    for raw in (text, json.dumps(text)):
        assert is_silence(raw) is False
        assert decode_reply(raw, [{"id": "101"}])["text"] == raw


@pytest.mark.parametrize("targets, error", [
    (["101"], None), ([], "invalid_reply_targets"),
    (["999"], "invalid_reply_targets"), (["101", "999"], "invalid_reply_targets"),
])
def test_terminal_silence_validates_structured_targets_first(targets, error):
    """Terminal disposition never grants a missing or fabricated target permission."""
    raw = json.dumps({"reply_to": targets, "text": "internal narration\nSILENT"})
    result = decode_reply(raw, [{"id": "101"}, {"id": "102"}])
    assert result == {
        "text": "", "reply_to": ["101"] if error is None else [],
        "quote_id": None, "structured": True, "error": error,
    }


@pytest.mark.parametrize("records", [None, []])
def test_terminal_silence_keeps_no_batch_envelopes_opt_in(records):
    """Only authorized no-batch envelopes interpret their validated text as control."""
    raw = json.dumps({"reply_to": [], "text": "internal narration\nSILENT"})
    assert decode_reply(raw, records)["text"] == raw
    assert decode_reply(raw, records, allow_unattributed=True) == {
        "text": "", "reply_to": [], "quote_id": None,
        "structured": True, "error": None,
    }
