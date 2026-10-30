"""Offline root-transport contracts, plus real Linux-root Unix socket checks."""
import asyncio
import json
import os
import socket
import stat
import struct
from types import SimpleNamespace

import pytest

from plugin.history import Recorder
from plugin.local_runtime import (
    LOCAL_ORIGIN, LOCAL_SCOPE, MAX_REQUEST_BYTES, LocalEvent, LocalRuntime,
    is_local_event, local_route,
)


class FakePlugin:
    """Retain a real existing Recorder while avoiding framework/model startup."""

    def __init__(self, root):
        self.recorder = Recorder(str(root / "history"))
        self.calls = []

    def _hist(self):
        """Reuse exactly the production history abstraction."""
        return self.recorder

    async def _local_handle(self, event, prompt):
        """Observe exact identity and output without any QQ or model dependency."""
        self.calls.append((event, prompt))
        await event.send("answer: " + prompt)
        return {"text": "answer: " + prompt}


class FakeWriter:
    """Observe authentication ordering and writes without opening sockets."""

    def __init__(self, uid=0):
        self.uid = uid
        self.data = b""
        self.closed = False

    def get_extra_info(self, name):
        """Return a credential-bearing socket substitute only for the socket query."""
        assert name == "socket"
        return self

    def getsockopt(self, level, option, length):
        """Produce native Linux pid/uid/gid bytes, not request-supplied credentials."""
        assert length == struct.calcsize("3i")
        return struct.pack("3i", 10, self.uid, self.uid)

    def write(self, data):
        """Capture only explicit protocol responses."""
        self.data += data

    async def drain(self):
        """Mimic a completed local write without claiming a remote receipt."""
        return None

    def close(self):
        """Record connection cleanup."""
        self.closed = True

    async def wait_closed(self):
        """Support normal stream teardown."""
        return None


@pytest.fixture
def runtime(tmp_path):
    """Construct a single-instance runtime with existing history in temporary storage."""
    return LocalRuntime(FakePlugin(tmp_path), root=tmp_path)


def test_event_capability_and_plugin_identity(runtime, tmp_path):
    """Only capability-bound events count as operator input, never copied JSON flags."""
    event = LocalEvent(runtime, "hello", capability=runtime._capability)
    assert is_local_event(event)
    assert is_local_event(event, runtime.plugin)
    assert not is_local_event(event, FakePlugin(tmp_path))
    assert not is_local_event(SimpleNamespace(_runtime=runtime, _capability=runtime._capability))
    with pytest.raises(ValueError, match="capability"):
        LocalEvent(runtime, "forged", capability=object())
    event._capability = object()
    assert not is_local_event(event)


def test_local_event_has_no_qq_identity(runtime):
    """Local sender, platform and IDs remain explicit non-QQ values."""
    event = LocalEvent(runtime, "literal prompt", capability=runtime._capability)
    assert event.get_sender_id() == "local-operator"
    assert event.get_sender_name() == "Local operator"
    assert event.get_group_id() == ""
    assert event.get_platform_name() == "local"
    assert event.get_self_id() == "local-bot"
    assert event.get_message_str() == "literal prompt"
    assert event.get_messages() == []
    assert len(event.message_obj.message_id) == 32
    assert event.message_obj.sender.user_id == "local-operator"


@pytest.mark.parametrize("scope,origin,valid", [
    (LOCAL_SCOPE, LOCAL_ORIGIN, True),
    ("private:operator", LOCAL_ORIGIN, False),
    (LOCAL_SCOPE, "local:LocalMessage:other", False),
    ("42", "napcat:GroupMessage:42", False),
    (LOCAL_SCOPE + "/../42", LOCAL_ORIGIN, False),
])
def test_fixed_local_route(scope, origin, valid):
    """A route is exact fixed data and is not an authentication shortcut."""
    assert local_route(scope, origin) is valid


@pytest.mark.parametrize("rpc_request", [
    {"op": "run", "prompt": "x", "scope": "42"},
    {"op": "run", "prompt": "x", "origin": LOCAL_ORIGIN},
    {"op": "run", "prompt": "x", "uid": 0},
    {"op": "run", "prompt": "x", "_capability": "owner"},
    {"op": "run", "prompt": ""}, {"op": "run", "prompt": 1},
    {"op": "history", "limit": True}, {"op": "history", "limit": 201},
    {"op": "history", "scope": "42"}, {"op": "health", "prompt": "x"},
    [], None, {"op": "unknown"},
])
def test_strict_schemas_reject_forged_fields(runtime, rpc_request):
    """JSON cannot choose a destination, spoof authority or inject event attributes."""
    assert asyncio.run(runtime._dispatch(rpc_request)) == {"ok": False, "error": "invalid_request"}
    assert runtime.plugin.calls == []


def test_run_uses_same_plugin_and_existing_history_once(runtime):
    """One request calls the real instance hook and captures one neutral local record."""
    result = asyncio.run(runtime._dispatch({"op": "run", "prompt": "hello"}))
    event, prompt = runtime.plugin.calls[0]
    assert event.plugin is runtime.plugin and prompt == "hello"
    assert result["result"] == {"text": "answer: hello"}
    assert len(result["outputs"]) == 1
    record = result["outputs"][0]
    assert record["transport"] == "local" and record["client_received"] is False
    assert record["status"] == "local_recorded"
    assert "message_id" not in record and "delivered" not in record
    history = runtime.plugin._hist().load_records(LOCAL_SCOPE)
    assert len(history) == 1
    assert history[0]["record"] == record


def test_history_cannot_export_other_scopes(runtime):
    """History RPC is limited to the operator outbox despite other existing archives."""
    runtime.plugin._hist().append("42", "private foreign content")
    runtime.plugin._hist().append(LOCAL_SCOPE, "local only")
    result = asyncio.run(runtime._dispatch({"op": "history", "limit": 1}))
    assert result == {"ok": True, "history": ["local only"]}


def test_output_components_and_recording_after_close(runtime):
    """Native media descriptors persist via Recorder, even during graceful drain."""
    chain = SimpleNamespace(chain=[SimpleNamespace(type="Plain", text="visible"),
                                   SimpleNamespace(type="Image", file="/tmp/output.png")])

    async def scenario():
        """Close intake without disabling persisted-task output."""
        await runtime.close()
        return await runtime.record_output(chain)

    result = asyncio.run(scenario())
    assert result == {"transport": "local", "status": "local_recorded",
                      "recorded": True, "client_received": False}
    records = runtime.plugin._hist().load_records(LOCAL_SCOPE)
    assert records[0]["record"]["components"][1] == {"type": "Image", "file": "/tmp/output.png"}


def test_unauthorized_peer_is_closed_before_read(runtime, monkeypatch):
    """A non-root peer cannot trigger even a single request read or history lookup."""
    monkeypatch.setattr(socket, "SO_PEERCRED", 17, raising=False)

    class Unreadable:
        """Explode if credentials were not checked before reading."""

        async def readline(self):
            """Reading an unauthorized client is forbidden."""
            raise AssertionError("read before authentication")

    writer = FakeWriter(uid=1000)
    asyncio.run(runtime._serve(Unreadable(), writer))
    assert writer.closed and not writer.data and not runtime.plugin.calls


@pytest.mark.parametrize("payload", [b"not json\n", b'{"op":"health"}',
                                      b"x" * (MAX_REQUEST_BYTES + 10) + b"\n"])
def test_invalid_or_oversized_protocol(runtime, monkeypatch, payload):
    """Malformed, unterminated and oversized lines fail before model execution."""
    monkeypatch.setattr(socket, "SO_PEERCRED", 17, raising=False)

    async def scenario():
        """Feed one bounded in-memory stream through the actual server handler."""
        reader = asyncio.StreamReader(limit=MAX_REQUEST_BYTES + 1)
        reader.feed_data(payload)
        reader.feed_eof()
        writer = FakeWriter()
        await runtime._serve(reader, writer)
        return writer

    writer = asyncio.run(scenario())
    assert json.loads(writer.data) == {"ok": False, "error": "invalid_request"}
    assert not runtime.plugin.calls


def test_close_preserves_accepted_work_and_disconnect(runtime, monkeypatch):
    """Client disappearance and closing intake never cancel an accepted operation."""
    monkeypatch.setattr(socket, "SO_PEERCRED", 17, raising=False)

    async def scenario():
        """Pause an accepted turn across close, then finish with a failed response write."""
        entered, release = asyncio.Event(), asyncio.Event()
        original = runtime.plugin._local_handle

        async def delayed(event, prompt):
            """Represent an in-flight model turn with explicit synchronization."""
            entered.set()
            await release.wait()
            return await original(event, prompt)

        class Disconnected(FakeWriter):
            """Simulate a client that disappears while its model is running."""

            def write(self, data):
                """A write error must happen only after the model completes."""
                raise ConnectionResetError("disconnected")

        runtime.plugin._local_handle = delayed
        reader = asyncio.StreamReader()
        reader.feed_data(b'{"op":"run","prompt":"finish me"}\n')
        runtime._accept(reader, Disconnected())
        await entered.wait()
        await runtime.close_acceptor()
        assert runtime._handlers and not runtime.plugin.calls
        release.set()
        await runtime.drain()
        assert runtime.plugin.calls[0][1] == "finish me"
        assert runtime.plugin._hist().load(LOCAL_SCOPE) == ["彼岸酱：answer: finish me"]

    asyncio.run(scenario())


def test_close_acceptor_does_not_wait_for_active_server_clients(runtime):
    """Python 3.13 wait_closed semantics must not turn intake closure into a drain."""
    class WaitingServer:
        """A server whose final closure depends on active-client completion."""

        def __init__(self):
            self.closed = False
            self.waited = False

        def close(self):
            """Close only the listener immediately."""
            self.closed = True

        async def wait_closed(self):
            """Record that full transport drain happens only in drain()."""
            self.waited = True

    async def scenario():
        """Separate immediate intake closure from later client teardown."""
        server = WaitingServer()
        runtime._server = server
        await runtime.close_acceptor()
        assert server.closed and not server.waited
        await runtime.drain()
        assert server.waited

    asyncio.run(scenario())


def test_output_rejects_event_from_another_runtime_of_same_plugin(runtime):
    """A borrowed capability cannot append another runtime's transient output list."""
    other = LocalRuntime(runtime.plugin, root=runtime.root)
    event = LocalEvent(other, "other", capability=other._capability)
    with pytest.raises(ValueError, match="local_event_invalid"):
        asyncio.run(runtime.record_output("no", event=event))
    assert runtime.plugin._hist().load(LOCAL_SCOPE) == []


LINUX_ROOT = (hasattr(os, "geteuid") and os.geteuid() == 0
              and hasattr(socket, "SO_PEERCRED"))


@pytest.mark.skipif(not LINUX_ROOT, reason="requires native Linux root Unix credentials")
def test_real_socket_permissions_health_and_cleanup(runtime):
    """Exercise actual SO_PEERCRED, 0700/0600 modes, JSON lines and owned cleanup."""
    async def scenario():
        """Use the native Unix transport, never a TCP or credential mock."""
        await runtime.start()
        assert stat.S_IMODE(runtime.socket_path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(runtime.socket_path.stat().st_mode) == 0o600
        reader, writer = await asyncio.open_unix_connection(str(runtime.socket_path))
        writer.write(b'{"op":"health"}\n')
        await writer.drain()
        response = json.loads(await reader.readline())
        writer.close()
        await writer.wait_closed()
        reader, writer = await asyncio.open_unix_connection(str(runtime.socket_path))
        writer.write(b'{"op":"run","prompt":"native root"}\n')
        await writer.drain()
        run_response = json.loads(await reader.readline())
        assert run_response["result"] == {"text": "answer: native root"}
        assert run_response["outputs"][0]["client_received"] is False
        writer.close()
        await writer.wait_closed()
        await runtime.stop()
        assert not runtime.socket_path.exists()
        return response

    assert asyncio.run(scenario()) == {"ok": True, "transport": "local"}


@pytest.mark.skipif(not LINUX_ROOT, reason="requires native Linux root Unix credentials")
def test_refuses_existing_file_and_insecure_directory(runtime):
    """Never overwrite unknown sockets/files or repair an unsafe directory silently."""
    directory = runtime.socket_path.parent
    directory.mkdir(mode=0o700)
    runtime.socket_path.write_text("retain", encoding="utf-8")
    with pytest.raises(OSError):
        asyncio.run(runtime.start())
    assert runtime.socket_path.read_text(encoding="utf-8") == "retain"
    directory.chmod(0o755)
    with pytest.raises(RuntimeError, match="directory_insecure"):
        asyncio.run(runtime.start())


def test_start_fails_closed_on_non_linux_or_non_root(runtime, monkeypatch):
    """Unsupported Unix hosts must not bypass root; Windows has a separate ACL gate."""
    monkeypatch.setattr("plugin.local_runtime._is_windows", lambda: False)
    monkeypatch.setattr(os, "geteuid", lambda: 1000, raising=False)
    with pytest.raises(RuntimeError, match="requires_linux_root"):
        asyncio.run(runtime.start())
    assert runtime._server is None
