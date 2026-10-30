"""Independent offline security contract for the full-runtime setup listener.

Only disposable IPv4 loopback listeners are used. No Docker, model, QQ session,
existing installation, or real credential is accessed by this module.
"""

from __future__ import annotations

import http.client
import json
import re
import threading
from urllib.parse import urlsplit

import pytest


class _Controller:
    """Record UI commands without executing any installer or privileged action."""

    def __init__(self):
        self.calls = []
        self.fail_snapshot = False
        self.fail_action = False

    def snapshot(self):
        """Return only synthetic public readiness, never private configuration."""
        if self.fail_snapshot:
            raise RuntimeError("PRIVATE-EXCEPTION model-key=fictitious-never-expose")
        return {
            "initialized": False,
            "environment": "unknown",
            "runtime": "unverified",
            "model": "unverified",
            "qq": "unverified",
            "busy": False,
            "stage": "welcome",
            "message": "Ready / 可以开始",
        }

    def action(self, action, payload):
        """Capture the declared action protocol; failures contain a leak sentinel."""
        if self.fail_action:
            raise RuntimeError("PRIVATE-EXCEPTION model-key=fictitious-never-expose")
        self.calls.append((action, payload))
        return {"ok": True}


@pytest.fixture
def listener():
    """Start the actual server against a safe controller and always release it."""
    from tools.setup_wizard import Server

    controller = _Controller()
    server = Server(controller, port=0)
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.01},
        daemon=True,
    )
    thread.start()
    try:
        yield controller, server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


def _request(server, method="GET", path="/api/state", body=None, *,
             authenticated=True, origin=True, extra_headers=None):
    """Send one bounded local request while retaining response text for leak checks."""
    headers = {}
    if authenticated:
        headers["Authorization"] = "Bearer " + server.token
    if origin:
        headers["Origin"] = server.origin
    if body is not None:
        headers["Content-Type"] = "application/json"
    headers.update(extra_headers or {})
    if isinstance(body, (dict, list)):
        body = json.dumps(body, ensure_ascii=False).encode("utf-8")
    connection = http.client.HTTPConnection(*server.server_address, timeout=5)
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read().decode("utf-8")
    finally:
        connection.close()


def _raw_request(server, headers, body=b"{}", path="/api/action"):
    """Preserve duplicate headers that a dictionary-based client cannot represent."""
    connection = http.client.HTTPConnection(*server.server_address, timeout=5)
    try:
        connection.putrequest("POST", path, skip_host=True, skip_accept_encoding=True)
        for name, value in headers:
            connection.putheader(name, value)
        connection.endheaders(body)
        response = connection.getresponse()
        return response.status, response.read().decode("utf-8")
    finally:
        connection.close()


def _headers(server, body):
    """Return unambiguous headers; individual tests introduce exactly one defect."""
    return [
        ("Host", server.authority),
        ("Origin", server.origin),
        ("Authorization", "Bearer " + server.token),
        ("Content-Type", "application/json"),
        ("Content-Length", str(len(body))),
    ]


def test_listener_binds_only_loopback_with_ephemeral_fragment_authorization(listener):
    """The generated capability belongs to one listener and never a URL query."""
    _, server = listener
    assert server.server_address[0] == "127.0.0.1"
    assert server.server_address[1] > 0
    assert len(server.token) >= 40
    assert server.url == server.origin + "/#token=" + server.token
    assert urlsplit(server.url).query == ""
    assert urlsplit(server.url).hostname == "127.0.0.1"
    from tools.setup_wizard import Server
    other = Server(_Controller(), port=0)
    try:
        assert other.token != server.token
    finally:
        other.server_close()


def test_static_root_is_public_but_never_embeds_listener_secret(listener):
    """A page load cannot disclose credentials or cached installation information."""
    _, server = listener
    status, headers, text = _request(server, path="/", authenticated=False, origin=False)
    assert status == 200
    assert "text/html" in headers["Content-Type"]
    assert headers["Cache-Control"] == "no-store"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "Access-Control-Allow-Origin" not in headers
    assert server.token not in text
    assert "fictitious-never-expose" not in text
    assert not re.search(r"(?:localStorage|sessionStorage)\s*(?:\.|\[|=)", text)
    assert "innerHTML" not in text


@pytest.mark.parametrize("path", ["/api/state", "/api/state?token=not-a-credential"])
def test_state_requires_bearer_even_on_loopback(listener, path):
    """Neither loopback reachability nor query strings count as authorization."""
    controller, server = listener
    status, _, text = _request(server, path=path, authenticated=False)
    assert status in (401, 403, 404)
    assert "可以开始" not in text
    assert not controller.calls


def test_authenticated_state_is_public_projection(listener):
    """State exposes the declared projection rather than raw settings or secrets."""
    controller, server = listener
    status, _, text = _request(server)
    assert status == 200
    value = json.loads(text)
    assert value.get("initialized") is False
    assert value.get("busy") is False
    assert "fictitious-never-expose" not in text
    assert not controller.calls


@pytest.mark.parametrize("host", ["localhost:1", "attacker.invalid", "127.0.0.1:1"])
def test_exact_host_blocks_rebinding_on_page_and_state(listener, host):
    """Numeric loopback binding is not a substitute for exact authority checks."""
    _, server = listener
    for path in ("/", "/api/state"):
        status, _, _ = _request(server, path=path, extra_headers={"Host": host})
        assert status == 403


@pytest.mark.parametrize("origin", [None, "null", "https://attacker.invalid", "http://localhost:6185"])
def test_mutations_require_exact_origin(listener, origin):
    """Browsers cannot authorize writes from another site or an absent Origin."""
    controller, server = listener
    headers = {} if origin is None else {"Origin": origin}
    status, _, _ = _request(server, "POST", "/api/action", {"action": "check"},
                             origin=origin is not None, extra_headers=headers)
    assert status == 403
    assert not controller.calls


def test_action_does_not_accept_missing_or_wrong_bearer(listener):
    """Origin agreement alone never grants a page the installation capability."""
    controller, server = listener
    for auth in (None, "Bearer incorrect"):
        headers = {} if auth is None else {"Authorization": auth}
        status, _, _ = _request(server, "POST", "/api/action", {"action": "check"},
                                 authenticated=False, extra_headers=headers)
        assert status in (401, 403)
    assert not controller.calls


def test_valid_action_is_forwarded_once_with_unicode_payload(listener):
    """The listener passes a deliberate save request without altering user text."""
    controller, server = listener
    payload = {
        "model": {"base_url": "https://provider.invalid/v1", "model": "custom-model",
                  "api_key": "fictitious-private-test-key"},
        "owner_qqs": [], "persona": "保持原样：你好。",
    }
    status, _, text = _request(server, "POST", "/api/action", {"action": "save", **payload})
    assert status == 200
    assert json.loads(text)["ok"] is True
    assert len(controller.calls) == 1
    assert controller.calls[0][0] == "save"
    assert all(controller.calls[0][1][key] == value for key, value in payload.items())
    assert "fictitious-private-test-key" not in text


@pytest.mark.parametrize("body", [
    b'{"action":"check","action":"start"}',
    b'{"action":"save","model":{"api_key":"a","api_key":"b"}}',
    b'{"action":"check","extra":NaN}',
    b'{"action":"check","extra":Infinity}',
    b'[]', b'null', b'{', b'\xff',
])
def test_ambiguous_or_invalid_json_is_rejected_before_controller(listener, body):
    """Nested duplicate keys and nonstandard numbers cannot bypass validation."""
    controller, server = listener
    status, _, text = _request(server, "POST", "/api/action", body)
    assert 400 <= status < 500
    assert "Traceback" not in text
    assert not controller.calls


@pytest.mark.parametrize("duplicate", ["Host", "Origin", "Authorization", "Content-Length", "Content-Type"])
def test_duplicate_security_or_framing_headers_are_rejected(listener, duplicate):
    """Even identical duplicates are ambiguous to different HTTP intermediaries."""
    controller, server = listener
    body = b'{"action":"check"}'
    headers = _headers(server, body)
    value = next(value for key, value in headers if key == duplicate)
    status, _ = _raw_request(server, headers + [(duplicate, value)], body)
    assert 400 <= status < 500
    assert not controller.calls


def test_oversized_body_is_rejected_without_running_actions(listener):
    """A local tab cannot send an unbounded secret/configuration upload."""
    controller, server = listener
    # Declare the excessive body without transmitting it: the listener must reject
    # framing before reading bytes (sending a megabyte causes OS-specific resets).
    headers = [(key, str(1024 * 1024) if key == "Content-Length" else value)
               for key, value in _headers(server, b"")]
    status, _ = _raw_request(server, headers, b"")
    assert status in (400, 413)
    assert not controller.calls


@pytest.mark.parametrize("path", ["/api/action/", "/api/action?x=1", "/api/start", "/settings.json"])
def test_only_exact_declared_routes_are_mutable(listener, path):
    """Path normalization cannot accidentally expose a file or alternate action."""
    controller, server = listener
    status, _, text = _request(server, "POST", path, {"action": "check"})
    assert status in (403, 404)
    assert "fictitious-never-expose" not in text
    assert not controller.calls


@pytest.mark.parametrize("which", ["action", "snapshot"])
def test_raw_controller_exceptions_never_reach_http_or_logs(listener, which, capsys):
    """The server maps internal failures to fixed messages instead of traceback text."""
    controller, server = listener
    if which == "action":
        controller.fail_action = True
        status, _, text = _request(server, "POST", "/api/action", {"action": "check"})
    else:
        controller.fail_snapshot = True
        status, _, text = _request(server)
    assert 400 <= status < 600
    captured = capsys.readouterr()
    assert "PRIVATE-EXCEPTION" not in text + captured.out + captured.err
    assert "fictitious-never-expose" not in text + captured.out + captured.err
    assert "Traceback" not in text + captured.out + captured.err
