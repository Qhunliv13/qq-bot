"""Offline integration contracts for AST-loaded production plugin entry methods.

Only selected production definitions are compiled: importing main would initialize
AstrBot/startup services. Framework edges and HTTP transport are stubs; dispatch,
native message building, agent loop, archive, send, and entry snapshots are real.
"""

import ast
import asyncio
import copy
from datetime import datetime, timedelta, timezone
import io
import json
import logging
import os
from pathlib import Path
import random
import re
import sys
import time
from types import SimpleNamespace
import urllib.request

import pytest

# Pure modules have no AstrBot dependency; keep relative agent_loop imports valid.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from plugin.history import Recorder  # noqa: E402
from plugin.tool_context import ToolContextArchive  # noqa: E402


class At(SimpleNamespace):
    """Minimal identity-bearing framework component, never a network client."""


class Reply(SimpleNamespace):
    """Represent quoted source text and nested components for real context parsing."""


class Image(SimpleNamespace):
    """Represent a local media attachment without reading or uploading its bytes."""

    @classmethod
    def fromFileSystem(cls, path):
        """Keep the actual outgoing path observable at the mocked send boundary."""
        return cls(file=path)


class MessageChain:
    """Retain outgoing framework components so visible text can be verified."""

    def __init__(self):
        self.chain = []

    def message(self, text):
        """Append text as the real send method expects, without delivery side effects."""
        self.chain.append(SimpleNamespace(text=text))
        return self


class Event:
    """Deterministic current-event fixture with inspectable OneBot and send edges."""

    def __init__(self, text="current question", message_id="1234", group="42", components=None):
        self.text, self.group = text, group
        self.components = components or []
        self.message_obj = SimpleNamespace(message_id=message_id, message=self.components)
        self.unified_msg_origin = "offline:42"
        self.bot = SimpleNamespace(api=SimpleNamespace(call_action=self.action))
        self.actions, self.sent = [], []
        self.action_results = {}
        self.fail_send = False
        self.stopped = False

    async def action(self, name, **kwargs):
        """Return configured local OneBot results and record exact explicit event use."""
        self.actions.append((name, kwargs))
        result = self.action_results.get(name, {})
        if isinstance(result, Exception):
            raise result
        return copy.deepcopy(result)

    async def send(self, chain):
        """Confirm delivery only on success, matching production's awaited boundary."""
        if self.fail_send:
            raise RuntimeError("offline send failed")
        self.sent.append(chain)
        return [{"message_id": "sent-1"}]

    def get_group_id(self):
        """Return the scope used by the actual entry method."""
        return self.group

    def get_sender_id(self):
        """Keep the actual current sender distinct from quoted/tool identities."""
        return "10001"

    def get_sender_name(self):
        """Expose a harmless fixture nickname, not runtime user information."""
        return "Current speaker"

    def get_self_id(self):
        """Provide bot identity for self_status and mention comparisons."""
        return "90001"

    def get_messages(self):
        """Return current components without implicit mutable framework state."""
        return self.components

    def get_message_str(self):
        """Expose the original user text to the production entry method."""
        return self.text

    def stop_event(self):
        """Record suppression of the separate framework default reply path."""
        self.stopped = True


def _load_production(root):
    """Compile unchanged production AST methods without imports, __init__, or decorators."""
    path = ROOT / "plugin" / "main.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names = {
        "_at_targets", "_who", "_clock_context", "_request_context", "_tool_evidence",
        "_quoted_text", "_ref_of", "_image_refs", "_record_visible_reply", "_mark_replied",
        "_to_plain", "_strip_tool_syntax", "_strip_internal", "_tool_schemas",
        "_task_tool_schemas", "_llm_tools", "_dispatch_tool", "_decide_tools_first",
        "_send", "_at_components", "on_message", "_drain_previous_instances",
        "_web_search", "_search_queries",
    }
    original = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "HiganPlugin")
    methods = [copy.deepcopy(node) for node in original.body
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    assert {node.name for node in methods} == names, "Production entry contract renamed; update tests explicitly"
    for method in methods:
        # Preserve native descriptor semantics, but do not import/register framework hooks.
        method.decorator_list = [dec for dec in method.decorator_list
                                 if isinstance(dec, ast.Name) and dec.id in {"staticmethod", "classmethod"}]
    constants = [copy.deepcopy(node) for node in tree.body
                 if (isinstance(node, ast.Assign) and all(isinstance(target, ast.Name) and target.id.isupper() for target in node.targets))
                 or (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id.isupper())]
    dedupe = next(copy.deepcopy(node) for node in tree.body
                  if isinstance(node, ast.FunctionDef) and node.name == "_dedupe_tool_schemas")
    klass = ast.ClassDef(name="HiganPlugin", bases=[], keywords=[], body=methods, decorator_list=[])
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *constants, dedupe, klass], type_ignores=[])
    namespace = {
        "__file__": str(path), "__package__": "plugin", "__name__": "plugin.main_ast_tests", "asyncio": asyncio,
        "json": json, "os": os, "re": re, "time": time, "Path": Path,
        "datetime": datetime, "timedelta": timedelta, "timezone": timezone,
        "random": random, "logger": logging.getLogger("offline-production-tests"),
        "At": At, "Reply": Reply, "Image": Image, "MessageChain": MessageChain,
        "ToolContextArchive": ToolContextArchive,
    }
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    namespace["HIGAN_ROOT"] = str(root)
    return namespace["HiganPlugin"]


@pytest.fixture
def production(tmp_path, monkeypatch):
    """Supply real production methods with local state and a fail-closed HTTP edge."""
    def deny_network(*args, **kwargs):
        """Unexpected networking fails immediately instead of using machine credentials."""
        raise AssertionError("real network forbidden in production integration tests")

    monkeypatch.setattr(urllib.request, "urlopen", deny_network)
    klass = _load_production(tmp_path)

    def make_instance():
        """Recreate plugin process state while sharing only temporary durable stores."""
        plugin = klass()
        plugin._stopping = False
        plugin.context = SimpleNamespace(get_using_provider=lambda origin: object())
        plugin.persona = "offline persona"
        plugin._recent, plugin._pending, plugin._busy = {}, {}, {}
        plugin._all_recent, plugin._turn_context = [], {}
        plugin._last_reply_at, plugin._reply_times, plugin._last_sent = {}, {}, {}
        plugin._hist_obj = Recorder(str(tmp_path / "history"))
        plugin._hist = lambda: plugin._hist_obj
        plugin._tasks_mod = lambda: None
        plugin._her_llm_config = lambda: ("https://offline.invalid/v1", "stub-not-a-real-key", "offline-model")
        plugin._llm_extra = lambda: {}
        plugin._as_llm_url = lambda ref: ref
        plugin._refs_for = _empty_refs
        plugin._decide = _unexpected_fallback
        return plugin

    return make_instance


async def _empty_refs(*args):
    """Avoid image acquisition while retaining actual request-context image parsing."""
    return [], ""


async def _unexpected_fallback(*args):
    """Fail explicitly if an integration error is hidden by the plain-reply fallback."""
    raise AssertionError("production unexpectedly fell back instead of finishing native loop")


def _call(name, arguments=None, call_id=None):
    """Build real OpenAI native calls with stable identities for exact role pairing."""
    return {"id": call_id or name, "type": "function", "function": {
        "name": name, "arguments": json.dumps(arguments or {})}}


def _provider(monkeypatch, replies):
    """Intercept the real _llm_tools HTTP edge and preserve immutable request bodies."""
    requests = []

    def urlopen(request, timeout):
        """Provide OpenAI envelopes locally; no sockets or real credentials are used."""
        requests.append(json.loads(request.data.decode("utf-8")))
        assert request.full_url == "https://offline.invalid/v1/chat/completions"
        assert timeout > 0
        content, calls = replies.pop(0)
        return io.BytesIO(json.dumps({"choices": [{"message": {
            "content": content, "tool_calls": calls}}]}).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return requests


def test_native_production_pairs_all_results_persists_files_and_survives_restart(production, monkeypatch):
    """Two native rounds retain exact paired results and every group file across restart."""
    plugin, event = production(), Event()
    nonce = "observation-nonce-6f7291"
    files = [{"file_name": f"{nonce}-{number}.txt", "file_size": number,
              "uploader": "10001", "upload_time": 100 + number, "file_id": str(number)}
             for number in range(7)]
    event.action_results["get_group_root_files"] = {"files": files, "folders": []}
    calls = [_call("self_status", call_id="status-native"),
             _call("group_files", {"limit": 20}, "files-native")]
    requests = _provider(monkeypatch, [
        ("discard first draft", [calls[0]]), ("discard second draft", [calls[1]]),
        ("I found seven files.", []), ("I remember the earlier result.", [])])
    answer = asyncio.run(plugin._decide_tools_first(event, "42", event.text, False))
    assert answer == "I found seven files."
    assert len(requests) == 3
    assert [message["role"] for message in requests[-1]["messages"]] == [
        "system", "user", "assistant", "tool", "assistant", "tool"]
    paired = [message for message in requests[-1]["messages"] if message["role"] == "tool"]
    assert [message["tool_call_id"] for message in paired] == [call["id"] for call in calls]
    assert [json.loads(message["content"])["tool_name"] for message in paired] == ["self_status", "group_files"]
    records = plugin._tool_evidence().recent("42")
    assert [record["tool_name"] for record in records] == ["self_status", "group_files"]
    assert [json.loads(message["content"])["result"] for message in paired] == [record["result"] for record in records]
    assert [item["name"] for item in records[1]["result"]["files"]] == [item["file_name"] for item in files]
    assert len(requests[1]["messages"]) == 4
    assert requests[1]["messages"][-1] == paired[0]
    assert event.actions == [("get_group_root_files", {"group_id": 42, "file_count": 20})]
    persisted = next((Path(plugin._tool_evidence().base_dir)).glob("*.jsonl")).read_text(encoding="utf-8")
    assert all(item["file_name"] in persisted for item in files)
    restored = production()
    assert restored._tool_evidence() is not plugin._tool_evidence()
    assert asyncio.run(restored._decide_tools_first(Event("next question"), "42", "next question", False)) == "I remember the earlier result."
    assert nonce in requests[-1]["messages"][1]["content"]
    assert nonce not in restored._request_context(event, "another-scope", "next question", False)


def test_request_context_frozen_history_current_identity_and_source_data(production):
    """Current message occurs once, and quoted/image/tool sources cannot replace its speaker."""
    plugin = production()
    plugin._turn_context["42"] = {"history": ("prior-only",), "clock": "frozen clock"}
    plugin._recent["42"] = ["mutated later history", "current-nonce"]
    quote = Reply(message_str="quoted-nonce", sender_nickname="Other speaker", chain=[Image(url="https://offline.invalid/quoted.png")])
    event = Event("current-nonce", components=[quote, At(qq="80001"), Image(file="local.png")])
    prompt = plugin._request_context(event, "42", event.text, True)
    history_text, current_text = prompt.split("【当前消息（JSON 数据）】\n")
    current = json.loads(current_text.split("\n\n", 1)[0])
    assert "prior-only" in history_text and "current-nonce" not in history_text
    assert "mutated later history" not in prompt
    assert prompt.count("current-nonce") == 1
    assert current["speaker"].startswith("Current speaker")
    assert current["quoted_text"] == "Other speaker：quoted-nonce"
    assert current["mentions"] == ["80001"]
    assert current["message_id"] == "1234"
    assert current["image_refs"] == ["https://offline.invalid/quoted.png", "local.png"]
    assert prompt.endswith("frozen clock")


def test_dispatch_self_status_and_forward_use_explicit_event(production, tmp_path):
    """Real dispatch reads safe local config and routes forward lookups through its event."""
    plugin, event = production(), Event()
    (tmp_path / "groups.json").write_text(json.dumps({"reply_pct": {"42": 75}}), encoding="utf-8")
    (tmp_path / "llm.json").write_text(json.dumps({"model": "offline", "api_base": "https://offline.invalid/v1", "api_key": "DO-NOT-RETURN"}), encoding="utf-8")
    result = asyncio.run(plugin._dispatch_tool("self_status", {}, "42", event=event))
    assert result["ok"] and result["self"]["qq"] == "90001"
    assert result["groups"]["reply_pct"] == {"42": 75}
    assert result["model"] == {"model": "offline", "api_host": "offline.invalid"}
    assert "DO-NOT-RETURN" not in json.dumps(result)
    event.action_results.update({
        "get_msg": {"message": [{"type": "forward", "data": {"id": "forward-nonce"}}]},
        "get_forward_msg": {"messages": [{"sender": {"nickname": "Quoted sender"}, "message": [
            {"type": "text", "data": {"text": "quoted result"}}, {"type": "image", "data": {}}]}]}})
    result = asyncio.run(plugin._dispatch_tool("forward_expand", {"msg_id": "#1234"}, "42", event=event))
    assert result == {"ok": True, "count": 1, "items": [{"from": "Quoted sender", "text": "quoted result[图片]"}]}
    assert event.actions == [("get_msg", {"message_id": 1234}), ("get_forward_msg", {"id": "forward-nonce"})]
    assert not asyncio.run(plugin._dispatch_tool("forward_expand", {"id": "x"}, "42"))["ok"]


def test_production_tool_schema_dispatch_parity_and_offline_web(production):
    """Advertised generic web/evidence tools execute corresponding real dispatcher branches."""
    plugin = production()
    schemas = {item["function"]["name"]: item["function"] for item in plugin._tool_schemas()}
    for name, key in [("web_search", "query"), ("web_fetch", "url"), ("tool_observations", "limit")]:
        assert key in schemas[name]["parameters"]["properties"]
    assert schemas["web_search"]["parameters"]["required"] == ["query"]
    assert schemas["web_fetch"]["parameters"]["required"] == ["url"]
    plugin._web_search = lambda query: [{"title": "fixture", "url": "https://offline.invalid", "snippet": query}]
    plugin._fetch_page = lambda url: "offline page body"
    plugin._tool_evidence().append("42", "q", "fixture", {}, {"nonce": "prior-evidence"})
    search = asyncio.run(plugin._dispatch_tool("web_search", {"query": "ordinary question"}, "42"))
    assert search["ok"] and search["results"][0]["snippet"] == "ordinary question"
    assert asyncio.run(plugin._dispatch_tool("web_fetch", {"url": "https://offline.invalid"}, "42"))["body"] == "offline page body"
    observation = asyncio.run(plugin._dispatch_tool("tool_observations", {"limit": 1}, "42"))
    assert observation["observations"][0]["result"] == {"nonce": "prior-evidence"}
    assert asyncio.run(plugin._dispatch_tool("tool_observations", {}, "other"))["observations"] == []
    assert asyncio.run(plugin._dispatch_tool("web_search", {"query": " "}, "42"))["error"] == "empty_query"
    assert asyncio.run(plugin._dispatch_tool("web_fetch", [], "42"))["error"] == "arguments_must_be_object"


def test_llm_tools_native_transport_disables_tools_without_losing_roles(production, monkeypatch):
    """Native continuation preserves paired IDs, with final-pass schemas genuinely absent."""
    plugin = production()
    requests = _provider(monkeypatch, [("final answer", [])])
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "q"},
                {"role": "assistant", "content": "", "tool_calls": [_call("self_status", call_id="paired")]},
                {"role": "tool", "tool_call_id": "paired", "name": "self_status", "content": '{"ok":true}'}]
    original = copy.deepcopy(messages)
    assert asyncio.run(plugin._llm_tools("s", "q", [], messages=messages, allow_tools=False)) == ("final answer", [])
    assert requests[0]["messages"] == original == messages
    assert "tools" not in requests[0] and "tool_choice" not in requests[0]


@pytest.mark.parametrize("fail", [False, True])
def test_send_records_only_actual_confirmed_visible_reply(production, tmp_path, fail):
    """A failed actual send never claims delivery or writes phantom visible history."""
    plugin, event = production(), Event()
    event.fail_send = fail
    media = tmp_path / "image.png"
    media.write_bytes(b"offline media fixture")
    visible = asyncio.run(plugin._send(event, "42", f"hello [quote:1234] [发图:{media}]"))
    if fail:
        assert visible is None and event.sent == []
        assert plugin._recent == {} and plugin._hist().load("42") == []
        assert plugin._last_reply_at == {} and plugin._last_sent == {}
    else:
        assert visible == "hello [图片]"
        assert plugin._recent["42"] == ["彼岸酱：hello [图片]"]
        assert plugin._hist().load("42") == plugin._recent["42"]
        assert plugin._last_sent["42"] == ["sent-1"]
        assert any(isinstance(component, Image) and component.file == str(media) for component in event.sent[0].chain)
        assert any(isinstance(component, Reply) and component.id == "1234" for component in event.sent[0].chain)
        assert "[发图:" not in plugin._hist().load("42")[0]


def test_group_files_failure_is_archived_without_false_success(production, monkeypatch):
    """OneBot failure remains paired error evidence rather than fabricated file delivery."""
    plugin, event = production(), Event()
    event.action_results["get_group_root_files"] = RuntimeError("offline files unavailable")
    requests = _provider(monkeypatch, [("draft", [_call("group_files")]), ("I could not list the files.", [])])
    assert asyncio.run(plugin._decide_tools_first(event, "42", "list files", False)) == "I could not list the files."
    record = plugin._tool_evidence().recent("42")[0]
    assert record["status"] == "error" and record["result"]["ok"] is False
    assert "delivered" not in record["result"] and "files" not in record["result"]
    assert json.loads(requests[-1]["messages"][-1]["content"])["result"] == record["result"]
    assert event.sent == [] and plugin._recent == {}


def test_plugin_media_failure_never_marks_delivered(production, monkeypatch, tmp_path):
    """Tool-delivery confirmation uses actual _send failure and preserves original evidence."""
    plugin, event = production(), Event()
    event.fail_send = True
    media = tmp_path / "media.png"
    media.write_bytes(b"offline")

    async def media_tool(name, args, scope="", event=None):
        """Supply only the external media producer boundary; production delivers it."""
        return {"ok": True, "deliver": "plugin", "path": str(media), "nonce": "media-evidence"}

    plugin._dispatch_tool = media_tool
    requests = _provider(monkeypatch, [("draft", [_call("media_fixture")]), ("The upload failed.", [])])
    assert asyncio.run(plugin._decide_tools_first(event, "42", "send it", False)) == "The upload failed."
    record = plugin._tool_evidence().recent("42")[0]
    assert record["result"]["delivered"] is False
    assert record["result"]["path"] == str(media)
    assert json.loads(requests[-1]["messages"][-1]["content"])["result"] == record["result"]
    assert not getattr(plugin, "_delivered_media", None)
    assert plugin._recent == {} and event.sent == []


def test_on_message_snapshot_excludes_current_and_latest_wins(production):
    """Actual entry freezes prior history, serializes turns, and drops only superseded work."""
    plugin = production()
    plugin._hist().append("42", "persisted prior line")
    plugin._forward_to_text = _empty_forward
    plugin._is_woken = lambda event: True
    plugin._group_policy = lambda group: {}
    plugin._wake_hit = lambda *args: ""
    snapshots = []
    first, second, third = Event("first-nonce"), Event("second-nonce", "1235"), Event("third-nonce", "1236")

    async def handle(event, group, text, has_image, woken):
        """Inject newer arrivals during an active turn to exercise the real pending loop."""
        snapshots.append((text, copy.deepcopy(plugin._turn_context[group]), plugin._request_context(event, group, text, has_image)))
        if text == "first-nonce":
            await plugin.on_message(second)
            await plugin.on_message(third)

    plugin._handle_one = handle
    asyncio.run(plugin.on_message(first))
    assert [item[0] for item in snapshots] == ["first-nonce", "third-nonce"]
    assert snapshots[0][1]["history"] == ("persisted prior line",)
    assert snapshots[0][2].count("first-nonce") == 1
    assert "third-nonce" not in json.dumps(snapshots[1][1]["history"])
    assert "second-nonce" in json.dumps(snapshots[1][1]["history"])
    assert len(plugin._hist().load("42")) == 4
    assert all(event.stopped for event in (first, second, third))
    assert plugin._pending == {} and plugin._busy == {"42": False} and plugin._turn_context == {}


async def _empty_forward(event):
    """Avoid OneBot expansion for snapshot tests while preserving entry control flow."""
    return ""


def test_reload_drains_only_old_owned_background_tasks_and_preserves_delivery_state(production):
    """Same-class/module old loops are canceled once; unrelated and handler tasks survive."""
    old, new = production(), production()
    old._recent = {"42": ["prior confirmed reply"]}
    old._last_sent = {"42": ["sent-before-reload"]}
    old._busy = {"42": False}

    async def _watch_members(self, started, release):
        """Emulate the frame ownership and method name of an old production watcher."""
        started.set()
        await asyncio.sleep(60)
        return self

    async def unrelated_handler(self, started, release):
        """A plugin-owned request is not an idle background loop and must not cancel."""
        started.set()
        await release.wait()
        return self

    # Identical method name alone is insufficient: this owner belongs to another module.
    foreign_class = type("HiganPlugin", (), {"__module__": "unrelated_plugin"})
    foreign = foreign_class()

    async def scenario():
        """Keep all tasks inside one loop and clean up each fixture task explicitly."""
        releases = [asyncio.Event() for _ in range(3)]
        started = [asyncio.Event() for _ in range(3)]
        tasks = [asyncio.create_task(_watch_members(old, started[0], releases[0])),
                 asyncio.create_task(_watch_members(foreign, started[1], releases[1])),
                 asyncio.create_task(unrelated_handler(old, started[2], releases[2]))]
        try:
            await asyncio.gather(*(item.wait() for item in started))
            await new._drain_previous_instances()
            assert tasks[0].cancelled()
            assert not tasks[1].done() and not tasks[2].done()
            assert old._stopping and not getattr(foreign, "_stopping", False)
            assert new._recent == old._recent and new._recent is not old._recent
            assert new._last_sent == old._last_sent
            await new._drain_previous_instances()
            assert not tasks[1].done() and not tasks[2].done()
        finally:
            for release in releases:
                release.set()
            tasks[1].cancel()  # Explicit fixture cleanup, not production migration cancellation.
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(scenario())


def test_reload_waits_wrapped_prebusy_message_and_active_background_work(production):
    """Framework-wrapped pre-busy handlers and active watcher actions finish before migration."""
    old, new = production(), production()

    async def on_message(self, started, release):
        """Represent an old handler suspended before setting its busy flag."""
        started.set()
        await release.wait()
        self._recent["42"] = ["finished handler"]

    async def _watch_members(self, started, release):
        """An active action must drain; only the subsequent direct loop sleep is idle."""
        started.set()
        await release.wait()
        self._last_sent["42"] = ["finished-background"]
        await asyncio.sleep(60)

    async def framework_wrapper(awaitable):
        """Hide plugin ownership below a realistic outer framework coroutine."""
        await awaitable

    async def scenario():
        """Release old work explicitly and assert no premature cancellation or state copy."""
        handler_started, background_started = asyncio.Event(), asyncio.Event()
        handler_release, background_release = asyncio.Event(), asyncio.Event()
        handler = asyncio.create_task(framework_wrapper(on_message(old, handler_started, handler_release)))
        background = asyncio.create_task(framework_wrapper(_watch_members(old, background_started, background_release)))
        await asyncio.gather(handler_started.wait(), background_started.wait())
        drain = asyncio.create_task(new._drain_previous_instances())
        try:
            await asyncio.sleep(0.01)
            assert not drain.done() and not handler.done() and not background.done()
            assert old._stopping and new._recent == {}
            handler_release.set()
            await handler
            await asyncio.sleep(0.01)
            assert not drain.done() and not background.done()
            background_release.set()
            await asyncio.wait_for(drain, 2)
            assert not handler.cancelled() and background.cancelled()
            assert new._recent == {"42": ["finished handler"]}
            assert new._last_sent == {"42": ["finished-background"]}
        finally:
            handler_release.set()
            background_release.set()
            for task in (handler, background, drain):
                if not task.done():
                    task.cancel()
            await asyncio.gather(handler, background, drain, return_exceptions=True)

    asyncio.run(scenario())


def test_reload_timeout_fails_closed_without_canceling_handler(production, monkeypatch):
    """A drain timeout propagates without canceling an active old message or copying state."""
    old, new = production(), production()
    actual_wait_for = asyncio.wait_for

    async def limited_wait(awaitable, timeout):
        """Shorten only the migration deadline, keeping the failure semantics unchanged."""
        return await actual_wait_for(awaitable, min(timeout, 0.03))

    async def on_message(self, started, release):
        """Keep a real old-owner frame suspended until explicit test cleanup."""
        started.set()
        await release.wait()
        return self

    async def scenario():
        """Verify timeout leaves ongoing work alive and replacement state untouched."""
        started, release = asyncio.Event(), asyncio.Event()
        task = asyncio.create_task(on_message(old, started, release))
        await started.wait()
        monkeypatch.setattr(asyncio, "wait_for", limited_wait)
        try:
            with pytest.raises(asyncio.TimeoutError):
                await new._drain_previous_instances()
            assert old._stopping and not task.done()
            assert new._recent == {} and new._last_sent == {}
        finally:
            release.set()
            await task

    asyncio.run(scenario())


def test_web_search_rss_preserves_engine_order_without_lexical_filter(production, monkeypatch):
    """Structured source ranking survives without query-word or product/domain filtering."""
    plugin = production()
    requests = []
    feed = """<rss><channel>
      <item><title>Unrelated-looking ranked first</title><link>https://first.invalid/a</link><description>&lt;b&gt;Primary&lt;/b&gt; &amp;amp; evidence</description></item>
      <item><title>Duplicate should not replace first</title><link>https://first.invalid/a</link><description>duplicate</description></item>
      <item><title>Not a public URL scheme</title><link>file:///fixture</link></item>
      <item><title></title><link>https://empty.invalid</link></item>
      <item><title>Ranked second</title><link>http://second.invalid/b</link><description>Another result</description></item>
    </channel></rss>"""

    def urlopen(request, timeout):
        """Return RSS only; successful structured evidence must not invoke HTML fallback."""
        requests.append(request.full_url)
        assert "format=rss" in request.full_url and timeout > 0
        return io.BytesIO(feed.encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    results = plugin._web_search("opaque-query-z91", limit=5)
    assert [item["url"] for item in results] == ["https://first.invalid/a", "http://second.invalid/b"]
    assert [item["title"] for item in results] == ["Unrelated-looking ranked first", "Ranked second"]
    assert "Primary" in results[0]["snippet"] and "<b>" not in results[0]["snippet"]
    assert len(requests) == 1 and "q=opaque-query-z91" in requests[0]


def test_web_search_html_fallback_accepts_attribute_order_and_class_variants(production, monkeypatch):
    """Broken RSS falls back to HTML with extra classes and href after other attributes."""
    plugin = production()
    requests = []
    page = """<html><ol>
      <li data-index="1" class="decorated b_algo extra"><h2 class="heading"><a data-id="x" target="_blank" href="https://first.invalid/?a=1&amp;b=2"><strong>First</strong> &amp; title</a></h2><p class="summary">Visible <em>body</em> &amp; evidence</p></li>
      <LI class='b_algo' data-index='2'><H2><A href='https://second.invalid/' data-more='y'>Second</A></H2><P>Next body</P></LI>
      <li class="not_b_algo"><h2><a href="https://ignored.invalid/">Ignored ad</a></h2></li>
    </ol></html>"""

    def urlopen(request, timeout):
        """Serve malformed feed then deterministic HTML without reaching either real host."""
        requests.append(request.full_url)
        return io.BytesIO(("not valid RSS <" if "format=rss" in request.full_url else page).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    results = plugin._web_search("arbitrary plain query", limit=5)
    assert results == [
        {"title": "First & title", "url": "https://first.invalid/?a=1&b=2", "snippet": "Visible body & evidence"},
        {"title": "Second", "url": "https://second.invalid/", "snippet": "Next body"},
    ]
    assert len(requests) == 2 and "format=rss" in requests[0]
    assert requests[1].startswith("https://cn.bing.com/search?")
    assert all("q=arbitrary+plain+query" in url for url in requests)


def test_web_search_source_errors_are_explicit_and_dispatch_reports_failure(production, monkeypatch):
    """Both unavailable sources raise a clear error, never success with fabricated evidence."""
    plugin = production()
    requests = []

    def urlopen(request, timeout):
        """Fail each source locally so raw source errors cannot trigger real retries."""
        requests.append(request.full_url)
        raise OSError("offline source unavailable")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(RuntimeError, match="^search_sources_unavailable$") as error:
        plugin._web_search("generic query")
    assert isinstance(error.value.__cause__, OSError)
    assert len(requests) == 2
    result = asyncio.run(plugin._dispatch_tool("web_search", {"query": "generic query"}, "42"))
    assert result == {"ok": False, "query": "generic query", "error": "RuntimeError"}
    assert len(requests) == 4


def test_on_message_waits_readiness_and_rejects_stopping_instance(production):
    """Reload lifecycle blocks premature work and does not admit new old-instance turns."""
    plugin = production()
    plugin._forward_to_text = _empty_forward
    plugin._is_woken = lambda event: True
    plugin._group_policy = lambda group: {}
    plugin._wake_hit = lambda *args: ""
    handled = []

    async def handle(*args):
        """Record admitted turns without model or delivery effects."""
        handled.append(args[2])

    plugin._handle_one = handle

    async def scenario():
        """Coordinate readiness deterministically without real service startup."""
        plugin._ready = asyncio.Event()
        waiting = asyncio.create_task(plugin.on_message(Event("after-ready")))
        await asyncio.sleep(0)
        assert not waiting.done() and handled == []
        plugin._ready.set()
        await waiting
        assert handled == ["after-ready"]
        plugin._stopping = True
        await plugin.on_message(Event("must-not-admit"))
        assert handled == ["after-ready"]
        assert "must-not-admit" not in json.dumps(plugin._recent)

    asyncio.run(scenario())
