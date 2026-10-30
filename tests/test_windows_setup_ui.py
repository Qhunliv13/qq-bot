"""Offline desktop-adapter and capability URL tests; never install or open real UI."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which('powershell.exe') or shutil.which('pwsh')
pytestmark = pytest.mark.skipif(not POWERSHELL, reason='PowerShell required')
TOKEN = 'test_only_capability_' + 'a' * 32


def _run_script(tmp_path, text):
    """Execute a BOM-encoded isolated harness so PowerShell 5.1 handles Chinese paths."""
    runner = tmp_path / 'runner.ps1'
    runner.write_text(text, encoding='utf-8-sig')
    return subprocess.run(
        [POWERSHELL, '-NoLogo', '-NoProfile', '-NonInteractive', '-File', str(runner)],
        capture_output=True, text=True, timeout=30, check=False,
    )


def _mock_graphical_setup(tmp_path, *, missing_wsl=False, consent=False,
                          prerequisite_exit=0, launch_exit=0, check_only=False,
                          welcome=True, distro_index=1):
    """Run the real setup with a presentation-only adapter and fake WSL/package actions."""
    checkout = tmp_path / 'checkout 测试'
    tools = checkout / 'tools'
    tools.mkdir(parents=True)
    shutil.copy2(ROOT / 'tools/windows-setup.ps1', tools / 'windows-setup.ps1')
    events = tmp_path / 'events.jsonl'
    launch = tmp_path / 'launch.json'
    # Fake only UI presentation: real setup orchestration and all guard branches still execute.
    (tools / 'windows-setup-ui.ps1').write_text(
        f'function Show-SetupUiWelcome {{ Record-Event welcome; return ${str(welcome).lower()} }}\n'
        f'function Confirm-SetupUiAction {{ param($Message); Record-Event consent; return ${str(consent).lower()} }}\n'
        f'function Select-SetupUiDistribution {{ param($Distributions); Record-Event select; return {distro_index} }}\n'
        'function Show-SetupUiOutcome { param($ExitCode); Record-Event ("outcome:" + $ExitCode) }\n',
        encoding='ascii',
    )
    (tools / 'windows.ps1').write_text(
        'param([string]$Command,[string]$Distro,[string]$User,[string]$DataDir)\n'
        f"[System.IO.File]::WriteAllText('{launch.as_posix()}', (ConvertTo-Json -Compress -InputObject $PSBoundParameters))\n"
        f'exit {launch_exit}\n', encoding='utf-8-sig',
    )
    source = (
        f"function Record-Event {{ param($Value); [System.IO.File]::AppendAllText('{events.as_posix()}', (ConvertTo-Json -Compress -InputObject $Value) + [Environment]::NewLine) }}\n"
        'function Read-Host { throw "graphical mode must not read terminal input" }\n'
        'function Start-Process { Record-Event install-wsl; return [pscustomobject]@{ExitCode=0} }\n'
        'function wsl.exe {\n'
        '  $global:LASTEXITCODE = 0; Record-Event @($args)\n'
        "  if ($args -contains '--verbose') { '* Ubuntu Stopped 2'; ' Debian Stopped 2' }\n"
        "  elseif ($args -contains '--list') { 'Ubuntu'; 'Debian' }\n"
        "  elseif ($args -contains 'HOME') { '/home/default user' }\n"
        "  elseif ($args -contains 'wslpath') { '/mnt/c/checkout 测试' }\n"
        "  elseif ($args -contains '--install') { $global:LASTEXITCODE = 0 }\n"
        f'  else {{ $global:LASTEXITCODE = {prerequisite_exit} }}\n'
        '}\n'
        + ('function Get-Command { return $null }\n' if missing_wsl else '')
        + f"& '{(tools / 'windows-setup.ps1').as_posix()}' -Graphical {'-CheckOnly' if check_only else ''}\nexit $LASTEXITCODE\n"
    )
    result = _run_script(tmp_path, source)
    calls = [json.loads(line) for line in events.read_text(encoding='utf-8-sig').splitlines()] if events.exists() else []
    launched = json.loads(launch.read_text(encoding='utf-8-sig')) if launch.exists() else None
    return result, calls, launched


def test_double_click_is_sta_graphical_and_ui_has_no_install_implementation():
    """Only the desktop adapter owns Chinese prompts, not another package installer."""
    entry = (ROOT / 'install.cmd').read_text(encoding='utf-8')
    assert '-STA' in entry and 'windows-native.ps1' in entry
    assert 'windows-setup.ps1' not in entry  # WSL is now an explicit compatibility path.
    ui_path = ROOT / 'tools/windows-setup-ui.ps1'
    assert ui_path.read_bytes().startswith(b'\xef\xbb\xbf')
    ui = ui_path.read_text(encoding='utf-8-sig')
    for term in ('欢迎', 'WSL2', 'Python', 'Docker', 'YesNo', 'Button2', 'DropDownList'):
        assert term in ui
    for forbidden in ('Read-Host', 'wsl.exe', 'apt-get', 'Start-Process', 'bootstrap.py'):
        # Comments document the boundary and are not executable logic.
        assert forbidden not in '\n'.join(line for line in ui.splitlines() if not line.lstrip().startswith('#'))


def test_desktop_scripts_parse_in_windows_powershell_without_execution(tmp_path):
    """Validate real Chinese UI source in the same 5.1 parser used by double-click."""
    scripts = ['windows-setup.ps1', 'windows-setup-ui.ps1', 'windows.ps1']
    paths = ', '.join(f"'{(ROOT / 'tools' / name).as_posix()}'" for name in scripts)
    result = _run_script(tmp_path,
        f'foreach ($path in @({paths})) {{\n'
        ' $tokens = $null; $errors = $null\n'
        ' [void][System.Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$errors)\n'
        ' if ($errors.Count) { exit 1 }\n'
        '}\nexit 0\n',
    )
    assert result.returncode == 0, result.stderr


def test_graphical_selects_distro_and_opens_wizard_with_same_instance(tmp_path):
    """The novice branch never invokes up, terminal configuration or a second runtime."""
    result, calls, launch = _mock_graphical_setup(tmp_path)
    assert result.returncode == 0, result.stderr
    assert calls[0] == 'welcome' and 'select' in calls and calls[-1] == 'outcome:0'
    assert launch == {
        'Command': 'wizard', 'Distro': 'Debian', 'User': 'root',
        'DataDir': '/home/default user/.local/share/qhunliv-bot',
    }
    assert '--install' not in repr(calls)


@pytest.mark.parametrize('options,expected_calls', [({'welcome': False}, 2), ({'distro_index': -1}, 5)])
def test_graphical_close_cancels_before_root_commands(tmp_path, options, expected_calls):
    """Closing either welcome or distro selection never starts prerequisite installation."""
    result, calls, launch = _mock_graphical_setup(tmp_path, **options)
    assert result.returncode == 2, result.stderr
    assert len(calls) == expected_calls and launch is None
    assert '--user' not in repr(calls) and '--install' not in repr(calls)


def test_graphical_package_consent_reuses_helper_and_launch_exit(tmp_path):
    """A single explicit approval uses the shared helper rather than another installer."""
    result, calls, launch = _mock_graphical_setup(
        tmp_path, consent=True, prerequisite_exit=10, launch_exit=9,
    )
    assert result.returncode == 9, result.stderr
    installations = [call for call in calls if isinstance(call, list) and '--install' in call]
    assert len(installations) == 1
    assert installations[0][-2:] == ['/mnt/c/checkout 测试/tools/windows-prerequisites.sh', '--install']
    assert launch['Command'] == 'wizard' and calls[-1] == 'outcome:9'


@pytest.mark.parametrize('consent,expected', [(False, 2), (True, 3)])
def test_missing_wsl_welcomes_and_asks_before_installing(tmp_path, consent, expected):
    """Even without WSL/Python, desktop consent precedes any Windows installation request."""
    result, calls, launch = _mock_graphical_setup(tmp_path, missing_wsl=True, consent=consent)
    assert result.returncode == expected, result.stderr
    assert calls[:2] == ['welcome', 'consent']
    assert ('install-wsl' in calls) is consent
    assert launch is None


@pytest.mark.parametrize('prerequisite_exit', [10, 11, 12])
def test_graphical_package_refusal_stops_before_install_and_wizard(tmp_path, prerequisite_exit):
    """Missing Linux prerequisites explain and require consent without terminal Read-Host."""
    result, calls, launch = _mock_graphical_setup(tmp_path, prerequisite_exit=prerequisite_exit)
    assert result.returncode == 2, result.stderr
    assert 'consent' in calls and calls[-1] == 'outcome:2'
    assert '--install' not in repr(calls) and launch is None


def test_graphical_checkonly_does_not_load_or_prompt_ui(tmp_path):
    """Combining Graphical and CheckOnly preserves the original pure preflight contract."""
    result, calls, launch = _mock_graphical_setup(tmp_path, check_only=True)
    assert result.returncode == 2, result.stderr  # Multiple distributions require a later choice.
    assert len(calls) == 2 and all(isinstance(call, list) for call in calls)
    assert launch is None


def _mock_browser_launcher(tmp_path, marker, *, launch_exit=0, browser_fails=False, root=False):
    """Stream the actual launcher against a fake WSL, recording only local test capabilities."""
    calls = tmp_path / 'calls.jsonl'
    browser = tmp_path / 'browser.txt'
    entry = ROOT / 'install.ps1' if root else ROOT / 'tools/windows.ps1'
    result = _run_script(tmp_path,
        'function wsl.exe {\n'
        ' $global:LASTEXITCODE = 0\n'
        f" [System.IO.File]::AppendAllText('{calls.as_posix()}', (ConvertTo-Json -Compress -InputObject @($args)) + [Environment]::NewLine)\n"
        " if ($args -contains 'wslpath') { '/mnt/c/checkout 测试' }\n"
        " elseif ($args -contains 'tools/bootstrap.py') {\n"
        f"  '{marker}'\n"
        "  'safe foreground status'\n"
        f'  $global:LASTEXITCODE = {launch_exit}\n'
        ' }\n}\n'
        'function Start-Process { param($FilePath);\n'
        + (' throw "do not leak URL"\n' if browser_fails else
           f" [System.IO.File]::WriteAllText('{browser.as_posix()}', $FilePath)\n")
        + '}\n'
        + f"& '{entry.as_posix()}' wizard -Distro Debian -User root -DataDir '/home/default user/bot'\nexit $LASTEXITCODE\n",
    )
    arguments = [json.loads(line) for line in calls.read_text(encoding='utf-8-sig').splitlines()]
    return result, arguments, browser.read_text(encoding='utf-8-sig') if browser.exists() else None


@pytest.mark.parametrize('root', [False, True])
def test_wizard_streams_url_to_windows_browser_and_never_public_stdout(tmp_path, root):
    """Use the exact selected instance and preserve foreground exit without logging the token."""
    marker = f'BOT_SETUP_URL=http://127.0.0.1:65535/#token={TOKEN}'
    result, calls, browser = _mock_browser_launcher(tmp_path, marker, launch_exit=7, root=root)
    assert result.returncode == 7, result.stderr
    assert browser == marker.removeprefix('BOT_SETUP_URL=')
    assert TOKEN not in result.stdout + result.stderr
    assert 'BOT_SETUP_URL=' not in result.stdout + result.stderr
    assert 'safe foreground status' in result.stdout
    assert calls[-1] == [
        '--distribution', 'Debian', '--user', 'root', '--cd', '/mnt/c/checkout 测试',
        '--exec', 'python3', 'tools/bootstrap.py', 'wizard',
        '--data-dir', '/home/default user/bot', '--no-browser',
    ]


@pytest.mark.parametrize('url', [
    'http://127.0.0.1:0/#token=' + TOKEN,
    'http://127.0.0.1:65536/#token=' + TOKEN,
    'http://localhost:8080/#token=' + TOKEN,
    'https://127.0.0.1:8080/#token=' + TOKEN,
    'http://example.invalid:8080/#token=' + TOKEN,
    'http://127.0.0.1:8080/evil#token=' + TOKEN,
    'http://127.0.0.1:8080/#token=short',
    'http://127.0.0.1:8080/#token=' + TOKEN + '?extra=1',
    'http://127.0.0.1:8080/#token=' + TOKEN + ' ',
])
def test_wizard_rejects_untrusted_url_without_launch_or_token_echo(tmp_path, url):
    """Reject every variant outside the strict loopback/port/URL-safe token contract."""
    result, _, browser = _mock_browser_launcher(tmp_path, 'BOT_SETUP_URL=' + url)
    assert result.returncode == 0, result.stderr
    assert browser is None and TOKEN not in result.stdout + result.stderr
    assert 'invalid local browser address' in result.stderr


@pytest.mark.parametrize('prefix,suffix', [(' ', ''), ('log: ', ''), ('', ' extra')])
def test_wizard_requires_exact_prefix_and_suppresses_embedded_marker(tmp_path, prefix, suffix):
    """Malformed or log-prefixed capability lines must not open a browser or echo tokens."""
    marker = f'{prefix}BOT_SETUP_URL=http://127.0.0.1:1234/#token={TOKEN}{suffix}'
    result, _, browser = _mock_browser_launcher(tmp_path, marker)
    assert result.returncode == 0, result.stderr
    assert browser is None and TOKEN not in result.stdout + result.stderr
    assert 'invalid local browser address' in result.stderr


def test_browser_failure_never_renders_capability_or_raw_exception(tmp_path):
    """Desktop browser errors preserve safe diagnostics and do not expose capability URLs."""
    result, _, browser = _mock_browser_launcher(
        tmp_path, f'BOT_SETUP_URL=http://127.0.0.1:1234/#token={TOKEN}', browser_fails=True,
    )
    assert result.returncode == 0, result.stderr
    assert browser is None and TOKEN not in result.stdout + result.stderr
    assert 'do not leak URL' not in result.stdout + result.stderr
    assert 'browser could not open' in result.stderr
