"""Regression tests for public-only ChatCompletions message extraction."""

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def parse_message():
    """Load the pure module without importing or initializing the QQ plugin."""
    path = Path(__file__).resolve().parents[1] / "plugin" / "model_output.py"
    spec = importlib.util.spec_from_file_location("isolated_model_output", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.parse_message


def test_raw_content_is_public_and_reasoning_is_never_fallback(parse_message):
    """Treat separate reasoning fields as unavailable, even with no public content."""
    assert parse_message({"content": "回答", "reasoning_content": "secret", "reasoning": "hidden"}) == ("回答", [])
    assert parse_message({"content": None, "reasoning_content": "secret"}) == ("", [])
    assert parse_message({"reasoning": "secret"}) == ("", [])


def test_only_complete_leading_raw_think_blocks_are_removed(parse_message):
    """Fail closed on unclosed leading private blocks, preserving public tails."""
    assert parse_message({"content": "  <think>secret</think>\n<think>more</think>Visible"}) == ("Visible", [])
    assert parse_message({"content": "<think>unclosed secret"}) == ("", [])
    assert parse_message({"content": "<think>closed</think> <think>unclosed"}) == ("", [])


def test_quoted_fenced_and_inline_think_are_literal(parse_message):
    """Do not broadly strip ordinary quoted text or fenced examples."""
    for text in ("Quote: <think>literal</think>", "\"<think>quoted</think>\"", "```xml\n<think>example</think>\n```"):
        assert parse_message({"content": text}) == (text, [])
    assert parse_message({"content": "你好，中文无需审查"}) == ("你好，中文无需审查", [])


def test_structured_text_blocks_are_public_only_by_type_and_channel(parse_message):
    """Accept only known text types on final/default channels."""
    blocks = [
        {"type": "text", "text": "A"},
        {"type": "output_text", "channel": "final", "text": "B"},
        {"type": "text", "channel": "analysis", "text": "secret"},
        {"type": "output_text", "channel": "reasoning", "text": "secret"},
        {"type": "text", "channel": "thinking", "text": "secret"},
        {"type": "text", "channel": "other", "text": "unknown"},
        {"type": "image_url", "text": "unknown"},
        {"type": "text", "text": 42},
        "not a text block",
    ]
    assert parse_message({"content": blocks}) == ("AB", [])
    assert parse_message({"channel": "analysis", "content": "secret"}) == ("", [])


class Unprintable:
    """Detect any accidental conversion of unknown objects to public text."""

    def __str__(self):
        """Raise so tests detect unexpected string serialization."""
        raise AssertionError("untrusted object was serialized")


def test_malicious_objects_are_not_serialized(parse_message):
    """Unknown block values are ignored, and invalid top-level content rejects."""
    hostile = Unprintable()
    assert parse_message({"content": [hostile, {"type": "text", "text": hostile}]}) == ("", [])
    with pytest.raises(ValueError, match="^invalid_model_output$"):
        parse_message({"content": hostile})
    with pytest.raises(ValueError, match="^invalid_model_output$"):
        parse_message(hostile)


def test_tool_calls_preserved_and_malformed_type_rejected(parse_message):
    """Preserve list identity and order while rejecting unsupported tool-call types."""
    calls = [{"id": "one"}, {"id": "two"}]
    assert parse_message({"content": None, "tool_calls": calls}) == ("", calls)
    assert parse_message({"content": "done", "reasoning_content": "secret", "tool_calls": calls})[1] is calls
    with pytest.raises(ValueError, match="^invalid_model_output$"):
        parse_message({"tool_calls": {"id": "not a list"}})
