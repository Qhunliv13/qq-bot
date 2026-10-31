"""Offline native Windows launcher tests; no installers, browsers, models or runtime execute."""

import ast
import ctypes
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "tools" / "windows-native.ps1"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
# Keep a separate Windows PowerShell executable: pwsh alone cannot reproduce the 5.1 ASCII pipe bug.
WINDOWS_POWERSHELL = shutil.which("powershell.exe")
# The exact marker matches setup_wizard; the capability is synthetic and never a real secret.
TOKEN = "offline_test_capability_0123456789_abcdef"
SETUP_URL = f"http://127.0.0.1:26185/#token={TOKEN}"


def _ps_quote(value):
    """Quote test-controlled values as literal PowerShell strings without interpolation."""
    return "'" + str(value).replace("'", "''") + "'"


def _invoke_mock_native(tmp_path, command="", *, versions=None, marker=None,
                        consent=False, winget=True, install_exit=0, launch_exit=0,
                        pipe=False, stdin=None, browser_fails=False,
                        real_probe=False, known_path=False, store_alias=False,
                        venv_cfg=None, native_marker=True, existing_state=False,
                        known_minor="312", probe_cleanup_fails=False):
    """Run the real entry code with only OS/process functions replaced by offline stubs."""
    checkout = tmp_path / "checkout with spaces"
    tools = checkout / "tools"
    tools.mkdir(parents=True)
    # The launcher requires the delegated entry point, but a stub must never execute it.
    (tools / "native_runtime.py").write_text("# offline entry sentinel\n", encoding="utf-8")
    entry = tools / "windows-native.ps1"
    log = tmp_path / "calls.jsonl"
    browser_log = tmp_path / "browser.txt"
    input_log = tmp_path / "stdin.txt"
    local_app_data = tmp_path / "Local App Data"
    if venv_cfg is not None or existing_state:
        # Metadata is intentionally untrusted: only the real Python entry may validate ownership.
        data = local_app_data / "QhunlivBot" / "Full"
        data.mkdir(parents=True)
        (data / (".native-bootstrap.json" if native_marker else ".bootstrap.json")).write_text(
            '{"runtime": "deliberately unverified"}\n', encoding="utf-8",
        )
        if venv_cfg is not None:
            venv = data / "runtime" / ".venv"
            venv.mkdir(parents=True)
            (venv / "pyvenv.cfg").write_text(venv_cfg, encoding="utf-8")
    # Python launcher selectors are deliberately separate so fallback ordering is observable.
    version_map = versions if versions is not None else {"py:-3.12": "3.12"}
    version_json = _ps_quote(json.dumps(version_map))
    marker_expression = "$null" if marker is None else _ps_quote(marker)
    setup = (
        f"$script:versions = ConvertFrom-Json {version_json}\n"
        f"$script:mockMarker = {marker_expression}\n"
        "$script:installed = $false\n"
        "function Get-Command {\n"
        "  param([string] $Name, $ErrorAction)\n"
        f"  if ($Name -eq 'py.exe' -and -not ${str(known_path).lower()}) {{ return @{{Name='mock_py'; Source=''}} }}\n"
        + ("  if ($Name -in @('python.exe', 'python3.exe')) { return @{Name='python.exe'; Source='C:\\Users\\test\\AppData\\Local\\Microsoft\\WindowsApps\\python.exe'} }\n" if store_alias else
           f"  if ($Name -in @('python.exe', 'python3.exe') -and -not ${str(known_path).lower()}) {{ return @{{Name='mock_python'; Source=''}} }}\n")
        + f"  if (${str(known_path).lower()} -and $Name -like '*Python{known_minor}\\python.exe') {{ return @{{Name='mock_python'; Source=''}} }}\n"
        f"  if ($Name -eq 'winget.exe' -and ${str(winget).lower()}) {{ return @{{Name='mock_winget'; Source=''}} }}\n"
        "  return $null\n"
        "}\n"
        "function Write-MockCall {\n"
        "  param([string] $Kind, [string[]] $Words)\n"
        f"  [IO.File]::AppendAllText({_ps_quote(log)}, (ConvertTo-Json -Compress -InputObject @{{kind=$Kind; words=@($Words)}}) + [Environment]::NewLine)\n"
        "}\n"
        "function Invoke-MockPython {\n"
        "  param([string] $Kind, [string[]] $Words)\n"
        "  Write-MockCall $Kind $Words\n"
        "  $global:LASTEXITCODE = 0\n"
        "  if ($Words -contains '-c') {\n"
        "    $selector = if ($Kind -eq 'py') { $Words[0] } else { '' }\n"
        "    $key = $Kind + ':' + $selector\n"
        "    $version = $script:versions.PSObject.Properties[$key]\n"
        "    if ($script:installed) { '3.12' }\n"
        "    elseif ($version) { [string] $version.Value }\n"
        "    else { $global:LASTEXITCODE = 1 }\n"
        + "  } else {\n"
        "    if ($null -ne $script:mockMarker) { $script:mockMarker; $script:mockMarker }\n"
        "    'runtime output'\n"
        f"    $global:LASTEXITCODE = {launch_exit}\n"
        "  }\n"
        "}\n"
        "function mock_py {\n"
        "  [CmdletBinding(PositionalBinding=$false)]\n"
        "  param([Parameter(ValueFromRemainingArguments=$true)][string[]] $Words, [Parameter(ValueFromPipeline=$true)][string] $Line)\n"
        f"  process {{ if ($Line) {{ [IO.File]::AppendAllText({_ps_quote(input_log)}, $Line) }} }}\n"
        "  end { Invoke-MockPython 'py' @($Words) }\n"
        "}\n"
        "function mock_python {\n"
        "  [CmdletBinding(PositionalBinding=$false)]\n"
        "  param([Parameter(ValueFromRemainingArguments=$true)][string[]] $Words, [Parameter(ValueFromPipeline=$true)][string] $Line)\n"
        f"  process {{ if ($Line) {{ [IO.File]::AppendAllText({_ps_quote(input_log)}, $Line) }} }}\n"
        "  end { Invoke-MockPython 'python' @($Words) }\n"
        "}\n"
        "function mock_winget {\n"
        "  [CmdletBinding(PositionalBinding=$false)]\n"
        "  param([Parameter(ValueFromRemainingArguments=$true)][string[]] $Words)\n"
        "  Write-MockCall 'winget' @($Words)\n"
        f"  $global:LASTEXITCODE = {install_exit}\n"
        "  $script:installed = $true\n"
        "}\n"
        "function Confirm-NativePythonInstall {\n"
        "  Write-MockCall 'consent' @()\n"
        f"  return ${str(consent).lower()}\n"
        "}\n"
        "function Start-Process {\n"
        "  param([string] $FilePath, $ErrorAction)\n"
        + ("  throw ('Do not print this capability: ' + $FilePath)\n" if browser_fails else
           f"  [IO.File]::AppendAllText({_ps_quote(browser_log)}, $FilePath + [Environment]::NewLine)\n")
        + "}\n"
    )
    # Mock the single probe boundary, not its production containment or discovery logic.
    # Real-probe coverage resolves a real executable and keeps its isolated Win32 execution intact.
    if real_probe:
        setup += (
            "function Invoke-NativePythonProbe {\n"
            "  param([string] $Executable, [string[]] $Prefix)\n"
            "  Write-MockCall 'python' @('-I', '-S', '-B', '-c')\n"
            f"  $result = [Qhunliv.NativePythonProbe]::Run({_ps_quote(sys.executable)}, '', 2000)\n"
            "  if ($result -in @(312, 313)) { return '3.' + ($result - 300) }\n"
            "}\n"
        )
        # Initialize the real helper once before installing this recording-only test adapter.
        setup = f"Invoke-NativePythonProbe {_ps_quote(sys.executable)} @() | Out-Null\n" + setup
    else:
        setup += (
            "function Invoke-NativePythonProbe {\n"
            "  param([string] $Executable, [string[]] $Prefix)\n"
            "  $kind = if ($Executable -eq 'mock_py') { 'py' } else { 'python' }\n"
            + ("  Write-MockCall $kind @($Prefix + @('-I', '-S', '-B', '-c')); throw 'Uncertain cleanup.'\n"
               if probe_cleanup_fails else
               "  Invoke-MockPython $kind @($Prefix + @('-I', '-S', '-B', '-c'))\n")
            + "}\n"
        )
    # Override only process/desktop functions after their definitions, not entry control flow.
    source = LAUNCHER.read_text(encoding="utf-8")
    anchor = "# Native launcher entry point."
    assert source.count(anchor) == 1
    entry.write_text(source.replace(anchor, setup + "\n" + anchor), encoding="utf-8")
    runner = tmp_path / "run.ps1"
    runner.write_text(
        f"$env:LOCALAPPDATA = {_ps_quote(local_app_data)}\n"
        + ("'private chat text' | " if pipe else "")
        + f"& {_ps_quote(entry)} {command}\n"
        "if (-not $?) { if ($null -eq $LASTEXITCODE) { exit 1 } }\n"
        "exit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner)],
        cwd=ROOT, input=stdin, capture_output=True, text=True, timeout=30, check=False,
    )
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
    browsers = browser_log.read_text(encoding="utf-8").splitlines() if browser_log.exists() else []
    inputs = input_log.read_text(encoding="utf-8") if input_log.exists() else ""
    return result, calls, browsers, inputs, checkout, local_app_data


def _runtime_calls(calls):
    """Select actual runtime delegation separately from bounded interpreter probes."""
    return [call for call in calls if call["kind"] in ("py", "python") and "-c" not in call["words"]]


def _invoke_real_ps51_native(tmp_path, *, runtime_exit=0, missing_executable=False,
                             no_console=False):
    """Exercise real 5.1 native pipes using a stdlib byte sentinel, never the bot runtime or installers."""
    checkout = tmp_path / "real pipe checkout"
    tools = checkout / "tools"
    tools.mkdir(parents=True)
    byte_log = tmp_path / "stdin.bin"
    argv_log = tmp_path / "argv.json"
    output_log = tmp_path / "stdout.txt"
    state_log = tmp_path / "encoding-state.json"
    data = tmp_path / "private untouched data"
    text = "中文输入与输出 😀🧪"
    # Raw bytes deliberately avoid Python's platform stdio defaults: the wrapper transport is under test.
    (tools / "native_runtime.py").write_text(
        "import json\nfrom pathlib import Path\nimport sys\n"
        f"Path({str(byte_log)!r}).write_bytes(sys.stdin.buffer.read())\n"
        f"Path({str(argv_log)!r}).write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n"
        f"sys.stdout.buffer.write({(text + chr(10)).encode('utf-8')!r})\n"
        f"sys.stderr.buffer.write({('离线错误流 😀' + chr(10)).encode('utf-8')!r})\n"
        f"raise SystemExit({runtime_exit})\n",
        encoding="utf-8",
    )
    executable = tmp_path / "missing-python.exe" if missing_executable else Path(sys.executable)
    # Only interpreter discovery is substituted; '& executable', native stdin and stdout decoding stay real.
    override = (
        "function Get-NativePython {\n"
        "  <# Locate only the test interpreter; no probe, installer or actual runtime is allowed. #>\n"
        "  param([string] $RequiredVersion = '')\n"
        f"  return @{{ Executable = {_ps_quote(executable)}; Prefix = @() }}\n"
        "}\n"
    )
    source = LAUNCHER.read_text(encoding="utf-8")
    entry = tools / "windows-native.ps1"
    entry.write_text(source.replace("# Native launcher entry point.", override + "\n# Native launcher entry point."),
                     encoding="utf-8-sig")
    runner = tmp_path / "real-ps51-runner.ps1"
    runner.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "if ($PSVersionTable.PSVersion.Major -ne 5 -or $PSVersionTable.PSVersion.Minor -ne 1) { throw 'Requires Windows PowerShell 5.1.' }\n"
        "$OutputEncoding = [System.Text.ASCIIEncoding]::new()\n"
        "$beforeOutput = $OutputEncoding\n"
        "[Console]::OutputEncoding = [System.Text.Encoding]::GetEncoding(1252)\n"
        "$beforeConsole = [Console]::OutputEncoding\n"
        f"$actual = @({_ps_quote(text)} | & {_ps_quote(entry)} chat -NonInteractive -DataDir {_ps_quote(data)})\n"
        "$code = $LASTEXITCODE\n"
        f"[IO.File]::WriteAllText({_ps_quote(output_log)}, ($actual -join [Environment]::NewLine), (New-Object System.Text.UTF8Encoding $false))\n"
        "$state = @{ output_restored = [object]::ReferenceEquals($OutputEncoding, $beforeOutput); "
        # Console's .NET setter clones Encoding; semantic equality, not object identity, proves restoration.
        "console_restored = [Console]::OutputEncoding.Equals($beforeConsole); "
        "output_codepage = $OutputEncoding.CodePage; console_codepage = [Console]::OutputEncoding.CodePage; "
        "version = $PSVersionTable.PSVersion.ToString() }\n"
        f"[IO.File]::WriteAllText({_ps_quote(state_log)}, (ConvertTo-Json -Compress $state))\n"
        "exit $code\n",
        # 5.1 treats a BOM-less file as legacy ANSI, so exercise Chinese literals from an explicit UTF-8 BOM.
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [WINDOWS_POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(runner)],
        cwd=ROOT, input=b"", capture_output=True, timeout=30, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if no_console else 0,
    )
    state = json.loads(state_log.read_text(encoding="utf-8")) if state_log.exists() else None
    output = output_log.read_text(encoding="utf-8") if output_log.exists() else None
    raw_input = byte_log.read_bytes() if byte_log.exists() else None
    argv = json.loads(argv_log.read_text(encoding="utf-8")) if argv_log.exists() else None
    return result, state, output, raw_input, argv, data, text


def _invoke_real_ps51_discovery(tmp_path, *, launcher_mode=None):
    """Run actual isolated probes and normal stdin transport using only temporary, controlled programs."""
    checkout = tmp_path / "real discovery checkout"
    tools = checkout / "tools"
    tools.mkdir(parents=True)
    input_log = tmp_path / "runtime-input.bin"
    startup_log = tmp_path / "startup.txt"
    process_log = tmp_path / "owned-pids.txt"
    argument_log = tmp_path / "probe-argv.txt"
    result_log = tmp_path / "timing.json"
    data = tmp_path / "untouched state"
    chat = "发现探测不能吞输入 😀\r\n".encode("utf-8")
    site = tmp_path / "startup site"
    site.mkdir()
    # This deliberately hostile startup hook reproduces inherited stdin consumption and a 3s hang.
    # It is confined to PYTHONPATH in the test subprocess, not an interpreter/global site directory.
    (site / "sitecustomize.py").write_text(
        "from pathlib import Path\nimport sys\nimport time\n"
        f"with Path({str(startup_log)!r}).open('a', encoding='utf-8') as stream:\n"
        "    stream.write('probe\\n' if sys.argv[0] == '-c' else 'runtime\\n')\n"
        "if sys.argv[0] == '-c':\n"
        "    sys.stdin.buffer.read()\n    time.sleep(3)\n",
        encoding="utf-8",
    )
    (tools / "native_runtime.py").write_text(
        "from pathlib import Path\nimport sys\n"
        f"Path({str(input_log)!r}).write_bytes(sys.stdin.buffer.read())\n",
        encoding="utf-8",
    )
    controlled = tmp_path / "controlled launcher.exe"
    child = tmp_path / "owned-child.py"
    child.write_text(
        "from pathlib import Path\nimport os\nimport time\n"
        f"with Path({str(process_log)!r}).open('a') as stream:\n"
        "    stream.write(str(os.getpid()) + '\\n')\n"
        "time.sleep(3)\n",
        encoding="utf-8",
    )
    compile_launcher = ""
    if launcher_mode:
        # The fake launcher uses a real descendant, as py.exe/venv redirectors can. It never runs a bot.
        csharp = (
            "using System; using System.Diagnostics; using System.IO; using System.Threading;\n"
            "public static class ControlledProbeLauncher {\n"
            "  public static int Main(string[] args) {\n"
            f'    File.AppendAllText({json.dumps(str(process_log))}, Process.GetCurrentProcess().Id + "\\n");\n'
            f"    File.WriteAllLines({json.dumps(str(argument_log))}, args);\n"
            # A correct probe sees EOF even while the caller still owns an unread chat payload.
            '    if (Console.In.ReadToEnd() != "") return 7;\n'
            + (f"    if (args.Length > 0) return {int(launcher_mode[5:])};\n"
               if launcher_mode.startswith("exit-") else "")
            + "    var start = new ProcessStartInfo();\n"
            f"    start.FileName = {json.dumps(sys.executable)};\n"
            f"    start.Arguments = {json.dumps('-I -S -B "' + str(child) + '"')};\n"
            "    start.UseShellExecute = false; Process.Start(start);\n"
            + ("    return 312;\n" if launcher_mode == "root-exits" else
               "    Thread.Sleep(3000); return 312;\n")
            + "  }\n}\n"
        )
        # PS5.1 compiles only this test-owned local fixture; no downloaded/native external helper exists.
        compile_launcher = (
            "Add-Type -TypeDefinition @'\n" + csharp + "'@ -OutputType ConsoleApplication "
            f"-OutputAssembly {_ps_quote(controlled)} -ErrorAction Stop\n"
        )
    override = (
        "$script:probeSelectors = 0\n"
        "function Get-Command {\n"
        "  param([string] $Name, $ErrorAction)\n"
        + ("  if ($Name -eq 'py.exe') { $script:probeSelectors++; "
           f"if ($script:probeSelectors -eq 1) {{ return @{{Source={_ps_quote(controlled)}}} }} }}\n"
           if launcher_mode else "")
        + f"  if ($Name -eq 'python.exe') {{ return @{{Source={_ps_quote(sys.executable)}}} }}\n"
        "  return $null\n}\n"
    )
    source = LAUNCHER.read_text(encoding="utf-8")
    # A private, test-only deadline injection keeps the regression short; production exposes no knob.
    source = source.replace("[int] $TimeoutMilliseconds = 5000", "[int] $TimeoutMilliseconds = 400")
    entry = tools / "windows-native.ps1"
    entry.write_text(source.replace("# Native launcher entry point.", override + "\n# Native launcher entry point."),
                     encoding="utf-8-sig")
    runner = tmp_path / "real-discovery.ps1"
    runner.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "if ($PSVersionTable.PSVersion.Major -ne 5 -or $PSVersionTable.PSVersion.Minor -ne 1) { throw 'Requires PS5.1.' }\n"
        + compile_launcher
        + f"$env:PYTHONPATH = {_ps_quote(site)}\n"
        # Include lazy binding compilation in a generous overall bound; each child uses only 400ms.
        + "$clock = [Diagnostics.Stopwatch]::StartNew()\n"
        + f"& {_ps_quote(entry)} chat -NonInteractive -DataDir {_ps_quote(data)}\n"
        "$code = $LASTEXITCODE\n"
        f"[IO.File]::WriteAllText({_ps_quote(result_log)}, (ConvertTo-Json -Compress @{{elapsed=$clock.ElapsedMilliseconds}}))\n"
        "exit $code\n",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [WINDOWS_POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(runner)],
        cwd=ROOT, input=chat, capture_output=True, timeout=15, check=False,
    )
    pids = [int(line) for line in process_log.read_text().splitlines()] if process_log.exists() else []
    arguments = argument_log.read_text().splitlines() if argument_log.exists() else []
    timing = json.loads(result_log.read_text()) if result_log.exists() else {}
    raw_input = input_log.read_bytes() if input_log.exists() else None
    startup = startup_log.read_text(encoding="utf-8").splitlines() if startup_log.exists() else []
    return result, raw_input, chat, startup, pids, arguments, timing, data


def _pid_is_running(pid):
    """Inspect only a fixture-owned PID with Win32 synchronization rights, never process-name enumeration."""
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    api.OpenProcess.restype = ctypes.c_void_p
    api.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    api.WaitForSingleObject.restype = ctypes.c_uint32
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    api.CloseHandle.restype = ctypes.c_int
    handle = api.OpenProcess(0x100000, False, pid)
    if not handle:
        # ERROR_INVALID_PARAMETER means this exact process no longer exists; access failures are not success.
        assert ctypes.get_last_error() == 87
        return False
    try:
        state = api.WaitForSingleObject(handle, 0)
        assert state in (0, 258)
        return state == 258
    finally:
        api.CloseHandle(handle)


@pytest.mark.skipif(WINDOWS_POWERSHELL is None or sys.version_info[:2] not in ((3, 12), (3, 13)),
                    reason="A supported Python and Windows PowerShell 5.1 are required")
def test_real_ps51_probe_skips_startup_hook_without_consuming_chat(tmp_path):
    """Isolation prevents startup's stdin read/3s sleep, while normal runtime still loads that same hook."""
    result, raw_input, chat, startup, pids, arguments, timing, data = _invoke_real_ps51_discovery(tmp_path)
    assert result.returncode == 0, result.stderr
    assert raw_input == chat
    assert startup == ["runtime"]
    assert not pids and not arguments
    assert timing["elapsed"] < 3000
    assert not data.exists()


@pytest.mark.skipif(WINDOWS_POWERSHELL is None or sys.version_info[:2] not in ((3, 12), (3, 13)),
                    reason="A supported Python and Windows PowerShell 5.1 are required")
@pytest.mark.parametrize("launcher_mode", ["blocked-root", "root-exits"])
def test_real_ps51_probe_timeout_reaps_owned_launcher_descendants_then_falls_back(tmp_path, launcher_mode):
    """A blocked launcher or already-exited root cannot leave a child or prevent the next interpreter probe."""
    result, raw_input, chat, startup, pids, arguments, timing, data = _invoke_real_ps51_discovery(
        tmp_path, launcher_mode=launcher_mode,
    )
    assert result.returncode == 0, result.stderr
    assert raw_input == chat and startup == ["runtime"]
    assert len(pids) == 2 and all(not _pid_is_running(pid) for pid in pids)
    assert arguments[:5] == ["-3.12", "-I", "-S", "-B", "-c"]
    assert "import sys; sys.exit(" in arguments[5]
    assert timing["elapsed"] < 3000
    assert not data.exists()


@pytest.mark.skipif(WINDOWS_POWERSHELL is None or sys.version_info[:2] not in ((3, 12), (3, 13)),
                    reason="A supported Python and Windows PowerShell 5.1 are required")
@pytest.mark.parametrize("code", [0, 1, 314])
def test_real_ps51_probe_rejects_unknown_exit_status_then_falls_back(tmp_path, code):
    """Only the exact supported-version protocol is accepted, never ordinary success or an unknown status."""
    result, raw_input, chat, startup, pids, arguments, _, data = _invoke_real_ps51_discovery(
        tmp_path, launcher_mode=f"exit-{code}",
    )
    assert result.returncode == 0, result.stderr
    assert raw_input == chat and startup == ["runtime"]
    assert len(pids) == 1 and not _pid_is_running(pids[0])
    assert arguments[:5] == ["-3.12", "-I", "-S", "-B", "-c"]
    assert not data.exists()


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_uncertain_probe_cleanup_blocks_all_next_candidates_and_install(tmp_path):
    """Cleanup uncertainty must escape discovery rather than authorize another process or installer."""
    result, calls, browsers, _, _, _ = _invoke_mock_native(
        tmp_path, consent=True, probe_cleanup_fails=True,
    )
    assert result.returncode == 2
    assert len(calls) == 1 and "-c" in calls[0]["words"]
    assert not browsers and not _runtime_calls(calls)
    assert "Uncertain cleanup" not in result.stdout + result.stderr


def test_probe_source_has_one_isolated_atomic_and_nonpersistent_transport():
    """Keep discovery isolated, parent-death safe and independent from Python/bootstrap/global configuration."""
    source = LAUNCHER.read_text(encoding="utf-8")
    assert source.count("function Invoke-NativePythonProbe {") == 1
    assert " -I -S -B -c " in source
    assert "new IntPtr(0x2000D)" in source and "new IntPtr(0x20002)" in source
    assert "startup.Info.Input = nul" in source
    assert "limits.Basic.Flags = 0x2000" in source
    assert "clock.ElapsedMilliseconds < milliseconds" in source
    assert "if ($result -eq -2) { throw" in source
    for forbidden in ("AssignProcessToJobObject", "Get-Process", "taskkill", "RedirectStandardInput", "ReadToEnd"):
        assert forbidden not in source


def test_native_launcher_is_only_a_transport_not_a_lite_runtime():
    """Keep capability/runtime behavior unified in Python and prohibit machine-changing shortcuts."""
    source = LAUNCHER.read_text(encoding="utf-8")
    assert "'wizard', 'init', 'up', 'doctor', 'info', 'status', 'stop', 'logs', 'health', 'chat'" in source
    assert "[string] $Command = 'wizard'" in source
    assert "tools\\native_runtime.py" in source
    assert "QhunlivBot\\Full" in source
    assert "Get-Command 'winget.exe'" in source
    assert "$runtimeArgs += @('--no-browser', '--print-setup-url')" in source
    assert "& $executable @launchArgs | ForEach-Object" in source
    for forbidden in ("Invoke-Expression", "Set-ExecutionPolicy", "SetEnvironmentVariable", "RunAs", "wsl.exe", "docker.exe", "bootstrap.py", "--api-key'"):
        assert forbidden not in source


def test_test_helpers_and_cases_have_docstrings():
    """Maintain the root convention for documented functions even in launcher tests."""
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    assert all(ast.get_docstring(node) for node in ast.walk(tree)
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_default_wizard_opens_only_exact_marker_and_uses_full_data(tmp_path):
    """Use the preferred Python, unbuffered unified wizard, and isolated Full state by default."""
    result, calls, browsers, _, checkout, local = _invoke_mock_native(tmp_path, marker="BOT_SETUP_URL=" + SETUP_URL)
    assert result.returncode == 0, result.stderr
    launch = _runtime_calls(calls)
    assert len(launch) == 1
    assert launch[0]["words"] == ["-3.12", "-u", str(checkout / "tools" / "native_runtime.py"),
                                   "wizard", "--data-dir", str(local / "QhunlivBot" / "Full"),
                                   "--no-browser", "--print-setup-url"]
    assert browsers == [SETUP_URL]
    assert TOKEN not in result.stdout + result.stderr
    assert "runtime output" in result.stdout


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
@pytest.mark.parametrize("versions,kind,prefix", [
    ({"py:-3.12": "3.11", "py:-3.13": "3.13"}, "py", ["-3.13"]),
    ({"py:-3.12": "3.14", "py:-3.13": "3.11", "python:": "3.12"}, "python", []),
])
def test_discovery_rejects_unsupported_versions_then_uses_installed_python(tmp_path, versions, kind, prefix):
    """Do not trust py selection names or accept unsupported 3.11/3.14 for native dependencies."""
    result, calls, _, _, _, _ = _invoke_mock_native(tmp_path, "doctor", versions=versions)
    assert result.returncode == 0, result.stderr
    launch = _runtime_calls(calls)[0]
    assert launch["kind"] == kind
    assert launch["words"][:len(prefix)] == prefix
    assert not any(call["kind"] in ("consent", "winget") for call in calls)


@pytest.mark.skipif(POWERSHELL is None or sys.version_info[:2] not in ((3, 12), (3, 13)),
                    reason="A supported Python and PowerShell are required")
def test_probe_code_survives_real_powershell_native_argument_quoting(tmp_path):
    """Run only the finite stdlib version probe for real, never the runtime or installer."""
    result, calls, _, _, _, _ = _invoke_mock_native(tmp_path, "doctor", real_probe=True)
    assert result.returncode == 0, result.stderr
    assert len(_runtime_calls(calls)) == 1
    assert not any(call["kind"] == "winget" for call in calls)


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_known_user_install_is_discovered_without_mutating_path(tmp_path):
    """A current process can find user-installed Python even before its PATH is refreshed."""
    result, calls, _, _, _, _ = _invoke_mock_native(
        tmp_path, "doctor", versions={"python:": "3.12"}, known_path=True,
    )
    assert result.returncode == 0, result.stderr
    assert _runtime_calls(calls)[0]["kind"] == "python"
    assert not any(call["kind"] in ("consent", "winget") for call in calls)


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
@pytest.mark.parametrize("minor", ["3.12", "3.13"])
@pytest.mark.parametrize("explicit_data", [False, True])
def test_existing_venv_selects_matching_system_minor_only(tmp_path, minor, explicit_data):
    """Retain each existing venv minor without executing cfg paths or selecting the other minor."""
    cfg = ("version = " + minor + ".9\ninclude-system-site-packages = false\n"
           "home = C:\\untrusted\\python\nexecutable = https://example.invalid/python.exe\n")
    command = "up"
    if explicit_data:
        # Version selection must use the validated caller-selected directory, not just the default.
        command += " -DataDir " + _ps_quote(tmp_path / "Local App Data/QhunlivBot/Full")
    result, calls, _, _, _, local = _invoke_mock_native(
        tmp_path, command, versions={"py:-3.12": "3.12", "py:-3.13": "3.13"}, venv_cfg=cfg,
    )
    assert result.returncode == 0, result.stderr
    assert _runtime_calls(calls)[0]["words"][0] == "-" + minor
    probes = [call["words"] for call in calls if "-c" in call["words"]]
    assert probes == [["-" + minor, "-I", "-S", "-B", "-c"]]
    assert not any(call["kind"] in ("consent", "winget") for call in calls)
    assert (local / "QhunlivBot/Full/runtime/.venv/pyvenv.cfg").read_text(encoding="utf-8") == cfg


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_existing_313_known_install_fallback_rejects_wrong_probe_minor(tmp_path):
    """Require the probed minor even for a matching selector, then try the known user install."""
    result, calls, _, _, _, _ = _invoke_mock_native(
        tmp_path, "up", versions={"python:": "3.13"}, known_path=True, known_minor="313",
        venv_cfg="version = 3.13.9\ninclude-system-site-packages = false\n",
    )
    assert result.returncode == 0, result.stderr
    assert _runtime_calls(calls)[0]["kind"] == "python"
    assert not any(call["kind"] in ("consent", "winget") for call in calls)


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_existing_313_missing_matching_base_never_installs_or_launches(tmp_path):
    """Do not fall back to 3.12 or winget when the original 3.13 base is missing."""
    result, calls, _, _, _, _ = _invoke_mock_native(
        tmp_path, "up", versions={"py:-3.13": "3.12", "python:": "3.12"}, consent=True,
        venv_cfg="version = 3.13.9\ninclude-system-site-packages = false\n",
    )
    assert result.returncode == 2
    assert "matching Python" in result.stderr
    assert not _runtime_calls(calls)
    assert not any(call["kind"] in ("consent", "winget") for call in calls)
    assert all("-3.12" not in call["words"] for call in calls)


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
@pytest.mark.parametrize("isolation", ["include-system-site-packages = true\n", ""])
def test_version_hint_does_not_adopt_global_or_incomplete_venv(tmp_path, isolation):
    """Select only the base interpreter; Python still rejects invalid isolation/completion unchanged."""
    cfg = "version = 3.13.9\n" + isolation
    result, calls, _, _, _, local = _invoke_mock_native(
        tmp_path, "up", versions={"py:-3.13": "3.13"}, venv_cfg=cfg, launch_exit=2,
    )
    assert result.returncode == 2
    assert _runtime_calls(calls)[0]["words"][0] == "-3.13"
    assert not any(call["kind"] in ("consent", "winget") for call in calls)
    assert (local / "QhunlivBot/Full/runtime/.venv/pyvenv.cfg").read_text(encoding="utf-8") == cfg


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
@pytest.mark.parametrize("cfg", ["", "version = 3.14.0\n", "version = https://example.invalid\n",
                                 "version = 3.13.9\nversion = 3.12.9\n", "x" * 4097])
def test_invalid_existing_venv_metadata_fails_without_discovery_or_install(tmp_path, cfg):
    """Bound metadata reads and refuse ambiguous/unsupported hints without exposing their contents."""
    result, calls, _, _, _, _ = _invoke_mock_native(tmp_path, "up", venv_cfg=cfg, consent=True)
    assert result.returncode == 2
    assert "Existing native state could not be inspected" in result.stderr
    assert calls == []
    assert "example.invalid" not in result.stdout + result.stderr


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
@pytest.mark.parametrize("native_marker", [False, True])
def test_foreign_or_incomplete_state_never_triggers_automatic_install(tmp_path, native_marker):
    """Existing state may only reach Python validation, never a new installer consent flow."""
    result, calls, _, _, _, _ = _invoke_mock_native(
        tmp_path, "up", versions={}, consent=True, existing_state=True, native_marker=native_marker,
    )
    assert result.returncode == 2
    assert not _runtime_calls(calls)
    assert not any(call["kind"] in ("consent", "winget") for call in calls)


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_foreign_marker_never_selects_venv_version_or_executable(tmp_path):
    """A Lite/container marker leaves default selection intact for Python's authoritative rejection."""
    result, calls, _, _, _, _ = _invoke_mock_native(
        tmp_path, "doctor", versions={"py:-3.12": "3.12", "py:-3.13": "3.13"},
        venv_cfg="version = 3.13.9\nexecutable = C:\\foreign\\python.exe\n", native_marker=False,
    )
    assert result.returncode == 0, result.stderr
    assert _runtime_calls(calls)[0]["words"][0] == "-3.12"
    assert all("C:\\foreign\\python.exe" not in call["words"] for call in calls)


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_store_execution_alias_is_not_launched_as_python(tmp_path):
    """Windows Store aliases can open installers, so skip them even when named python.exe."""
    result, calls, _, _, _, _ = _invoke_mock_native(tmp_path, "doctor", versions={}, store_alias=True)
    assert result.returncode == 2
    assert all(call["kind"] == "py" for call in calls)
    assert not _runtime_calls(calls)


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_flags_paths_with_spaces_arrays_and_exit_status_are_preserved(tmp_path):
    """Forward typed options as separate argv entries and retain the Python exit status."""
    result, calls, _, _, _, _ = _invoke_mock_native(
        tmp_path, "init -ModelBase 'https://example.invalid/v1' -Model 'model one' "
        "-ApiKeyEnv MY_MODEL_KEY -PersonaFile 'C:\\My Files\\persona.txt' "
        "-DataDir 'C:\\My Files\\bot-state' -RuntimeName my-bot -WaitTimeout 90 "
        "-NonInteractive -OwnerQQ @('123456789','987654321')", launch_exit=17,
    )
    assert result.returncode == 17, result.stderr
    words = _runtime_calls(calls)[0]["words"]
    assert words[3:] == ["init", "--model-base", "https://example.invalid/v1", "--model", "model one",
                         "--api-key-env", "MY_MODEL_KEY", "--persona-file", "C:\\My Files\\persona.txt",
                         "--runtime-name", "my-bot", "--data-dir", "C:\\My Files\\bot-state",
                         "--wait-timeout", "90", "--non-interactive", "--owner-qq", "123456789",
                         "--owner-qq", "987654321"]
    assert "MY_MODEL_KEY" not in result.stderr


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_piped_chat_stays_on_stdin(tmp_path):
    """Keep prompt text out of all executable arguments while forwarding PowerShell pipeline input."""
    result, calls, _, inputs, _, _ = _invoke_mock_native(tmp_path, "chat", pipe=True)
    assert result.returncode == 0, result.stderr
    assert "private chat text" not in repr(calls)
    assert inputs == "private chat text"


@pytest.mark.skipif(WINDOWS_POWERSHELL is None, reason="Windows PowerShell 5.1 is not installed")
@pytest.mark.parametrize("runtime_exit", [0, 19])
@pytest.mark.parametrize("no_console", [False, True])
def test_real_ps51_native_pipe_preserves_utf8_bytes_and_restores_encodings(tmp_path, runtime_exit, no_console):
    """Reproduce 5.1's ASCII-default pipe with Chinese/emoji through a real native byte sentinel."""
    result, state, output, raw_input, argv, data, text = _invoke_real_ps51_native(
        tmp_path, runtime_exit=runtime_exit, no_console=no_console,
    )
    assert result.returncode == runtime_exit, result.stderr
    assert state["version"].startswith("5.1.")
    assert state["output_restored"] and state["console_restored"]
    assert (state["output_codepage"], state["console_codepage"]) == (20127, 1252)
    expected = (text + "\r\n").encode("utf-8")
    # PS5.1 can prepend one transport BOM from its inherited input stream; native_runtime reads utf-8-sig.
    # Allow only that exact optional prefix, never replacement characters, missing bytes or double BOMs.
    assert raw_input in (expected, b"\xef\xbb\xbf" + expected)
    assert output == text
    assert text not in repr(argv)
    assert argv == ["chat", "--data-dir", str(data), "--non-interactive"]
    assert "离线错误流 😀".encode("utf-8") in result.stderr
    assert not data.exists()


@pytest.mark.skipif(WINDOWS_POWERSHELL is None, reason="Windows PowerShell 5.1 is not installed")
def test_real_ps51_launch_failure_restores_encodings_without_runtime_side_effects(tmp_path):
    """The finally cleanup also runs when a native executable cannot launch, without raw path disclosure."""
    result, state, output, raw_input, argv, data, _ = _invoke_real_ps51_native(
        tmp_path, missing_executable=True,
    )
    assert result.returncode == 2, result.stderr
    assert state["output_restored"] and state["console_restored"]
    assert (state["output_codepage"], state["console_codepage"]) == (20127, 1252)
    assert raw_input is None and argv is None
    assert output == ""
    assert b"Native runtime launch failed" in result.stderr
    assert b"missing-python" not in result.stderr
    assert not data.exists()


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
@pytest.mark.parametrize("command", ["wizard", "wizard -NoBrowser"])
@pytest.mark.parametrize("marker", [
    f"BOT_SETUP_URL=https://127.0.0.1:26185/#token={TOKEN}",
    f"BOT_SETUP_URL=http://localhost:26185/#token={TOKEN}",
    f"BOT_SETUP_URL=http://127.0.0.1:0/#token={TOKEN}",
    f"BOT_SETUP_URL=http://127.0.0.1:65536/#token={TOKEN}",
    f"BOT_SETUP_URL=http://127.0.0.1:26185/other#token={TOKEN}",
    f"BOT_SETUP_URL={SETUP_URL}&remote=example.invalid",
    f"before BOT_SETUP_URL={SETUP_URL}",
    f"BOT_SETUP_URL={SETUP_URL} ",
    "BOT_SETUP_URL=http://127.0.0.1:26185/#token=short",
])
def test_browser_rejects_and_suppresses_untrusted_capability_markers(tmp_path, command, marker):
    """Malformed markers stay hidden even when deliberate manual display authorizes valid loopback URLs."""
    result, _, browsers, _, _, _ = _invoke_mock_native(tmp_path, command, marker=marker)
    assert result.returncode == 0, result.stderr
    assert browsers == []
    assert "BOT_SETUP_URL=" not in result.stdout + result.stderr
    assert TOKEN not in result.stdout + result.stderr
    assert "invalid local browser address" in result.stderr


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_no_browser_displays_one_private_manual_capability_only_by_explicit_choice(tmp_path):
    """Explicit NoBrowser authorizes one usable loopback URL and warns against sharing or public logs."""
    result, calls, browsers, _, _, _ = _invoke_mock_native(tmp_path, "wizard -NoBrowser", marker="BOT_SETUP_URL=" + SETUP_URL)
    assert result.returncode == 0
    assert browsers == []
    assert result.stdout.count(SETUP_URL) == 1
    assert "only on this computer" in result.stdout
    assert "Do not share" in result.stdout
    assert "public logs" in result.stdout
    assert "BOT_SETUP_URL=" not in result.stdout
    assert TOKEN not in result.stderr
    assert _runtime_calls(calls)[0]["words"][-2:] == ["--no-browser", "--print-setup-url"]


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_browser_exception_text_is_not_reported(tmp_path):
    """A desktop error may embed its URL, so expose only the launcher's fixed remediation."""
    result, _, browsers, _, _, _ = _invoke_mock_native(tmp_path, marker="BOT_SETUP_URL=" + SETUP_URL, browser_fails=True)
    assert result.returncode == 0
    assert browsers == []
    assert "browser could not open" in result.stderr
    assert "wizard -NoBrowser" in result.stderr
    assert result.stderr.count("browser could not open") == 1
    assert TOKEN not in result.stdout + result.stderr


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
@pytest.mark.parametrize("command", ["wizard -NonInteractive", "doctor", "status", "stop", "logs", "health", "chat", "info"])
def test_missing_python_never_installs_for_noninteractive_or_diagnostic_commands(tmp_path, command):
    """Read-only commands and noninteractive callers may not trigger an install or consent dialog."""
    result, calls, _, _, _, _ = _invoke_mock_native(tmp_path, command, versions={}, consent=True)
    assert result.returncode == 2
    assert "Python 3.12 or 3.13 is required" in result.stderr
    assert not any(call["kind"] in ("consent", "winget") for call in calls)
    assert not _runtime_calls(calls)


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
@pytest.mark.parametrize("consent,winget", [(False, True), (True, False)])
def test_missing_python_declined_or_unavailable_installer_has_no_side_effect(tmp_path, consent, winget):
    """No consent or absent winget must fail safely rather than downloading an unverified installer."""
    result, calls, _, _, _, _ = _invoke_mock_native(tmp_path, versions={}, consent=consent, winget=winget)
    assert result.returncode == 2
    assert not any(call["kind"] == "winget" for call in calls)
    assert not _runtime_calls(calls)


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_confirmed_install_uses_only_user_scope_official_exact_package_then_reprobes(tmp_path):
    """Verify the fallback argv with a stub; this test must never execute a real installation."""
    result, calls, _, _, _, _ = _invoke_mock_native(tmp_path, versions={}, consent=True)
    assert result.returncode == 0, result.stderr
    installers = [call for call in calls if call["kind"] == "winget"]
    assert len(installers) == 1
    assert installers[0]["words"] == ["install", "--id", "Python.Python.3.12", "--exact", "--source", "winget",
                                      "--scope", "user", "--silent", "--accept-source-agreements",
                                      "--accept-package-agreements", "--disable-interactivity"]
    kinds = [call["kind"] for call in calls]
    assert kinds.index("consent") < kinds.index("winget")
    assert len(_runtime_calls(calls)) == 1


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_failed_install_never_launches_python_runtime(tmp_path):
    """An installer failure is actionable and cannot degrade into a substitute runtime."""
    result, calls, _, _, _, _ = _invoke_mock_native(tmp_path, versions={}, consent=True, install_exit=19)
    assert result.returncode == 2
    assert "installation did not finish" in result.stderr
    assert not _runtime_calls(calls)


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
@pytest.mark.parametrize("command", [
    "init -ApiKeyEnv 'BAD; secret'", "init -DataDir relative-state", "init -OwnerQQ '12x45'",
    "init -RuntimeName 'bad`nsecret'", "up -ShowPassword", "status -Json", "init -NoBrowser",
    "init -ApiKey secret-value", "info -Json -ShowPassword",
])
def test_invalid_arguments_fail_before_python_discovery(tmp_path, command):
    """Reject raw key flags, malformed transport values and unsupported switch combinations early."""
    result, calls, browsers, _, _, _ = _invoke_mock_native(tmp_path, command)
    assert result.returncode != 0
    assert calls == []
    assert browsers == []
    assert "secret-value" not in result.stdout


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
@pytest.mark.parametrize("command", ["info -Json", "info -ShowPassword"])
def test_info_optional_switches_are_forwarded(tmp_path, command):
    """Access metadata and deliberate password display remain capabilities of the unified CLI."""
    result, calls, _, _, _, _ = _invoke_mock_native(tmp_path, command)
    assert result.returncode == 0, result.stderr
    expected = "--json" if "Json" in command else "--show-password"
    assert _runtime_calls(calls)[0]["words"][-1] == expected
