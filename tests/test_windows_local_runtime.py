"""Owned endpoint publication/rollback using real temporary files and inert OS gates."""
import asyncio
import json
import os
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest

from plugin import local_runtime as transport


class InertServer:
    """Observe listener lifecycle without opening a socket or launching any host."""

    def __init__(self):
        self.sockets = [SimpleNamespace(getsockname=lambda: ("127.0.0.1", 12345))]
        self.closed = False
        self.waited = False
        self.served = False
        self.serving_error = None

    def close(self):
        """Close intake immediately, independently of the later drain."""
        self.closed = True

    async def wait_closed(self):
        """Record a completed drain without performing any network operation."""
        self.waited = True

    async def start_serving(self):
        """Publish only after complete JSON and descriptor closure succeeded."""
        if self.serving_error is not None:
            raise self.serving_error
        self.served = True


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    """Keep real owned file identities while mocking only ACL gates and TCP bind."""
    server = InertServer()
    (tmp_path / "run").mkdir()
    monkeypatch.setattr(transport, "_is_windows", lambda: True)
    monkeypatch.setattr(transport, "_windows_acl_is_private", lambda path: True)
    monkeypatch.setattr(transport, "_protect_new_windows_path", lambda path, **kwargs: Path(path))

    async def start_server(callback, **kwargs):
        """Refuse a changed transport contract and return an entirely inert listener."""
        assert callable(callback)
        assert kwargs == {"host": "127.0.0.1", "port": 0, "family": socket.AF_INET,
                          "limit": transport.MAX_REQUEST_BYTES + 1, "start_serving": False}
        return server

    monkeypatch.setattr(asyncio, "start_server", start_server)
    return transport.LocalRuntime(SimpleNamespace(), root=tmp_path), server


def inject_write_failure(monkeypatch, stage):
    """Fail one publication phase while preserving actual file/descriptor behavior."""
    if stage in {"protect", "cancel"}:
        def protect(path, *, directory):
            """Fail before credentials exist, including cancellation cleanup."""
            assert not directory and path.stat().st_size == 0
            if stage == "cancel":
                raise asyncio.CancelledError("injected_cancel")
            raise OSError("injected_protect")

        monkeypatch.setattr(transport, "_protect_new_windows_path", protect)
    elif stage == "acl_verify":
        def protect(path, *, directory):
            """Reject an unverified ACL while the new file still has no token bytes."""
            assert not directory and path.stat().st_size == 0
            monkeypatch.setattr(transport, "_windows_acl_is_private", lambda path: False)
            transport._require_private_windows_path(path)

        monkeypatch.setattr(transport, "_protect_new_windows_path", protect)
    elif stage == "json":
        def dump(value, stream, **kwargs):
            """Leave partial output in the real stream before a serialization error."""
            stream.write('{"token":')
            stream.flush()
            raise OSError("injected_json")

        monkeypatch.setattr(transport.json, "dump", dump)
    elif stage == "fdopen":
        def fdopen(*args, **kwargs):
            """Fail stream construction without consuming the created descriptor."""
            raise OSError("injected_fdopen")

        monkeypatch.setattr(transport.os, "fdopen", fdopen)
    elif stage == "stream_close":
        original_fdopen = os.fdopen

        class FailingClose:
            """Flush/close the real text stream, then report publication failure."""

            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                """Expose the actual stream so serialization is not simulated."""
                return self.stream.__enter__()

            def __exit__(self, *args):
                """Represent an error discovered only during final stream close."""
                self.stream.__exit__(*args)
                raise OSError("injected_stream_close")

        def fdopen(*args, **kwargs):
            """Wrap only the stream close while preserving closefd=False ownership."""
            assert kwargs["closefd"] is False
            return FailingClose(original_fdopen(*args, **kwargs))

        monkeypatch.setattr(transport.os, "fdopen", fdopen)
    elif stage in {"descriptor_close", "descriptor_close_before"}:
        original_close = os.close
        closed = []

        def close(descriptor):
            """Fail once before release, or after release, to exercise both OS states."""
            closed.append(descriptor)
            if stage == "descriptor_close_before" and len(closed) == 1:
                raise OSError("injected_descriptor_close_before")
            original_close(descriptor)
            if stage == "descriptor_close":
                raise OSError("injected_descriptor_close")

        monkeypatch.setattr(transport.os, "close", close)
    else:
        raise AssertionError("unknown failure stage")


@pytest.mark.parametrize("stage", ["protect", "acl_verify", "json", "fdopen", "stream_close",
                                   "descriptor_close", "descriptor_close_before", "cancel"])
def test_failed_first_endpoint_write_rolls_back_and_can_retry(runtime, monkeypatch, stage):
    """Every unpublished failure removes only our file and permits a clean retry."""
    local, server = runtime
    expected = (RuntimeError if stage == "acl_verify" else
                asyncio.CancelledError if stage == "cancel" else OSError)
    message = "path_insecure" if stage == "acl_verify" else "injected_" + stage

    async def scenario():
        """Exercise exact startup failure/cleanup without a host or network listener."""
        with monkeypatch.context() as failing:
            inject_write_failure(failing, stage)
            with pytest.raises(expected, match=message):
                await local.start()
        assert not local.endpoint_path.exists()
        assert local._server is None and local._endpoint_identity is None
        assert local._token is None
        assert server.closed and server.waited and not server.served
        # The previously broken first-write path must not poison the next boot.
        await local.start()
        assert local.endpoint_path.is_file() and server.served
        await local.stop()
        assert not local.endpoint_path.exists()

    asyncio.run(scenario())


def test_uncaptured_identity_never_falls_back_to_untrusted_path(runtime, monkeypatch):
    """If fstat itself fails, retain an empty file rather than claim a path identity."""
    local, _server = runtime
    descriptors = []
    original_close = os.close

    def fstat(descriptor):
        """Fail before identity capture, so no ACL or credential write is possible."""
        descriptors.append(descriptor)
        raise OSError("injected_fstat")

    def close(descriptor):
        """Confirm the creation handle is still released on identity-read failure."""
        assert descriptors == [descriptor]
        original_close(descriptor)

    monkeypatch.setattr(transport.os, "fstat", fstat)
    monkeypatch.setattr(transport.os, "close", close)
    with pytest.raises(OSError, match="injected_fstat"):
        transport._write_private_windows_json(local.endpoint_path, {"token": "fixture-only"})
    assert local.endpoint_path.read_bytes() == b""


def test_owned_cleanup_refuses_reparse_even_if_identity_matches(runtime, monkeypatch):
    """A reparse point cannot be removed just because mocked file IDs happen to match."""
    local, _server = runtime
    local.endpoint_path.write_text("foreign", encoding="utf-8")
    original_lstat = Path.lstat
    info = original_lstat(local.endpoint_path)

    def reparse_lstat(path, *args, **kwargs):
        """Simulate the Windows reparse attribute without changing real global ACLs."""
        current = original_lstat(path, *args, **kwargs)
        return SimpleNamespace(st_mode=current.st_mode, st_dev=current.st_dev,
                               st_ino=current.st_ino, st_file_attributes=0x400)

    monkeypatch.setattr(Path, "lstat", reparse_lstat)
    transport._unlink_owned_windows_json(local.endpoint_path, (info.st_dev, info.st_ino),
                                         require_private=False)
    assert local.endpoint_path.read_text(encoding="utf-8") == "foreign"


def test_writer_protects_before_secret_bytes_and_returns_handle_identity(runtime, monkeypatch):
    """The identity precedes ACL work, which precedes every credential-bearing byte."""
    local, _server = runtime
    identities = []
    original_fstat = os.fstat

    def fstat(descriptor):
        """Observe the actual Win32 file ID rather than synthesizing a path identity."""
        info = original_fstat(descriptor)
        identities.append((info.st_dev, info.st_ino))
        return info

    def protect(path, *, directory):
        """Verify a live identity exists while the file is still entirely empty."""
        assert identities and not directory and path.stat().st_size == 0
        return path

    monkeypatch.setattr(transport.os, "fstat", fstat)
    monkeypatch.setattr(transport, "_protect_new_windows_path", protect)
    value = {"token": "fixture-only", "label": "临时凭据"}
    identity = transport._write_private_windows_json(local.endpoint_path, value)
    info = local.endpoint_path.lstat()
    assert identity == identities[0] == (info.st_dev, info.st_ino)
    assert json.loads(local.endpoint_path.read_text(encoding="utf-8")) == value
    assert local.endpoint_path.read_bytes().endswith(b"\n")


def test_exclusive_writer_never_touches_existing_file(runtime, monkeypatch):
    """An O_EXCL collision must neither protect nor remove someone else's locator."""
    local, _server = runtime
    local.endpoint_path.write_text("foreign", encoding="utf-8")

    def forbidden(*args, **kwargs):
        """Any ACL or removal action after failed creation violates ownership."""
        raise AssertionError("foreign path touched")

    monkeypatch.setattr(transport, "_protect_new_windows_path", forbidden)
    monkeypatch.setattr(transport, "_unlink_owned_windows_json", forbidden)
    with pytest.raises(FileExistsError):
        transport._write_private_windows_json(local.endpoint_path, {"token": "fixture-only"})
    assert local.endpoint_path.read_text(encoding="utf-8") == "foreign"


@pytest.mark.parametrize("stage", ["protect", "json", "descriptor_close"])
def test_failed_write_keeps_foreign_replacement_after_handle_close(runtime, monkeypatch, stage):
    """Even first-write rollback cannot unlink a foreign file installed at close."""
    local, _server = runtime
    replacement = local.endpoint_path.with_name("foreign.json")
    replacement.write_text("foreign", encoding="utf-8")
    original_close = os.close

    def replace_on_close(descriptor):
        """Replace only after the live handle closes, as Windows sharing requires."""
        original_close(descriptor)
        os.replace(replacement, local.endpoint_path)
        if stage == "descriptor_close":
            raise OSError("injected_descriptor_close")

    with monkeypatch.context() as failing:
        if stage != "descriptor_close":
            inject_write_failure(failing, stage)
        failing.setattr(transport.os, "close", replace_on_close)
        with pytest.raises(OSError, match="injected_" + stage):
            transport._write_private_windows_json(local.endpoint_path, {"token": "fixture-only"})
    assert local.endpoint_path.read_text(encoding="utf-8") == "foreign"


def test_replacement_during_protection_cannot_receive_secret_bytes(runtime, monkeypatch):
    """The post-ACL identity recheck rejects a swapped path before serialization."""
    local, _server = runtime
    original_lstat = Path.lstat
    inspected = []

    def changed_lstat(path, *args, **kwargs):
        """Simulate an external path swap without closing our creation handle."""
        info = original_lstat(path, *args, **kwargs)
        if path == local.endpoint_path:
            inspected.append(info.st_size)
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0,
                                   st_dev=info.st_dev, st_ino=info.st_ino + 1)
        return info

    monkeypatch.setattr(Path, "lstat", changed_lstat)
    with pytest.raises(RuntimeError, match="endpoint_changed"):
        transport._write_private_windows_json(local.endpoint_path, {"token": "fixture-only"})
    # An unprovable identity must be retained rather than loosely unlinked.
    assert inspected == [0, 0]
    assert local.endpoint_path.read_bytes() == b""


def test_serving_failure_rolls_back_completed_owned_endpoint(runtime):
    """A failed listener activation still removes the successfully written locator."""
    local, server = runtime
    server.serving_error = OSError("injected_serving")
    with pytest.raises(OSError, match="injected_serving"):
        asyncio.run(local.start())
    assert local._server is None and server.closed and server.waited
    assert local._token is None and not local.endpoint_path.exists()


@pytest.mark.parametrize("fail_serving", [False, True])
def test_runtime_never_adopts_replacement_after_successful_write(runtime, monkeypatch, fail_serving):
    """Startup and shutdown retain the original handle identity, not a late lstat."""
    local, server = runtime
    replacement = local.endpoint_path.with_name("foreign.json")
    replacement.write_text("foreign", encoding="utf-8")
    foreign = replacement.lstat()
    original_close = os.close

    def replace_on_close(descriptor):
        """Install a foreign locator exactly as writing completes, after handle close."""
        original_close(descriptor)
        os.replace(replacement, local.endpoint_path)

    async def scenario():
        """Retain foreign contents across both activation failure and normal teardown."""
        if fail_serving:
            server.serving_error = OSError("injected_serving")
        with monkeypatch.context() as swapping:
            swapping.setattr(transport.os, "close", replace_on_close)
            if fail_serving:
                with pytest.raises(OSError, match="injected_serving"):
                    await local.start()
            else:
                await local.start()
        assert local._endpoint_identity != (foreign.st_dev, foreign.st_ino)
        await local.stop()
        assert local.endpoint_path.read_text(encoding="utf-8") == "foreign"

    asyncio.run(scenario())


def test_owned_endpoint_success_lifecycle_is_idempotent_and_drains(runtime):
    """Successful publication survives repeat start and separates intake from drain."""
    local, server = runtime

    async def scenario():
        """Reuse the existing close_acceptor/drain/stop API, not a second shutdown."""
        assert await local.start() is local
        identity = local._endpoint_identity
        contents = local.endpoint_path.read_bytes()
        assert await local.start() is local
        assert local._endpoint_identity == identity
        assert local.endpoint_path.read_bytes() == contents
        assert transport.read_windows_endpoint(local.endpoint_path)["token"] == local._token
        entered, release = asyncio.Event(), asyncio.Event()

        async def accepted_work():
            """Keep an accepted operation alive across intake closure until released."""
            entered.set()
            await release.wait()

        task = asyncio.create_task(accepted_work())
        local._handlers.add(task)
        task.add_done_callback(local._handlers.discard)
        await entered.wait()
        await local.close_acceptor()
        await local.close_acceptor()
        assert not local.endpoint_path.exists()
        assert server.closed and not server.waited and not task.done()
        release.set()
        await local.drain()
        await local.stop()
        assert server.waited and task.done() and not task.cancelled()
        assert not local._handlers and not local._closed_servers

    asyncio.run(scenario())
