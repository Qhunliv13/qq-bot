"""Root-only local transport for the existing, single HiganPlugin instance.

This module owns no model, memory engine, database or QQ identity. Authentication
comes exclusively from Linux SO_PEERCRED before reading any request bytes.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import socket
import stat
import struct
import sys
from types import SimpleNamespace
import uuid

# Fixed routes cannot be selected by RPC callers or mistaken for QQ destinations.
LOCAL_SCOPE = "local:operator"
LOCAL_ORIGIN = "local:LocalMessage:operator"
# A bounded single JSON line prevents idle/oversized clients consuming memory.
MAX_REQUEST_BYTES = 16 * 1024
READ_TIMEOUT = 2.0


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
    """Serve one bounded root-only JSON request per Unix connection."""

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

    async def start(self):
        """Bind only an owned private Linux Unix socket; never unlink unknown paths."""
        if self._server is not None:
            return self
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
            if not self._root_peer(writer):
                return
            try:
                line = await asyncio.wait_for(reader.readline(), READ_TIMEOUT)
                if not line.endswith(b"\n") or len(line) > MAX_REQUEST_BYTES:
                    response = {"ok": False, "error": "invalid_request"}
                elif self._closing:
                    response = {"ok": False, "error": "local_runtime_closing"}
                else:
                    response = await self._dispatch(json.loads(line))
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

    async def close_acceptor(self):
        """Reject new work but preserve accepted model operations and their output sink."""
        self._closing = True
        if self._server is not None:
            self._server.close()
            # New Python versions wait for active clients in wait_closed(); intake
            # closure must return immediately so callers can release model gates.
            self._closed_servers.append(self._server)
            self._server = None
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
