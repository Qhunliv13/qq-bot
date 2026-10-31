"""Offline exact-wire client bounds without real sockets, accounts or model calls."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from plugin.local_runtime import MAX_REQUEST_BYTES
from tools import local_runtime_client as client


@pytest.mark.parametrize("windows", [False, True])
@pytest.mark.parametrize("piece", ["a", "汉", "😀", "\x00", '"', "\\", "\n"])
def test_exact_wire_limit_includes_utf8_escaping_and_native_token(monkeypatch, windows, piece):
    """Exactly 16 KiB succeeds; one additional byte fails before socket construction."""
    assert client.MAX_REQUEST_BYTES == MAX_REQUEST_BYTES == 16 * 1024

    def document(prompt):
        """Use the real envelope helper so tests do not duplicate its implementation."""
        payload = {"op": "run", "prompt": prompt}
        return client._authenticated_request(payload, "f" * 64) if windows else payload

    overhead = len(client.encode_request(document("")))
    unit = len(client.encode_request(document(piece))) - overhead
    count, padding = divmod(MAX_REQUEST_BYTES - overhead, unit)
    prompt = piece * count + "a" * padding
    payload = {"op": "run", "prompt": prompt}
    wire = client.encode_request(document(prompt))
    assert len(wire) == MAX_REQUEST_BYTES and wire.endswith(b"\n")
    assert wire.count(b"\n") == 1  # Embedded controls must remain JSON escapes.
    assert json.loads(wire) == document(prompt)
    client.validate_request_size(payload, windows=windows)
    with pytest.raises(ValueError, match="^request_too_large$"):
        client.validate_request_size({"op": "run", "prompt": prompt + "a"}, windows=windows)
    sent = []

    class Connection:
        """Capture bytes from the real exchange while preventing actual I/O."""

        def __enter__(self):
            """Provide the socket context used by the production exchange."""
            return self

        def __exit__(self, *args):
            """No real socket was acquired and no cleanup is necessary."""

        def settimeout(self, value):
            """Accept the tested timeout without starting a timer."""

        def connect(self, address):
            """Keep the exchange entirely offline."""

        def sendall(self, encoded):
            """Observe the unchanged exact wire document sent once."""
            sent.append(encoded)

        def makefile(self, mode):
            """Return a bounded deterministic local response in the normal framing."""
            return io.BytesIO(b'{"ok": true}\n')

    # Windows Python may not expose AF_UNIX; only the inert family selector is
    # substituted here, while production encoding/exchange remain under test.
    if not windows:
        monkeypatch.setattr(client.socket, "AF_UNIX", -1, raising=False)
    monkeypatch.setattr(client.socket, "socket", lambda *args: Connection())
    assert client._exchange("unused", document(prompt), timeout=1, windows=windows) == {"ok": True}
    assert sent == [wire]

    def forbidden(*args):
        """Oversized data must fail before creating or connecting a socket."""
        raise AssertionError("unexpected socket")

    monkeypatch.setattr(client.socket, "socket", forbidden)
    with pytest.raises(ValueError, match="^request_too_large$"):
        client._exchange("unused", document(prompt + "a"), timeout=1, windows=windows)


def test_unix_validation_does_not_reserve_native_envelope_bytes():
    """The UI target, not its host OS, decides whether native token overhead applies."""
    overhead = len(client.encode_request({"op": "run", "prompt": ""}))
    payload = {"op": "run", "prompt": "a" * (MAX_REQUEST_BYTES - overhead)}
    client.validate_request_size(payload, windows=False)
    with pytest.raises(ValueError, match="^request_too_large$"):
        client.validate_request_size(payload, windows=True)


def test_native_request_reuses_same_codec_and_envelope(monkeypatch):
    """Identity preflight and the real turn use the sole bounded wire implementation."""
    endpoint = {"token": "a" * 64, "nonce": "b" * 64, "port": 12345,
                "pid": 77, "instance": str(Path.cwd())}
    monkeypatch.setattr(client.sys, "platform", "win32")
    monkeypatch.setattr(client, "read_windows_endpoint", lambda path: endpoint)
    payload = {"op": "run", "prompt": '汉😀\n"\\'}
    encoded = []

    def exchange(address, document, **kwargs):
        """Record encoding without bypassing the exact production codec."""
        encoded.append(client.encode_request(document))
        if len(encoded) == 1:
            return {"ok": True, "transport": "local", "pid": endpoint["pid"],
                    "nonce": endpoint["nonce"],
                    "instance_hash": client.windows_instance_hash(endpoint["instance"])}
        return {"ok": True}

    monkeypatch.setattr(client, "_exchange", exchange)
    assert client.request("unused", payload) == {"ok": True}
    assert len(encoded) == 2
    assert json.loads(encoded[-1]) == client._authenticated_request(payload, endpoint["token"])
    client.validate_request_size(payload, windows=True)
