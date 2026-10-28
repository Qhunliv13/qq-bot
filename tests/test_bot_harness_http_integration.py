"""Actual local HTTP/CLI transport exercise; the responder is NOT a real model."""
import json
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def test_cli_uses_shared_loop_through_a_local_http_fixture():
    """Exercise the real entrypoint and socket without QQ, credentials or remote IO."""
    requests = []

    class FixtureHandler(BaseHTTPRequestHandler):
        """Return a synthetic protocol completion, not evidence of model capability."""

        def do_POST(self):
            """Capture request shape and return a standards-shaped local fixture."""
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append({'path': self.path, 'body': body, 'authorization': self.headers.get('Authorization')})
            result = {'choices': [{'message': {'role': 'assistant', 'content': '本地 HTTP 传输验证完成'}}]}
            response = json.dumps(result, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, format, *args):
            """Keep test-only server logs out of CLI output assertions."""
            return

    server = ThreadingHTTPServer(('127.0.0.1', 0), FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = 'http://127.0.0.1:' + str(server.server_port) + '/v1'
        completed = subprocess.run(
            [sys.executable, '-m', 'bot_core', '--base-url', base, '--model', 'fixture-only',
             '--api-key-env', 'HARNESS_FIXTURE_NO_KEY', '--system', '合成测试，不执行工具',
             '--prompt', '验证调用，不发送 QQ'],
            cwd=Path(__file__).resolve().parents[1], capture_output=True,
            text=True, encoding='utf-8', timeout=15, check=False)
        assert completed.returncode == 0, completed.stderr
        result = json.loads(completed.stdout)
        assert result == {'text': '本地 HTTP 传输验证完成', 'used_tools': [],
                          'published': False, 'destination': 'local'}
        assert len(requests) == 1
        assert requests[0]['path'] == '/v1/chat/completions'
        assert requests[0]['body']['model'] == 'fixture-only'
        assert [item['role'] for item in requests[0]['body']['messages']] == ['system', 'user']
        assert requests[0]['authorization'] is None
        assert 'tools' not in requests[0]['body']
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
