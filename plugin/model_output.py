"""Extract public model text without promoting separate reasoning fields."""

import re


# Only an explicit, leading raw protocol marker identifies private text.
_LEADING_THINK = re.compile(r"\s*<think>")
_END_THINK = "</think>"
_PUBLIC_BLOCK_TYPES = frozenset(("text", "output_text"))
_PRIVATE_CHANNELS = frozenset(("analysis", "reasoning", "thinking"))


def _public_raw_text(content):
    """Remove complete leading protocol think blocks, failing closed if unclosed."""
    text = content
    while match := _LEADING_THINK.match(text):
        end = text.find(_END_THINK, match.end())
        if end < 0:
            return ""
        text = text[end + len(_END_THINK):]
    return text


def parse_message(message):
    """Return public text and original tool calls from one ChatCompletions message.

    Reasoning fields are intentionally not inspected or used as fallback content.
    Unknown structured block types are ignored rather than serialized.
    """
    if not isinstance(message, dict):
        raise ValueError("invalid_model_output")
    calls = message.get("tool_calls")
    if calls is None:
        calls = []
    elif not isinstance(calls, list):
        raise ValueError("invalid_model_output")

    content = message.get("content")
    if content is None:
        text = ""
    elif isinstance(content, str):
        text = _public_raw_text(content)
    elif isinstance(content, list):
        parts = []
        for block in content:
            # Only recognized public text blocks are eligible; never stringify objects.
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if not isinstance(block_type, str) or block_type not in _PUBLIC_BLOCK_TYPES:
                continue
            channel = block.get("channel")
            if channel is not None and (not isinstance(channel, str) or channel != "final"):
                continue
            value = block.get("text")
            if isinstance(value, str):
                parts.append(value)
        text = "".join(parts)
    else:
        raise ValueError("invalid_model_output")

    # Message-level analysis never becomes public, even with a populated content.
    channel = message.get("channel")
    if isinstance(channel, str) and channel in _PRIVATE_CHANNELS:
        text = ""
    return text, calls
