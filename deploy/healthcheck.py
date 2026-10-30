"""Probe local plugin intake and bundled dashboard, never model/tool readiness."""

import http.client
import sys
import urllib.request

from local_runtime_client import request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Keep deployment probes on the fixed local endpoint, including on redirects."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Refuse redirects rather than probing a different or external service."""
        return None


def main():
    """Require both local intake and a bounded HTML response from the dashboard.

    The two independent two-second I/O timeouts leave process-start overhead
    inside Docker's six-second timeout. Docker remains the overall deadline.
    Neither successful probe certifies provider credentials or tool dependencies.
    """
    try:
        health = request('/root/higan/run/local.sock', {'op': 'health'}, timeout=2)
        if health.get('ok') is not True or health.get('transport') != 'local':
            raise ValueError('local intake is not ready')
        # Explicitly ignore proxy environment variables and never follow redirects.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        with opener.open('http://127.0.0.1:6185/', timeout=2) as response:
            if response.status != 200 or response.headers.get_content_type() != 'text/html':
                raise ValueError('dashboard is not serving HTML')
            prefix = response.read(4096).lower()
            if b'<!doctype html' not in prefix and b'<html' not in prefix:
                raise ValueError('dashboard HTML is missing')
    except (OSError, ValueError, http.client.HTTPException):
        # Health output must not disclose credentials, response bodies or raw errors.
        print('local intake or dashboard is not ready', file=sys.stderr)
        return 1
    print('local intake and dashboard are ready; model and tools not probed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
