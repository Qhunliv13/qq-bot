"""Real PowerShell entry bindings with fake WSL and browser edges only."""
import pytest

from test_windows_launcher import POWERSHELL, _invoke_mock_wsl


@pytest.mark.skipif(POWERSHELL is None, reason='PowerShell is not installed')
@pytest.mark.parametrize('command,tail', [
    ('info', ['info']), ('info -ShowPassword', ['info', '--show-password']), ('stop', ['stop']),
])
def test_info_and_stop_forward_to_single_bootstrap(tmp_path, command, tail):
    """Both thin Windows entry layers expose shared commands without another implementation."""
    result, calls = _invoke_mock_wsl(tmp_path, command, root=True)
    assert result.returncode == 0, result.stderr
    assert calls[-1][-len(tail):] == tail


@pytest.mark.skipif(POWERSHELL is None, reason='PowerShell is not installed')
def test_password_flag_cannot_attach_to_up(tmp_path):
    """Do not accidentally disclose a password on an ordinary start or status."""
    result, calls = _invoke_mock_wsl(tmp_path, 'up -ShowPassword', root=True)
    assert result.returncode == 2
    assert not any('tools/bootstrap.py' in call for call in calls)


@pytest.mark.skipif(POWERSHELL is None, reason='PowerShell is not installed')
def test_browser_uses_saved_linux_port_not_windows_default(tmp_path):
    """Get a secret-free origin from the same selected WSL state after successful health."""
    result, calls = _invoke_mock_wsl(tmp_path, 'up -OpenDashboard -Distro Debian -User root -DataDir /home/user/bot-state -RuntimeName personal', root=True)
    assert result.returncode == 0, result.stderr
    assert calls[-1][-6:] == ['info', '--json', '--data-dir', '/home/user/bot-state', '--runtime-name', 'personal']
    assert calls[-1][:4] == ['--distribution', 'Debian', '--user', 'root']
    assert (tmp_path / 'browser.txt').read_text() == 'http://127.0.0.1:26185'
    assert '--show-password' not in repr(calls)


@pytest.mark.skipif(POWERSHELL is None, reason='PowerShell is not installed')
def test_browser_refuses_nonlocal_info_without_masking_success(tmp_path):
    """A corrupted machine-info response cannot open arbitrary URLs or invent install failure."""
    result, calls = _invoke_mock_wsl(tmp_path, 'up -OpenDashboard', root=True, dashboard_url='https://example.invalid')
    assert result.returncode == 0
    assert not (tmp_path / 'browser.txt').exists()
    assert 'browser could not open' in result.stderr
