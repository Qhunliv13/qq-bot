"""Real loopback socket tests with synthetic injected handlers, never QQ login."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qq_transport.actions import ActionDispatcher, Handler
from qq_transport.http_api import ActionHttpServer
from qq_transport.store import OperationJournal


class HttpApiTests(unittest.IsolatedAsyncioTestCase):
    """Exercise HTTP framing, auth and the existing durable dispatcher contract."""

    async def asyncSetUp(self):
        """Inject a synthetic capability and a genuine temporary durable journal."""
        self.temp = tempfile.TemporaryDirectory()
        self.journal = OperationJournal(Path(self.temp.name) / "journal.sqlite")
        self.calls = []

        async def native(account, params):
            """Record untouched native parameters without inventing message IDs."""
            self.calls.append((account, params))
            return {"status": "ok", "retcode": 0, "data": {"accepted": True}}

        self.dispatcher = ActionDispatcher(
            {"read": Handler(native), "write": Handler(native, mutating=True)}, self.journal)
        self.server = ActionHttpServer(self.dispatcher, account="123", token="test-secret")
        self.assertIsNone(self.server.address)
        await self.server.start()

    async def asyncTearDown(self):
        """Drain before removing the durable journal, even after a failed assertion."""
        await self.server.close()
        self.temp.cleanup()

    async def raw(self, request):
        """Send real HTTP bytes and decode the explicitly framed JSON response."""
        reader, writer = await asyncio.open_connection(*self.server.address)
        try:
            writer.write(request)
            await writer.drain()
            response = await reader.read()
            headers, payload = response.split(b"\r\n\r\n", 1)
            self.assertIn(b"Content-Type: application/json", headers)
            self.assertIn(f"Content-Length: {len(payload)}".encode(), headers)
            return int(headers.split(b" ")[1]), json.loads(payload)
        finally:
            writer.close()
            await writer.wait_closed()

    async def request(self, path="/read", body=b"{}", *, token="test-secret",
                      method="POST", extra=b""):
        """Construct one standard JSON POST without a mock HTTP transport."""
        auth = b"" if token is None else f"Authorization: Bearer {token}\r\n".encode()
        return await self.raw(
            f"{method} {path} HTTP/1.1\r\nHost: localhost\r\n".encode() + auth
            + b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\n".encode() + extra + b"\r\n" + body)

    async def test_native_payload_and_account_are_not_rewritten(self):
        """Account and echo fields stay parameters; configured account routes calls."""
        params = {"account": "999", "echo": {"native": 1}, "message": "中文"}
        status, result = await self.request(body=json.dumps(params).encode())
        self.assertEqual(status, 200)
        self.assertEqual(result["retcode"], 0)
        self.assertIsNone(result["echo"])
        self.assertEqual(self.calls, [("123", params)])

    async def test_auth_is_required_without_query_fallback(self):
        """Missing/wrong authorization cannot execute or expose secrets."""
        for token in (None, "wrong", ""):
            status, result = await self.request(token=token)
            self.assertEqual((status, result["message"]), (401, "unauthorized"))
            self.assertNotIn("test-secret", json.dumps(result))
        status, _ = await self.request("/read?access_token=test-secret", token=None)
        self.assertEqual(status, 401)
        self.assertEqual(self.calls, [])

    async def test_strict_paths_method_and_unknown_action(self):
        """Raw action identifiers are reused; HTTP paths have no alias expansion."""
        for path in ("/../read", "/..", "/.", "/read/", "/%72ead", "/read?q=1", "//read"):
            self.assertEqual((await self.request(path))[0], 404)
        self.assertEqual((await self.request(method="GET"))[0], 405)
        status, result = await self.request("/unregistered")
        self.assertEqual((status, result["retcode"]), (200, 1404))
        self.assertEqual(self.calls, [])

    async def test_invalid_json_is_fixed_400(self):
        """Strict JSON objects reject constants, overflow, malformed UTF-8 and lists."""
        for body in (b"[]", b"null", b"{", b'{"x":NaN}', b'{"x":Infinity}',
                     b'{"x":1e999}', b'{"x":"\xff"}'):
            self.assertEqual(await self.request(body=body),
                             (400, {"status": "failed", "retcode": 1400,
                                    "data": None, "message": "invalid_request"}))
        self.assertEqual(self.calls, [])

    async def test_request_size_and_framing_limits(self):
        """Reject oversized bodies/headers, duplicates and chunked ambiguity."""
        self.assertEqual((await self.request(body=b" " * 65537))[0], 400)
        self.assertEqual((await self.request(extra=b"X-Pad: " + b"x" * 16384 + b"\r\n"))[0], 400)
        self.assertEqual((await self.request(extra=b"Content-Length: 2\r\n"))[0], 400)
        self.assertEqual((await self.request(extra=b"Transfer-Encoding: chunked\r\n"))[0], 400)
        self.assertEqual((await self.request(extra=b"X: a\r\n" * 65))[0], 400)
        self.assertEqual(self.calls, [])

    async def test_exact_body_limit_and_json_content_type(self):
        """Accept 64 KiB exactly while rejecting missing/wrong content types."""
        body = b'{"x":"' + b"a" * (65536 - 8) + b'"}'
        self.assertEqual(len(body), 65536)
        self.assertEqual((await self.request(body=body))[1]["retcode"], 0)
        for content_type in (b"", b"Content-Type: text/plain\r\n"):
            request = (b"POST /read HTTP/1.1\r\nAuthorization: Bearer test-secret\r\n"
                       + content_type + b"Content-Length: 2\r\n\r\n{}")
            self.assertEqual((await self.raw(request))[0], 400)

    async def test_pending_claim_is_never_executed(self):
        """An existing durable pending claim survives HTTP retries unchanged."""
        self.journal.claim("123", "pending-100", "write", {})
        status, result = await self.request("/write", extra=b"X-Operation-Id: pending-100\r\n")
        self.assertEqual((status, result["retcode"], result["outcome"]), (200, 1504, "unknown"))
        self.assertEqual(self.calls, [])

    async def test_mutation_replay_and_conflict_use_existing_journal(self):
        """Header operation identity is durable; duplicate mutations never execute."""
        self.assertEqual((await self.request("/write"))[1]["retcode"], 1400)
        extra = b"X-Operation-Id: operation-100\r\n"
        first = await self.request("/write", body=b'{"x":1}', extra=extra)
        self.assertEqual(await self.request("/write", body=b'{"x":1}', extra=extra), first)
        self.assertEqual((await self.request("/write", body=b'{"x":2}', extra=extra))[1]["retcode"], 1409)
        self.assertEqual(len(self.calls), 1)

    async def test_unknown_outcome_never_reexecutes(self):
        """A native exception remains a journaled unknown, not retry permission."""
        async def uncertain(account, params):
            """Simulate an unconfirmed mutation without exposing diagnostic details."""
            self.calls.append((account, params))
            raise RuntimeError("private native detail")

        self.dispatcher._handlers["uncertain"] = Handler(uncertain, mutating=True)
        extra = b"X-Operation-Id: unknown-100\r\n"
        first = await self.request("/uncertain", extra=extra)
        self.assertEqual(first[1]["outcome"], "unknown")
        self.assertEqual(await self.request("/uncertain", extra=extra), first)
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("private native detail", json.dumps(first))

    async def test_read_timeout_does_not_timeout_execution_or_cancel_drain(self):
        """Only acquisition is timed; cancelled close callers do not cancel actions."""
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow(account, params):
            """Hold an accepted mutation until the test explicitly allows completion."""
            entered.set()
            await release.wait()
            return {"status": "ok", "retcode": 0, "data": None}

        self.dispatcher._handlers["slow"] = Handler(slow, mutating=True)
        with patch("qq_transport.http_api._READ_TIMEOUT", 0.03):
            pending = asyncio.create_task(self.request("/slow", extra=b"X-Operation-Id: slow-100\r\n"))
            await asyncio.wait_for(entered.wait(), 1)
            await asyncio.sleep(0.06)
            self.assertFalse(pending.done())
            closing = asyncio.create_task(self.server.close())
            await asyncio.sleep(0)
            closing.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await closing
            release.set()
            self.assertEqual((await pending)[1]["retcode"], 0)
            await self.server.close()
            self.assertIsNone(self.server.address)

    async def test_concurrent_start_is_idempotent_and_closed_stays_closed(self):
        """Lifecycle serialization binds once and cannot restart after shutdown."""
        other = ActionHttpServer(self.dispatcher, account="123", token="x")
        try:
            await asyncio.gather(other.start(), other.start())
            self.assertIsNotNone(other.address)
            await asyncio.gather(other.close(), other.close())
            self.assertIsNone(other.address)
            with self.assertRaises(RuntimeError):
                await other.start()
        finally:
            await other.close()

    async def test_incomplete_request_times_out(self):
        """A stalled local client cannot hold acquisition open indefinitely."""
        with patch("qq_transport.http_api._READ_TIMEOUT", 0.02):
            self.assertEqual((await self.raw(b"POST /read HTTP/1.1\r\n"))[0], 400)

    async def test_constructor_rejects_unsafe_defaults(self):
        """Never bind wildcard/non-loopback hosts or silently permit empty auth."""
        for host in ("0.0.0.0", "::", "192.0.2.1", "localhost"):
            with self.assertRaises(ValueError):
                ActionHttpServer(self.dispatcher, account="123", token="secret", host=host)
        for token in (None, "", " ", 123):
            with self.assertRaises(ValueError):
                ActionHttpServer(self.dispatcher, account="123", token=token)
        with self.assertRaises(ValueError):
            ActionHttpServer(None, account="123", token="secret")
        # No arbitrary minimum length requirement is imposed on injected secrets.
        server = ActionHttpServer(self.dispatcher, account="123", token="x", host="::1")
        self.assertIsNone(server.address)
        await server.close()
