"""Offline native WebChat lifecycle regressions with the unchanged plugin tool loop.

Compile only the pinned response queue, terminal sender and scheduler entry. No
AstrBot startup, credentials, model HTTP calls or production state are involved.
"""

import ast
import asyncio
import copy
from types import SimpleNamespace

import test_production_integration as integration
from test_batch_flow import _final
from test_waiting_batch_flow import _settle, _waiting_entry

production = integration.production


def _native_webchat():
    """Load actual vendored completion and queue semantics without importing core."""
    root = integration.ROOT / "vendor" / "astrbot" / "astrbot" / "core"
    definitions = []
    for relative, class_name, method_name, output_name in (
        ("platform/sources/webchat/webchat_queue_mgr.py", "WebChatQueueMgr", None, None),
        ("platform/sources/webchat/webchat_event.py", "WebChatMessageEvent", "_send", "native_send"),
        ("pipeline/scheduler.py", "PipelineScheduler", "execute", "native_execute"),
    ):
        path = root / relative
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        klass = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                     and node.name == class_name)
        selected = copy.deepcopy(klass if method_name is None else next(
            node for node in klass.body if isinstance(node, ast.AsyncFunctionDef)
            and node.name == method_name))
        if method_name:
            selected.name = output_name
            selected.decorator_list = []
        definitions.append(selected)
    namespace = {
        "asyncio": asyncio, "logger": integration.logging.getLogger("offline-webchat"),
        "Plain": SimpleNamespace,
        "active_event_registry": SimpleNamespace(register=lambda event: None,
                                                  unregister=lambda event: None),
        "WecomAIBotMessageEvent": type("UnusedWecomEvent", (), {}),
    }
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[
        ast.alias(name="annotations")], level=0), *definitions], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), "<pinned-webchat-ast>", "exec"), namespace)
    queue = namespace["WebChatQueueMgr"]()
    namespace["webchat_queue_mgr"] = queue

    class WebEvent(integration.Event):
        """Expose the native private conversation identity and response transport."""

        def __init__(self, ident="first", conversation="conversation-a"):
            super().__init__("first dashboard question", ident, group="", sender_id="astrbot")
            self.unified_msg_origin = "webchat:FriendMessage:webchat!astrbot!" + conversation

        def get_platform_name(self):
            """Return trusted adapter metadata, never a model-supplied route."""
            return "webchat"

        async def send(self, chain):
            """Use pinned WebChat terminal and plain response queue behavior."""
            if chain is not None:
                chain.type = "plain"
            await namespace["native_send"](self.message_obj.message_id, chain,
                                            session_id=self.unified_msg_origin)
            return []

        def cleanup_temporary_local_files(self):
            """The synthetic text request owns no temporary media."""
            return None

    namespace["WebChatMessageEvent"] = WebEvent
    return queue, WebEvent, namespace["native_execute"]


def test_first_webchat_tool_reply_arrives_before_native_scheduler_end(production):
    """Do not close and discard the first tool-enabled response before delivery."""
    plugin = _waiting_entry(production())
    queue, event_type, execute = _native_webchat()
    event = event_type()
    requests, tools = [], []

    async def scenario():
        """Gate a native tool so early pipeline completion fails deterministically."""
        entered, release = asyncio.Event(), asyncio.Event()
        back_queue = queue.get_or_create_back_queue("first", event.unified_msg_origin)

        async def model(system, prompt, refs, messages=None, allow_tools=True):
            """Return a native tool request followed by its correlated final reply."""
            requests.append(copy.deepcopy(messages))
            if len(requests) == 1:
                assert allow_tools
                return "", [integration._call("self_status", call_id="first-native-tool")]
            assert messages[-1]["role"] == "tool"
            assert messages[-1]["tool_call_id"] == "first-native-tool"
            return _final("WebChat tool completed.", "first"), []

        async def tool(name, args, group="", event=None):
            """Wait without networking, while retaining real agent-loop dispatch."""
            tools.append(name)
            entered.set()
            await release.wait()
            return {"ok": True, "offline": True}

        plugin._llm_tools, plugin._dispatch_tool = model, tool
        scheduler = SimpleNamespace(_process_stages=plugin.on_message)
        pipeline = asyncio.create_task(execute(scheduler, event))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            assert not pipeline.done(), "WebChat scheduler emitted end before its tool completed"
            assert back_queue.empty()
        finally:
            release.set()
            await asyncio.wait_for(pipeline, timeout=2)
            await _settle(plugin)
        emitted = []
        while not back_queue.empty():
            emitted.append(back_queue.get_nowait())
        assert [part["type"] for part in emitted] == ["plain", "end"]
        assert emitted[0]["data"] == "WebChat tool completed."
        assert not plugin._scope_workers and not plugin._background_tasks

    asyncio.run(scenario())
    assert len(requests) == 2 and tools == ["self_status"]


def test_webchat_conversations_do_not_share_history_or_pending_batches(production):
    """A shared dashboard login must not merge independent conversation IDs."""
    plugin = _waiting_entry(production())
    queue, event_type, execute = _native_webchat()
    # Real UUID-like IDs differ after the legacy archive's filename cut point.
    events = [event_type("request-a", "same-prefix-" * 6 + "a"),
              event_type("request-b", "same-prefix-" * 6 + "b")]
    turns = []

    async def handle(event, group, text, has_image, woken):
        """Inspect native scope and history without a model call."""
        turns.append((group, copy.deepcopy(plugin._turn_context[group])))

    async def scenario():
        """Run distinct dashboard conversations through actual ingress and workers."""
        plugin._handle_one = handle
        for event in events:
            queue.get_or_create_back_queue(event.message_obj.message_id, event.unified_msg_origin)
            await execute(SimpleNamespace(_process_stages=plugin.on_message), event)
        await _settle(plugin)

    asyncio.run(scenario())
    assert len(turns) == 2 and turns[0][0] != turns[1][0]
    assert all(not turn[1]["history"] for turn in turns)
    assert [[record["id"] for record in turn[1]["messages"]] for turn in turns] == [
        ["request-a"], ["request-b"]]
    assert len(plugin._recent) == 2
    assert len({plugin._hist()._path(scope) for scope in plugin._recent}) == 2


def test_concurrent_webchat_requests_each_keep_their_own_response_queue(production):
    """Do not coalesce two in-flight dashboard request IDs into one reply anchor."""
    plugin = _waiting_entry(production())
    queue, event_type, execute = _native_webchat()
    events = [event_type('parallel-a'), event_type('parallel-b')]

    async def scenario():
        """Hold the shared lock until both requests have reached the owned scope worker."""
        admitted = asyncio.Event()
        original_policy = plugin._policy_allows

        def policy(event, group):
            """Expose the second native admission without sleeps or timing windows."""
            if event is events[1]:
                admitted.set()
            return original_policy(event, group)

        async def handle(event, group, text, has_image, woken):
            """Emit one ordinary reply on each actual request transport."""
            chain = integration.MessageChain().message(event.message_obj.message_id)
            await event.send(chain)

        plugin._policy_allows, plugin._handle_one = policy, handle
        plugin._execution_lock = asyncio.Lock()
        await plugin._execution_lock.acquire()
        queues = [queue.get_or_create_back_queue(event.message_obj.message_id,
                                                 event.unified_msg_origin) for event in events]
        pipelines = [asyncio.create_task(execute(SimpleNamespace(_process_stages=plugin.on_message), event))
                     for event in events]
        try:
            await asyncio.wait_for(admitted.wait(), timeout=2)
            assert not any(pipeline.done() for pipeline in pipelines)
            assert len(plugin._background_tasks) == 1
        finally:
            plugin._execution_lock.release()
            await asyncio.wait_for(asyncio.gather(*pipelines), timeout=2)
            await _settle(plugin)
        for event, responses in zip(events, queues):
            reply, end = responses.get_nowait(), responses.get_nowait()
            assert reply['type'] == 'plain' and reply['data'] == event.message_obj.message_id
            assert end['type'] == 'end' and responses.empty()
        assert not plugin._scope_workers

    asyncio.run(scenario())


def test_cancelled_webchat_waiter_never_cancels_active_tool_worker(production):
    """Request cancellation releases its handler without interrupting owned work."""
    plugin = _waiting_entry(production())
    queue, event_type, execute = _native_webchat()
    event = event_type('cancelled-request')

    async def scenario():
        """Cancel only the waiting native request after its worker entered the turn."""
        started, release, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def handle(*args):
            """Keep active execution visible until explicitly released by this test."""
            started.set()
            await release.wait()
            completed.set()

        plugin._handle_one = handle
        queue.get_or_create_back_queue(event.message_obj.message_id, event.unified_msg_origin)
        pipeline = asyncio.create_task(execute(SimpleNamespace(_process_stages=plugin.on_message), event))
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            pipeline.cancel()
            result = await asyncio.gather(pipeline, return_exceptions=True)
            assert isinstance(result[0], asyncio.CancelledError)
            assert plugin._background_tasks and not completed.is_set()
            assert not any(task.cancelled() for task in plugin._background_tasks)
        finally:
            release.set()
            await _settle(plugin)
        assert completed.is_set() and not plugin._scope_workers

    asyncio.run(scenario())


def test_qq_ingress_still_returns_while_owned_worker_is_running(production):
    """Preserve nonblocking QQ admission instead of awaiting every platform worker."""
    plugin = _waiting_entry(production())
    event = integration.Event("QQ question", "qq-request")

    async def scenario():
        """Hold the tool/send execution lock while admitting an ordinary QQ event."""
        plugin._execution_lock = asyncio.Lock()
        await plugin._execution_lock.acquire()
        try:
            await asyncio.wait_for(plugin.on_message(event), timeout=2)
            assert plugin._busy["42"] and len(plugin._pending["42"]) == 1
        finally:
            plugin._handle_one = _no_op_turn
            plugin._execution_lock.release()
            await _settle(plugin)
        assert not plugin._scope_workers

    asyncio.run(scenario())


async def _no_op_turn(*args):
    """Complete synthetic queued work without a model or transport operation."""
    return None
