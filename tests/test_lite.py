"""Offline contract tests for the portable text-only CLI."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from bot_core import http_host
from tools import lite


class _Response:
    """Minimal context manager for the host's urllib response boundary."""

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        """Return the bounded response reader without opening a socket."""
        return self

    def __exit__(self, *args):
        """Do not suppress exceptions from the real host parser."""
        return False

    def read(self, limit):
        """Respect the host's maximum response read size."""
        return self.body[:limit]


class _Opener:
    """Capture outgoing native messages and answer without networking."""

    def __init__(self, transcript, replies):
        self.transcript = transcript
        self.replies = replies

    def open(self, request, timeout):
        """Snapshot payload and headers to assert no tool schemas or leaks."""
        self.transcript.append((json.loads(request.data.decode("utf-8")),
                                request.get_header("Authorization")))
        return _Response(json.dumps({"choices": [{"message": self.replies.pop(0)}]}).encode("utf-8"))


class LiteTests(unittest.TestCase):
    """Cover the new user-facing flow with no provider calls."""

    def setUp(self):
        """Fix benign local defaults for reproducible config tests."""
        self.env = patch.dict(os.environ, {"BOT_MODEL_BASE_URL": "http://127.0.0.1:3333/v1",
                                        "BOT_MODEL": "fake-model",
                                        "BOT_MODEL_API_KEY": "not-a-real-secret"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def invoke(self, argv, replies=None, stdin=""):
        """Patch the actual HTTP host opener while capturing plain CLI streams."""
        sent, out, err = [], io.StringIO(), io.StringIO()
        opener = _Opener(sent, list(replies or []))
        with patch.object(http_host, "build_opener", return_value=opener), \
             patch.object(lite.sys, "stdin", io.StringIO(stdin)), \
             redirect_stdout(out), redirect_stderr(err):
            status = lite.main(argv)
        return status, out.getvalue(), err.getvalue(), sent

    def test_one_shot_uses_host_and_drops_separate_reasoning(self):
        """Verify UTF-8 prompt and persona, native host path and no tool schema."""
        status, out, err, sent = self.invoke(
            ["--system", "Be neutral", "--prompt", "你好"],
            [{"role": "assistant", "content": "公开答案", "reasoning_content": "PRIVATE"}])
        self.assertEqual((status, out, err), (0, "公开答案\n", ""))
        self.assertEqual(sent[0][0], {"model": "fake-model", "messages": [
            {"role": "system", "content": "Be neutral"},
            {"role": "user", "content": "你好"}]})
        self.assertEqual(sent[0][1], "Bearer not-a-real-secret")
        self.assertNotIn("PRIVATE", out)

    def test_interactive_bounded_history_and_new_process_separation(self):
        """Check recent successful pairs only; fresh invocations cannot reuse context."""
        args = ["--system", "Persona", "--interactive", "--history-turns", "2"]
        replies = [{"role": "assistant", "content": f"a{i}"} for i in range(1, 5)]
        status, out, err, sent = self.invoke(args, replies, "u1\nu2\nu3\nu4\n/exit\n")
        self.assertEqual((status, out, err), (0, "a1\na2\na3\na4\n", ""))
        self.assertEqual([item["content"] for item in sent[3][0]["messages"]],
                         ["Persona", "u2", "a2", "u3", "a3", "u4"])
        status, out, err, second = self.invoke(args,
            [{"role": "assistant", "content": "new"}], "other\n")
        self.assertEqual(status, 0)
        self.assertEqual(second[0][0]["messages"][-1]["content"], "other")
        self.assertEqual(len(second[0][0]["messages"]), 2)

    def test_stdin_and_explicit_utf8_system_file(self):
        """Read only caller-selected persona file and piped one-shot text."""
        with tempfile.TemporaryDirectory() as folder:
            persona = Path(folder) / "persona.txt"
            persona.write_text("温和", encoding="utf-8")
            status, out, err, sent = self.invoke(
                ["--system-file", str(persona)],
                [{"role": "assistant", "content": "ok"}], "piped prompt\n")
        self.assertEqual((status, out, err), (0, "ok\n", ""))
        self.assertEqual(sent[0][0]["messages"][0]["content"], "温和")
        self.assertEqual(sent[0][0]["messages"][1]["content"], "piped prompt\n")

    def test_tool_calls_refused_and_no_history_from_failure(self):
        """Reject provider tool-call attempts, even with harmless-looking text."""
        status, out, err, _ = self.invoke(["--system", "S", "--prompt", "P"],
            [{"role": "assistant", "content": "hi",
              "tool_calls": [{"id": "attempt", "type": "function"}]}])
        self.assertEqual(status, 1)
        self.assertEqual(out, "")
        self.assertEqual(err, lite._ERROR + "\n")

    def test_invalid_configuration_and_provider_error_hide_secrets(self):
        """Do not echo accidental arguments, provider errors or key values."""
        for argv in (["--system", "x", "--history-turns", "0"],
                     ["--system", "x", "--api-key-env", "KEY=leaked"],
                     ["--system", "x", "--prompt", "p",
                      "--base-url", "http://remote.example/v1"]):
            with self.subTest(argv=argv):
                status, out, err, sent = self.invoke(argv)
                self.assertEqual((status, out, err, sent),
                                 (1, "", lite._ERROR + "\n", []))
        with patch.object(http_host, "build_opener", side_effect=RuntimeError("secret=PRIVATE")), \
             redirect_stderr(io.StringIO()) as stderr, redirect_stdout(io.StringIO()) as stdout:
            status = lite.main(["--system", "S", "--prompt", "p"])
        self.assertEqual((status, stdout.getvalue(), stderr.getvalue()),
                         (1, "", lite._ERROR + "\n"))

    def test_script_direct_execution_with_no_extra_dependencies(self):
        """Exercise real script imports on Windows or POSIX without network."""
        script = Path(lite.__file__).resolve()
        result = subprocess.run([sys.executable, str(script), "--system", "S",
                                 "--prompt", "hello", "--base-url", "http://evil.invalid/v1"],
                                cwd=script.parent.parent, capture_output=True, text=True,
                                encoding="utf-8", timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, lite._ERROR + "\n")


if __name__ == "__main__":
    unittest.main()
