"""Offline HTTP and browser-contract tests for dependency-free loopback chat."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import re
import threading
import unittest
from unittest.mock import Mock, patch

from bot_core.http_host import ChatCompletionHost
from tools import lite_web


class _Host:
    """Async text-only test host that captures native messages without networking."""

    def __init__(self):
        self.calls = []
        self.failure = False
        self.tools = False

    async def __call__(self, messages, allow_tools=True):
        """Snapshot inputs and simulate public replies, provider errors or tool calls."""
        self.calls.append((deepcopy(messages), allow_tools))
        if self.failure:
            raise RuntimeError("PRIVATE provider_key=do-not-leak")
        if self.tools:
            return "not executable", [{"id": "danger", "type": "function"}]
        return "回答: " + messages[-1]["content"], []


class LiteWebTests(unittest.TestCase):
    """Exercise the real threaded listener with no third-party or model services."""

    def setUp(self):
        """Bind a fresh random loopback listener and conversation for each test."""
        self.host = _Host()
        self.server = lite_web.create_server(self.host, "PRIVATE system persona", history_turns=2)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        """Release listener and worker resources even when an assertion fails."""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, method="GET", path="/", data=None, headers=None, auth=True):
        """Send one local HTTP request, preserving raw text for leak assertions."""
        defaults = {}
        if auth:
            defaults["Authorization"] = "Bearer " + self.server.token
        if data is not None:
            defaults["Content-Type"] = "application/json"
            defaults["Origin"] = self.server.origin
        defaults.update(headers or {})
        body = json.dumps(data).encode("utf-8") if isinstance(data, (dict, list)) else data
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        try:
            connection.request(method, path, body=body, headers=defaults)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode("utf-8")
        finally:
            connection.close()

    def chat(self, prompt):
        """Submit a valid chat request and decode its public response."""
        status, _, raw = self.request("POST", "/api/chat", {"prompt": prompt})
        return status, json.loads(raw)

    def test_loopback_random_port_and_fragment_contract(self):
        """Never bind public interfaces; credentials live in a 64-hex fragment only."""
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertGreater(self.server.server_address[1], 0)
        self.assertRegex(self.server.token, r"^[a-f0-9]{64}$")
        self.assertEqual(self.server.url, self.server.origin + "/#" + self.server.token)
        other = lite_web.create_server(_Host(), "S")
        try:
            self.assertNotEqual(other.token, self.server.token)
        finally:
            other.server_close()

    def test_explicit_port_reuses_validated_loopback_only(self):
        """Honor an OS-selected available explicit port without adding a host option."""
        spare = lite_web.create_server(self.host, "S")
        port = spare.server_address[1]
        spare.server_close()
        explicit = lite_web.create_server(self.host, "S", port=port)
        try:
            self.assertEqual(explicit.server_address, ("127.0.0.1", port))
        finally:
            explicit.server_close()

    def test_public_html_security_headers_and_no_credentials(self):
        """Root is public static UI with CSP and no persona or bearer credential."""
        status, headers, html = self.request(auth=False)
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertNotIn(self.server.token, html)
        self.assertNotIn(self.server.system, html)
        self.assertNotIn("PRIVATE", html)
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        nonce = re.search(r'<script nonce="([a-f0-9]+)">', html).group(1)
        self.assertIn("'nonce-" + nonce + "'", headers["Content-Security-Policy"])
        self.assertIn('<style nonce="' + nonce + '">', html)
        self.assertNotEqual(nonce, self.server.token)

    def test_browser_uses_text_and_fragment_only_with_no_external_assets(self):
        """Prevent HTML execution, token URL forwarding and network dependencies."""
        _, _, html = self.request(auth=False)
        self.assertIn("history.replaceState(null, '', location.pathname)", html)
        self.assertIn("Authorization: 'Bearer ' + token", html)
        self.assertIn("node.textContent = text", html)
        self.assertNotIn("innerHTML", html)
        self.assertNotIn("localStorage", html)
        self.assertNotIn("sessionStorage", html)
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)
        self.assertNotIn("src=", html)
        self.assertNotIn("href=", html)
        self.assertIn("All tabs share one bounded conversation", html)
        self.assertIn("Shift+Enter", html)
        self.assertIn("event.isComposing", html)
        self.assertIn("No tools or QQ", html)

    def test_exact_root_only_without_redirects(self):
        """Queries, file paths and alternate asset routes never expose a page."""
        for path in ("/?token=" + self.server.token, "/index.html", "/favicon.ico", "/../tools/lite.py",
                     "//", "///", "/%2f", "/api/history/", self.server.origin + "/"):
            with self.subTest(path=path):
                status, headers, raw = self.request(path=path)
                self.assertEqual(status, 404)
                self.assertNotIn("Location", headers)
                self.assertNotIn(self.server.token, raw)

    def test_all_data_requires_bearer_not_query_or_cookie(self):
        """History/reset/chat never accept missing, malformed or URL-borne auth."""
        for method, path, body in (("GET", "/api/history", None),
                                   ("POST", "/api/history", {}),
                                   ("POST", "/api/reset", {}),
                                   ("POST", "/api/chat", {"prompt": "hello"})):
            for headers in ({}, {"Authorization": "Bearer wrong"},
                            {"Authorization": "Basic " + self.server.token},
                            {"Cookie": "token=" + self.server.token}):
                with self.subTest(method=method, path=path, headers=headers):
                    status, response_headers, raw = self.request(method, path, body, headers, auth=False)
                    self.assertEqual(status, 401)
                    self.assertNotIn("Access-Control-Allow-Origin", response_headers)
                    self.assertNotIn(self.server.token, raw)
        status, _, _ = self.request(path="/api/history?token=" + self.server.token, auth=False)
        self.assertEqual(status, 401)
        self.assertFalse(self.host.calls)

    def test_host_rebinding_rejected_even_for_public_root(self):
        """Match the exact bound authority, not localhost aliases or forwarded hosts."""
        for host in ("evil.example", "localhost:" + str(self.server.server_address[1]),
                     "127.0.0.1", self.server.authority + ".evil"):
            for method, path, data in (("GET", "/", None),
                                       ("POST", "/api/chat", {"prompt": "hello"})):
                with self.subTest(host=host, method=method):
                    status, _, raw = self.request(method, path, data, {"Host": host})
                    self.assertEqual(status, 403)
                    self.assertNotIn(host, raw)
        self.assertFalse(self.host.calls)

    def test_origin_rejected_without_cors_or_redirect(self):
        """Cross-site and null origins cannot read or mutate even with a valid token."""
        for origin in ("null", "https://evil.example", self.server.origin + "/", "http://localhost"):
            for method, path, data in (("GET", "/api/history", None),
                                       ("POST", "/api/reset", {}),
                                       ("POST", "/api/chat", {"prompt": "p"})):
                with self.subTest(origin=origin, method=method):
                    status, headers, _ = self.request(method, path, data, {"Origin": origin})
                    self.assertEqual(status, 403)
                    self.assertNotIn("Access-Control-Allow-Origin", headers)
                    self.assertNotIn("Location", headers)
        status, headers, _ = self.request("OPTIONS", "/api/chat", headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 501)
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertFalse(self.host.calls)

    def test_duplicate_security_headers_rejected(self):
        """Do not let intermediary/header parser disagreement bypass local auth."""
        for name, value in (("Host", self.server.authority),
                            ("Origin", self.server.origin),
                            ("Authorization", "Bearer " + self.server.token)):
            connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
            try:
                connection.putrequest("POST", "/api/reset", skip_host=True)
                base = {"Host": self.server.authority, "Origin": self.server.origin,
                        "Authorization": "Bearer " + self.server.token,
                        "Content-Length": "2", "Content-Type": "application/json"}
                for key, item in base.items():
                    connection.putheader(key, item)
                connection.putheader(name, value)
                connection.endheaders(b"{}")
                response = connection.getresponse()
                self.assertIn(response.status, (401, 403))
                response.read()
            finally:
                connection.close()

    def test_strict_prompt_validation(self):
        """Invalid types, blanks, oversized strings and surrogate text never call host."""
        for data in ({}, [], {"prompt": ""}, {"prompt": " \r\n\t"}, {"prompt": 2},
                     {"prompt": None}, {"prompt": False}, {"prompt": ["p"]},
                     {"prompt": "x" * 32001}, {"prompt": "\ud800"},
                     {"prompt": "p", "system": "override"}, {"prompt": "p", "tools": []}):
            with self.subTest(data=str(data)[:80]):
                status, _, raw = self.request("POST", "/api/chat", data)
                self.assertEqual(status, 400)
                self.assertEqual(json.loads(raw), {"error": lite_web._BAD_REQUEST})
        self.assertFalse(self.host.calls)

    def test_body_size_type_utf8_and_json_errors_are_fixed(self):
        """Reject malformed, non-JSON and over-limit bodies before model invocation."""
        cases = ((b"{" + b"x" * (128 * 1024), {}, 413),
                 (b'{}', {"Content-Type": "text/plain"}, 415),
                 (b'{broken PRIVATE', {}, 400),
                 (b'{"prompt":"p","prompt":"replacement"}', {}, 400),
                 (b'{"prompt":"p","extra":NaN}', {}, 400),
                 (b'{"prompt":"p","extra":Infinity}', {}, 400),
                 (b'\xff', {}, 400),
                 (b'{"prompt":"p"}', {"Transfer-Encoding": "chunked"}, 400),
                 (b'{}', {"Content-Length": "-1"}, 400),
                 (b'{}', {"Content-Length": "bad"}, 400))
        for body, headers, expected in cases:
            with self.subTest(headers=headers, length=len(body)):
                status, _, raw = self.request("POST", "/api/chat", body, headers)
                self.assertEqual(status, expected)
                self.assertNotIn("PRIVATE", raw)
                self.assertIsInstance(json.loads(raw)["error"], str)
        self.assertFalse(self.host.calls)

    def test_rejected_posts_discard_without_parsing_or_mutating(self):
        """Drain framed rejects before response close, never JSON-parse or execute them."""
        self.server.history[:] = [("kept", "answer")]
        cases = (("/api/reset", b"not-json", {}, False, 401),
                 ("/api/chat", b"not-json", {"Authorization": "Basic invalid"}, False, 401),
                 ("/api/chat", b"not-json", {"Host": "evil.example"}, True, 403),
                 ("/api/reset", b"not-json", {"Origin": "null"}, True, 403),
                 ("/unknown", b"not-json", {}, True, 404),
                 ("/api/chat", b"x" * (lite_web._MAX_BODY + 1), {}, True, 413),
                 ("/api/chat", b"not-json", {"Content-Type": "text/plain"}, True, 415))
        original = lite_web._Handler._discard_rejected_body
        discarded = []

        def observed_discard(handler):
            """Observe real reads before replying, without changing transport behavior."""
            reader = handler.rfile
            with patch.object(reader, "read1", wraps=reader.read1) as read:
                original(handler)
                discarded.append(read.call_count)

        with patch.object(lite_web._Handler, "_discard_rejected_body", observed_discard), \
             patch.object(lite_web.json, "loads", side_effect=AssertionError("rejected JSON parsed")):
            for path, body, headers, auth, expected in cases:
                with self.subTest(path=path, expected=expected):
                    status, _, raw = self.request("POST", path, body, headers, auth=auth)
                    self.assertEqual(status, expected)
                    self.assertIn('"error"', raw)
        self.assertEqual(len(discarded), len(cases))
        self.assertTrue(all(discarded))
        self.assertEqual(self.server.history, [("kept", "answer")])
        self.assertFalse(self.host.calls)

    def test_withheld_rejected_bodies_return_under_short_deadline(self):
        """Real sockets cannot hold auth or size errors for the normal 30s read timeout."""
        for auth, length, expected in ((False, 2, 401),
                                       (True, lite_web._MAX_BODY + 1, 413),
                                       (True, 999999999, 413)):
            with self.subTest(auth=auth, length=length):
                connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
                try:
                    connection.putrequest("POST", "/api/chat")
                    connection.putheader("Content-Length", str(length))
                    connection.putheader("Content-Type", "application/json")
                    if auth:
                        connection.putheader("Authorization", "Bearer " + self.server.token)
                    # Keep the write side open but intentionally send no body bytes.
                    connection.endheaders()
                    response = connection.getresponse()
                    self.assertEqual(response.status, expected)
                    self.assertIn(b'"error"', response.read())
                finally:
                    connection.close()
        self.assertFalse(self.host.calls)
        self.assertEqual(self.server.history, [])

    def test_unicode_bounded_history_and_shared_reset(self):
        """Reuse CLI algorithm, keeping only successful pairs and clearing all tabs."""
        for prompt in ("一", "二", "三"):
            status, result = self.chat(prompt)
            self.assertEqual(status, 200)
            self.assertEqual(result["reply"], "回答: " + prompt)
        self.assertEqual(self.host.calls[0], ([{"role": "system", "content": self.server.system},
                                             {"role": "user", "content": "一"}], False))
        status, _, raw = self.request(path="/api/history")
        self.assertEqual(status, 200)
        self.assertEqual([m["content"] for m in json.loads(raw)["history"]], ["二", "回答: 二", "三", "回答: 三"])
        self.assertNotIn(self.server.system, raw)
        status, _, raw = self.request("POST", "/api/reset", {})
        self.assertEqual((status, json.loads(raw)), (200, {"history": []}))
        self.chat("new")
        self.assertEqual(len(self.host.calls[-1][0]), 2)
        self.assertEqual(self.server.history, [("new", "回答: new")])

    def test_maximum_prompt_is_accepted(self):
        """The documented prompt cap is inclusive and Unicode-safe."""
        status, result = self.chat("x" * 32000)
        self.assertEqual(status, 200)
        self.assertEqual(len(result["reply"]), 32004)

    def test_provider_error_and_tools_are_safe_and_not_remembered(self):
        """No raw exception, tool invocation or failed turn enters browser history."""
        self.chat("good")
        for attribute in ("failure", "tools"):
            setattr(self.host, attribute, True)
            with self.subTest(attribute=attribute):
                status, result = self.chat("bad")
                self.assertEqual((status, result), (502, {"error": lite_web._MODEL_ERROR}))
                self.assertEqual(self.server.history, [("good", "回答: good")])
            setattr(self.host, attribute, False)
        self.chat("next")
        self.assertNotIn("bad", [m["content"] for m in self.host.calls[-1][0]])
        self.assertTrue(all(not allow for _, allow in self.host.calls))

    def test_reset_and_history_reject_unexpected_fields(self):
        """Only chat accepts prompt data; control endpoints have no hidden options."""
        for path in ("/api/reset", "/api/history"):
            status, _, _ = self.request("POST", path, {"prompt": "p"})
            self.assertEqual(status, 400)
        status, _, raw = self.request("POST", "/api/history", {})
        self.assertEqual((status, json.loads(raw)), (200, {"history": []}))

    def test_requests_never_log_urls_tokens_or_provider_errors(self):
        """Keep stdout/stderr silent for both successful and hostile HTTP requests."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.request(path="/?PRIVATE=" + self.server.token)
            self.chat("hello")
            self.host.failure = True
            self.chat("failure")
        self.assertEqual((out.getvalue(), err.getvalue()), ("", ""))

    def test_concurrent_turns_serialize_context_and_bound_memory(self):
        """Separate tabs cannot race model generation or corrupt the shared history."""
        barrier = threading.Barrier(3)
        results = []

        def submit(prompt):
            """Start one tab at the barrier and preserve its HTTP result."""
            barrier.wait(timeout=5)
            results.append(self.chat(prompt))

        workers = [threading.Thread(target=submit, args=(str(index),)) for index in range(2)]
        for worker in workers:
            worker.start()
        barrier.wait(timeout=5)
        for worker in workers:
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
        self.assertEqual(len(results), 2)
        self.assertTrue(all(status == 200 for status, _ in results))
        self.assertEqual([len(call[0]) for call in self.host.calls], [2, 4])
        self.assertEqual(len(self.server.history), 2)

    def test_reset_waits_for_inflight_reply_then_clears_shared_memory(self):
        """A reset cannot race an unfinished reply and leave a hidden remembered turn."""
        entered, release = threading.Event(), threading.Event()
        results = []
        original = lite_web._reply

        def blocked_reply(*args):
            """Pause the model boundary while holding the server conversation lock."""
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("test reply timeout")
            return original(*args)

        def submit_chat():
            """Collect the first tab's completed chat operation."""
            results.append(("chat", self.chat("inflight")[0]))

        def submit_reset():
            """Collect the other tab's serialized reset operation."""
            results.append(("reset", self.request("POST", "/api/reset", {})[0]))

        with patch.object(lite_web, "_reply", side_effect=blocked_reply):
            chat = threading.Thread(target=submit_chat)
            reset = threading.Thread(target=submit_reset)
            chat.start()
            try:
                self.assertTrue(entered.wait(timeout=5))
                reset.start()
            finally:
                release.set()
                chat.join(timeout=5)
                if reset.ident is not None:
                    reset.join(timeout=5)
        self.assertCountEqual(results, [("chat", 200), ("reset", 200)])
        self.assertEqual(self.server.history, [])
        status, _, raw = self.request(path="/api/history")
        self.assertEqual((status, json.loads(raw)), (200, {"history": []}))

    def test_invalid_configuration_fails_before_binding(self):
        """Ports stay numeric and bounded, and history always has a positive limit."""
        for kwargs in ({"port": -1}, {"port": 65536}, {"port": True}, {"port": "80"},
                       {"history_turns": 0}, {"history_turns": True}, {"history_turns": "2"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                lite_web.create_server(self.host, "S", **kwargs)
        for system in (None, 0, False):
            with self.subTest(system=system), self.assertRaises(ValueError):
                lite_web.create_server(self.host, system)
        for system in ("", " \t"):
            with self.subTest(system=system):
                server = lite_web.create_server(self.host, system)
                server.server_close()

    def test_run_web_browser_optional_and_ctrl_c_closes_listener(self):
        """Startup shares the fragment link, handles Ctrl+C and always closes."""
        for launch in (False, True):
            server = lite_web.create_server(self.host, "S")
            out = io.StringIO()
            with patch.object(lite_web, "create_server", return_value=server), \
                 patch.object(server, "serve_forever", side_effect=KeyboardInterrupt), \
                 patch.object(server, "server_close", wraps=server.server_close) as close, \
                 patch.object(lite_web.webbrowser, "open", side_effect=RuntimeError("PRIVATE")) as browser, \
                 redirect_stdout(out):
                lite_web.run_web(self.host, "S", open_browser=launch)
            self.assertEqual(close.call_count, 1)
            self.assertEqual(browser.call_count, int(launch))
            self.assertIn(server.url, out.getvalue())
            self.assertNotIn("PRIVATE", out.getvalue())
            self.assertEqual(server.socket.fileno(), -1)


class LiteWebRejectedBodyTests(unittest.TestCase):
    """Prove discard limits independently of platform-dependent TCP reset timing."""

    def handler(self, headers):
        """Build only the rejection reader with no listener or real credentials."""
        handler = object.__new__(lite_web._Handler)
        handler.headers = http.client.parse_headers(io.BytesIO(headers + b"\r\n"))
        handler.connection = Mock()
        handler.connection.gettimeout.return_value = 30
        handler.rfile = Mock()
        handler.rfile.read1.return_value = b"x"
        return handler

    def test_ambiguous_malformed_and_huge_lengths_never_read(self):
        """Attacker framing cannot cause speculative reads or unbounded conversion."""
        cases = (b"", b"Content-Length: -1\r\n", b"Content-Length: bad\r\n",
                 b"Content-Length: 2\r\nContent-Length: 2\r\n",
                 b"Content-Length: 2\r\nTransfer-Encoding: chunked\r\n",
                 b"Content-Length: 999999999\r\n",
                 b"Content-Length: " + b"9" * 5000 + b"\r\n",
                 f"Content-Length: {lite_web._MAX_REJECT_BODY + 1}\r\n".encode())
        for headers in cases:
            with self.subTest(length=len(headers)):
                handler = self.handler(headers)
                handler._discard_rejected_body()
                handler.rfile.read1.assert_not_called()
                handler.connection.settimeout.assert_not_called()

    def test_discard_has_total_deadline_and_restores_socket_timeout(self):
        """Trickling bytes cannot renew a read deadline beyond the fixed half-second."""
        handler = self.handler(b"Content-Length: 3\r\n")
        with patch.object(lite_web.time, "monotonic", side_effect=[10, 10.1, 10.6]):
            handler._discard_rejected_body()
        handler.rfile.read1.assert_called_once_with(3)
        self.assertAlmostEqual(handler.connection.settimeout.call_args_list[0].args[0], 0.4)
        self.assertEqual(handler.connection.settimeout.call_args_list[-1].args, (30,))

    def test_discard_timeout_still_allows_fixed_error_response(self):
        """A stalled or closed rejected client does not turn rejection into execution."""
        handler = self.handler(b"Content-Length: 2\r\n")
        handler.rfile.read1.side_effect = TimeoutError
        handler._discard_rejected_body()
        handler.rfile.read1.assert_called_once_with(2)
        self.assertEqual(handler.connection.settimeout.call_args_list[-1].args, (30,))

    def test_body_already_read_is_not_discarded_twice(self):
        """JSON errors after reading cannot wait again for the declared request size."""
        handler = self.handler(b"Content-Length: 2\r\n")
        handler.command = "POST"
        handler._body_started = True
        handler.server = Mock(nonce="public")
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler._json(400, {"error": lite_web._BAD_REQUEST})
        handler.rfile.read1.assert_not_called()
        self.assertTrue(handler.close_connection)


class _ProviderHandler(BaseHTTPRequestHandler):
    """Serve one synthetic OpenAI-compatible response for real-host integration."""

    def do_POST(self):
        """Capture the actual provider payload and return separate private reasoning."""
        self.server.received = (self.path, self.headers.get("Authorization"),
                                json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        raw = json.dumps({"choices": [{"message": {"role": "assistant", "content": "本地真实链路 OK",
                                                 "reasoning_content": "PRIVATE reasoning"}}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format, *args):
        """Keep synthetic HTTP tests silent, including the fake credential."""


class LiteWebHostIntegrationTests(unittest.TestCase):
    """Verify the browser endpoint uses the unmodified existing model HTTP host."""

    def test_real_synthetic_local_http_round_trip(self):
        """Cross both local HTTP boundaries with tools off and credentials server-side."""
        provider = ThreadingHTTPServer(("127.0.0.1", 0), _ProviderHandler)
        provider.daemon_threads = True
        provider_thread = threading.Thread(target=provider.serve_forever,
                                           kwargs={"poll_interval": 0.01}, daemon=True)
        provider_thread.start()
        host = ChatCompletionHost(f"http://127.0.0.1:{provider.server_address[1]}/v1",
                                  "synthetic-model", "fake-provider-secret")
        server = lite_web.create_server(host, "Persona")
        thread = threading.Thread(target=server.serve_forever,
                                  kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        try:
            connection.request("POST", "/api/chat", body=json.dumps({"prompt": "你好"}),
                               headers={"Authorization": "Bearer " + server.token,
                                        "Content-Type": "application/json", "Origin": server.origin})
            response = connection.getresponse()
            raw = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(raw)["reply"], "本地真实链路 OK")
            self.assertNotIn("PRIVATE", raw)
            self.assertNotIn("fake-provider-secret", raw)
            path, authorization, payload = provider.received
            self.assertEqual(path, "/v1/chat/completions")
            self.assertEqual(authorization, "Bearer fake-provider-secret")
            self.assertEqual(payload, {"model": "synthetic-model", "messages": [
                {"role": "system", "content": "Persona"}, {"role": "user", "content": "你好"}]})
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            provider.shutdown()
            provider.server_close()
            thread.join(timeout=3)
            provider_thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
