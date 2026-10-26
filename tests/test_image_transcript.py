"""Offline contracts for frozen initial images and append-only tool image data."""

import copy
from pathlib import Path
import sys

import asyncio

import test_production_integration as integration

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugin.image_transcript import IMAGE_OBSERVATION, with_images  # noqa: E402

# Reuse the offline AST host fixture without importing framework or touching network.
production = integration.production


def _url(ref):
    """Convert fixture references without files, network, or plugin framework imports."""
    return "data:image/png;base64," + str(ref)


def _images(message):
    """Return image URLs without interpreting neutral observation text as instructions."""
    return [part["image_url"]["url"] for part in message["content"]
            if part["type"] == "image_url"]


def test_initial_images_preserve_original_text_and_message_metadata():
    """Only initial enrichment copies the user dictionary; source data stays unchanged."""
    messages = [{"role": "system", "content": "system"},
                {"role": "user", "content": "original question", "name": "source"}]
    original = copy.deepcopy(messages)
    result = with_images(messages, ["a", "b", "c", "d"], _url, 3, initial=True)
    assert messages == original
    assert result is not messages
    assert result[0] is messages[0]
    assert result[1] is not messages[1]
    assert result[1]["name"] == "source"
    assert result[1]["content"][0] == {"type": "text", "text": "original question"}
    assert _images(result[1]) == [_url(ref) for ref in ("b", "c", "d")]


def test_multipart_initial_attachments_are_not_stringified_or_lost():
    """Existing text, image, and arbitrary attachment parts survive the first request."""
    parts = [{"type": "text", "text": "question"},
             {"type": "image_url", "image_url": {"url": _url("a"), "detail": "high"}},
             {"type": "file", "file": {"file_id": "file-fixture"}}]
    messages = [{"role": "user", "content": parts}]
    result = with_images(messages, ["a", "b", "b"], _url, 3, initial=True)
    assert result[0]["content"][:3] == parts
    assert result[0]["content"][:3][0] is parts[0]
    assert len(parts) == 3
    assert _images(result[0]) == [_url("a"), _url("b")]


def test_successive_image_batches_keep_exact_submitted_prefix_and_tool_pairing():
    """New screenshots append after results without reconverting or sliding older images."""
    calls = []

    def convert(ref):
        """Track conversion so prior attachments cannot be silently regenerated."""
        calls.append(ref)
        return _url(ref)

    messages = with_images([{"role": "system", "content": "system"},
                            {"role": "user", "content": "question"}],
                           ["initial"], convert, 3, initial=True)
    submitted = copy.deepcopy(messages)
    for index in range(5):
        messages.extend([
            {"role": "assistant", "content": "", "tool_calls": [{"id": str(index)}]},
            {"role": "tool", "tool_call_id": str(index), "content": "tool data"}])
        before = list(messages)
        messages = with_images(messages, [f"shot-{index}"], convert, 3)
        assert all(previous is current for previous, current in zip(before, messages))
        assert messages[:len(submitted)] == submitted
        assert messages[-2]["role"] == "tool"
        assert messages[-1]["content"][0]["text"] == IMAGE_OBSERVATION
        assert _images(messages[-1]) == [_url(f"shot-{index}")]
        submitted = copy.deepcopy(messages)
    assert calls == ["initial", *(f"shot-{index}" for index in range(5))]
    assert _images(messages[1]) == [_url("initial")]


def test_empty_failed_and_disabled_batches_preserve_content_types():
    """An unavailable image never creates an empty observation or multipart string rewrite."""
    messages = [{"role": "user", "content": "question"}]
    for refs, converter, limit in (([], _url, 3), (["a"], lambda ref: "", 3),
                                    (["a"], _url, 0), (["a"], _url, -1)):
        for initial in (True, False):
            result = with_images(messages, refs, converter, limit, initial=initial)
            assert result == messages
            assert result is not messages
            assert result[0] is messages[0]


def test_batch_limit_applies_only_to_new_observation_and_deduplicates():
    """The per-request addition cap never removes earlier initial or observation images."""
    messages = with_images([{"role": "user", "content": "question"}],
                           ["initial"], _url, 3, initial=True)
    prefix = list(messages)
    result = with_images(messages, ["old", "a", "b", "b"], _url, 3)
    assert result[:-1] == prefix
    assert result[0] is prefix[0]
    assert _images(result[-1]) == [_url("a"), _url("b")]


def test_production_vm_shots_freeze_prefix_across_multiple_requests(production, monkeypatch):
    """Real host closure retains images and native pairs across shots and repeated refs."""
    plugin, event = production(), integration.Event()
    converted = []

    async def initial_refs(*args):
        """Capture original attachments before the first screenshot tool can run."""
        return ["initial"], ""

    async def dispatch(name, args, scope="", event=None):
        """Return offline screenshot paths without invoking VM or file acquisition."""
        assert name == "vm_shot"
        return {"ok": True, "path": args["path"]}

    def convert(ref):
        """Change return values on every conversion to detect accidental prefix rewrites."""
        converted.append(ref)
        return _url(f"{ref}-{len(converted)}")

    plugin._refs_for = initial_refs
    plugin._dispatch_tool = dispatch
    plugin._as_llm_url = convert
    requests = integration._provider(monkeypatch, [
        ("", [integration._call("vm_shot", {"path": "shot-a"}, "a")]),
        ("", [integration._call("vm_shot", {"path": "shot-b"}, "b")]),
        ("", [integration._call("vm_shot", {"path": "shot-b"}, "repeat-b")]),
        ("finished", []),
    ])
    assert asyncio.run(plugin._decide_tools_first(event, "42", event.text, True)) == "finished"
    assert converted == ["initial", "shot-a", "shot-b", "shot-b"]
    for previous, current in zip(requests, requests[1:]):
        prefix = previous["messages"]
        assert current["messages"][:len(prefix)] == prefix
    assert [message["role"] for message in requests[-1]["messages"]] == [
        "system", "user", "assistant", "tool", "user", "assistant", "tool", "user",
        "assistant", "tool", "user"]
    assert _images(requests[-1]["messages"][10]) == [_url("shot-b-4")]
    assert _images(requests[-2]["messages"][7]) == [_url("shot-b-3")]
    assert _images(requests[-1]["messages"][1]) == [_url("initial-1")]
    assert _images(requests[-1]["messages"][4]) == [_url("shot-a-2")]
    assert _images(requests[-1]["messages"][7]) == [_url("shot-b-3")]
