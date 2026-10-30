"""Fresh-interpreter startup smoke for the real full-install setup entry point.

This starts only an owned Python child and loopback HTTP listener. No configuration
is saved and no Docker, provider, browser, or QQ operation is requested.
"""

from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
from urllib.parse import urlsplit

import pytest


@pytest.mark.parametrize("entry", ["bootstrap.py", "native_runtime.py"])
def test_fresh_bootstrap_wizard_no_browser_serves_private_state_without_initializing(tmp_path, entry):
    """The actual script launch emits one capability without requiring package imports."""
    repo = Path(__file__).resolve().parents[1]
    data = tmp_path / "fresh external process state"
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONUNBUFFERED"] = "1"
    # Preexisting port overrides must not make opening setup operate on state.
    environment.pop("BOT_WEB_PORT", None)
    environment.pop("BOT_ONEBOT_PORT", None)
    process = subprocess.Popen(
        [sys.executable, "-B", str(repo / "tools" / entry), "wizard",
         "--no-browser", "--data-dir", str(data), "--runtime-name", "process-wizard"],
        cwd=tmp_path, env=environment, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    output = queue.Queue()

    def read_marker():
        """Capture a single startup line only in memory, never print its capability."""
        try:
            output.put(process.stdout.readline())
        except Exception:
            output.put(b"")

    reader = threading.Thread(target=read_marker, daemon=True)
    reader.start()
    try:
        try:
            line = output.get(timeout=10)
        except queue.Empty:
            raise AssertionError("Fresh setup did not emit its local startup marker") from None
        match = re.fullmatch(rb"BOT_SETUP_URL=(http://127\.0\.0\.1:[0-9]{1,5}/#token=[A-Za-z0-9_-]{40,})\r?\n", line)
        # Use a boolean assertion to keep pytest from echoing the capability on failure.
        assert bool(match), "Fresh setup emitted an invalid or absent startup marker"
        url = match.group(1).decode("ascii")
        parsed = urlsplit(url)
        token = parsed.fragment.removeprefix("token=")
        connection = http.client.HTTPConnection("127.0.0.1", parsed.port, timeout=5)
        try:
            connection.request("GET", "/api/state", headers={"Authorization": "Bearer " + token})
            response = connection.getresponse()
            status = response.status
            raw = response.read()
        finally:
            connection.close()
        assert status == 200
        value = json.loads(raw)
        assert value["deployment_mode"] == ("windows-native" if entry == "native_runtime.py" else "container")
        assert value["initialized"] is False
        assert value["busy"] is False
        assert value["environment"] == "unknown"
        assert value["runtime"] == value["model"] == value["qq"] == "unverified"
        assert not data.exists()
        assert process.poll() is None
        # Even this uninitialized public projection needs the local capability.
        connection = http.client.HTTPConnection("127.0.0.1", parsed.port, timeout=5)
        try:
            connection.request("GET", "/api/state")
            response = connection.getresponse()
            unauthorized = response.status
            response.read()
        finally:
            connection.close()
        assert unauthorized in (401, 403)
    finally:
        # Terminate only the exact Popen child owned by this test, never a process
        # discovered by name, port, pid-file, or another installation's marker.
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        reader.join(timeout=3)
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()
    assert not reader.is_alive()
    assert not data.exists()
