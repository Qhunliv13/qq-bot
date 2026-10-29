"""Offline source-bound visual contracts against actual production entry methods.

Framework events and provider replies are local fixtures. The real entry, batch,
source collector, context renderer, image transcript, and serializers run unchanged;
assertions concern submitted source data, never a model's chosen form of address.
"""

import ast
import asyncio
import copy
import json
from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest

from test_batch_flow import _arrive_while_execution_locked, _entry
from test_production_integration import Event, Image, Reply, _provider
from test_production_integration import production as _production_fixture
from plugin.reply_selection import decode_reply

# Share the production host instead of copying its startup-free AST fixture.
production = _production_fixture


OWNER_ID = "2084526117"  # Existing production owner allowlist, not a display-name inference.
MEMBER_ID = "61001"  # Synthetic non-owner publisher, independent of the active host user.


def _event(sender_id, message_id, components=None, text="describe this attachment"):
    """Set both framework getters and canonical sender metadata to one real publisher."""
    event = Event(text=text, message_id=message_id, components=components)
    name = "Publisher " + sender_id
    event.get_sender_id = lambda: sender_id
    event.get_sender_name = lambda: name
    # Event's shared fixture does not populate message_obj.sender automatically.
    event.message_obj.sender = SimpleNamespace(user_id=sender_id, nickname=name)
    return event


def _bind_method(plugin, name):
    """Load one unchanged real method missing from the shared fixture's allowlist."""
    path = Path(__file__).resolve().parents[1] / "plugin" / "main.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    klass = next(node for node in tree.body
                 if isinstance(node, ast.ClassDef) and node.name == "HiganPlugin")
    method = copy.deepcopy(next(node for node in klass.body
                               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                               and node.name == name))
    method.decorator_list = []
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[
        ast.alias(name="annotations")], level=0), method], type_ignores=[])
    namespace = dict(plugin._llm_tools.__func__.__globals__)
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    setattr(plugin, name, MethodType(namespace[name], plugin))


def _visual_entry(plugin):
    """Restore actual reference selection while blocking only unrelated avatar fetching."""
    _entry(plugin)
    _bind_method(plugin, "_refs_for")
    _bind_method(plugin, "_remember_images")
    plugin._recent_images = {}
    plugin._looks_like_asking_image = lambda text: False

    async def no_avatar(event, text):
        """Plain messages must not fetch avatars or rely on external image services."""
        return "", ""

    async def no_archive(*args):
        """Do not persist or download incoming image bytes at unrelated archive edges."""
        return None

    plugin._avatar_ref = no_avatar
    plugin._archive_images = no_archive
    plugin._collect_images = no_archive


def _parts(request):
    """Return the actual first user payload without accepting stringified image data."""
    content = request["messages"][1]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    return content


def _conversation(request):
    """Decode the existing conversation data envelope from text or multipart transport."""
    content = request["messages"][1]["content"]
    prompt = content if isinstance(content, str) else content[0]["text"]
    envelope = json.loads(prompt)
    assert envelope["source"] == {"name": "conversation_context", "trust": "untrusted_data"}
    return envelope["conversation"]


def _visual_origins(request):
    """Require each real image to be immediately preceded by its JSON provenance data."""
    result = {}
    parts = _parts(request)
    for index, part in enumerate(parts):
        if part["type"] != "image_url":
            continue
        # Adjacency binds an origin to this image, rather than a separate prose hint.
        assert index > 0 and parts[index - 1]["type"] == "text"
        metadata = json.loads(parts[index - 1]["text"])
        assert metadata["source"] == "attachment_provenance"
        assert metadata["trust"] == "untrusted_data"
        assert metadata["origins"]
        url = part["image_url"]["url"]
        assert url not in result, "one actual image may have many origins, not duplicate parts"
        result[url] = metadata["origins"]
    assert result
    # Context records must agree with the image-adjacent data actually sent on wire.
    envelope = json.loads(parts[0]["text"])
    records = envelope["attachments"]["origins"]
    for ref, origins in result.items():
        assert [record["source"] for record in records if record["ref"] == ref] == origins
    return result


def _assert_origin(origin, sender_id, role, message_id, relation, containing_id=None):
    """Keep publisher ID, role, original message, and containing reference independent."""
    assert origin["sender"]["id"] == sender_id
    assert origin["sender"]["role"] == role
    assert origin["sender"]["display_name"] == "Publisher " + sender_id
    assert origin["message_id"] == message_id
    assert origin["relation"] == relation
    if containing_id is not None:
        assert origin["containing_message_id"] == containing_id


def test_member_image_reaches_provider_with_member_publisher_not_owner(production, monkeypatch):
    """Actual pixels and current sender metadata cannot silently acquire owner identity."""
    plugin = production()
    _visual_entry(plugin)
    ref = "https://offline.invalid/member-image.png"
    event = _event(MEMBER_ID, "member-current", [Image(url=ref)])
    requests = _provider(monkeypatch, [(json.dumps({"reply_to": [event.message_obj.message_id], "text": "An arbitrary visual response."}), [])])

    asyncio.run(plugin.on_message(event))

    assert len(requests) == 1
    origins = _visual_origins(requests[0])
    assert list(origins) == [ref] and len(origins[ref]) == 1
    _assert_origin(origins[ref][0], MEMBER_ID, "member", "member-current", "current")
    record = _conversation(requests[0])["messages"][0]
    assert record["sender"]["id"] == MEMBER_ID and record["sender"]["role"] == "member"
    assert event.stopped and len(event.sent) == 1


@pytest.mark.parametrize("current_id, current_role, source_id, source_role", [
    (MEMBER_ID, "member", OWNER_ID, "owner"),
    (OWNER_ID, "owner", MEMBER_ID, "member"),
])
def test_quote_image_preserves_original_publisher_not_containing_sender(
        production, monkeypatch, current_id, current_role, source_id, source_role):
    """Quoting transfers neither the source image publisher nor the current actor's role."""
    plugin = production()
    _visual_entry(plugin)
    ref = "https://offline.invalid/quoted-image.png"
    quote = Reply(id="original-image-message", sender_id=source_id,
                  sender_nickname="Publisher " + source_id, message_str="original caption",
                  chain=[Image(url=ref)])
    event = _event(current_id, "containing-reference", [quote])
    requests = _provider(monkeypatch, [(json.dumps({"reply_to": [event.message_obj.message_id], "text": "Any wording is permitted."}), [])])

    asyncio.run(plugin.on_message(event))

    assert len(requests) == 1
    origin = _visual_origins(requests[0])[ref]
    assert len(origin) == 1
    _assert_origin(origin[0], source_id, source_role, "original-image-message", "quoted",
                   "containing-reference")
    record = _conversation(requests[0])["messages"][0]
    assert record["sender"]["id"] == current_id
    assert record["sender"]["role"] == current_role
    assert record["quote"]["sender"]["id"] == source_id


def test_native_batch_keeps_owner_and_member_actor_and_image_roles_independent(production, monkeypatch):
    """One real batch carries per-message actors and per-image publishers, not one owner flag."""
    plugin = production()
    _visual_entry(plugin)
    refs = ["https://offline.invalid/owner.png", "https://offline.invalid/member.png"]
    events = [_event(OWNER_ID, "owner-record", [Image(url=refs[0])]),
              _event(MEMBER_ID, "member-record", [Image(url=refs[1])])]
    requests = _provider(monkeypatch, [(json.dumps({"reply_to": [event.message_obj.message_id for event in events], "text": "One response for both records."}), [])])

    asyncio.run(_arrive_while_execution_locked(plugin, events))

    assert len(requests) == 1
    records = _conversation(requests[0])["messages"]
    assert [(record["id"], record["sender"]["id"], record["sender"]["role"])
            for record in records] == [("owner-record", OWNER_ID, "owner"),
                                      ("member-record", MEMBER_ID, "member")]
    origins = _visual_origins(requests[0])
    assert list(origins) == refs
    _assert_origin(origins[refs[0]][0], OWNER_ID, "owner", "owner-record", "current")
    _assert_origin(origins[refs[1]][0], MEMBER_ID, "member", "member-record", "current")
    assert sum(len(event.sent) for event in events) == 1


def test_batch_dedup_and_three_image_cap_retain_every_shared_reference_origin(production, monkeypatch):
    """Visual limits drop old pixels, never another publisher of a retained shared image."""
    plugin = production()
    _visual_entry(plugin)
    dropped, shared, second, third = ["https://offline.invalid/" + name + ".png"
                                      for name in ("dropped", "shared", "second", "third")]
    events = [_event(OWNER_ID, "owner-batch", [Image(url=dropped), Image(url=shared)]),
              _event(MEMBER_ID, "member-batch", [Image(url=shared), Image(url=second), Image(url=third)])]
    requests = _provider(monkeypatch, [(json.dumps({"reply_to": [event.message_obj.message_id for event in events], "text": "No forced phrase."}), [])])

    asyncio.run(_arrive_while_execution_locked(plugin, events))

    assert len(requests) == 1
    origins = _visual_origins(requests[0])
    assert list(origins) == [shared, second, third]
    assert len(origins[shared]) == 2
    _assert_origin(origins[shared][0], OWNER_ID, "owner", "owner-batch", "current")
    _assert_origin(origins[shared][1], MEMBER_ID, "member", "member-batch", "current")
    for ref in (second, third):
        assert len(origins[ref]) == 1
        _assert_origin(origins[ref][0], MEMBER_ID, "member", "member-batch", "current")


def test_no_image_native_and_plain_transports_remain_string_content(production, monkeypatch):
    """Provenance support must not turn ordinary text into multipart or invent origins."""
    plugin = production()
    _visual_entry(plugin)
    _bind_method(plugin, "_llm_plain")
    event = _event(MEMBER_ID, "plain-current", text="ordinary text remains ordinary")
    requests = _provider(monkeypatch, [(json.dumps({"reply_to": [event.message_obj.message_id], "text": "Unrestricted native answer."}), []), (json.dumps({"reply_to": [], "text": "Plain answer."}), [])])

    asyncio.run(plugin.on_message(event))
    assert len(requests) == 1
    prompt = requests[0]["messages"][1]["content"]
    assert isinstance(prompt, str)
    assert _conversation(requests[0])["messages"][0]["body"] == event.text
    assert "attachment_provenance" not in prompt
    answer = asyncio.run(plugin._llm_plain("plain system", "unchanged plain prompt", []))
    selection = decode_reply(answer, [], allow_unattributed=True)
    assert selection["structured"] and selection["error"] is None
    assert selection["reply_to"] == [] and selection["text"] == "Plain answer."
    assert requests[1]["messages"] == [{"role": "system", "content": "plain system"},
                                         {"role": "user", "content": "unchanged plain prompt"}]


def test_plain_fallback_uses_same_source_bound_image_serialization(production, monkeypatch):
    """The actual separately loaded fallback shares native image/source transport semantics."""
    plugin = production()
    _visual_entry(plugin)
    _bind_method(plugin, "_llm_plain")
    ref = "https://offline.invalid/shared-native-plain.png"
    event = _event(MEMBER_ID, "fallback-current", [Image(url=ref)])
    requests = _provider(monkeypatch, [(json.dumps({"reply_to": [event.message_obj.message_id], "text": "Native response."}), []), (json.dumps({"reply_to": [event.message_obj.message_id], "text": "Fallback response."}), [])])
    asyncio.run(plugin.on_message(event))
    assert len(requests) == 1
    native = _visual_origins(requests[0])
    # The fallback recovers origins from the actual context envelope, not a test-only kwarg.
    prompt = requests[0]["messages"][1]["content"][0]["text"]
    sources = json.loads(prompt)["attachments"]["origins"]
    assert sources == [{"ref": ref, "source": native[ref][0]}]

    answer = asyncio.run(plugin._llm_plain("plain system", prompt, [ref]))
    selection = decode_reply(answer, [dict(record) for record in _conversation(requests[0])["messages"]])
    assert selection["structured"] and selection["error"] is None
    assert selection["reply_to"] == [event.message_obj.message_id]
    assert selection["text"] == "Fallback response."
    assert len(requests) == 2
    assert requests[1]["messages"][1]["content"] == requests[0]["messages"][1]["content"]
    assert _visual_origins(requests[1]) == native
