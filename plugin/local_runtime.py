"""Authenticated local transport for the existing, single HiganPlugin instance.

Linux retains root SO_PEERCRED authentication before reading any request bytes.
Windows uses a private owner-only locator and a random loopback capability; neither
transport owns a model, memory engine, database or second QQ identity.
"""
from __future__ import annotations

import asyncio
import csv
import ctypes
from ctypes import wintypes
from functools import lru_cache
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import struct
import subprocess
import sys
from types import SimpleNamespace
import uuid

# Fixed routes cannot be selected by RPC callers or mistaken for QQ destinations.
LOCAL_SCOPE = "local:operator"
LOCAL_ORIGIN = "local:LocalMessage:operator"
# A bounded single JSON line prevents idle/oversized clients consuming memory.
MAX_REQUEST_BYTES = 16 * 1024
READ_TIMEOUT = 2.0
# Windows discovery is separate from the unmodified Linux socket contract.
WINDOWS_ENDPOINT = "local-endpoint.json"
WINDOWS_CONFIG = "native-local.json"


def _is_windows():
    """Select native Windows without relying on Unix APIs being absent."""
    return sys.platform == "win32" or os.name == "nt"


@lru_cache(maxsize=1)
def _windows_sid():
    """Read the current token's SID via the OS utility, never a caller-supplied name."""
    executable = Path(os.environ["SystemRoot"]) / "System32" / "whoami.exe"
    result = subprocess.run([str(executable), "/user", "/fo", "csv", "/nh"],
                            capture_output=True, text=True, check=True,
                            timeout=READ_TIMEOUT, creationflags=0x08000000)
    rows = list(csv.reader(result.stdout.splitlines()))
    if len(rows) != 1 or len(rows[0]) != 2 or not re.fullmatch(r"S-1-\d+(?:-\d+)+", rows[0][1]):
        raise RuntimeError("local_windows_identity_invalid")
    return rows[0][1]


def _windows_acl_is_private(path):
    """Require an owned protected DACL containing only full-control current-SID ACEs.

    Inspect native descriptors rather than localized icacls output; NULL DACLs,
    inherited grants, reparse points and all other principals fail closed.
    """
    info = path.lstat()
    if getattr(info, "st_file_attributes", 0) & 0x400:
        return False
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.c_void_p
    advapi.GetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, wintypes.DWORD,
                                           wintypes.DWORD, ctypes.POINTER(pointer),
                                           pointer, ctypes.POINTER(pointer), pointer,
                                           ctypes.POINTER(pointer)]
    advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi.GetSecurityDescriptorControl.argtypes = [pointer, ctypes.POINTER(wintypes.WORD),
                                                  ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertSidToStringSidW.argtypes = [pointer, ctypes.POINTER(pointer)]
    advapi.GetAclInformation.argtypes = [pointer, pointer, wintypes.DWORD, wintypes.DWORD]
    advapi.GetAce.argtypes = [pointer, wintypes.DWORD, ctypes.POINTER(pointer)]
    kernel.LocalFree.argtypes = [pointer]
    kernel.LocalFree.restype = pointer
    owner, dacl, descriptor = pointer(), pointer(), pointer()
    result = advapi.GetNamedSecurityInfoW(str(path), 1, 5, ctypes.byref(owner), None,
                                        ctypes.byref(dacl), None, ctypes.byref(descriptor))
    if result:
        raise OSError("local_windows_acl_unavailable")

    def sid_text(value):
        """Convert native SID memory, releasing the OS allocation on every exit."""
        text = pointer()
        if not advapi.ConvertSidToStringSidW(value, ctypes.byref(text)):
            raise OSError("local_windows_identity_invalid")
        try:
            return ctypes.wstring_at(text)
        finally:
            kernel.LocalFree(text)

    try:
        control, revision = wintypes.WORD(), wintypes.DWORD()
        if (not advapi.GetSecurityDescriptorControl(descriptor, ctypes.byref(control),
                                                    ctypes.byref(revision))
                or not control.value & 0x1000 or not dacl.value
                or sid_text(owner) != _windows_sid()):
            return False
        # ACL_SIZE_INFORMATION is three DWORDs: count, bytes in use, bytes free.
        sizes = (wintypes.DWORD * 3)()
        if not advapi.GetAclInformation(dacl, sizes, ctypes.sizeof(sizes), 2) or not sizes[0]:
            return False
        for index in range(sizes[0]):
            ace = pointer()
            if not advapi.GetAce(dacl, index, ctypes.byref(ace)):
                return False
            header = ctypes.string_at(ace, 8)
            kind, flags, length, mask = struct.unpack("<BBHI", header)
            # Only ordinary current-user allow ACEs, never inherited/object/callback ACEs.
            if (kind != 0 or flags & 0x10 or flags & ~0x03 or length < 20
                    or mask & 0x1F01FF != 0x1F01FF
                    or sid_text(pointer(ace.value + 8)) != _windows_sid()):
                return False
        return True
    finally:
        kernel.LocalFree(descriptor)


def _require_private_windows_path(path, *, directory=False):
    """Validate ownership and DACL before reading configuration or any credential."""
    path = Path(path).absolute()
    mode = path.lstat().st_mode
    if (not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode))
            or not _windows_acl_is_private(path)):
        raise RuntimeError("local_windows_path_insecure")
    return path


def _protect_new_windows_path(path, *, directory):
    """Set owner-only protection only on a newly created runtime path, then verify."""
    executable = Path(os.environ["SystemRoot"]) / "System32" / "icacls.exe"
    grant = "*" + _windows_sid() + (":(OI)(CI)F" if directory else ":F")
    subprocess.run([str(executable), str(path), "/inheritance:r", "/grant:r", grant],
                   capture_output=True, check=True, timeout=READ_TIMEOUT,
                   creationflags=0x08000000)
    return _require_private_windows_path(path, directory=directory)


def ensure_private_windows_path(path, *, create=True):
    """Create a private operator directory; refuse to repair preexisting unsafe paths.

    Host installation may reuse this stdlib-only helper before writing its secrets.
    The parent must exist: never recursively ACL an installation or account tree.
    """
    path = Path(path).absolute()
    if create:
        try:
            # Windows Python 3.13's mode=0700 adds explicit Administrators/SYSTEM
            # grants. Use inheritance initially, then remove it before any secrets.
            path.mkdir()
        except FileExistsError:
            pass
        else:
            return _protect_new_windows_path(path, directory=True)
    return _require_private_windows_path(path, directory=True)


def _unlink_owned_windows_json(path, identity, *, require_private=True):
    """Remove only the captured regular file, including a failed unpublished write.

    ACL failure can leave an empty, not-yet-private file, so rollback may skip its
    DACL check but never its handle-derived identity or reparse-point checks.
    """
    if identity is None:
        return
    try:
        if require_private:
            _require_private_windows_path(path)
        # Recheck after ACL inspection: a replacement must never inherit ownership.
        info = path.lstat()
        if (stat.S_ISREG(info.st_mode)
                and not getattr(info, "st_file_attributes", 0) & 0x400
                and (info.st_dev, info.st_ino) == identity):
            path.unlink()
    except (OSError, RuntimeError):
        # Missing, inaccessible or foreign paths remain untouched on every retry.
        return


def _write_private_windows_json(path, value):
    """Write exclusively after private ACL verification; roll back only our own file.

    Capture identity from the live descriptor before protection or serialization.
    A stream never owns that descriptor, so even flush/close failure retains the
    identity needed for rollback; success returns it without adopting a new path.
    """
    path = Path(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    identity = None
    try:
        try:
            info = os.fstat(descriptor)
            identity = (info.st_dev, info.st_ino)
            _protect_new_windows_path(path, directory=False)
            current = path.lstat()
            if (not stat.S_ISREG(current.st_mode)
                    or getattr(current, "st_file_attributes", 0) & 0x400
                    or (current.st_dev, current.st_ino) != identity):
                raise RuntimeError("local_windows_endpoint_changed")
            with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as stream:
                json.dump(value, stream, ensure_ascii=False, allow_nan=False)
                stream.write("\n")
        finally:
            # Keep the descriptor through stream close. A close error can mean
            # either released or still-open: never blindly retry a recycled fd.
            try:
                os.close(descriptor)
            except OSError:
                try:
                    current = os.fstat(descriptor)
                    if identity is not None and (current.st_dev, current.st_ino) == identity:
                        os.close(descriptor)
                except OSError:
                    # Retry only once and only for the same still-open file.
                    pass
                raise
    except BaseException:
        # Cancellation also rolls back; no failure may be mistaken for publication.
        _unlink_owned_windows_json(path, identity, require_private=False)
        raise
    return identity


def windows_instance_hash(root):
    """Bind health to the exact normalized instance directory without exposing paths."""
    return hashlib.sha256(os.path.normcase(str(Path(root).resolve())).encode("utf-8")).hexdigest()


def read_windows_endpoint(path):
    """Resolve a root/socket/locator, checking private ACLs before reading its token."""
    path = Path(path).absolute()
    if path.name == "local.sock":
        path = path.with_name(WINDOWS_ENDPOINT)
    elif path.name != WINDOWS_ENDPOINT:
        path = path / "run" / WINDOWS_ENDPOINT
    _require_private_windows_path(path.parent.parent, directory=True)
    _require_private_windows_path(path.parent, directory=True)
    _require_private_windows_path(path)
    with path.open(encoding="utf-8") as stream:
        raw = stream.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("local_endpoint_invalid")
    record = json.loads(raw)
    expected = {"version", "transport", "host", "port", "pid", "nonce", "instance", "token"}
    if (not isinstance(record, dict) or set(record) != expected or record["version"] != 1
            or record["transport"] != "tcp" or record["host"] != "127.0.0.1"
            or type(record["port"]) is not int or not 1 <= record["port"] <= 65535
            or type(record["pid"]) is not int or record["pid"] < 1
            or not isinstance(record["instance"], str)
            or windows_instance_hash(record["instance"]) != windows_instance_hash(path.parent.parent)
            or not isinstance(record["nonce"], str) or not re.fullmatch(r"[0-9a-f]{64}", record["nonce"])
            or not isinstance(record["token"], str) or not re.fullmatch(r"[0-9a-f]{64}", record["token"])):
        raise ValueError("local_endpoint_invalid")
    return record


def local_route(scope, origin):
    """Recognize only the fixed local destination, without granting human authority."""
    return (type(scope) is str and type(origin) is str
            and scope == LOCAL_SCOPE and origin == LOCAL_ORIGIN)


def is_local_event(event, plugin=None):
    """Validate an internal capability and optionally its exact plugin instance."""
    if not isinstance(event, LocalEvent):
        return False
    runtime = getattr(event, "_runtime", None)
    return (isinstance(runtime, LocalRuntime)
            and getattr(event, "_capability", None) is runtime._capability
            and (plugin is None or runtime.plugin is plugin))


def _components(chain):
    """Describe native output components without invoking arbitrary object serializers."""
    items = getattr(chain, "chain", chain)
    if isinstance(items, str):
        return [{"type": "Plain", "text": items}]
    if not isinstance(items, (list, tuple)):
        raise ValueError("local_output_invalid")
    result = []
    for item in items:
        kind = getattr(item, "type", type(item).__name__)
        kind = getattr(kind, "value", kind)
        component = {"type": kind if isinstance(kind, str) else type(item).__name__}
        # Only known public scalar fields survive; repr/__dict__ may leak secrets.
        for key in ("text", "file", "url", "id"):
            value = getattr(item, key, None)
            if isinstance(value, (str, int, float, bool)):
                component[key] = value
        result.append(component)
    return result


class LocalEvent:
    """Internal authenticated operator event; request JSON never constructs this class."""

    def __init__(self, runtime, prompt, *, capability):
        if capability is not runtime._capability:
            raise ValueError("local_capability_invalid")
        self._runtime = runtime
        self._capability = capability
        self.plugin = runtime.plugin
        self.scope = LOCAL_SCOPE
        self.origin = LOCAL_ORIGIN
        self.unified_msg_origin = LOCAL_ORIGIN
        self.message_str = prompt
        self.outputs = []
        self.message_obj = SimpleNamespace(
            message_id=uuid.uuid4().hex, message=[], raw_message=prompt,
            sender=SimpleNamespace(user_id="local-operator", nickname="Local operator"))
        # Reuse the existing optional QQ bridge, never a new transport/tool engine.
        from .task_event import _TaskAPI
        self.bot = SimpleNamespace(api=_TaskAPI(runtime.plugin))

    def get_sender_id(self):
        """Return a non-QQ operator identifier, not a fabricated numeric owner ID."""
        return "local-operator"

    def get_sender_name(self):
        """Label the authenticated local transport explicitly."""
        return "Local operator"

    def get_group_id(self):
        """Local input has no QQ group membership."""
        return ""

    def get_platform_name(self):
        """Keep platform-specific policy separate from QQ."""
        return "local"

    def get_self_id(self):
        """Do not impersonate an attached QQ account."""
        return "local-bot"

    def get_message_str(self):
        """Expose only the validated operator prompt."""
        return self.message_str

    def get_messages(self):
        """Local RPC has no forged mentions or native attachments."""
        return []

    async def send(self, chain):
        """Record local output without claiming remote delivery or QQ receipt IDs."""
        return await self._runtime.record_output(chain, event=self)


class LocalRuntime:
    """Serve one bounded authenticated request through the existing plugin instance."""

    def __init__(self, plugin, *, root):
        self.plugin = plugin
        self.root = Path(root)
        self.socket_path = self.root / "run" / "local.sock"
        self._capability = object()
        self._server = None
        self._handlers = set()
        self._closed_servers = []
        self._closing = False
        self._socket_identity = None
        self._windows = False
        self._token = None
        self._nonce = None
        self._endpoint_identity = None
        self._shutdown_task = None
        self.endpoint_path = self.root / "run" / WINDOWS_ENDPOINT

    async def start(self):
        """Select authenticated native transport; retain Linux ownership checks intact."""
        if self._server is not None:
            return self
        if _is_windows():
            return await self._start_windows()
        if (not hasattr(os, "geteuid") or os.geteuid() != 0
                or not hasattr(socket, "SO_PEERCRED")):
            raise RuntimeError("local_runtime_requires_linux_root")
        root_stat = self.root.lstat()
        if (not stat.S_ISDIR(root_stat.st_mode) or root_stat.st_uid != 0
                or stat.S_IMODE(root_stat.st_mode) & 0o022):
            raise RuntimeError("local_root_insecure")
        directory = self.socket_path.parent
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            # Never chmod or follow a preexisting symlink/permissive directory.
            pass
        directory_stat = directory.lstat()
        if (not stat.S_ISDIR(directory_stat.st_mode) or directory_stat.st_uid != 0
                or stat.S_IMODE(directory_stat.st_mode) != 0o700):
            raise RuntimeError("local_socket_directory_insecure")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        bound = False
        try:
            # bind refuses existing files/sockets, including symlinks and stale paths.
            sock.bind(str(self.socket_path))
            bound = True
            info = self.socket_path.lstat()
            self._socket_identity = (info.st_dev, info.st_ino)
            os.chmod(self.socket_path, 0o600)
            sock.setblocking(False)
            self._closing = False
            # Python 3.13's automatic cleanup would defeat our inode ownership check.
            options = {"cleanup_socket": False} if sys.version_info >= (3, 13) else {}
            self._server = await asyncio.start_unix_server(
                self._accept, sock=sock, limit=MAX_REQUEST_BYTES + 1, **options)
        except BaseException:
            # Cleanup also applies to cancellation, but never suppress cancellation.
            sock.close()
            if bound:
                self._unlink_owned_socket()
            raise
        return self

    async def _start_windows(self):
        """Bind IPv4 loopback only after validating the operator's private data root."""
        self._windows = True
        ensure_private_windows_path(self.root, create=False)
        ensure_private_windows_path(self.endpoint_path.parent)
        port = 0  # Ephemeral avoids collisions; optional private config pins a port.
        config = self.endpoint_path.with_name(WINDOWS_CONFIG)
        if config.exists():
            _require_private_windows_path(config)
            with config.open(encoding="utf-8") as stream:
                raw = stream.read(MAX_REQUEST_BYTES + 1)
            value = json.loads(raw)
            if (len(raw) > MAX_REQUEST_BYTES or not isinstance(value, dict)
                    or set(value) != {"port"} or type(value["port"]) is not int
                    or not 0 <= value["port"] <= 65535):
                raise ValueError("local_windows_config_invalid")
            port = value["port"]
        # Never replace a locator we did not create, including stale instances.
        if self.endpoint_path.exists() or self.endpoint_path.is_symlink():
            raise RuntimeError("local_windows_endpoint_exists")
        self._token, self._nonce = secrets.token_hex(32), secrets.token_hex(32)
        self._closing = False
        try:
            self._server = await asyncio.start_server(
                self._accept, host="127.0.0.1", port=port, family=socket.AF_INET,
                limit=MAX_REQUEST_BYTES + 1, start_serving=False)
            record = {"version": 1, "transport": "tcp", "host": "127.0.0.1",
                      "port": self._server.sockets[0].getsockname()[1], "pid": os.getpid(),
                      "nonce": self._nonce, "instance": str(self.root.resolve()),
                      "token": self._token}
            # The writer owns rollback until success and returns the original
            # descriptor's identity; never adopt a replacement via a fresh lstat.
            self._endpoint_identity = _write_private_windows_json(self.endpoint_path, record)
            await self._server.start_serving()
        except BaseException:
            if self._server is not None:
                self._server.close()
                await self._server.wait_closed()
                self._server = None
            self._unlink_owned_endpoint()
            self._token = None
            raise
        return self

    def _accept(self, reader, writer):
        """Own accepted tasks independently of clients, preserving work on disconnect."""
        task = asyncio.create_task(self._serve(reader, writer))
        self._handlers.add(task)
        task.add_done_callback(self._handlers.discard)

    def _root_peer(self, writer):
        """Read native kernel credentials before any bytes or JSON are processed."""
        sock = writer.get_extra_info("socket")
        if sock is None:
            return False
        try:
            credentials = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                          struct.calcsize("3i"))
            _pid, uid, _gid = struct.unpack("3i", credentials)
            return uid == 0
        except (AttributeError, OSError, struct.error):
            return False

    async def _serve(self, reader, writer):
        """Authorize, validate and execute once; never cancel a model on write failure."""
        try:
            if self._windows:
                peer = writer.get_extra_info("peername")
                if not peer or peer[0] != "127.0.0.1":
                    return
            elif not self._root_peer(writer):
                return
            try:
                line = await asyncio.wait_for(reader.readline(), READ_TIMEOUT)
                if not line.endswith(b"\n") or len(line) > MAX_REQUEST_BYTES:
                    response = {"ok": False, "error": "invalid_request"}
                elif self._closing:
                    response = {"ok": False, "error": "local_runtime_closing"}
                else:
                    request = json.loads(line)
                    if self._windows:
                        # Token checks precede dispatch; JSON flags cannot grant capability.
                        token = request.get("token") if isinstance(request, dict) else None
                        if (not isinstance(request, dict) or set(request) != {"token", "request"}
                                or not isinstance(token, str) or not self._token
                                or not hmac.compare_digest(token.encode("utf-8"),
                                                           self._token.encode("ascii"))):
                            response = {"ok": False, "error": "unauthorized"}
                        else:
                            response = await self._dispatch(request["request"])
                    else:
                        response = await self._dispatch(request)
            except (ValueError, UnicodeError, asyncio.TimeoutError):
                response = {"ok": False, "error": "invalid_request"}
            except Exception:
                # Do not expose raw prompts, credentials, paths or upstream errors.
                response = {"ok": False, "error": "local_request_failed"}
            payload = json.dumps(response, ensure_ascii=False, allow_nan=False).encode() + b"\n"
            writer.write(payload)
            await writer.drain()
        except (ConnectionError, OSError, ValueError, TypeError):
            # Work already ran to completion; a disconnected client is not delivery.
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def _dispatch(self, request):
        """Accept only explicit operation schemas, with no caller-selected identity/scope."""
        if not isinstance(request, dict):
            return {"ok": False, "error": "invalid_request"}
        op = request.get("op")
        if self._windows and op == "shutdown" and set(request) == {"op"}:
            # Only the native supervisor may install lifecycle control; no shell,
            # process ID or callback can ever be selected by the RPC caller.
            callback = getattr(self.plugin, "_native_shutdown_requested", None)
            if not callable(callback):
                return {"ok": False, "error": "local_shutdown_unavailable"}
            if self._shutdown_task is None:
                await self.close_acceptor()
                self._shutdown_task = asyncio.create_task(self._finish_native_shutdown(callback))
            return {"ok": True, "transport": "local", "status": "stopping", "pid": os.getpid()}
        if self._windows and op == "health" and set(request) <= {"op", "nonce"}:
            # Health never echoes paths or credentials; the challenge binds exact boot.
            if request.get("nonce", self._nonce) != self._nonce:
                return {"ok": False, "error": "local_instance_mismatch"}
            return {"ok": True, "transport": "local", "pid": os.getpid(),
                    "nonce": self._nonce, "instance_hash": windows_instance_hash(self.root)}
        if op == "health" and set(request) == {"op"}:
            return {"ok": True, "transport": "local"}
        if op == "history" and set(request) <= {"op", "limit"}:
            limit = request.get("limit", 50)
            if type(limit) is not int or not 1 <= limit <= 200:
                return {"ok": False, "error": "invalid_request"}
            return {"ok": True, "history": self.plugin._hist().load(LOCAL_SCOPE, limit=limit)}
        if (op == "run" and set(request) == {"op", "prompt"}
                and isinstance(request["prompt"], str) and request["prompt"].strip()):
            event = LocalEvent(self, request["prompt"], capability=self._capability)
            result = await self.plugin._local_handle(event, request["prompt"])
            return {"ok": True, "result": result, "outputs": event.outputs}
        return {"ok": False, "error": "invalid_request"}

    async def _finish_native_shutdown(self, callback):
        """Drain every accepted local turn before handing lifecycle back to its host.

        This task deliberately stays outside _handlers: shutdown's own handler
        writes/closes its acknowledgement first, then finishes the same drain.
        Host-side lifecycle control owns QQ/scheduler teardown; this is no engine.
        """
        await self.drain()
        callback()

    async def record_output(self, chain, *, event=None):
        """Persist once through existing history, retaining only per-turn transient capture."""
        if event is not None and (not is_local_event(event, self.plugin)
                                  or event._runtime is not self):
            raise ValueError("local_event_invalid")
        components = _components(chain)
        record = {"id": uuid.uuid4().hex, "transport": "local", "scope": LOCAL_SCOPE,
                  "origin": LOCAL_ORIGIN, "components": components,
                  "status": "local_recorded", "client_received": False}
        text = "".join(item.get("text", "") for item in components
                       if isinstance(item.get("text", ""), str))
        # Match the shared assistant transcript convention across process reloads.
        self.plugin._hist().append(LOCAL_SCOPE, "彼岸酱：" + (text or "[local output]"), record=record)
        if event is not None:
            event.outputs.append(record)
        return {"transport": "local", "status": "local_recorded", "recorded": True,
                "client_received": False}

    def _unlink_owned_socket(self):
        """Remove only our exact still-owned socket inode, never replacement files."""
        try:
            info = self.socket_path.lstat()
        except FileNotFoundError:
            return
        if (stat.S_ISSOCK(info.st_mode) and info.st_uid == 0
                and (info.st_dev, info.st_ino) == self._socket_identity):
            self.socket_path.unlink()

    def _unlink_owned_endpoint(self):
        """Delete only this boot's still-private locator, never another instance's file."""
        _unlink_owned_windows_json(self.endpoint_path, self._endpoint_identity)

    async def close_acceptor(self):
        """Reject new work but preserve accepted model operations and their output sink."""
        self._closing = True
        if self._server is not None:
            self._server.close()
            # New Python versions wait for active clients in wait_closed(); intake
            # closure must return immediately so callers can release model gates.
            self._closed_servers.append(self._server)
            self._server = None
        if self._windows:
            self._unlink_owned_endpoint()
        else:
            self._unlink_owned_socket()

    async def close(self):
        """Alias for closing intake only; active operations must not be force-cancelled."""
        await self.close_acceptor()

    async def drain(self):
        """Wait for accepted work without applying a model-generation timeout."""
        tasks = tuple(self._handlers)
        if tasks:
            await asyncio.shield(asyncio.gather(*tasks))
        for server in self._closed_servers:
            await server.wait_closed()
        self._closed_servers.clear()

    async def stop(self):
        """Stop intake then gracefully finish every accepted operation."""
        await self.close_acceptor()
        await self.drain()
