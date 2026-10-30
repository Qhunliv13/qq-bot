"""Native Windows private loopback transport without model, QQ or admin startup."""
import asyncio
import json
import os
from pathlib import Path
import sys

import pytest

from plugin import local_runtime as transport
from tools import local_runtime_client as client


class FakePlugin:
    """Observe the exact existing capability path without creating a model loop."""

    def __init__(self):
        self.calls = []

    async def _local_handle(self, event, prompt):
        """Require the same native event authority as the shared Linux dispatcher."""
        assert transport.is_local_event(event, self)
        self.calls.append((event, prompt))
        return {"text": prompt}


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    """Use a real TCP listener while allowing non-Windows CI to mock only ACLs."""
    monkeypatch.setattr(transport, "_is_windows", lambda: True)
    monkeypatch.setattr(transport, "_windows_acl_is_private", lambda path: True)
    monkeypatch.setattr(transport, "_protect_new_windows_path", lambda path, **kwargs: Path(path))
    return transport.LocalRuntime(FakePlugin(), root=tmp_path)


async def rpc(runtime, value):
    """Exercise the actual bounded single-line loopback handler."""
    port = runtime._server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(json.dumps(value).encode() + b"\n")
    await writer.drain()
    result = json.loads(await reader.readline())
    writer.close()
    await writer.wait_closed()
    return result


def envelope(runtime, request):
    """Use only this boot's random transport capability."""
    return {"token": runtime._token, "request": request}


def test_windows_loopback_identity_and_existing_capability(runtime):
    """A private locator and exact identity handshake reach only the existing hook."""
    async def scenario():
        """Authenticate health and one turn, then remove only the owned locator."""
        await runtime.start()
        assert runtime._server.sockets[0].getsockname()[0] == "127.0.0.1"
        record = transport.read_windows_endpoint(runtime.socket_path)
        assert len(record["token"]) == len(record["nonce"]) == 64
        assert record["pid"] == os.getpid()
        health = await rpc(runtime, envelope(runtime, {"op": "health", "nonce": record["nonce"]}))
        assert health == {"ok": True, "transport": "local", "pid": os.getpid(),
                          "nonce": record["nonce"],
                          "instance_hash": transport.windows_instance_hash(runtime.root)}
        assert record["token"] not in json.dumps(health)
        assert str(runtime.root) not in json.dumps(health)
        result = await rpc(runtime, envelope(runtime, {"op": "run", "prompt": "native"}))
        assert result["ok"] and result["result"] == {"text": "native"}
        assert runtime.plugin.calls[0][0]._runtime is runtime
        await runtime.stop()
        assert not runtime.endpoint_path.exists()
    asyncio.run(scenario())


@pytest.mark.parametrize("value", [
    {"op": "run", "prompt": "forged"},
    {"token": "wrong", "request": {"op": "run", "prompt": "forged"}},
    {"token": 1, "request": {"op": "health"}},
    {"token": "\u2603", "request": {"op": "health"}},
])
def test_wrong_token_never_dispatches(runtime, monkeypatch, value):
    """Unauthenticated callers cannot trigger the dispatcher's health or model code."""
    async def forbidden(request):
        """Fail immediately if the authentication gate is bypassed."""
        raise AssertionError("unauthenticated dispatch")
    monkeypatch.setattr(runtime, "_dispatch", forbidden)
    async def scenario():
        """Send a forged envelope through the actual listener."""
        await runtime.start()
        assert await rpc(runtime, value) == {"ok": False, "error": "unauthorized"}
        await runtime.stop()
    asyncio.run(scenario())


def test_schema_nonce_and_oversize_rejected(runtime):
    """Capabilities never permit spoofed identity fields or unbounded request reads."""
    async def scenario():
        """Check strict dispatch and the existing byte bound on Windows."""
        await runtime.start()
        for request in ({"op": "health", "nonce": "wrong"},
                        {"op": "run", "prompt": "x", "scope": "admin"}):
            assert not (await rpc(runtime, envelope(runtime, request)))["ok"]
        result = await rpc(runtime, envelope(runtime, {"op": "run", "prompt": "x" * 17000}))
        assert result == {"ok": False, "error": "invalid_request"}
        assert not runtime.plugin.calls
        await runtime.stop()
    asyncio.run(scenario())


def test_acl_failure_stops_before_bind(runtime, monkeypatch):
    """An unsafe preexisting root is never repaired or exposed as a TCP endpoint."""
    monkeypatch.setattr(transport, "_windows_acl_is_private", lambda path: False)
    with pytest.raises(RuntimeError, match="insecure"):
        asyncio.run(runtime.start())
    assert runtime._server is None and not runtime.endpoint_path.exists()


def test_client_checks_acl_before_loading_token(tmp_path, monkeypatch):
    """A public operator locator is rejected before its credentials are parsed."""
    directory = tmp_path / "run"
    directory.mkdir()
    (directory / transport.WINDOWS_ENDPOINT).write_text("not JSON", encoding="utf-8")
    monkeypatch.setattr(transport, "_windows_acl_is_private", lambda path: False)
    with pytest.raises(RuntimeError, match="insecure"):
        transport.read_windows_endpoint(tmp_path)


def test_refuses_unknown_locator_and_keeps_replacement(runtime):
    """Neither startup nor shutdown overwrites another instance's locator."""
    async def scenario():
        """Exercise refusal and inode-based teardown ownership."""
        runtime.endpoint_path.parent.mkdir()
        runtime.endpoint_path.write_text("retain", encoding="utf-8")
        with pytest.raises(RuntimeError, match="endpoint_exists"):
            await runtime.start()
        assert runtime.endpoint_path.read_text(encoding="utf-8") == "retain"
        runtime.endpoint_path.unlink()
        await runtime.start()
        replacement = runtime.endpoint_path.with_name("replacement.json")
        replacement.write_text("replacement", encoding="utf-8")
        os.replace(replacement, runtime.endpoint_path)
        await runtime.stop()
        assert runtime.endpoint_path.read_text(encoding="utf-8") == "replacement"
    asyncio.run(scenario())


def test_client_identity_preflight_before_run(runtime, monkeypatch):
    """The client proves PID, boot nonce and instance hash before sending a turn."""
    monkeypatch.setattr(client.sys, "platform", "win32")
    async def scenario():
        """Use the synchronous client off-loop against the actual async endpoint."""
        await runtime.start()
        result = await asyncio.to_thread(client.request, runtime.root,
                                         {"op": "run", "prompt": "once"})
        assert result["ok"] and len(runtime.plugin.calls) == 1
        await runtime.stop()
    asyncio.run(scenario())


@pytest.mark.parametrize("field,value", [("pid", -1), ("nonce", "other"),
                                         ("instance_hash", "other")])
def test_client_mismatch_never_sends_prompt(monkeypatch, field, value):
    """Stale or substituted endpoints cannot receive a prompt before proof."""
    endpoint = {"token": "a" * 64, "nonce": "b" * 64, "port": 12345,
                "pid": 77, "instance": str(Path.cwd())}
    monkeypatch.setattr(client.sys, "platform", "win32")
    monkeypatch.setattr(client, "read_windows_endpoint", lambda path: endpoint)
    calls = []
    def exchange(address, payload, **kwargs):
        """Return incorrect identity and observe no subsequent run exchange."""
        calls.append(payload)
        health = {"ok": True, "transport": "local", "pid": 77, "nonce": "b" * 64,
                  "instance_hash": transport.windows_instance_hash(endpoint["instance"])}
        health[field] = value
        return health
    monkeypatch.setattr(client, "_exchange", exchange)
    with pytest.raises(ValueError, match="instance_mismatch"):
        client.request("ignored", {"op": "run", "prompt": "must not send"})
    assert len(calls) == 1 and calls[0]["request"]["op"] == "health"


def test_windows_configured_port(runtime):
    """Only the private port locator config controls the loopback binding."""
    runtime.endpoint_path.parent.mkdir()
    config = runtime.endpoint_path.with_name(transport.WINDOWS_CONFIG)
    config.write_text('{"port": 0}', encoding="utf-8")
    async def scenario():
        """Read an ephemeral configuration without any host/model settings."""
        await runtime.start()
        assert runtime._server.sockets[0].getsockname()[1] > 0
        await runtime.stop()
    asyncio.run(scenario())


def test_shutdown_drains_accepted_work_before_host_callback(runtime):
    """Native authenticated stop ACKs first and never cancels an accepted model turn."""
    async def scenario():
        """Hold a local turn across stop and release it before the host can exit."""
        entered, release, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original = runtime.plugin._local_handle
        async def delayed(event, prompt):
            """Keep a real accepted handler active until explicitly released."""
            entered.set()
            await release.wait()
            return await original(event, prompt)
        runtime.plugin._local_handle = delayed
        runtime.plugin._native_shutdown_requested = stopped.set
        await runtime.start()
        running = asyncio.create_task(rpc(runtime, envelope(runtime, {"op": "run", "prompt": "finish"})))
        await entered.wait()
        response = await rpc(runtime, envelope(runtime, {"op": "shutdown"}))
        assert response == {"ok": True, "transport": "local", "status": "stopping", "pid": os.getpid()}
        assert not stopped.is_set() and runtime._server is None
        release.set()
        assert (await running)["ok"]
        await runtime._shutdown_task
        assert stopped.is_set() and len(runtime.plugin.calls) == 1
    asyncio.run(scenario())


def test_shutdown_requires_native_host_callback(runtime):
    """Ordinary plugin hosts get no invented process-killing fallback."""
    async def scenario():
        """A missing host binding leaves the existing runtime running normally."""
        await runtime.start()
        assert await rpc(runtime, envelope(runtime, {"op": "shutdown"})) == {
            "ok": False, "error": "local_shutdown_unavailable"}
        assert runtime._server is not None
        await runtime.stop()
    asyncio.run(scenario())


def test_idle_and_unterminated_connections_are_bounded(runtime, monkeypatch):
    """The Windows authentication gate keeps the same two-second read deadline."""
    monkeypatch.setattr(transport, "READ_TIMEOUT", 0.01)
    async def scenario():
        """Leave an unauthenticated request incomplete and observe bounded teardown."""
        await runtime.start()
        port = runtime._server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b'{"token":')
        await writer.drain()
        assert json.loads(await asyncio.wait_for(reader.readline(), 1)) == {
            "ok": False, "error": "invalid_request"}
        writer.close()
        await writer.wait_closed()
        await runtime.stop()
        assert not runtime.plugin.calls
    asyncio.run(scenario())


def test_preexisting_directory_is_validated_not_repaired(tmp_path, monkeypatch):
    """Installation cannot silently bless a publicly accessible operator directory."""
    monkeypatch.setattr(transport, "_windows_acl_is_private", lambda path: False)
    def forbidden(*args, **kwargs):
        """Any attempt to rewrite an existing DACL violates the host contract."""
        raise AssertionError("must not repair preexisting path")
    monkeypatch.setattr(transport, "_protect_new_windows_path", forbidden)
    with pytest.raises(RuntimeError, match="insecure"):
        transport.ensure_private_windows_path(tmp_path)


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows DACL integration")
def test_native_temp_acl_and_secure_locator(tmp_path):
    """Exercise real user SID, icacls and Win32 DACL inspection only in a temp tree."""
    root = transport.ensure_private_windows_path(tmp_path / "operator")
    assert transport._windows_acl_is_private(root)
    runtime = transport.LocalRuntime(FakePlugin(), root=root)
    async def scenario():
        """Start native loopback and prove the full client contract without elevation."""
        await runtime.start()
        assert transport._windows_acl_is_private(runtime.endpoint_path)
        result = await asyncio.to_thread(client.request, runtime.socket_path, {"op": "health"})
        assert result["ok"] and result["pid"] == os.getpid()
        await runtime.stop()
    asyncio.run(scenario())
