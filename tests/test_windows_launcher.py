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


def _invoke_mock_wsl(tmp_path, command, *, stdin=None, pipe=False):
    """Run the real launcher with an in-process PowerShell wsl.exe stub."""
    runner = tmp_path / "run.ps1"
    log = tmp_path / "wsl.jsonl"
    prefix = "'private chat text' | " if pipe else ""
    # This mock records argv as JSON; it never starts WSL or Docker.
    runner.write_text(
        "function wsl.exe {\n"
        "  param([Parameter(ValueFromRemainingArguments=$true)][string[]] $Words)\n"
        "  begin { $allWords = @($Words); $global:LASTEXITCODE = 0 }\n"
        "  process {}\n"
        "  end {\n"
        f"    [System.IO.File]::AppendAllText('{log.as_posix()}', "
        "(ConvertTo-Json -Compress -InputObject @($allWords)) + [Environment]::NewLine)\n"
        "    if ($allWords -contains 'wslpath') { '/mnt/c/qhunliv13_web/_public_qqbot' }\n"
        "  }\n"
        "}\n"
        f"{prefix}& '{LAUNCHER.as_posix()}' {command}\n",
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
    assert len(calls) == 2
    assert calls[0][-4:-1] == ["wslpath", "-a", "-u"]
    assert calls[0][:2] == ["--distribution", "Ubuntu"]
    assert calls[1][:7] == ["--distribution", "Ubuntu", "--cd",
                            "/mnt/c/qhunliv13_web/_public_qqbot", "--exec",
                            "python3", "tools/bootstrap.py"]
    assert calls[1][7:] == [
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
    assert len(calls) == 2
    assert calls[1][-1] == "chat"
    assert "private chat text" not in repr(calls)


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
    assert len(calls) <= 1  # Path translation may occur, but bootstrap must not run.
