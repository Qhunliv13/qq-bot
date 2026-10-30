"""Real PowerShell wizard tests with fake WSL; no package, service or browser actions."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which('powershell.exe') or shutil.which('pwsh')
SETUP = ROOT / 'tools/windows-setup.ps1'


def test_double_click_entry_is_thin_and_checkout_relative():
    """Double-click must resolve its own checkout without another setup implementation."""
    cmd = (ROOT / 'install.cmd').read_text(encoding='utf-8')
    assert '%~dp0tools\\windows-setup.ps1' in cmd
    assert 'pause' in cmd and 'exit /b %result%' in cmd
    setup = SETUP.read_text(encoding='utf-8')
    assert 'bootstrap.py' in setup
    assert '-User root -DataDir' in setup
    assert '--unregister' not in setup and '--shutdown' not in setup
    assert 'Invoke-Expression' not in setup
    assert '-OpenDashboard' in setup
    prerequisites = (ROOT / 'tools/windows-prerequisites.sh').read_text(encoding='utf-8')
    assert '--no-upgrade' in prerequisites
    assert 'curl' not in '\n'.join(line for line in prerequisites.splitlines() if not line.lstrip().startswith('#'))
    assert '--check' in prerequisites and '--install' in prerequisites


@pytest.mark.skipif(not POWERSHELL, reason='PowerShell required')
def test_wizard_functions_strip_nuls_exclude_internal_distro_and_validate_choice(tmp_path):
    """Exercise the real parser rather than searching its source for expected behavior."""
    runner = tmp_path / 'run.ps1'
    runner.write_text(
        f". '{SETUP.as_posix()}'\n"
        "$names = @(Get-SetupDistributions @('Ubuntu', ('D' + [char]0 + 'ebian'), 'docker-desktop', 'docker-desktop-data', '', 'bad;command'))\n"
        "$valid = Get-SetupDistroIndex '2' 2\n"
        "$rejected = @(); foreach ($value in @('0','3','1;command')) { try { Get-SetupDistroIndex $value 2 | Out-Null } catch { $rejected += $value } }\n"
        "$cancelled = @(); foreach ($value in @('', 'q', ' Q ', $null)) { $cancelled += (Get-SetupDistroIndex $value 2) }\n"
        "@{ names=$names; valid=$valid; rejected=$rejected.Count; cancelled=$cancelled } | ConvertTo-Json -Compress\n",
        encoding='ascii',
    )
    result = subprocess.run([POWERSHELL, '-NoProfile', '-File', str(runner)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        'names': ['Ubuntu', 'Debian'], 'valid': 1, 'rejected': 3,
        'cancelled': [-1, -1, -1, -1],
    }


@pytest.mark.skipif(not POWERSHELL, reason='PowerShell required')
@pytest.mark.parametrize('prerequisite_exit', [0, 10, 11, 12, 127])
def test_checkonly_never_installs_starts_or_prompts(tmp_path, prerequisite_exit):
    """A real wizard preflight must return exact status without mutation even on failure."""
    runner = tmp_path / 'run.ps1'
    log = tmp_path / 'calls.jsonl'
    # Unicode checkout resolution is returned by WSL as data; it is never shell text.
    runner.write_text(
        "function wsl.exe {\n"
        "  $words = @($args); $global:LASTEXITCODE = 0\n"
        f"  [System.IO.File]::AppendAllText('{log.as_posix()}', (ConvertTo-Json -Compress -InputObject @($words)) + [Environment]::NewLine)\n"
        "  if ($words -contains '--verbose') { '* Ubuntu Running 2' }\n"
        "  elseif ($words -contains '--list') { 'Ubuntu' }\n"
        "  elseif ($words -contains 'HOME') { '/home/user' }\n"
        "  elseif ($words -contains 'wslpath') { '/mnt/c/checkout with spaces' }\n"
        f"  else {{ 'safe prerequisite output'; $global:LASTEXITCODE = {prerequisite_exit} }}\n"
        "}\n"
        "function Read-Host { throw 'must not prompt' }\n"
        "function Start-Process { throw 'must not start process' }\n"
        f"& '{SETUP.as_posix()}' -CheckOnly\nexit $LASTEXITCODE\n",
        encoding='ascii',
    )
    result = subprocess.run([POWERSHELL, '-NoProfile', '-File', str(runner)], capture_output=True, text=True, timeout=20)
    assert result.returncode == prerequisite_exit, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(calls) == 5
    assert calls[-1][-2:] == ['/mnt/c/checkout with spaces/tools/windows-prerequisites.sh', '--check']
    assert '--install' not in repr(calls)


def _run_isolated_wizard(tmp_path, *, choice='2', prerequisite_exit=0,
                         list_exit=0, verbose_exit=0, launch_exit=0,
                         missing_wsl=False, home_exit=0, linux_home='/home/default user'):
    """Copy the real wizard to a Unicode/spaced checkout; stub every external action."""
    checkout = tmp_path / '\u4ed3\u5e93 \u6d4b\u8bd5'
    tools = checkout / 'tools'
    tools.mkdir(parents=True)
    setup = tools / 'windows-setup.ps1'
    shutil.copyfile(SETUP, setup)
    calls_path = tmp_path / 'calls.jsonl'
    launch_path = tmp_path / 'launch.json'
    linux_repo = '/mnt/c/\u4ed3\u5e93 \u6d4b\u8bd5'
    # Test harnesses use a BOM so Windows PowerShell 5.1 reads real Chinese literals.
    (tools / 'windows.ps1').write_text(
        "param([string] $Command, [string] $Distro, [string] $User, [string] $DataDir, [switch] $OpenDashboard)\n"
        f"[System.IO.File]::WriteAllText('{launch_path.as_posix()}', (ConvertTo-Json -Compress -InputObject $PSBoundParameters))\n"
        f"exit {launch_exit}\n",
        encoding='utf-8-sig',
    )
    runner = tmp_path / 'run.ps1'
    runner.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "function wsl.exe {\n"
        "  $words = @($args); $global:LASTEXITCODE = 0\n"
        f"  [System.IO.File]::AppendAllText('{calls_path.as_posix()}', (ConvertTo-Json -Compress -InputObject @($words)) + [Environment]::NewLine)\n"
        f"  if ($words -contains '--verbose') {{ '* Ubuntu Stopped 2'; ' Debian Stopped 2'; $global:LASTEXITCODE = {verbose_exit} }}\n"
        f"  elseif ($words -contains '--list') {{ 'Ubuntu'; 'Debian'; 'docker-desktop'; $global:LASTEXITCODE = {list_exit} }}\n"
        f"  elseif ($words -contains 'HOME') {{ '{linux_home}'; $global:LASTEXITCODE = {home_exit} }}\n"
        f"  elseif ($words -contains 'wslpath') {{ '{linux_repo}' }}\n"
        f"  else {{ 'safe prerequisite output'; $global:LASTEXITCODE = {prerequisite_exit} }}\n"
        "}\n"
        f"function Read-Host {{ return '{choice}' }}\n"
        "function Start-Process { throw 'must not start a process' }\n"
        + ("function Get-Command { return $null }\n" if missing_wsl else '')
        + f"& '{setup.as_posix()}'\nexit $LASTEXITCODE\n",
        encoding='utf-8-sig',
    )
    result = subprocess.run(
        [POWERSHELL, '-NoProfile', '-File', str(runner)],
        capture_output=True, text=True, timeout=20,
    )
    calls = [json.loads(line) for line in calls_path.read_text(encoding='utf-8').splitlines()] if calls_path.exists() else []
    launch = json.loads(launch_path.read_text(encoding='utf-8')) if launch_path.exists() else None
    return result, calls, launch, checkout, linux_repo


@pytest.mark.skipif(not POWERSHELL, reason='PowerShell required')
@pytest.mark.parametrize('launch_exit', [0, 7])
def test_wizard_real_chinese_space_checkout_selects_distro_and_preserves_exit(tmp_path, launch_exit):
    """A copied real wizard preserves Unicode argv, selected distro, data home and status."""
    result, calls, launch, checkout, linux_repo = _run_isolated_wizard(tmp_path, launch_exit=launch_exit)
    assert result.returncode == launch_exit, result.stderr
    assert calls[2] == ['--distribution', 'Debian', '--exec', 'printenv', 'HOME']
    assert calls[3] == ['--distribution', 'Debian', '--exec', 'wslpath', '-a', '-u', str(checkout)]
    assert calls[4] == ['--distribution', 'Debian', '--user', 'root', '--exec', 'bash',
                        linux_repo + '/tools/windows-prerequisites.sh', '--check']
    assert launch == {
        'Command': 'up', 'Distro': 'Debian', 'User': 'root',
        'DataDir': '/home/default user/.local/share/qhunliv-bot', 'OpenDashboard': {'IsPresent': True},
    }
    assert '--install' not in repr(calls)
    assert SETUP.read_bytes().isascii()
    if launch_exit == 0:
        assert 'Manage this same installation' in result.stdout
        assert '-Distro "Debian" -User root -DataDir "/home/default user/.local/share/qhunliv-bot"' in result.stdout
        assert 'Add -ShowPassword to info only' in result.stdout
    else:
        assert 'Manage this same installation' not in result.stdout


@pytest.mark.skipif(not POWERSHELL, reason='PowerShell required')
@pytest.mark.parametrize('choice', ['', 'q', ' Q ', '0', '3', '1;command'])
def test_multiple_wsl_choice_cancellation_and_invalid_input_never_mutate(tmp_path, choice):
    """Cancelled/invalid menus must stop before default-home lookup or root prerequisite work."""
    result, calls, launch, _, _ = _run_isolated_wizard(tmp_path, choice=choice)
    assert result.returncode == 2, result.stderr
    assert len(calls) == 2
    assert launch is None
    assert '1;command' not in result.stdout + result.stderr
    if choice.strip().lower() in ('', 'q'):
        assert 'Setup cancelled.' in result.stdout
    else:
        assert 'Select a displayed WSL distribution number' in result.stderr


@pytest.mark.skipif(not POWERSHELL, reason='PowerShell required')
@pytest.mark.parametrize('list_exit,verbose_exit,expected_calls', [(1, 0, 1), (0, 1, 2)])
def test_unsupported_wsl_listing_flags_do_not_trigger_install(tmp_path, list_exit, verbose_exit, expected_calls):
    """An unavailable WSL flag is a diagnostic, not evidence that Ubuntu needs installing."""
    result, calls, launch, _, _ = _run_isolated_wizard(
        tmp_path, list_exit=list_exit, verbose_exit=verbose_exit,
    )
    assert result.returncode == 2, result.stderr
    assert len(calls) == expected_calls
    assert launch is None
    assert '--quiet / --verbose is unsupported' in result.stdout
    assert 'installation requested' not in result.stdout


@pytest.mark.skipif(not POWERSHELL, reason='PowerShell required')
@pytest.mark.parametrize('prerequisite_exit', [10, 11, 12, 127])
def test_prerequisite_failure_hints_preserve_manual_recovery(tmp_path, prerequisite_exit):
    """Missing tools/daemon offer local recovery, never install or launch after refusal."""
    result, calls, launch, _, _ = _run_isolated_wizard(tmp_path, prerequisite_exit=prerequisite_exit)
    assert result.returncode == 2, result.stderr
    assert len(calls) == 5
    assert launch is None
    assert '--install' not in repr(calls)
    if prerequisite_exit in (11, 12):
        assert 'Docker Desktop' in result.stdout and 'WSL Integration' in result.stdout
    if prerequisite_exit != 127:
        assert 'apt-based distribution' in result.stdout
    else:
        assert 'needs bash and wslpath' in result.stdout


@pytest.mark.skipif(not POWERSHELL, reason='PowerShell required')
@pytest.mark.parametrize('failure', ['missing_wsl', 'home_exit'])
def test_missing_wsl_command_or_uninitialized_home_has_specific_safe_hint(tmp_path, failure):
    """Missing command/first-run failures must return 2 without generic or raw error text."""
    options = {'missing_wsl': True} if failure == 'missing_wsl' else {'home_exit': 1}
    result, calls, launch, _, _ = _run_isolated_wizard(tmp_path, **options)
    assert result.returncode == 2, result.stderr
    assert launch is None
    assert len(calls) == (0 if failure == 'missing_wsl' else 3)
    if failure == 'missing_wsl':
        assert 'WSL command is missing' in result.stdout
    else:
        assert 'Complete the distribution first-run prompts' in result.stderr


@pytest.mark.skipif(not POWERSHELL, reason='PowerShell required')
@pytest.mark.parametrize('process_exit,expected_exit', [(0, 3), (3010, 3), (1, 2)])
def test_wsl_install_request_never_reports_distribution_ready(tmp_path, process_exit, expected_exit):
    """A fake Microsoft installer result is only a request, never proof of WSL readiness."""
    runner = tmp_path / 'run.ps1'
    runner.write_text(
        f". '{SETUP.as_posix()}'\n"
        "function Read-Host { return 'y' }\n"
        f"function Start-Process {{ return [pscustomobject] @{{ ExitCode = {process_exit} }} }}\n"
        "exit (Install-SetupWsl)\n",
        encoding='ascii',
    )
    result = subprocess.run(
        [POWERSHELL, '-NoProfile', '-File', str(runner)],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == expected_exit, result.stderr
    if expected_exit == 3:
        assert 'requested, not verified ready' in result.stdout
        assert 'reboot/Ubuntu first-run prompts' in result.stdout
    else:
        assert 'installation request failed' in result.stdout
        assert '--install / --no-launch' in result.stdout
        assert 'installation requested' not in result.stdout


@pytest.mark.skipif(not POWERSHELL, reason='PowerShell required')
def test_consent_eof_is_refusal_not_an_exception(tmp_path):
    """A closed input stream cannot grant consent or trigger a null-method failure."""
    runner = tmp_path / 'run.ps1'
    runner.write_text(
        f". '{SETUP.as_posix()}'\n"
        "function Read-Host { return $null }\n"
        "function Start-Process { throw 'must not install' }\n"
        "exit (Install-SetupWsl)\n",
        encoding='ascii',
    )
    result = subprocess.run(
        [POWERSHELL, '-NoProfile', '-File', str(runner)],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 2
    assert result.stderr == ''
