"""Generic Windows setup accepts real user homes rather than an ASCII username convention."""
import pytest

from test_windows_setup import POWERSHELL, _run_isolated_wizard


@pytest.mark.skipif(not POWERSHELL, reason='PowerShell required')
def test_unicode_linux_home_is_passed_as_one_native_argument(tmp_path):
    """Non-ASCII home names must not require editing the open-source installer."""
    home = '/home/\u7528\u6237 \u6d4b\u8bd5'
    result, calls, launch, _, _ = _run_isolated_wizard(tmp_path, linux_home=home)
    assert result.returncode == 0, result.stderr
    assert launch['DataDir'] == home + '/.local/share/qhunliv-bot'
    assert len(calls) == 5


@pytest.mark.skipif(not POWERSHELL, reason='PowerShell required')
@pytest.mark.parametrize('home', ['relative/user', '/home/../root', '/home/user;command', '/home/$USER'])
def test_unsupported_home_fails_before_privileged_prerequisites(tmp_path, home):
    """Broader Unicode support does not permit traversal or ambiguous shell-like paths."""
    result, calls, launch, _, _ = _run_isolated_wizard(tmp_path, linux_home=home)
    assert result.returncode == 2
    assert len(calls) == 3
    assert launch is None
