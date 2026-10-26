"""Offline contracts for shared forwarded-chat parsing and event-API resolution."""

import asyncio
from enum import Enum
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugin.forward_context import parse_forward_descriptors, resolve  # noqa: E402


class ComponentType(Enum):
    """Match AstrBot's enum-valued component discriminator rather than a string."""

    Forward = "Forward"
    Nodes = "Nodes"
    Node = "Node"
    Plain = "Plain"


class FakeAPI:
    """Serve deterministic responses while proving no HTTP fallback is needed."""

    def __init__(self, replies):
        self.replies = replies
        self.calls = []

    async def call_action(self, action, **parameters):
        """Record exact live-API actions and return or raise the selected fixture."""
        self.calls.append((action, parameters))
        key = parameters.get("id", parameters.get("message_id"))
        response = self.replies.get((action, key))
        if isinstance(response, Exception):
            raise response
        return response


def event(components=None, raw=None, replies=None, msg_id="current"):
    """Build a minimal framework-free event with the actual bot.api shape."""
    return SimpleNamespace(message_obj=SimpleNamespace(message=components or [], raw_message=raw),
                           bot=SimpleNamespace(api=FakeAPI(replies or {})), msg_id=msg_id)


def node(text, name="Alice", uid=123):
    """Create a realistic get_forward_msg message including sender metadata."""
    return {"sender": {"nickname": name, "user_id": uid}, "content": [
        {"type": "text", "data": {"text": text}}]}


def run(value, **kwargs):
    """Run each resolver independently without an async test plugin dependency."""
    return asyncio.run(resolve(value, **kwargs))


def test_ordinary_raw_event_is_not_a_forward_node():
    """OneBot event sender/message fields describe the current source, not a quoted node."""
    raw = {"post_type": "message", "message_type": "group", "self_id": 90001,
           "sender": {"user_id": 123, "nickname": "Alice"},
           "message": [{"type": "text", "data": {"text": "ordinary"}}]}
    value = event([SimpleNamespace(type=ComponentType.Plain, text="ordinary")], raw=raw)
    result = run(value)
    assert result["recognized"] is False
    assert result["items"] == []
    assert value.bot.api.calls == []


def test_raw_event_with_nested_forward_still_recovers_resource():
    """Skipping the event wrapper must still traverse its actual forward component."""
    raw = {"post_type": "message", "message_type": "group", "self_id": 90001,
           "sender": {"user_id": 123},
           "message": [{"type": "forward", "data": {"id": "resource"}}]}
    value = event(raw=raw, replies={("get_forward_msg", "resource"): {"messages": [node("hello")]}})
    result = run(value)
    assert result["recognized"] and result["ok"]
    assert result["items"][0]["text"] == "hello"
    assert value.bot.api.calls == [("get_forward_msg", {"id": "resource"})]


def test_enum_forward_attribute_id_uses_existing_api():
    """Actual Forward.id is used even when component.data does not contain it."""
    component = SimpleNamespace(type=ComponentType.Forward, id="resource")
    value = event([component], replies={("get_forward_msg", "resource"): {"messages": [node("hello")]}})
    result = run(value)
    assert result["ok"] and result["recognized"]
    assert result["items"] == [{"sender": {"id": 123, "display_name": "Alice"},
                                "text": "hello", "media": []}]
    assert result["text"] == "Alice: hello"
    assert value.bot.api.calls == [("get_forward_msg", {"id": "resource"})]


def test_raw_message_dict_fallback_and_success_envelope():
    """Raw OneBot events retain forwards discarded by component conversion."""
    value = event(raw={"message": [{"type": "forward", "data": {"id": "raw"}}]},
                  replies={("get_forward_msg", "raw"): {
                      "status": "ok", "retcode": 0, "data": {"messages": [node("raw text")]}}})
    assert run(value)["text"] == "Alice: raw text"


def test_nodes_container_and_astrbot_node_sender_content():
    """Nodes exposes Node content directly, without creating a fake wrapper sender."""
    child = SimpleNamespace(type=ComponentType.Node, name="Bob", uin="42", content=[
        SimpleNamespace(type=ComponentType.Plain, text="first\nsecond")])
    value = event([SimpleNamespace(type=ComponentType.Nodes, nodes=[child])])
    result = run(value)
    assert result["ok"]
    assert result["items"][0]["sender"] == {"id": "42", "display_name": "Bob"}
    assert result["text"] == "Bob: first\nsecond"
    assert value.bot.api.calls == []


def test_onebot_node_data_sender_and_preferred_card_name():
    """OneBot node data may carry sender metadata and content, not top-level fields."""
    value = {"type": "node", "data": {"sender": {"user_id": 7, "nickname": "Nick", "card": "Card"},
                                         "content": "exact  spacing\nkept"}}
    result = run(event([value]))
    assert result["text"] == "Card: exact  spacing\nkept"
    assert result["items"][0]["sender"]["id"] == 7


@pytest.mark.parametrize("content", [
    {"type": "text", "data": {"text": "ordinary message", "id": "not-forward"}},
    {"type": "image", "data": {"id": "image-id", "url": "local-image"}},
    {"type": "json", "data": {"data": '{"app":"other","id":"x","resid":"x"}'}},
    {"type": "xml", "data": {"data": '<msg id="x" resid="x" action="viewSomething"/>'}},
    {"id": "ordinary", "content": "not a node"},
])
def test_ordinary_messages_never_trigger_blind_fetch(content):
    """An arbitrary id or card must never become a fetchable forward resource."""
    value = event([content])
    result = run(value)
    assert not result["recognized"] and result["error"] == "not_forward"
    assert result["items"] == [] and result["text"] == ""
    assert value.bot.api.calls == []


@pytest.mark.parametrize("component", [
    {"type": "json", "data": {"data": '{"app":"com.tencent.multimsg","meta":{"detail":{"resid":"r"}},"id":"wrong"}'}},
    {"type": "xml", "data": {"data": '<msg action="viewMultiMsg" m_resid="r" id="wrong"/>'}},
    {"type": "xml", "data": {"data": '<msg><item action="viewMultiMsg" resid="r"/></msg>'}},
])
def test_real_multimsg_cards_extract_only_resource_ids(component):
    """Multimsg discriminators permit resid/m_resid but never arbitrary card ids."""
    assert parse_forward_descriptors(component) == [{"kind": "forward", "id": "r"}]
    value = event([component], replies={("get_forward_msg", "r"): [node("card")]})
    assert run(value)["text"] == "Alice: card"


def test_forward_without_id_recovers_current_message_via_get_msg():
    """Recognized forwards without resource ids may resolve their original event."""
    value = event([SimpleNamespace(type=ComponentType.Forward)], replies={
        ("get_msg", "current"): {"status": "ok", "retcode": 0, "data": {
            "message": [{"type": "forward", "data": {"id": "found"}}]}},
        ("get_forward_msg", "found"): {"messages": [node("recovered")]}})
    result = run(value)
    assert result["ok"] and result["text"] == "Alice: recovered"
    assert value.bot.api.calls == [("get_msg", {"message_id": "current"}),
                                   ("get_forward_msg", {"id": "found"})]


def test_explicit_message_id_and_forward_id_entry_points():
    """Tool calls share the exact resolver used for automatic incoming expansion."""
    value = event(replies={
        ("get_msg", 888): {"message": [{"type": "forward", "data": {"id": "r"}}]},
        ("get_forward_msg", "r"): {"messages": [node("explicit")]}})
    assert run(value, message_id=888)["text"] == "Alice: explicit"
    assert run(value, forward_id="r")["text"] == "Alice: explicit"


@pytest.mark.parametrize("reply,error", [
    ({"status": "failed", "retcode": 100, "data": {"messages": [node("must not leak")]}}, "api_error"),
    ({"status": "ok", "retcode": 5, "data": {"messages": [node("must not leak")]}}, "api_error"),
    ({"messages": []}, "empty_forward"),
    ({"status": "ok", "retcode": 0, "data": None}, "forward_unavailable"),
    (RuntimeError("offline transport failure"), "api_exception"),
])
def test_api_errors_empty_and_unavailable_are_distinct(reply, error):
    """Failures never become successful empty chat messages or fabricated prose."""
    value = event(replies={("get_forward_msg", "r"): reply})
    result = run(value, forward_id="r")
    assert result["recognized"] and not result["ok"]
    assert result["error"].startswith(error)
    assert result["items"] == [] and result["text"] == ""


def test_unavailable_api_is_explicit_without_http_fallback():
    """Missing framework transport is a reported failure, never a local HTTP guess."""
    result = run(SimpleNamespace(), forward_id="r")
    assert result["recognized"] and not result["ok"]
    assert result["error"] == "api_unavailable"


def test_recursive_nodes_keep_complete_text_and_unknown_sender():
    """There is no old forty-message/three-hundred-character truncation or fake user."""
    long_text = "full line\n" * 100
    nodes = [node(str(index) + long_text) for index in range(50)]
    unknown = {"type": "node", "data": {"content": "anonymous source"}}
    parent = {"type": "node", "data": {"content": [nodes, unknown]}}
    result = run(event([parent]))
    assert result["ok"] and len(result["items"]) == 51
    assert result["items"][49]["text"] == "49" + long_text
    assert result["items"][-1]["sender"] == {"id": None, "display_name": None}
    assert result["text"].endswith("anonymous source")


def test_nested_forward_cycles_terminate_and_repeated_ids_fetch_once():
    """ID ancestry blocks cycles while caching repeated sibling references."""
    nested = node("root")
    nested["content"].extend([{"type": "forward", "data": {"id": "r"}},
                              {"type": "forward", "data": {"id": "s"}},
                              {"type": "forward", "data": {"id": "s"}}])
    value = event(replies={("get_forward_msg", "r"): {"messages": [nested]},
                           ("get_forward_msg", "s"): {"messages": [node("child")]}})
    result = run(value, forward_id="r")
    assert not result["ok"] and result["error"] == "cyclic_forward: r"
    assert [item["text"] for item in result["items"]] == ["root", "child", "child"]
    assert len(value.bot.api.calls) == 2


def test_cyclic_embedded_python_nodes_terminate():
    """Malformed component object cycles cannot recurse indefinitely."""
    cyclic = {"type": "node", "data": {"content": []}}
    cyclic["data"]["content"].extend(["source", cyclic])
    result = run(event([cyclic]))
    assert result["items"][0]["text"] == "source"
    assert result["error"] == "cyclic_embedded_nodes"


def test_media_is_metadata_not_imagined_image_description():
    """Actual media is marked; the resolver never pretends to have read pixels."""
    source = node("caption")
    source["content"].append({"type": "image", "data": {"url": "fixture-image", "file": "image.png"}})
    result = run(event([source]))
    assert result["ok"] and result["text"] == "Alice: caption[image]"
    assert result["items"][0]["media"] == [{"type": "image", "url": "fixture-image", "file": "image.png"}]


def test_raw_id_recovers_lossy_forward_without_message_lookup():
    """A raw source id takes priority over fetching the current message again."""
    value = event([SimpleNamespace(type=ComponentType.Forward)],
                  raw={"message": [{"type": "forward", "data": {"id": "raw"}}]},
                  replies={("get_forward_msg", "raw"): {"messages": [node("raw fallback")]}})
    assert run(value)["text"] == "Alice: raw fallback"
    assert value.bot.api.calls == [("get_forward_msg", {"id": "raw"})]


def test_missing_resource_id_and_failed_message_recovery_are_explicit():
    """A known forward that cannot recover an id stays recognized and unavailable."""
    value = event([SimpleNamespace(type=ComponentType.Forward)],
                  replies={("get_msg", "current"): {"message": []}})
    result = run(value)
    assert result["recognized"] and not result["ok"]
    assert result["error"] == "forward_id_unavailable"
    assert result["items"] == []


def test_nonforward_explicit_message_lookup_does_not_fetch_forward():
    """An explicit message number is not itself a forward resource identifier."""
    value = event(replies={("get_msg", 9): {"message": [{"type": "text", "data": {"text": "normal"}}]}})
    result = run(value, message_id=9)
    assert not result["recognized"] and result["error"] == "not_forward"
    assert value.bot.api.calls == [("get_msg", {"message_id": 9})]


def test_parser_keeps_source_objects_unchanged_and_ignores_malformed_cards():
    """Pure parsing never annotates source events or treats malformed cards as ids."""
    source = {"type": "forward", "data": {"id": "r"}}
    assert parse_forward_descriptors(source) == [{"kind": "forward", "id": "r"}]
    assert source == {"type": "forward", "data": {"id": "r"}}
    assert parse_forward_descriptors({"type": "json", "data": {"data": "{broken"}}) == []
    assert parse_forward_descriptors({"type": "xml", "data": {"data": "<broken"}}) == []


def full_napcat_node(text, uid=123, name="Alice"):
    """Mirror observed NapCat source records, including legitimate event-like keys."""
    return {"post_type": "message", "message_type": "group", "self_id": 90001,
            "sender": {"user_id": uid, "nickname": name},
            "message": [{"type": "text", "data": {"text": text}}]}


def test_full_napcat_records_are_nodes_only_in_forward_context():
    """The same shape is a current event outside, but a real source inside a forward."""
    source = full_napcat_node("source text")
    assert parse_forward_descriptors(source) == []
    descriptors = parse_forward_descriptors([source], node_context=True)
    assert len(descriptors) == 1 and descriptors[0]["kind"] == "node"
    value = event([SimpleNamespace(type=ComponentType.Forward, id="r")],
                  replies={("get_forward_msg", "r"): {"messages": [source]}})
    result = run(value)
    assert result["ok"] and result["text"] == "Alice: source text"
    assert result["items"][0]["sender"]["id"] == 123


def test_raw_inline_full_records_beat_lossy_forward_id_without_api():
    """Actual raw inline content survives framework normalization and is not duplicated."""
    inner = full_napcat_node("nested", uid=456, name="Bob")
    outer = full_napcat_node("unused")
    outer["message"] = [{"type": "forward", "data": {"id": "nested-r", "content": [inner]}}]
    following = full_napcat_node("following")
    raw = full_napcat_node("ordinary current text")
    raw["message"] = [{"type": "forward", "data": {"id": "r", "content": [outer, following]}}]
    value = event([SimpleNamespace(type=ComponentType.Forward, id="r")], raw=raw)
    result = run(value)
    assert result["ok"] and result["source"] == "event"
    assert [item["text"] for item in result["items"]] == ["nested", "following"]
    assert [item["sender"]["id"] for item in result["items"]] == [456, 123]
    assert value.bot.api.calls == []


def test_api_full_records_preserve_inline_nested_media_and_source_order():
    """API payload nodes accept full fields while nested media stays attached to its sender."""
    inner = full_napcat_node("caption", uid=456, name="Bob")
    inner["message"].append({"type": "image", "data": {"url": "fixture-image"}})
    outer = full_napcat_node("unused")
    outer["message"] = [{"type": "forward", "data": {"id": "nested-r", "content": [inner]}}]
    value = event(replies={("get_forward_msg", "r"): {"messages": [outer, full_napcat_node("last")]}})
    result = run(value, forward_id="r")
    assert result["ok"] and len(result["items"]) == 2
    assert result["items"][0]["sender"]["id"] == 456
    assert result["items"][0]["media"] == [{"type": "image", "url": "fixture-image"}]
    assert result["text"] == "Bob: caption[image]\nAlice: last"
    assert value.bot.api.calls == [("get_forward_msg", {"id": "r"})]


def test_empty_source_nodes_do_not_create_empty_user_items():
    """Empty source content is visible as unavailable content, not fake dialogue."""
    result = run(event([node("")]))
    assert not result["ok"] and result["items"] == [] and result["text"] == ""
    assert result["error"] == "empty_forward"
