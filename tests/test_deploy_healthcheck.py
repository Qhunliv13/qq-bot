"""Offline health-probe contracts; these do not prove real image readiness."""

from contextlib import redirect_stderr, redirect_stdout
from email.message import Message
import http.client
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch
import urllib.error


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SPEC = importlib.util.spec_from_file_location('local_runtime_client', ROOT / 'tools' / 'local_runtime_client.py')
CLIENT = importlib.util.module_from_spec(CLIENT_SPEC)
CLIENT_SPEC.loader.exec_module(CLIENT)
SPEC = importlib.util.spec_from_file_location('deployment_healthcheck', ROOT / 'deploy' / 'healthcheck.py')
HEALTH = importlib.util.module_from_spec(SPEC)
# Mirror the two sibling files copied into /opt/qhunliv without changing sys.path.
with patch.dict(sys.modules, {'local_runtime_client': CLIENT}):
    SPEC.loader.exec_module(HEALTH)
SHARED_REQUEST = HEALTH.request


class HealthcheckTests(unittest.TestCase):
    """Exercise fixed targets and fail-closed responses without network access."""

    def setUp(self):
        """Replace only I/O boundaries; keep actual health-control logic and headers."""
        self.headers = Message()
        self.headers['Content-Type'] = 'text/html; charset=utf-8'
        self.response = MagicMock()
        self.response.status = 200
        self.response.headers = self.headers
        self.response.read.return_value = b'<!DOCTYPE HTML><html>dashboard</html>'
        self.opener = MagicMock()
        self.opener.open.return_value.__enter__.return_value = self.response
        self.request = self.enterContext(patch.object(HEALTH, 'request', return_value={'ok': True, 'transport': 'local'}))
        self.build = self.enterContext(patch.object(HEALTH.urllib.request, 'build_opener', return_value=self.opener))
        self.output = io.StringIO()
        self.errors = io.StringIO()
        self.enterContext(redirect_stdout(self.output))
        self.enterContext(redirect_stderr(self.errors))

    def test_both_fixed_local_probes_are_required(self):
        """Use the real shared protocol function with bounded transport arguments."""
        self.assertIs(HEALTH.request, self.request)
        self.assertEqual(HEALTH.main(), 0)
        self.request.assert_called_once_with('/root/higan/run/local.sock', {'op': 'health'}, timeout=2)
        self.opener.open.assert_called_once_with('http://127.0.0.1:6185/', timeout=2)
        self.response.read.assert_called_once_with(4096)
        proxy, redirect = self.build.call_args.args
        self.assertEqual(proxy.proxies, {})
        self.assertIsInstance(redirect, HEALTH._NoRedirect)
        self.assertIsNone(redirect.redirect_request(None, None, 302, '', {}, 'https://external.invalid'))

    def test_socket_failure_does_not_probe_dashboard_or_disclose_error(self):
        """Do not report partial readiness or include exception text in health output."""
        self.request.side_effect = OSError('private error detail')
        self.assertEqual(HEALTH.main(), 1)
        self.build.assert_not_called()
        self.assertNotIn('private error detail', self.errors.getvalue())

    def test_invalid_local_health_is_rejected(self):
        """Require actual boolean success and the intended local transport."""
        for result in ({'ok': False}, {'ok': 1, 'transport': 'local'}, {'ok': True, 'transport': 'qq'}):
            with self.subTest(result=result):
                self.request.return_value = result
                self.assertEqual(HEALTH.main(), 1)
        self.build.assert_not_called()

    def test_dashboard_errors_fail_closed(self):
        """Refused connections, timeouts and HTTP redirects never count as ready."""
        for error in (TimeoutError(), http.client.BadStatusLine('private'), urllib.error.URLError('private'), urllib.error.HTTPError('http://127.0.0.1:6185/', 302, 'redirect', {}, None)):
            with self.subTest(error=type(error).__name__):
                self.opener.open.side_effect = error
                self.assertEqual(HEALTH.main(), 1)

    def test_dashboard_requires_html_and_success_status(self):
        """Reject JSON authentication/errors and missing static content even on 200."""
        for status, content_type, body in ((503, 'text/html', b'<html>'), (200, 'application/json', b'{}'), (200, 'text/html', b'not the dashboard')):
            with self.subTest(status=status, content_type=content_type):
                self.response.status = status
                self.headers.replace_header('Content-Type', content_type)
                self.response.read.return_value = body
                self.assertEqual(HEALTH.main(), 1)

    def test_shared_client_is_not_reimplemented(self):
        """The deployed module imports exactly the existing local JSONL client."""
        self.assertIs(SHARED_REQUEST, CLIENT.request)

    def test_container_contracts_include_assets_health_and_private_tmpfs(self):
        """Check source contracts without claiming Docker build/mount verification."""
        dockerfile = (ROOT / 'deploy' / 'Dockerfile').read_text(encoding='utf-8')
        compose = (ROOT / 'deploy' / 'compose.yaml').read_text(encoding='utf-8')
        asset = 'astrbot/core/utils/t2i/template/shiki_runtime.iife.js'
        self.assertTrue((ROOT / 'vendor' / 'astrbot' / asset).is_file())
        self.assertIn('COPY vendor/astrbot/' + asset + ' /src/' + asset, dockerfile)
        self.assertIn('COPY deploy/healthcheck.py /opt/qhunliv/healthcheck.py', dockerfile)
        self.assertIn('CMD ["python", "/opt/qhunliv/healthcheck.py"]', dockerfile)
        self.assertIn('--timeout=6s', dockerfile)
        self.assertIn("test: ['CMD', 'python', '/opt/qhunliv/healthcheck.py']", compose)
        self.assertIn('timeout: 6s', compose)
        self.assertIn("- '/root/higan/run:rw,nosuid,nodev,noexec,mode=0700,size=1m'", compose)


if __name__ == '__main__':
    unittest.main()
