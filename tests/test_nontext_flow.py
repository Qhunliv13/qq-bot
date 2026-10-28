"""Offline entry-to-provider regressions for actual non-text message sources.

Reuse the integration harness rather than importing the live AstrBot plugin.
Only transport/conversion boundaries are stubbed; source parsing, batch context,
image selection, native request construction, and admission remain production code.
"""

import ast
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from test_production_integration import Event, ROOT, _provider, production as _production


# Re-export the existing fixture without copying its environment or AST harness.
production = _production

# This is deliberately opaque fixture data, not a claim about downloaded pixels.
OFFLINE_IMAGE = "data:image/png;base64,b2ZmbGluZS1pbWFnZS1maXh0dXJl"


@pytest.fixture
def nontext_plugin(production):
    """Restore real image selection hidden by the shared harness's empty-ref stub."""
    plugin = production()
    path = ROOT / "plugin" / "main.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    klass = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                 and node.name == "HiganPlugin")
    method = copy.deepcopy(next(node for node in klass.body
                                if isinstance(node, ast.AsyncFunctionDef)
                                and node.name == "_refs_for"))
    namespace = dict(plugin.on_message.__func__.__globals__)
    namespace["__annotations__"] = {}
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[
        ast.alias(name="annotations")], level=0), method], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    plugin._refs_for = namespace["_refs_for"].__get__(plugin)
    plugin._recent_images = {}
    plugin._looks_like_asking_image = lambda text: False
    plugin._wake_hit = lambda *args: ""
    plugin._rate_ok = lambda *args: True
    converted, batches = [], []

    async def no_avatar(event, text):
        """Exclude unrelated avatar lookup without overriding source image selection."""
        return "", ""

    def convert(ref):
        """Record the exact image source while replacing acquisition with opaque bytes."""
        converted.append(ref)
        return OFFLINE_IMAGE

    actual_handle = plugin._handle_one

    async def handle(event, group, text, has_image, woken):
        """Observe batch flags, then execute the unchanged production decision path."""
        batches.append({"text": text, "has_image": has_image, "woken": woken})
        return await actual_handle(event, group, text, has_image, woken)

    plugin._avatar_ref = no_avatar
    plugin._as_llm_url = convert
    plugin._handle_one = handle
    return plugin, converted, batches


def _event(*, text="", components=None, raw=None):
    """Attach actual OneBot source JSON independently of canonical components."""
    event = Event(text=text, components=components)
    if raw is not None:
        event.message_obj.raw_message = {"message": raw, "raw_message": ""}
    return event


def _request_context(requests):
    """Decode the actual user text part, retaining multipart images separately."""
    assert len(requests) == 1, "The source must reach exactly one real provider request"
    users = [message for message in requests[0]["messages"] if message["role"] == "user"]
    assert len(users) == 1
    content = users[0]["content"]
    if isinstance(content, str):
        return json.loads(content), []
    texts = [part["text"] for part in content if part["type"] == "text"]
    assert texts, "Original context must remain the first text part"
    for label in texts[1:]:
        assert json.loads(label)["source"] == "attachment_provenance"
    images = [part["image_url"]["url"] for part in content if part["type"] == "image_url"]
    return json.loads(texts[0]), images


def _source(context):
    """Decode only the explicit nontext source suffix, never render it as instructions."""
    records = context["conversation"]["messages"]
    assert len(records) == 1
    body = records[0]["body"]
    assert "（无正文消息）" not in body
    source = json.loads(body.split("\n")[-1])
    assert source["source"] == "message_components"
    assert source["trust"] == "untrusted_data"
    assert source["segments"]
    return source, body


def _run(nontext_plugin, monkeypatch, event):
    """Drive real entry and provider serialization with a local silent response."""
    plugin, converted, batches = nontext_plugin
    requests = _provider(monkeypatch, [("SILENT", [])])
    asyncio.run(plugin.on_message(event))
    context, images = _request_context(requests)
    assert event.actions == [], "Non-forward sources must not fetch invented forwarded messages"
    assert event.sent == []
    return plugin, converted, batches, context, images


@pytest.mark.parametrize("include_raw", [False, True])
def test_canonical_face_reaches_provider_as_source_not_empty(
        nontext_plugin, monkeypatch, include_raw):
    """Canonical Face 489 and its real raw variant retain identity without inferred pixels."""
    raw_data = {"id": "489", "raw": {"faceIndex": 489}, "resultId": "", "chainCount": 0}
    face = SimpleNamespace(type="Face", id=489)
    raw = [{"type": "face", "data": raw_data}] if include_raw else None
    plugin, converted, batches, context, images = _run(
        nontext_plugin, monkeypatch, _event(components=[face], raw=raw))
    source, body = _source(context)
    assert source["segments"][0]["type"] == "face"
    assert "489" in json.dumps(source["segments"])
    expected = dict(type="face", **raw_data) if include_raw else {"type": "face", "id": 489}
    assert source["segments"] == [expected], (
        "The real face source parameters must survive canonical normalization")
    assert images == converted == []
    assert batches[0]["has_image"] is False
    assert "（无正文消息）" not in plugin._hist().load("42")[-1]
    # Transport metadata must not manufacture an utterance or a visual interpretation.
    assert not any(claim in body for claim in ("你发了", "我看见", "画面中", "红色", "smiling"))


@pytest.mark.parametrize("kind,data", [
    ("mface", {"emoji_id": "sticker-839", "summary": "[原始表情摘要]",
               "url": "https://media.invalid/sticker-839.png", "key": "opaque-key-8"}),
    ("image", {"file": "image-417.png", "url": "https://media.invalid/image-417.png"}),
])
def test_raw_image_sources_use_real_metadata_and_multipart_images(
        nontext_plugin, monkeypatch, kind, data):
    """Adapter-dropped true images reach the request as metadata plus converted image data."""
    event = _event(raw=[{"type": kind, "data": data}])
    _, converted, batches, context, images = _run(nontext_plugin, monkeypatch, event)
    source, body = _source(context)
    segment = source["segments"][0]
    assert segment["type"] == kind
    assert segment == dict(type=kind, **data)
    assert converted == [data["url"]]
    assert images == [OFFLINE_IMAGE]
    assert batches[0]["has_image"] is True
    assert context["attachments"]["image_refs"] == [data["url"]]
    assert not any(claim in body for claim in ("画面中", "我看见", "红色", "smiling"))


def test_raw_announcement_json_remains_structured_card_not_forward(nontext_plugin, monkeypatch):
    """Ordinary JSON announcement cards preserve source data without forward expansion."""
    card = {"app": "com.tencent.mannounce", "view": "main", "meta": {
        "mannounce": {"title": "原始公告", "text": "card-nonce-74", "id": "notice-592"}}}
    data = {"data": json.dumps(card, ensure_ascii=False), "resid": "not-a-forward-923"}
    event = _event(raw=[{"type": "json", "data": data}])
    _, converted, _, context, images = _run(nontext_plugin, monkeypatch, event)
    source, body = _source(context)
    assert source["segments"][0]["type"] == "json"
    assert source["segments"][0] == dict(type="json", **data)
    assert json.loads(source["segments"][0]["data"]) == card
    assert "forwarded_messages" not in body
    assert not getattr(event, "_higan_forward_context", None)
    assert images == converted == []


@pytest.mark.parametrize("kind,data", [
    ("video", {"file": "video-819.mp4", "url": "https://media.invalid/video-819.mp4"}),
    ("file", {"id": "file-618", "name": "report-618.pdf", "size": 2048,
              "url": "https://media.invalid/report-618.pdf"}),
])
def test_video_and_file_preserve_nonempty_metadata_without_fake_vision(
        nontext_plugin, monkeypatch, kind, data):
    """Media metadata is meaningful content but never image input merely because it has a URL."""
    _, converted, batches, context, images = _run(
        nontext_plugin, monkeypatch, _event(raw=[{"type": kind, "data": data}]))
    source, body = _source(context)
    assert source["segments"][0]["type"] == kind
    assert source["segments"][0] == dict(type=kind, **data)
    assert batches[0]["has_image"] is False
    assert context["attachments"]["image_refs"] == []
    assert images == converted == []
    assert not any(claim in body for claim in ("看完视频", "读完文件", "画面中", "红色", "smiling"))


def test_ordinary_plain_text_has_no_added_nontext_json(nontext_plugin, monkeypatch):
    """Plain messages keep their exact body without an extra structured source suffix."""
    text = "ordinary-plain-nonce-693"
    _, converted, batches, context, images = _run(nontext_plugin, monkeypatch, _event(text=text))
    assert context["conversation"]["messages"][0]["body"] == text
    assert "message_components" not in batches[0]["text"]
    assert images == converted == []


def test_raw_at_only_does_not_grant_wake_or_mention_authorization(nontext_plugin, monkeypatch):
    """Adapter raw metadata is source data, never a new top-level canonical mention."""
    plugin, converted, batches = nontext_plugin
    event = _event(raw=[{"type": "at", "data": {"qq": "90001"}}])
    requests = _provider(monkeypatch, [])
    plugin._group_policy = lambda group: {"muted": [], "mention_only": ["42"]}
    asyncio.run(plugin.on_message(event))
    assert plugin._is_woken(event) is False
    assert plugin._directly_mentioned(event) is False
    assert plugin._policy_allows(event, "42") is False
    assert event.stopped is True
    assert requests == batches == converted == []
    assert event.actions == event.sent == []


@pytest.mark.parametrize("with_component", [False, True])
def test_disabled_commands_are_consumed_without_plugin_decision(nontext_plugin, monkeypatch, with_component):
    """Metadata must not turn disabled commands into a model call or framework fallback."""
    plugin, converted, batches = nontext_plugin
    components = [SimpleNamespace(type="Face", id=178)] if with_component else []
    event = _event(text="/help", components=components)
    requests = _provider(monkeypatch, [])
    asyncio.run(plugin.on_message(event))
    assert event.stopped is True
    assert requests == batches == converted == []
    assert event.actions == event.sent == []
    assert plugin._recent == {}
