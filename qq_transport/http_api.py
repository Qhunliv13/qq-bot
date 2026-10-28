"""Authenticated loopback HTTP boundary, NOT a QQ backend or login service.

Only an explicitly injected dispatcher can execute actions. No native handlers,
backend, environment defaults, aliases, or network-facing launch are supplied.
"""

import asyncio
import hmac
import ipaddress
import json
import re

from .store import copy_json, validate_account, validate_action


# Bound only request acquisition, never a possibly mutating handler's execution.
_HEADER_LIMIT = 16 * 1024
_BODY_LIMIT = 64 * 1024
_HEADER_COUNT = 64
_READ_TIMEOUT = 5
_HEADER_NAME = re.compile(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")


def _failure(code, message):
    """Use a fixed public envelope; never expose parser or callback exceptions."""
    return {"status": "failed", "retcode": code, "data": None, "message": message}


class ActionHttpServer:
    """Serve one POST /<raw-action> per connection with durable dispatch reuse.

    Account selection is configuration-only. All JSON object fields are native
    parameters, including account/echo if present; no envelope is inferred.
    Shutdown stops accepting and drains handlers without canceling mutations.
    """

    def __init__(self, dispatcher, *, account, token, host="127.0.0.1", port=0):
        validate_account(account)
        if not isinstance(token, str) or not token or not token.strip():
            raise ValueError("A nonempty bearer token is required")
        # Literal loopback addresses avoid DNS rebinding and accidental exposure.
        if not isinstance(host, str) or not ipaddress.ip_address(host).is_loopback:
            raise ValueError("A literal loopback address is required")
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("Invalid port")
        if not callable(getattr(dispatcher, "dispatch", None)):
            raise ValueError("An explicit dispatcher is required")
        self._dispatcher = dispatcher
        self._account = account
        self._authorization = ("Bearer " + token).encode("utf-8")
        self._host, self._port = host, port
        self._server = None
        self._tasks = set()
        self._closing = False
        self._close_task = None
        self._lifecycle_lock = asyncio.Lock()

    @property
    def address(self):
        """Return the bound (host, port), or None before start/after close."""
        if self._server is None or not self._server.sockets:
            return None
        return self._server.sockets[0].getsockname()[:2]

    async def start(self):
        """Bind only the configured loopback socket; repeated starts are harmless."""
        # Serialize bind with shutdown so concurrent lifecycle calls cannot leak sockets.
        async with self._lifecycle_lock:
            if self._closing:
                raise RuntimeError("Server is closing or closed")
            if self._server is None:
                self._server = await asyncio.start_server(
                    self._accept, self._host, self._port, limit=_HEADER_LIMIT)
        return self

    def _accept(self, reader, writer):
        """Track immediately so shutdown cannot miss an accepted connection."""
        task = asyncio.create_task(self._handle(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def close(self):
        """Shield shared draining from caller cancellation; mutations keep running."""
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._drain())
        await asyncio.shield(self._close_task)

    async def _drain(self):
        """Close the listener then await every accepted handler, without timeouts."""
        async with self._lifecycle_lock:
            if self._server is not None:
                self._server.close()
                await self._server.wait_closed()
        # Connection callbacks queued before listener close may run on this turn.
        await asyncio.sleep(0)
        while self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        self._server = None

    async def _request(self, reader):
        """Parse bounded HTTP framing and authenticate before acquiring the body."""
        header = await reader.readuntil(b"\r\n\r\n")
        if len(header) > _HEADER_LIMIT:
            raise ValueError("headers too large")
        lines = header[:-4].split(b"\r\n")
        if len(lines) - 1 > _HEADER_COUNT:
            raise ValueError("too many headers")
        request = lines[0].split(b" ")
        if len(request) != 3 or request[2] not in (b"HTTP/1.0", b"HTTP/1.1"):
            raise ValueError("invalid request line")
        method, target, _ = request
        if (not _HEADER_NAME.fullmatch(method) or not target
                or any(c <= 32 or c >= 127 for c in target)):
            raise ValueError("invalid request line")
        headers = {}
        for line in lines[1:]:
            name, separator, value = line.partition(b":")
            if not separator or not _HEADER_NAME.fullmatch(name):
                raise ValueError("invalid header")
            name = name.lower()
            # Reject ambiguous framing/auth rather than selecting one duplicate.
            if name in headers or any(c < 32 and c != 9 or c == 127 for c in value):
                raise ValueError("invalid header")
            headers[name] = value.strip(b" \t")
        if not hmac.compare_digest(headers.get(b"authorization", b""), self._authorization):
            return 401, _failure(1401, "unauthorized")
        if method != b"POST":
            return 405, _failure(1405, "method_not_allowed")
        # Do not URL-decode: encoded slashes, traversal, query actions and aliases fail.
        try:
            if not target.startswith(b"/"):
                raise ValueError("invalid path")
            action = validate_action(target[1:].decode("ascii"))
        except (ValueError, UnicodeError):
            return 404, _failure(1404, "not_found")
        if b"transfer-encoding" in headers or b"expect" in headers:
            raise ValueError("unsupported framing")
        raw_length = headers.get(b"content-length", b"")
        if not raw_length or not raw_length.isdigit() or len(raw_length) > 10:
            raise ValueError("invalid length")
        length = int(raw_length)
        if length > _BODY_LIMIT:
            raise ValueError("body too large")
        media_type = headers.get(b"content-type", b"").split(b";", 1)[0].strip().lower()
        if media_type != b"application/json":
            raise ValueError("JSON content type required")
        body = await reader.readexactly(length)
        params = json.loads(body.decode("utf-8"))
        if not isinstance(params, dict):
            raise ValueError("object required")
        # The shared dispatcher codec rejects non-finite values including 1e999.
        params = copy_json(params)
        operation_id = headers.get(b"x-operation-id")
        if operation_id is not None:
            operation_id = operation_id.decode("utf-8")
        return action, params, operation_id

    async def _handle(self, reader, writer):
        """Timeout request setup only, then dispatch without execution deadlines."""
        try:
            try:
                request = await asyncio.wait_for(self._request(reader), _READ_TIMEOUT)
            except (ValueError, UnicodeError, RecursionError, OverflowError,
                    asyncio.IncompleteReadError, asyncio.LimitOverrunError,
                    asyncio.TimeoutError):
                request = 400, _failure(1400, "invalid_request")
            if len(request) == 2:
                status, result = request
            else:
                action, params, operation_id = request
                status = 200
                result = await self._dispatcher.dispatch(
                    self._account, action, params, operation_id=operation_id)
            await self._respond(writer, status, result)
        except (ConnectionError, OSError):
            # A disconnected client must not cause action replay or cancellation.
            pass
        except Exception:
            # An injected implementation must not leak raw exceptions over HTTP.
            try:
                await self._respond(writer, 500, _failure(1500, "internal_error"))
            except (ConnectionError, OSError):
                pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def _respond(self, writer, status, result):
        """Write a strict JSON response with explicit framing and no keep-alive."""
        payload = json.dumps(result, ensure_ascii=False, allow_nan=False,
                             separators=(",", ":")).encode("utf-8")
        reasons = {200: "OK", 400: "Bad Request", 401: "Unauthorized",
                   404: "Not Found", 405: "Method Not Allowed", 500: "Internal Server Error"}
        header = (f"HTTP/1.1 {status} {reasons[status]}\r\n"
                  f"Content-Length: {len(payload)}\r\n"
                  "Content-Type: application/json; charset=utf-8\r\n"
                  "Connection: close\r\n\r\n").encode("ascii")
        writer.write(header + payload)
        await writer.drain()
