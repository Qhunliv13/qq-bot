"""Real Windows PowerShell-to-native-lite integration against an offline loopback model."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


# Native Windows PowerShell 5.1, not pwsh, is the compatibility target of these root launchers.
ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell.exe") if os.name == "nt" else None


def ps_literal(value):
    """Quote only controlled test paths as PowerShell literals, never shell commands."""
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(POWERSHELL, "Real Windows PowerShell 5.1 integration")
class WindowsLiteIntegrationTests(unittest.TestCase):
    """Configure and chat through the actual root PS entry with no mocked runtime layers."""

    def test_root_configuration_and_unicode_multiturn_chat(self):
        """Exercise real stdin, persisted settings, reply core and provider role continuity."""
        requests = []
        failures = []

        class Provider(BaseHTTPRequestHandler):
            """Serve only synthetic local completion requests, without logging private content."""

            def log_message(self, *unused):
                """Keep request text and authorization data out of test output."""

            def do_POST(self):
                """Record the real portable host request and return a Unicode assistant reply."""
                # Record protocol errors explicitly: assertions in request threads could be lost.
                if self.path != "/v1/chat/completions":
                    failures.append("unexpected request path")
                value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(value)
                body = json.dumps({"choices": [{"message": {
                    "role": "assistant", "content": "合成回复🙂"}}]}, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="lite-native-ps-") as temporary:
                work = Path(temporary)
                data = work / "外部 配置 中文"
                base = "http://127.0.0.1:" + str(server.server_port) + "/v1"
                entry = ps_literal(ROOT / "lite.ps1")
                configure = work / "configure.ps1"
                # BOM is deliberate: Windows PowerShell 5.1 otherwise reads UTF-8 source as ANSI.
                configure.write_text(
                    "$ErrorActionPreference = 'Stop'\n"
                    "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)\n"
                    f"& {entry} configure -NonInteractive -ModelBase {ps_literal(base)} "
                    f"-Model synthetic-only -DataDir {ps_literal(data)}\n"
                    "exit $LASTEXITCODE\n", encoding="utf-8-sig")
                initialized = subprocess.run(
                    [POWERSHELL, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(configure)],
                    cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=20, check=False,
                )
                self.assertEqual(initialized.returncode, 0, initialized.stderr)
                settings = data / "settings.json"
                before = settings.read_bytes()
                self.assertEqual(requests, [])
                chat_script = work / "chat.ps1"
                chat_script.write_text(
                    "$ErrorActionPreference = 'Stop'\n"
                    "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)\n"
                    f"@('你好🙂', '第二轮', '/exit') | & {entry} chat -DataDir {ps_literal(data)}\n"
                    "exit $LASTEXITCODE\n", encoding="utf-8-sig")
                chat = subprocess.run(
                    [POWERSHELL, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(chat_script)],
                    cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=20, check=False,
                )
                self.assertEqual(chat.returncode, 0, chat.stderr)
                self.assertEqual(chat.stdout.count("合成回复🙂"), 2)
                self.assertEqual(failures, [])
                self.assertEqual(len(requests), 2)
                self.assertEqual(requests[0]["model"], "synthetic-only")
                self.assertEqual(requests[0]["messages"][-1]["content"], "你好🙂")
                self.assertEqual([item["role"] for item in requests[1]["messages"]],
                                 ["system", "user", "assistant", "user"])
                self.assertEqual(requests[1]["messages"][-1]["content"], "第二轮")
                self.assertEqual(requests[1]["messages"][-2]["content"], "合成回复🙂")
                self.assertEqual(settings.read_bytes(), before)
                self.assertEqual(sorted(path.name for path in data.iterdir()), ["settings.json"])
        finally:
            # Stop only the loopback provider created here, including after a timed-out subprocess.
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
