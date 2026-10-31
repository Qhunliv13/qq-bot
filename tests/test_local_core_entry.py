"""Offline integration of the local adapter with actual production core methods."""
import ast
import asyncio
import logging
from functools import wraps
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin.local_runtime import LOCAL_ORIGIN, LOCAL_SCOPE, LocalEvent, LocalRuntime
from plugin.task_event import TaskEvent
from test_production_integration import production as production_fixture, _provider, _call

# Reuse the established isolated native-core fixture without changing old tests.
production = production_fixture


SOURCE = Path(__file__).resolve().parents[1] / "plugin" / "main.py"


def run_async(function):
    """Run isolated async contracts without requiring pytest-asyncio."""
    @wraps(function)
    def wrapper(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return wrapper


def core_class():
    """Extract production entry/security methods without AstrBot initialization."""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    original = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "HiganPlugin")
    names = {"_local_handle", "_is_owner", "_policy_allows", "_drain_previous_instances",
             "_owned_task_nodes", "_drain_owned_work"}
    methods = [n for n in original.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        ast.ClassDef(name="Core", bases=[], keywords=[], body=methods, decorator_list=[])], type_ignores=[])
    ns = {"__package__": "plugin", "asyncio": asyncio, "OWNER_QQS": {"12345"},
          "logger": logging.getLogger(__name__), "TOOL_TURN_TIMEOUT": None}
    exec(compile(ast.fix_missing_locations(module), str(SOURCE), "exec"), ns)
    return ns["Core"]


class History:
    """Capture the real recorder interface without external storage or transport."""
    def __init__(self):
        self.rows = []

    def load(self, scope, limit):
        """Restore only this destination's actual prior lines."""
        rows = [line for key, line, _ in self.rows if key == scope]
        return rows if limit is None else rows[-limit:]

    def load_records(self, scope, limit):
        """Return native source records for chronological local/public context merging."""
        rows = [{"scope": key, "line": line, "record": record, "ts": index}
                for index, (key, line, record) in enumerate(self.rows) if key == scope]
        return rows if limit is None else rows[-limit:]

    def append(self, scope, line, record=None):
        """Retain source metadata for exact-once assertions."""
        self.rows.append((scope, line, record))


def plugin(tmp_path):
    """Create one live core and its privately bound local runtime."""
    obj = core_class()()
    history = History()
    obj._hist = lambda: history
    obj._recent = {}
    obj._ready = asyncio.Event()
    obj._ready.set()
    obj._execution_lock = asyncio.Lock()
    obj._stopping = False
    obj._clock_context = lambda: {"now": "offline"}
    obj._local_runtime = LocalRuntime(obj, root=tmp_path)
    return obj, history


def event(obj, text="hello"):
    """Construct a server-capability event, never one supplied by RPC JSON."""
    runtime = obj._local_runtime
    return LocalEvent(runtime, text, capability=runtime._capability)


def test_full_native_core_retains_persona_tools_and_local_output(production, monkeypatch, tmp_path):
    """Exercise the actual full native model/tool core through local admission offline."""
    from types import MethodType
    obj = production()
    methods = core_class()
    obj._local_handle = MethodType(methods._local_handle, obj)
    obj._is_owner = MethodType(methods._is_owner, obj)
    obj._local_runtime = LocalRuntime(obj, root=tmp_path)
    obj._ready = asyncio.Event()
    obj._ready.set()
    obj._execution_lock = asyncio.Lock()
    requests = _provider(monkeypatch, [("", [_call("reply_now", {"text": "native output", "finish": True})])])
    local = event(obj)
    result = asyncio.run(obj._local_handle(local, "use the existing core"))
    assert len(requests) == 1
    assert requests[0]["messages"][0]["content"].startswith("offline persona")
    assert any(item["function"]["name"] == "reply_now" for item in requests[0]["tools"])
    assert result["recorded"] is True and len(result["outputs"]) == 1
    assert result["client_received"] is False
    lines = obj._hist().load(LOCAL_SCOPE, 200)
    assert len(lines) == 2
    assert "native output" in lines[-1]
    turn = obj._turn_context[LOCAL_SCOPE]
    assert turn["_deliveries"][0]["delivery"]["status"] == "local_recorded"
    assert turn["_deliveries"][0]["delivery"]["delivered"] is False


@run_async
async def test_same_instance_core_and_exact_once_output(tmp_path):
    """Local input reaches the existing decision entry and shared recorder once."""
    obj, history = plugin(tmp_path)
    local = event(obj)
    calls = []

    async def decide(ev, scope, text, has_image):
        """Prove the actual instance, lock, original prompt and native turn reach core."""
        assert obj._execution_lock.locked()
        assert ev is local
        assert scope == LOCAL_SCOPE
        assert text == "hello" and not has_image
        assert obj._turn_context[scope]["history"] == ()
        calls.append(obj)
        return "world"

    async def send(ev, scope, text, **kwargs):
        """Use the actual LocalEvent send sink rather than any QQ adapter."""
        return await ev.send(text)

    obj._decide_tools_first = decide
    obj._send = send
    result = await obj._local_handle(local, "hello")
    assert calls == [obj]
    assert result["text"] == "world" and result["recorded"] is True
    assert result["client_received"] is False and result["transport"] == "local"
    assert len(history.rows) == 2
    assert len(result["outputs"]) == 1
    assert all(scope == LOCAL_SCOPE for scope, _, _ in history.rows)
    assert "message_id" not in result


@run_async
async def test_forgery_and_foreign_instance_rejected_before_history(tmp_path):
    """Names, route strings and another instance's capability cannot grant entry."""
    obj, history = plugin(tmp_path)
    other, _ = plugin(tmp_path)
    forged = SimpleNamespace(unified_msg_origin=LOCAL_ORIGIN, get_sender_id=lambda: "local-operator")
    for candidate in (forged, event(other)):
        with pytest.raises(PermissionError):
            await obj._local_handle(candidate, "forged")
        assert obj._is_owner(candidate) is False
    assert history.rows == []
    assert obj._is_owner(event(obj)) is True


@run_async
async def test_local_turn_serializes_whole_input_and_result(tmp_path):
    """A queued turn cannot append input while an accepted decision is running."""
    obj, history = plugin(tmp_path)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def decide(ev, scope, text, has_image):
        """Pause the first model boundary while the next local turn waits."""
        calls.append(text)
        if text == "first":
            started.set()
            await release.wait()
        return ""

    obj._decide_tools_first = decide
    first = asyncio.create_task(obj._local_handle(event(obj, "first"), "first"))
    await started.wait()
    second = asyncio.create_task(obj._local_handle(event(obj, "second"), "second"))
    await asyncio.sleep(0)
    assert len(history.rows) == 1
    assert calls == ["first"]
    release.set()
    await asyncio.gather(first, second)
    assert calls == ["first", "second"]
    assert len(history.rows) == 2


def test_public_memory_and_local_history_keep_original_sources(production, monkeypatch, tmp_path):
    """Local core sees public memory plus own history, never unrelated private records."""
    from types import MethodType
    import json
    obj = production()
    methods = core_class()
    obj._local_handle = MethodType(methods._local_handle, obj)
    obj._is_owner = MethodType(methods._is_owner, obj)
    obj._local_runtime = LocalRuntime(obj, root=tmp_path)
    obj._ready = asyncio.Event()
    obj._ready.set()
    obj._execution_lock = asyncio.Lock()
    obj._hist().append("42", "public prior")
    obj._hist().append("private:other", "private secret")
    obj._hist().append(LOCAL_SCOPE, "local prior")
    requests = _provider(monkeypatch, [("SILENT", [])])
    asyncio.run(obj._local_handle(event(obj), "current local"))
    contexts = [json.loads(item["content"]) for item in requests[0]["messages"]
                if item["role"] == "user"]
    history = contexts[0]["conversation"]["history"]
    assert {row["line"] for row in history} == {"public prior", "local prior"}
    assert {row["origin_scope"] for row in history} == {"42", LOCAL_SCOPE}
    assert contexts[0]["conversation"]["messages"][0]["sender"]["role"] == "owner"
    assert "private secret" not in str(contexts)
    assert LOCAL_SCOPE not in obj._hist().group_scopes()


@run_async
async def test_stopping_refuses_new_input_without_writing(tmp_path):
    """Reload closes admission without silently accepting a new generation."""
    obj, history = plugin(tmp_path)
    obj._stopping = True
    with pytest.raises(RuntimeError, match="local_entry_stopping"):
        await obj._local_handle(event(obj), "hello")
    assert history.rows == []


@run_async
async def test_reload_closes_listener_and_drains_accepted_local_turn(tmp_path):
    """Nested model waits are active handlers, not idle loops safe to cancel."""
    old, _ = plugin(tmp_path)
    new, _ = plugin(tmp_path)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def decide(ev, scope, text, has_image):
        """Keep a real local entry suspended at its model boundary during reload."""
        entered.set()
        await release.wait()
        return ""

    old._decide_tools_first = decide
    accepted = asyncio.create_task(old._local_handle(event(old), "accepted"))
    await entered.wait()
    draining = asyncio.create_task(new._drain_previous_instances())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert old._stopping is True and old._local_runtime._closing is True
    assert not accepted.done() and not draining.done()
    release.set()
    await asyncio.gather(accepted, draining)
    assert not accepted.cancelled()


@run_async
async def test_local_task_records_without_qq_or_human_identity(tmp_path):
    """Persisted local tasks publish through the same output sink, never QQ."""
    obj, history = plugin(tmp_path)
    task = TaskEvent(obj, {"scope": LOCAL_SCOPE, "origin": LOCAL_ORIGIN})
    result = await task.send("task result")
    assert result == {"transport": "local", "status": "local_recorded", "recorded": True,
                      "client_received": False}
    assert task.get_sender_id() == "background-task"
    assert not obj._is_owner(task)
    assert obj._policy_allows(task, LOCAL_SCOPE)
    assert len(history.rows) == 1
    assert "message_id" not in result
