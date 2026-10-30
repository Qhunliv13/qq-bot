"""Administrative endpoint overrides remain local and do not expose credentials."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def command_tool():
    """Import the tool without network, credentials or server state."""
    spec = importlib.util.spec_from_file_location('isolated_command_tool', ROOT / 'tools/command_toggle.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_custom_local_port_is_supported(command_tool, monkeypatch):
    """Operators may select their own port without editing source or weakening locality."""
    monkeypatch.setenv('HIGAN_DASHBOARD_URL', 'http://127.0.0.1:26185/')
    assert command_tool.dashboard_base_url() == 'http://127.0.0.1:26185'


@pytest.mark.parametrize('value', [
    'https://example.invalid:443', 'http://example.invalid:6185',
    'http://user:secret@127.0.0.1:6185', 'http://127.0.0.1:6185/other',
    'http://127.0.0.1:6185?key=private', 'http://127.0.0.1:bad',
    'http://localhost:6185', 'http://127.0.0.1',
])
def test_override_never_routes_key_to_external_or_ambiguous_origin(command_tool, monkeypatch, value):
    """Avoid turning configurability into credential exfiltration via host/path injection."""
    monkeypatch.setenv('HIGAN_DASHBOARD_URL', value)
    with pytest.raises(ValueError, match='HIGAN_DASHBOARD_URL'):
        command_tool.dashboard_base_url()


def test_privileged_redirect_is_refused(command_tool):
    """Reject HTTP redirect processing even when the destination claims to be local."""
    assert command_tool._NoRedirect().redirect_request(None, None, 302, '', {}, 'http://example.invalid/') is None
