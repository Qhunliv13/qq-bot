"""Offline delivery regressions against the unchanged production AST entry points.

The shared fixture compiles production dispatch, native turn, task runner and
scheduler methods. Only provider, native transport, clock and local store paths
are replaced; no QQ, VM, models, startup services or live network are used.
"""

import asyncio
import json
import pytest

import test_production_integration as integration
from test_production_integration import Event, _call, _provider
from plugin import tasks

# Reuse the existing AST loader rather than maintaining a second production copy.
production = integration.production


@pytest.fixture
def task_runtime(production, monkeypatch, tmp_path):
    """Bind real production methods to an isolated durable store and permissive scope."""
    monkeypatch.setattr(tasks, "STORE", str(tmp_path / "delivery-tasks.json"))
    plugin = production()
    plugin._tasks_mod = lambda: tasks
    plugin._group_policy = lambda scope: {"muted": [], "mention_only": []}
    return plugin


def _saved_task():
    """Create genuine due work so successful once completion exercises the real store."""
    return tasks.add("offline receipt task", "send a receipt", "42", repeat="once",
                     origin="offline:42", run_at=0)


def _run_scheduler(plugin, monkeypatch):
    """Run one actual scheduler claim/mark cycle without waiting or extra iterations."""
    original_mark = tasks.mark
    marks = []

    def mark(task_id, result, **kwargs):
        """Persist the real acknowledgement and stop only after the owned task is marked."""
        marks.append((task_id, result, kwargs))
        try:
            return original_mark(task_id, result, **kwargs)
        finally:
            plugin._stopping = True

    async def tick(delay):
        """Yield a deterministic tick instead of opening a periodic background timer."""
        assert delay == 25

    monkeypatch.setattr(tasks, "mark", mark)
    monkeypatch.setattr(asyncio, "sleep", tick)
    asyncio.run(plugin._task_loop())
    assert len(marks) == 1
    return marks[0], tasks.listing(only_active=False)[0]


@pytest.mark.parametrize("native_result", [{"ok": True, "data": None}, None])
def test_real_qq_send_root_health_is_not_delivery(task_runtime, native_result):
    """A healthy root or null body cannot become a native delivery receipt."""
    calls = []

    def native(action, parameters):
        """Expose only the local native boundary, never contact OneBot or the VM."""
        calls.append((action, parameters))
        return native_result

    task_runtime._onebot_http = native
    result = asyncio.run(task_runtime._dispatch_tool(
        "qq_send", {"text": "offline answer"}, "42", event=Event()))
    assert result["ok"] is False and result["delivered"] is False
    assert calls[0][0] == "send_group_msg"


def test_real_qq_send_native_mentions_and_message_id(task_runtime):
    """The real dispatch builds native at arrays and preserves the platform message ID."""
    calls = []

    def native(action, parameters):
        """Return the precise accepted OneBot envelope without a live send."""
        calls.append((action, parameters))
        return {"status": "ok", "retcode": 0, "data": {"message_id": 7654321}}

    task_runtime._onebot_http = native
    result = asyncio.run(task_runtime._dispatch_tool(
        "qq_send", {"text": "offline answer", "mentions": ["10001"]}, "42", event=Event()))
    assert result["ok"] is True and result["delivered"] is True
    assert str(result["message_id"]) == "7654321"
    action, parameters = calls[0]
    assert action == "send_group_msg" and str(parameters["group_id"]) == "42"
    assert isinstance(parameters["message"], list)
    assert {"type": "at", "data": {"qq": "10001"}} in parameters["message"]


@pytest.mark.parametrize("confirmed", [False, True])
def test_task_silent_after_real_qq_send_requires_receipt(task_runtime, monkeypatch, confirmed):
    """SILENT neither hides failed delivery nor forces a duplicate successful final send."""
    task = _saved_task()
    native_calls, final_sends = [], []

    def native(action, parameters):
        """Model successful acceptance or the historically misleading healthy root."""
        native_calls.append((action, parameters))
        return ({"status": "ok", "retcode": 0, "data": {"message_id": 8765432}}
                if confirmed else {"ok": True, "data": None})

    async def final_send(*args, **kwargs):
        """Detect any inappropriate forced final after the model explicitly chose SILENT."""
        final_sends.append((args, kwargs))
        raise AssertionError("SILENT must not force another final send")

    task_runtime._onebot_http = native
    task_runtime._send = final_send
    requests = _provider(monkeypatch, [("", [_call("qq_send", {"text": "offline delivery"})]),
                                       ("SILENT", [])])
    mark, stored = _run_scheduler(task_runtime, monkeypatch)
    assert mark[0] == task["id"] and mark[2]["success"] is confirmed
    assert stored["status"] == ("done" if confirmed else "paused")
    assert final_sends == [] and len(native_calls) == 1
    result = json.loads(next(message["content"] for message in requests[-1]["messages"]
                             if message["role"] == "tool"))["result"]
    assert result["delivered"] is confirmed
    outcome = stored["last_outcome"]
    assert outcome["delivery_attempted"] is True
    assert outcome["delivery_confirmed"] is confirmed
    # Missing native receipts are ambiguous, distinct from an explicit rejection.
    assert outcome["delivery_failed"] is False
    assert outcome["delivery_unknown"] is (not confirmed)
    assert outcome["delivery_message_ids"] == (["8765432"] if confirmed else [])


@pytest.mark.parametrize("tool_name", ["send_file", "send_shot", "vm_send_shot"])
def test_task_silent_after_unreceipted_file_send_pauses(task_runtime, monkeypatch, tmp_path, tool_name):
    """Native upload acceptance and legacy ok responses cannot prove recipient delivery."""
    from plugin import qq_native_actions

    _saved_task()
    calls, native_calls = [], []
    path = tmp_path / "report.txt"
    path.write_text("offline task report", encoding="utf-8")

    async def prepare_file(local):
        """Return an inert container path without starting the production container copier."""
        assert local == str(path)
        return "/offline-staged/report.txt"

    def native(action, parameters):
        """Acknowledge native upload without fabricating a recipient message ID."""
        native_calls.append((action, parameters))
        return {"status": "ok", "retcode": 0, "data": None}

    monkeypatch.setattr(qq_native_actions, "prepare_container_file", prepare_file)
    task_runtime._onebot_http = native

    async def vm(name, arguments):
        """Stub the actual external file boundary while preserving native dispatch logic."""
        calls.append((name, arguments))
        return {"ok": True}

    task_runtime._vm_tool = vm
    _provider(monkeypatch, [("", [_call(tool_name, {"path": str(path)})]),
                            ("SILENT", [])])
    mark, stored = _run_scheduler(task_runtime, monkeypatch)
    assert mark[2]["success"] is False and stored["status"] == "paused"
    if tool_name == "send_file":
        # Files use native upload now; a VM welcome response cannot participate.
        assert calls == []
        assert native_calls[0][0] == "upload_group_file"
    else:
        assert calls[0][0] == "vm_send_shot"
        assert native_calls == []
    assert stored["last_outcome"]["delivery_unknown"] is True
    assert stored["last_outcome"]["delivery_confirmed"] is False


@pytest.mark.parametrize("confirmed", [True, False])
def test_media_handoff_observes_final_plugin_delivery(task_runtime, monkeypatch, tmp_path, confirmed):
    """Observe the completed local-image handoff, not its intermediate unreceipted result."""
    _saved_task()
    delivered = []
    path = tmp_path / "report.png"
    path.write_bytes(b"offline image fixture")

    async def vm(name, arguments):
        """Fail if a local media handoff incorrectly invokes the obsolete VM sender."""
        raise AssertionError("local media must not invoke VM transport")

    async def deliver_media(event, scope, path, send_as):
        """Replace only the framework boundary and retain real production result handling."""
        delivered.append((event.task["id"], scope, path, send_as))
        return confirmed

    task_runtime._vm_tool = vm
    task_runtime._deliver_media = deliver_media
    requests = _provider(monkeypatch, [
        ("", [_call("send_file", {"path": str(path)})]), ("SILENT", [])])
    mark, stored = _run_scheduler(task_runtime, monkeypatch)
    assert delivered == [(stored["id"], "42", str(path), "image")]
    assert mark[2]["success"] is confirmed
    assert stored["status"] == ("done" if confirmed else "paused")
    outcome = stored["last_outcome"]
    assert outcome["delivery_attempted"] is True
    assert outcome["delivery_confirmed"] is confirmed
    if confirmed:
        # A handoff awaiting the plugin is not an earlier ambiguous send attempt.
        assert outcome["delivery_unknown"] is False
        assert outcome["delivery_failed"] is False
    else:
        assert outcome["delivery_unknown"] or outcome["delivery_failed"]
    result = json.loads(next(message["content"] for message in requests[-1]["messages"]
                             if message["role"] == "tool"))["result"]
    assert result["delivered"] is confirmed


def test_failed_qq_send_cannot_be_overridden_by_task_done(task_runtime, monkeypatch):
    """The real tool callback refuses explicit completion after a failed QQ attempt."""
    task = _saved_task()
    task_runtime._onebot_http = lambda action, parameters: {
        "status": "failed", "retcode": 100, "data": None}
    requests = _provider(monkeypatch, [
        ("", [_call("qq_send", {"text": "offline delivery"})]),
        ("", [_call("task_done", {"id": task["id"]})]), ("SILENT", [])])
    assert asyncio.run(task_runtime._run_task_once(task)) == ""
    replies = [json.loads(message["content"])["result"] for message in requests[-1]["messages"]
               if message["role"] == "tool"]
    assert len(replies) == 2 and replies[0]["delivered"] is False
    assert replies[1]["ok"] is False and replies[1].get("error")
    assert tasks.listing(only_active=False)[0]["status"] == "active"
    assert task["_delivery_outcome"]["delivery_failed"] is True


def test_task_add_absolute_deadline_survives_actual_dispatch(task_runtime):
    """Absolute scheduling reaches the real store without parsing natural-language time."""
    deadline = 1900000000.25
    result = asyncio.run(task_runtime._dispatch_tool(
        "task_add", {"title": "absolute schedule", "goal": "offline", "run_at": deadline,
                     "repeat": "once"}, "42", event=Event()))
    assert result["ok"] is True
    assert result["task"]["next_run_at"] == deadline
    assert tasks.listing(only_active=False)[0]["next_run_at"] == deadline
    assert result.get("scheduler_accepted") is True
    assert result.get("delivered") is not True


@pytest.mark.parametrize("destination", ["43", "private:10002"])
def test_task_add_rejects_cross_scope_origin_even_for_owner(task_runtime, destination):
    """Owner identity cannot reuse a stored source origin for an unobserved destination."""
    event = Event()
    owner = next(iter(task_runtime._dispatch_tool.__func__.__globals__["OWNER_QQS"]), "10001")
    event.get_sender_id = lambda: str(owner)
    result = asyncio.run(task_runtime._dispatch_tool(
        "task_add", {"title": "cross scope", "scope": destination}, "42", event=event))
    assert result["ok"] is False and result.get("error")
    assert tasks.listing(only_active=False) == []


def test_task_add_missing_origin_is_not_scheduler_acceptance(task_runtime):
    """A task without a usable destination must not claim scheduler acceptance."""
    event = Event()
    event.unified_msg_origin = ""
    result = asyncio.run(task_runtime._dispatch_tool(
        "task_add", {"title": "missing origin"}, "42", event=event))
    assert result["ok"] is False and result.get("error")
    assert tasks.listing(only_active=False) == []


def test_task_add_paused_upsert_does_not_claim_scheduler_acceptance(task_runtime):
    """An existing paused task remains paused and cannot report an accepted active schedule."""
    task = tasks.add("paused schedule", "offline", "42", origin="offline:42")
    tasks.set_status(task["id"], status="paused")
    result = asyncio.run(task_runtime._dispatch_tool(
        "task_add", {"title": "paused schedule", "goal": "offline"}, "42", event=Event()))
    assert result["task"]["status"] == "paused"
    assert result.get("scheduler_accepted") is False
    assert result.get("delivered") is not True
