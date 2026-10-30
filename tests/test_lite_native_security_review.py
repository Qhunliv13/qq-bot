"""Focused regression cases proven by independent review of native-lite integration."""

import http.client
import json
import threading
from types import SimpleNamespace

import pytest

from tools import lite_native as native
from tools import lite_web


def _args(**overrides):
    """Provide explicit offline local configuration without a real model service."""
    return SimpleNamespace(**(dict(model_base="http://127.0.0.1:18080/v1", model="synthetic-only",
                                   api_key_env=None, persona_file=None, non_interactive=True) | overrides))


def test_default_native_persona_can_create_real_web_server(tmp_path, monkeypatch):
    """Default setup launches actual run_web and replies over local HTTP with no persona."""
    value = native.configure(tmp_path, _args())
    assert value["system"] == ""
    received = []

    async def local_host(self, messages, allow_tools=True):
        """Return synthetic public text through the real existing _reply boundary."""
        received.append((messages, allow_tools))
        return "synthetic reply", []

    def exercise_then_interrupt(server, poll_interval=0.2):
        """Serve one real HTTP request then emulate Ctrl+C in the main serving thread."""
        server.timeout = 5
        worker = threading.Thread(target=server.handle_request, daemon=True)
        worker.start()
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        try:
            connection.request("POST", "/api/chat", body=json.dumps({"prompt": "hello"}),
                               headers={"Authorization": "Bearer " + server.token,
                                        "Content-Type": "application/json"})
            response = connection.getresponse()
            assert response.status == 200
            assert json.loads(response.read())["reply"] == "synthetic reply"
        finally:
            connection.close()
            worker.join(timeout=5)
        raise KeyboardInterrupt

    monkeypatch.setattr(native.ChatCompletionHost, "__call__", local_host)
    monkeypatch.setattr(lite_web._ChatServer, "serve_forever", exercise_then_interrupt)
    assert native.main(["web", "--data-dir", str(tmp_path), "--no-browser"]) == 0
    assert received == [([{"role": "system", "content": ""},
                          {"role": "user", "content": "hello"}], False)]


def test_switch_local_keyless_config_to_https_cannot_save_unusable_credentials(tmp_path):
    """A local-to-HTTPS edit without a key must preserve the last usable settings."""
    original = native.configure(tmp_path, _args())
    before = (tmp_path / "settings.json").read_bytes()
    assert original["credential_storage"] == "environment"
    assert original["api_key_env"] is None
    with pytest.raises(ValueError):
        native.configure(tmp_path, _args(model_base="https://synthetic.invalid/v1"), original)
    assert (tmp_path / "settings.json").read_bytes() == before
