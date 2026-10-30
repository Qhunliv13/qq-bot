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
        "$rejected = @(); foreach ($value in @('0','3','1;command','')) { try { Get-SetupDistroIndex $value 2 | Out-Null } catch { $rejected += $value } }\n"
        "@{ names=$names; valid=$valid; rejected=$rejected.Count } | ConvertTo-Json -Compress\n",
        encoding='ascii',
    )
    result = subprocess.run([POWERSHELL, '-NoProfile', '-File', str(runner)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'names': ['Ubuntu', 'Debian'], 'valid': 1, 'rejected': 4}


@pytest.mark.skipif(not POWERSHELL, reason='PowerShell required')
@pytest.mark.parametrize('prerequisite_exit', [0, 11, 12])
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
