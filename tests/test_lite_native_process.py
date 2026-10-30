"""Real source-only native process and browser HTTP chain, without external services."""
import http.client
import json
import os
import queue
import subprocess
import sys
import threading
from urllib.parse import urlsplit
import zipfile

from tools import build_lite
from test_lite_native import provider


def test_extracted_native_web_process_chats_with_default_persona_and_no_pip(tmp_path):
    """Drive an actual minimal ZIP child through startup/auth/two turns/reset/stop."""
    output = tmp_path / 'lite.zip'
    build_lite.build(output)
    extracted = tmp_path / '轻量 独立源码'
    with zipfile.ZipFile(output) as archive:
        archive.extractall(extracted)
    data = tmp_path / '私有 数据'
    command = [sys.executable, '-S', '-X', 'utf8', '-B', str(extracted / 'tools/lite_native.py')]
    env = {name: value for name, value in os.environ.items() if name not in ('PYTHONPATH', 'PYTHONHOME')}
    with provider() as (base, observations):
        configure = subprocess.run(command + ['configure', '--non-interactive', '--data-dir', str(data),
                                               '--model-base', base, '--model', 'synthetic-only'],
                                   env=env, cwd=tmp_path, capture_output=True, timeout=10)
        assert configure.returncode == 0, configure.stderr
        settings = (data / 'settings.json').read_bytes()
        process = subprocess.Popen(command + ['web', '--no-browser', '--data-dir', str(data)],
                                   env=env, cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, encoding='utf-8')
        lines = queue.Queue()

        def read_lines():
            """Read child startup without an unbounded blocking readline in the test thread."""
            for line in process.stdout:
                lines.put(line)
            lines.put(None)

        reader = threading.Thread(target=read_lines, daemon=True)
        reader.start()
        try:
            startup = []
            for _ in range(10):
                line = lines.get(timeout=15)
                assert line is not None, 'native server exited before printing its local URL'
                startup.append(line)
                if line.startswith('Lite Chat: '):
                    address = urlsplit(line[len('Lite Chat: '):].strip())
                    break
            else:
                raise AssertionError('native startup URL missing')
            assert address.hostname == '127.0.0.1' and len(address.fragment) == 64
            assert base not in ''.join(startup)

            def request(method, path, body=None, authenticate=True):
                """Use real HTTP and keep the synthetic bearer token out of reported evidence."""
                connection = http.client.HTTPConnection(address.hostname, address.port, timeout=10)
                headers = {'Origin': 'http://' + address.netloc}
                if authenticate:
                    headers['Authorization'] = 'Bearer ' + address.fragment
                encoded = None
                if body is not None:
                    encoded = json.dumps(body, ensure_ascii=False).encode('utf-8')
                    headers['Content-Type'] = 'application/json'
                try:
                    connection.request(method, path, body=encoded, headers=headers)
                    response = connection.getresponse()
                    return response.status, response.read()
                finally:
                    connection.close()

            status, html = request('GET', '/', authenticate=False)
            assert status == 200 and '轻聊'.encode('utf-8') in html
            assert address.fragment.encode() not in html
            assert request('POST', '/api/chat', {'prompt': 'not authorized'}, authenticate=False)[0] == 401
            for prompt in ('你好🙂', '第二轮'):
                status, raw = request('POST', '/api/chat', {'prompt': prompt})
                assert status == 200
                assert json.loads(raw)['reply'] == '合成回复🙂'
            assert len(observations) == 2
            assert observations[0][0] == {'role': 'system', 'content': ''}
            assert [message['role'] for message in observations[1]] == ['system', 'user', 'assistant', 'user']
            assert len(json.loads(request('GET', '/api/history')[1])['history']) == 4
            assert request('POST', '/api/reset', {})[0] == 200
            assert json.loads(request('GET', '/api/history')[1])['history'] == []
            assert (data / 'settings.json').read_bytes() == settings
            assert [path.name for path in data.iterdir()] == ['settings.json']
        finally:
            # This child is created exclusively by this test, never an existing runtime.
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()
            errors = process.stderr.read()
            process.stderr.close()
            reader.join(timeout=2)
        assert errors == ''
