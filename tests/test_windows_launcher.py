"""Offline Windows launcher contract tests; no WSL, Docker, network or model use."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "tools" / "windows.ps1"
BOOTSTRAP = ROOT / "tools" / "bootstrap.py"


POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")


def test_launcher_delegates_to_existing_linux_bootstrap():
    """Keep one runtime implementation and avoid shell/string command injection."""
    script = LAUNCHER.read_text(encoding="utf-8")
    assert "Get-Command 'wsl.exe'" in script
    assert "--exec wslpath -a -u $repo" in script
    assert "'--cd', $linuxRepo, '--exec', 'python3', 'tools/bootstrap.py'" in script
    assert "$bootstrapArgs += @('--prompt'" not in script
    for dangerous in ("Invoke-Expression", "cmd /c", "$env:", "SetEnvironmentVariable"):
        assert dangerous not in script
    assert "& $wsl @distroArgs --exec bash" not in script
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
    for command in ("init", "doctor", "up", "status", "logs", "health", "chat"):
        assert f"'{command}'" in script
        assert f'"{command}"' in bootstrap
    for option in ("--model-base", "--model", "--api-key-env", "--persona-file",
                   "--data-dir", "--runtime-name", "--non-interactive", "--owner-qq"):
        assert option in script and option in bootstrap


def test_guide_warns_about_linux_secret_and_local_full_runtime():
    """Document critical platform boundaries without claiming key forwarding or QQ provisioning."""
    guide = (ROOT / "docs" / "WINDOWS.md").read_text(encoding="utf-8")
    for term in ("WSL2", "Docker Desktop", "WSL Integration", "Linux", "-ApiKeyEnv",
                 "-Distro", "-DataDir", "standard input", "full local", "QQ"):
        assert term in guide


def _invoke_mock_wsl(tmp_path, command, *, stdin=None, pipe=False, root=False, python_exit=0, launch_exit=0):
    """Run the real launcher with an in-process PowerShell wsl.exe stub."""
    runner = tmp_path / "run.ps1"
    log = tmp_path / "wsl.jsonl"
    prefix = "'private chat text' | " if pipe else ""
    # Copy both entry layers under a path with spaces to test root-relative resolution.
    checkout = tmp_path / 'checkout with spaces'
    (checkout / 'tools').mkdir(parents=True)
    shutil.copy2(LAUNCHER, checkout / 'tools' / 'windows.ps1')
    shutil.copy2(ROOT / 'install.ps1', checkout / 'install.ps1')
    entry = checkout / 'install.ps1' if root else checkout / 'tools' / 'windows.ps1'
    input_log = tmp_path / 'stdin.txt'
    # This mock records argv and stdin; it never starts WSL or Docker.
    runner.write_text(
        "function wsl.exe {\n"
        "  [CmdletBinding(PositionalBinding=$false)]\n"
        "  param([Parameter(ValueFromRemainingArguments=$true)][string[]] $Words,\n"
        "        [Parameter(ValueFromPipeline=$true)][string] $Line)\n"
        "  begin { $allWords = @($Words); $global:LASTEXITCODE = 0 }\n"
        f"  process {{ if ($Line) {{ [System.IO.File]::AppendAllText('{input_log.as_posix()}', $Line) }} }}\n"
        "  end {\n"
        f"    [System.IO.File]::AppendAllText('{log.as_posix()}', "
        "(ConvertTo-Json -Compress -InputObject @($allWords)) + [Environment]::NewLine)\n"
        "    if ($allWords -contains 'wslpath') { '/mnt/c/checkout with spaces' }\n"
        f"    elseif ($allWords -contains 'tools/bootstrap.py') {{ $global:LASTEXITCODE = {launch_exit} }}\n"
        f"    else {{ $global:LASTEXITCODE = {python_exit} }}\n"
        "  }\n"
        "}\n"
        f"{prefix}& '{entry.as_posix()}' {command}\n"
        # A test runner must not mask a direct script's parameter-binding failure.
        "if (-not $?) { if ($null -eq $LASTEXITCODE) { exit 1 } }\n"
        "exit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-File", str(runner)],
        cwd=ROOT, input=stdin, capture_output=True, text=True, timeout=20, check=False,
    )
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
    return result, calls


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_mocked_wsl_invocation_maps_flags_and_uses_linux_repo(tmp_path):
    """Verify distinct argv elements, selected distro and exact Python entry point."""
    result, calls = _invoke_mock_wsl(
        tmp_path, "init -Distro Ubuntu -ModelBase 'https://example.invalid/v1' "
        "-Model 'model one' -ApiKeyEnv MY_MODEL_KEY -PersonaFile /home/user/persona.txt "
        "-DataDir /home/user/bot-state -RuntimeName my-bot -NonInteractive "
        "-OwnerQQ @('123456789','987654321')"
    )
    assert result.returncode == 0, result.stderr
    assert len(calls) == 3
    assert calls[0][-4:-1] == ["wslpath", "-a", "-u"]
    assert calls[0][:2] == ["--distribution", "Ubuntu"]
    assert calls[2][:7] == ["--distribution", "Ubuntu", "--cd",
                            "/mnt/c/checkout with spaces", "--exec",
                            "python3", "tools/bootstrap.py"]
    assert calls[2][7:] == [
        "init", "--model-base", "https://example.invalid/v1", "--model", "model one",
        "--api-key-env", "MY_MODEL_KEY", "--persona-file", "/home/user/persona.txt",
        "--data-dir", "/home/user/bot-state", "--runtime-name", "my-bot",
        "--non-interactive", "--owner-qq", "123456789", "--owner-qq", "987654321",
    ]
    assert "Set the API key in the selected WSL Linux distribution" in result.stderr
    assert "MY_MODEL_KEY" not in result.stderr


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_mocked_chat_does_not_put_prompt_in_argv(tmp_path):
    """A piped chat prompt must never become a Windows-side process argument."""
    result, calls = _invoke_mock_wsl(tmp_path, "chat -Distro Ubuntu", pipe=True)
    assert result.returncode == 0, result.stderr
    assert len(calls) == 3
    assert calls[2][-1] == "chat"
    assert "private chat text" not in repr(calls)
    assert (tmp_path / 'stdin.txt').read_text() == 'private chat text'


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
@pytest.mark.parametrize("command", [
    "init -DataDir 'C:\\Users\\Public'",
    "init -ApiKeyEnv 'BAD; echo secret'",
    "init -PersonaFile '/home/user/secret`$'",
])
def test_mocked_launcher_rejects_invalid_flags(tmp_path, command):
    """Reject unsupported paths or metacharacters before running bootstrap."""
    result, calls = _invoke_mock_wsl(tmp_path, command)
    assert 'Windows launcher:' in result.stderr
    assert result.returncode == 2
    assert len(calls) <= 1  # Path translation may occur, but bootstrap must not run.


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
@pytest.mark.parametrize('root', [False, True])
def test_default_up_and_exit_code(tmp_path, root):
    """Both entry points default to up and preserve the bootstrap exit status."""
    result, calls = _invoke_mock_wsl(tmp_path, '', root=root, launch_exit=17)
    assert result.returncode == 17, result.stderr
    assert calls[-1][-1] == 'up'
    assert '--distribution' not in calls[-1]
    assert '--wait-timeout' not in calls[-1]
    assert 'checkout with spaces' in calls[0][-1]


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_root_forwards_flags_arrays_and_stdin(tmp_path):
    """The thin wrapper must not reparse or lose typed owner arrays or input."""
    result, calls = _invoke_mock_wsl(
        tmp_path, "chat -Distro Ubuntu -Model 'model one' -OwnerQQ @('123456789','987654321')",
        root=True, pipe=True,
    )
    assert result.returncode == 0, result.stderr
    assert calls[-1][-6:] == ['--model', 'model one', '--owner-qq', '123456789', '--owner-qq', '987654321']
    assert (tmp_path / 'stdin.txt').read_text() == 'private chat text'


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_python_failure_is_actionable_and_stops_before_bootstrap(tmp_path):
    """Missing/old Linux Python must fail with a safe remediation, not a generic error."""
    result, calls = _invoke_mock_wsl(tmp_path, '', root=True, python_exit=127)
    assert result.returncode == 2
    assert 'Python 3.10+' in result.stderr
    assert 'manually' in result.stderr
    assert len(calls) == 2
    assert not any('tools/bootstrap.py' in call for call in calls)


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
@pytest.mark.parametrize('seconds', [1, 3600])
def test_wait_timeout_bounds_forwarded_from_root(tmp_path, seconds):
    """Accept both timeout bounds without adding a second root parameter parser."""
    result, calls = _invoke_mock_wsl(tmp_path, f'-WaitTimeout {seconds}', root=True)
    assert result.returncode == 0, result.stderr
    assert calls[-1][-3:] == ['up', '--wait-timeout', str(seconds)]


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
@pytest.mark.parametrize('seconds', [0, 3601])
@pytest.mark.parametrize('root', [False, True])
def test_wait_timeout_outside_bounds_never_calls_wsl(tmp_path, seconds, root):
    """Reject out-of-range binding with nonzero status before any WSL invocation."""
    result, calls = _invoke_mock_wsl(tmp_path, f'-WaitTimeout {seconds}', root=root)
    assert result.returncode == (2 if root else 1), result.stderr
    assert ('Windows launcher:' if root else 'WaitTimeout') in result.stderr
    assert calls == []


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
@pytest.mark.parametrize('root', [False, True])
def test_invalid_command_direct_file_invocation_fails(root):
    """Check actual -File binding failure independently of the fake-WSL runner."""
    entry = ROOT / 'install.ps1' if root else LAUNCHER
    result = subprocess.run(
        [POWERSHELL, '-NoProfile', '-File', str(entry), 'invalid-command'],
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == (2 if root else 1), result.stderr
