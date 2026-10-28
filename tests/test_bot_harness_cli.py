"""Synthetic HTTP/CLI checks: no real model, QQ login or production access."""

import asyncio
from copy import deepcopy
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import sys
import threading
import types
from unittest.mock import Mock
from urllib.error import HTTPError

import pytest

from bot_core import __main__ as cli
from bot_core.harness import Harness
from bot_core.http_host import (
    ChatCompletionHost, MAX_RESPONSE_BYTES, ModelRequestError, _NoRedirect,
    create_http_harness, validate_base_url,
)


def _mock_response(monkeypatch, data):
    """Install a private opener mock; requests never leave this process."""
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.read.return_value = data
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr("bot_core.http_host.build_opener", Mock(return_value=opener))
    return opener, response


def _reply(content="synthetic answer", calls=None):
    """Build a clearly synthetic provider message, not evidence of a real LLM."""
    message = {"role": "assistant", "content": content}
    if calls is not None:
        message["tool_calls"] = calls
    return json.dumps({"choices": [{"message": message}]}).encode()


def test_http_request_preserves_messages_and_no_tool_advertising(monkeypatch):
    """HTTP posts once with copied native messages and an environment-supplied key."""
    opener, response = _mock_response(monkeypatch, _reply("你好"))
    messages = [{"role": "system", "content": "persona"},
                {"role": "user", "content": "question"}]
    before = deepcopy(messages)
    host = ChatCompletionHost("https://provider.invalid/v1/", "chosen", "test-only", 7)
    assert asyncio.run(host(messages, allow_tools=False)) == ("你好", [])
    request = opener.open.call_args.args[0]
    assert request.full_url == "https://provider.invalid/v1/chat/completions"
    assert request.get_header("Authorization") == "Bearer test-only"
    assert request.get_method() == "POST"
    assert json.loads(request.data) == {"model": "chosen", "messages": messages}
    assert opener.open.call_args.kwargs == {"timeout": 7}
    response.read.assert_called_once_with(MAX_RESPONSE_BYTES + 1)
    assert messages == before
    assert opener.open.call_count == 1


@pytest.mark.parametrize("url", [
    "http://provider.invalid/v1", "https://user:secret@provider.invalid/v1",
    "https://provider.invalid/v1?secret=x", "https://provider.invalid/v1#secret",
    "https://provider.invalid/v1?", "https://provider.invalid/v1#",
    "file:///tmp/model", "http://127.0.0.2/v1", "https://provider.invalid:99999/v1",
    "https://provider.invalid/\nsecret",
])
def test_bad_base_urls(url):
    """Disallow credential-bearing URLs, remote plaintext and ambiguous inputs."""
    with pytest.raises(ValueError, match="invalid model base URL"):
        validate_base_url(url)


@pytest.mark.parametrize("url", ["http://localhost:8000/v1", "http://127.0.0.1/v1", "http://[::1]/v1"])
def test_loopback_can_be_anonymous(url):
    """An explicitly local model need not invent a provider credential."""
    assert ChatCompletionHost(url, "local").api_key == ""


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True])
def test_invalid_timeout(timeout):
    """Timeout configuration must be finite and positive before a request."""
    with pytest.raises(ValueError):
        ChatCompletionHost("http://localhost/v1", "local", timeout=timeout)


def test_remote_requires_key_and_redirects_fail_closed():
    """Remote credentials are explicit and never forwarded through a redirect."""
    with pytest.raises(ValueError):
        ChatCompletionHost("https://provider.invalid/v1", "model")
    with pytest.raises(ModelRequestError):
        _NoRedirect().redirect_request(None, None, 302, "secret", {}, "http://localhost/")


@pytest.mark.parametrize("raw", [
    b"not-json secret", b'{"choices":NaN}', b'{"choices":[]}', b'{"choices":[false]}',
    b'{"choices":[{"message":{"role":"user","content":"x"}}]}',
    _reply([{"type": "text", "text": "not supported"}]),
    _reply(None), _reply("x", [False]), b"x" * (MAX_RESPONSE_BYTES + 1),
], ids=["non-json", "nan", "empty", "bool-choice", "role", "rich-content", "null", "bad-call", "oversized"])
def test_invalid_responses_are_sanitized(monkeypatch, raw):
    """Bad protocol shapes never stringify arbitrary provider content as success."""
    _mock_response(monkeypatch, raw)
    with pytest.raises(ModelRequestError) as failure:
        asyncio.run(ChatCompletionHost("http://localhost/v1", "local")([]))
    assert str(failure.value) == "model request failed"
    assert failure.value.__suppress_context__


def test_provider_http_error_is_sanitized(monkeypatch):
    """HTTP exceptions neither leak their URL/body nor trigger transport retries."""
    opener, _ = _mock_response(monkeypatch, b"")
    opener.open.side_effect = HTTPError("https://secret.invalid", 401, "private key", {}, io.BytesIO(b"private"))
    with pytest.raises(ModelRequestError, match="^model request failed$"):
        asyncio.run(ChatCompletionHost("http://localhost/v1", "local")([]))
    assert opener.open.call_count == 1


def test_provider_tool_calls_are_not_hidden(monkeypatch):
    """Even forced-finalization calls reach the loop's own rejection policy."""
    calls = [{"id": "synthetic-id", "type": "function",
              "function": {"name": "unavailable", "arguments": "{}"}}]
    _mock_response(monkeypatch, _reply(None, calls))
    assert asyncio.run(ChatCompletionHost("http://localhost/v1", "local")([], False)) == ("", calls)


def test_real_loopback_http_protocol_without_external_model(capsys):
    """Exercise actual stdlib HTTP transport against a local synthetic fixture."""
    requests = []

    class FixtureHandler(BaseHTTPRequestHandler):
        """Serve deterministic protocol data, not an actual model completion."""

        def do_POST(self):
            """Capture the native request and answer with synthetic JSON."""
            body = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append((self.path, json.loads(body)))
            response = _reply("local protocol fixture")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, format, *args):
            """Keep request paths and headers out of the test output."""
            return None

    with ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            assert cli.main(["--base-url", f"http://127.0.0.1:{server.server_port}/v1",
                             "--model", "synthetic", "--system", "fixture persona",
                             "--prompt", "fixture prompt"]) == 0
        finally:
            server.shutdown()
            thread.join(timeout=5)
    assert not thread.is_alive()
    assert requests == [("/v1/chat/completions", {"model": "synthetic", "messages": [
        {"role": "system", "content": "fixture persona"},
        {"role": "user", "content": "fixture prompt"}]})]
    output = json.loads(capsys.readouterr().out)
    assert output["text"] == "local protocol fixture"
    assert output["published"] is False


def test_http_harness_reuses_existing_loop(monkeypatch):
    """Factory produces an actual Harness backed by the synthetic HTTP adapter."""
    _mock_response(monkeypatch, _reply())
    harness = create_http_harness(system="persona", base_url="http://localhost/v1", model="test")
    assert isinstance(harness, Harness)
    assert asyncio.run(harness.run("question")).text == "synthetic answer"


def test_cli_flags_override_env_and_read_only_explicit_persona(monkeypatch, tmp_path, capsys):
    """CLI gives explicit configuration priority and omits transcript from output."""
    persona = tmp_path / "persona.txt"
    persona.write_text("用户提供的人设", encoding="utf-8")
    monkeypatch.setenv("BOT_MODEL_BASE_URL", "http://localhost/env")
    monkeypatch.setenv("BOT_MODEL", "env-model")
    monkeypatch.setenv("TEST_MODEL_KEY", "synthetic-key")
    opener, _ = _mock_response(monkeypatch, _reply())
    code = cli.main(["--base-url", "https://provider.invalid/v1", "--model", "flag-model",
                     "--system-file", str(persona), "--prompt", "", "--api-key-env", "TEST_MODEL_KEY"])
    assert code == 0
    request = opener.open.call_args.args[0]
    body = json.loads(request.data)
    assert body["model"] == "flag-model"
    assert body["messages"][0]["content"] == "用户提供的人设"
    assert request.get_header("Authorization") == "Bearer synthetic-key"
    out = capsys.readouterr()
    assert not out.err
    assert json.loads(out.out) == {"text": "synthetic answer", "used_tools": [],
                                   "published": False, "destination": "local"}


def test_cli_environment_fallback_and_stdin(monkeypatch, capsys):
    """Absent prompt reads all stdin without automatically reading any config."""
    monkeypatch.setenv("BOT_MODEL_BASE_URL", "http://localhost/v1")
    monkeypatch.setenv("BOT_MODEL", "env-model")
    monkeypatch.setattr(sys, "stdin", io.StringIO("first\nsecond\n"))
    opener, _ = _mock_response(monkeypatch, _reply())
    assert cli.main(["--system", "persona"]) == 0
    assert json.loads(opener.open.call_args.args[0].data)["messages"][1]["content"] == "first\nsecond\n"
    assert "transcript" not in json.loads(capsys.readouterr().out)


def test_explicit_host_is_trusted_injection(monkeypatch, capsys):
    """Only an explicitly requested factory loads, and returns the real Harness."""
    async def model(messages, allow_tools=True):
        """Return a synthetic completion without a model request or QQ modules."""
        assert "plugin.main" not in sys.modules
        return "factory response", []

    def factory():
        """Construct the trusted host without automatic registration hooks."""
        return Harness(system="host persona", llm_messages=model)

    module = types.ModuleType("synthetic_cli_host")
    module.create_harness = factory
    monkeypatch.setitem(sys.modules, module.__name__, module)
    assert cli.main(["--host", "synthetic_cli_host:create_harness", "--prompt", "question"]) == 0
    assert json.loads(capsys.readouterr().out)["text"] == "factory response"


@pytest.mark.parametrize("args", [
    [], ["--system", "one", "--system-file", "two"], ["--timeout", "nan"],
    ["--timeout", "0"], ["--max-rounds", "0"], ["--api-key", "accidental-secret"],
    ["--host", "missing:factory", "--model", "explicit"],
    ["--host", "missing:factory", "--timeout", "60"],
    ["--host", "missing:factory", "--system", "explicit"],
    ["--host", "missing:factory"],
])
def test_cli_errors_are_fixed_and_nonzero(monkeypatch, capsys, args):
    """Configuration errors do not echo arguments, credentials, URLs or traces."""
    monkeypatch.delenv("BOT_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("BOT_MODEL", raising=False)
    assert cli.main(args) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "bot_core: configuration or model completion failed\n"


def test_cli_completion_failure_is_not_fake_success(monkeypatch, capsys):
    """Exhausted completion failure remains a nonzero sanitized process result."""
    opener, _ = _mock_response(monkeypatch, b"private-provider-error")
    assert cli.main(["--base-url", "http://localhost/v1", "--model", "test",
                     "--system", "persona", "--prompt", "question"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "private-provider-error" not in output.err
    assert opener.open.call_count == 2  # Existing loop's finalization attempt, not transport retry.


def test_injected_provider_exception_cannot_leak_through_loop_logger(monkeypatch, capsys, caplog):
    """Sanitize trusted-adapter failures too and restore the loop logging state."""
    async def failing(messages, allow_tools=True):
        """Simulate an adapter exception containing sensitive raw details."""
        raise RuntimeError("synthetic-sensitive-provider-credential")

    def factory():
        """Build the explicit operator-selected failing adapter."""
        return Harness(system="persona", llm_messages=failing)

    module = types.ModuleType("synthetic_failing_host")
    module.factory = factory
    monkeypatch.setitem(sys.modules, module.__name__, module)
    logger = logging.getLogger("plugin.agent_loop")
    previous = logger.disabled
    assert cli.main(["--host", "synthetic_failing_host:factory", "--prompt", "q"]) == 1
    assert logger.disabled == previous
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "bot_core: configuration or model completion failed\n"
    assert "synthetic-sensitive-provider-credential" not in caplog.text
