"""Offline native Windows launcher tests; standard library only, with no installer use."""

import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


# Prefer Windows PowerShell itself: pwsh success cannot prove PS5.1 compatibility.
ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "tools" / "windows-lite.ps1"
POWERSHELL = shutil.which("powershell.exe") if os.name == "nt" else None


def _ps_quote(value):
    """Create a literal PowerShell string for trusted test paths and Unicode text."""
    return "'" + str(value).replace("'", "''") + "'"


class LauncherSourceTests(unittest.TestCase):
    """Verify safe structural boundaries even where Windows is not available."""

    def test_entries_delegate_without_installing_or_changing_policy(self):
        """Keep all binding in one helper and execution-policy bypass process-only."""
        script = LAUNCHER.read_text(encoding="ascii")
        wrapper = (ROOT / "lite.ps1").read_text(encoding="ascii")
        command = (ROOT / "lite.cmd").read_text(encoding="ascii")
        self.assertIn("Get-Command $candidate", script)
        self.assertIn("@('py.exe', 'python.exe')", script)
        self.assertIn("sys.version_info >= (3, 10)", script)
        self.assertIn("@('-X', 'utf8', '-B', $nativeScript)", script)
        self.assertIn("& $launcher @args", wrapper)
        self.assertNotIn("param(", wrapper)
        self.assertIn('-ExecutionPolicy Bypass -File "%~dp0lite.ps1" %*', command)
        self.assertIn('set "result=%errorlevel%"', command)
        self.assertIn("exit /b %result%", command)
        self.assertIn("pause", command)
        # Only existing interpreters may be invoked: no install, elevation or shell text.
        for forbidden in ("wsl.exe", "docker", "astrbot", "pip install", "Invoke-WebRequest",
                          "Invoke-Expression", "Set-ExecutionPolicy", "RunAs", "SetEnvironmentVariable"):
            self.assertNotIn(forbidden.lower(), (script + wrapper + command).lower())
        self.assertNotIn("--prompt", script)
        self.assertNotIn("--api-key'", script)

    def test_test_functions_have_docstrings(self):
        """Retain repository function documentation requirements in this test module."""
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.assertTrue(ast.get_docstring(node), node.name)


@unittest.skipUnless(POWERSHELL, "Native Windows PowerShell 5.1 is required")
class NativePowerShellTests(unittest.TestCase):
    """Run the actual PS5.1 launchers with offline Python command mocks and real stdin."""

    def setUp(self):
        """Create isolated Unicode and spaced paths without changing the real checkout."""
        temporary = tempfile.TemporaryDirectory(prefix="windows-lite-")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.checkout = self.work / "checkout with spaces \u4e2d\u6587"
        (self.checkout / "tools").mkdir(parents=True)
        shutil.copy2(LAUNCHER, self.checkout / "tools" / "windows-lite.ps1")
        shutil.copy2(ROOT / "lite.ps1", self.checkout / "lite.ps1")
        shutil.copy2(ROOT / "lite.cmd", self.checkout / "lite.cmd")
        # Existence matters to the launcher; mocks never execute this placeholder runtime.
        (self.checkout / "tools" / "lite_native.py").write_text("# Offline test placeholder.\n", encoding="ascii")

    def _run(self, runner, *, stdin=None, environment=None):
        """Run a saved script, never a multiline inline shell command."""
        path = self.work / "run.ps1"
        # BOM is necessary for a PS5.1 runner that embeds Unicode test data.
        path.write_text(runner, encoding="utf-8-sig")
        return subprocess.run(
            [POWERSHELL, "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path)],
            cwd=self.work, input=stdin, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=25, check=False, env=environment,
        )

    def _mock(self, arguments="", *, root=True, pipeline=None, py_exit=0,
              python_exit=0, launch_exit=0, missing=(), broken=()):
        """Mock existing interpreter commands in-process and record exact argv and stdin."""
        log = self.work / "calls.jsonl"
        input_log = self.work / "stdin.txt"
        entry = self.checkout / "lite.ps1" if root else self.checkout / "tools" / "windows-lite.ps1"
        missing_values = ",".join(_ps_quote(item) for item in missing)
        broken_values = ",".join(_ps_quote(item) for item in broken)
        runner = (
            "$ErrorActionPreference = 'Stop'\n"
            "$utf8 = New-Object System.Text.UTF8Encoding($false)\n"
            "[Console]::OutputEncoding = $utf8\n"
            f"$global:MissingCommands = @({missing_values})\n"
            f"$global:BrokenCommands = @({broken_values})\n"
            "function Get-Command {\n"
            "  param([string] $Name, [string] $ErrorAction)\n"
            "  if ($global:MissingCommands -contains $Name) { throw 'Interpreter not installed.' }\n"
            "  Microsoft.PowerShell.Core\\Get-Command $Name -ErrorAction Stop\n"
            "}\n"
            "$mockPython = {\n"
            "  [CmdletBinding(PositionalBinding=$false)]\n"
            "  param([Parameter(ValueFromRemainingArguments=$true)][string[]] $Words,\n"
            "        [Parameter(ValueFromPipeline=$true)][AllowEmptyString()][string] $Line)\n"
            "  begin { $allWords = @($Words); $name = $MyInvocation.MyCommand.Name }\n"
            "  process {\n"
            "    if ($MyInvocation.ExpectingInput) {\n"
            f"      [IO.File]::AppendAllText({_ps_quote(input_log)}, $Line + [Environment]::NewLine, $utf8)\n"
            "    }\n"
            "  }\n"
            "  end {\n"
            "    $record = @{ name = $name; argv = @($allWords); cwd = (Get-Location).Path }\n"
            f"    [IO.File]::AppendAllText({_ps_quote(log)}, (ConvertTo-Json -Compress $record) + [Environment]::NewLine, $utf8)\n"
            "    if ($global:BrokenCommands -contains $name) { throw 'Broken interpreter.' }\n"
            f"    if ($allWords -contains '-B') {{ $global:LASTEXITCODE = {launch_exit} }}\n"
            f"    elseif ($name -eq 'py.exe') {{ $global:LASTEXITCODE = {py_exit} }}\n"
            f"    else {{ $global:LASTEXITCODE = {python_exit} }}\n"
            "  }\n"
            "}\n"
            "Set-Item Function:\\py.exe $mockPython\n"
            "Set-Item Function:\\python.exe $mockPython\n"
        )
        prefix = ""
        if pipeline is not None:
            prefix = "@(" + ",".join(_ps_quote(item) for item in pipeline) + ") | "
        result = self._run(runner + f"{prefix}& {_ps_quote(entry)} {arguments}\nexit $LASTEXITCODE\n")
        calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []
        input_text = input_log.read_text(encoding="utf-8") if input_log.exists() else ""
        return result, calls, input_text

    def test_default_web_prefers_py_and_preserves_exit(self):
        """Both entry layers use py -3, default web and preserve nonzero application exits."""
        for root in (False, True):
            with self.subTest(root=root):
                result, calls, _ = self._mock(root=root, launch_exit=17)
                self.assertEqual(result.returncode, 17, result.stderr)
                self.assertEqual(calls[-2]["name"], "py.exe")
                self.assertEqual(calls[-2]["argv"][:4], ["-3", "-X", "utf8", "-c"])
                self.assertEqual(calls[-1]["argv"], ["-3", "-X", "utf8", "-B",
                    str(self.checkout / "tools" / "lite_native.py"), "web"])
                self.assertEqual(calls[-1]["cwd"], str(self.work))

    def test_named_flags_remain_distinct_and_unicode_survives(self):
        """Typed root arguments map once without shell parsing or secret values in argv."""
        state = "C:\\Users\\Public\\state \u4e2d\u6587"
        persona = "C:\\Users\\Public\\persona \u4e2d\u6587.txt"
        result, calls, _ = self._mock(
            "configure -DataDir " + _ps_quote(state) + " -ModelBase 'http://127.0.0.1:11434/v1' "
            "-Model 'model; $literal & name' -ApiKeyEnv MY_MODEL_KEY -PersonaFile " + _ps_quote(persona)
            + " -HistoryTurns 1000 -Port 0 -NoBrowser -NonInteractive"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[-1]["argv"][5:], ["configure", "--data-dir", state,
            "--model-base", "http://127.0.0.1:11434/v1", "--model", "model; $literal & name",
            "--api-key-env", "MY_MODEL_KEY", "--persona-file", persona,
            "--history-turns", "1000", "--port", "0", "--no-browser", "--non-interactive"])

    def test_pipeline_chat_uses_stdin_not_argv(self):
        """Preserve private Unicode chat lines and blank input through both PS entry layers."""
        lines = ["private \u4e2d\u6587 chat", "", "second line; & literal"]
        result, calls, text = self._mock("chat", pipeline=lines)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(text.splitlines(), lines)
        self.assertEqual(calls[-1]["argv"][-1], "chat")
        self.assertNotIn("private", repr(calls))

    def test_nonchat_pipeline_is_rejected_before_python(self):
        """Do not silently consume piped text when the selected mode cannot read it."""
        result, calls, _ = self._mock("info", pipeline=["private"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(calls, [])
        self.assertNotIn("private", result.stderr)

    def test_old_or_broken_py_falls_back_to_python(self):
        """An installed launcher does not hide a suitable python.exe when py -3 fails."""
        result, calls, _ = self._mock("info", py_exit=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call["name"] for call in calls], ["py.exe", "python.exe", "python.exe"])
        self.assertEqual(calls[-1]["argv"][:3], ["-X", "utf8", "-B"])
        self.assertEqual(calls[-1]["argv"][-1], "info")

    def test_launcher_exception_falls_back_to_python(self):
        """Treat a broken py installation as a candidate failure, not an installation request."""
        result, calls, _ = self._mock(broken=("py.exe",))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[-1]["name"], "python.exe")

    def test_missing_py_falls_back_to_python(self):
        """Resolve only python.exe when the Python launcher is not installed."""
        result, calls, _ = self._mock(missing=("py.exe",))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([call["name"] for call in calls], ["python.exe", "python.exe"])

    def test_missing_and_old_python_give_fixed_manual_hint(self):
        """Missing or pre-3.10 Python stops with exit 2 and never downloads an interpreter."""
        for missing, py_exit, python_exit in (((), 1, 1), (("py.exe", "python.exe"), 0, 0)):
            with self.subTest(missing=missing):
                result, calls, _ = self._mock(missing=missing, py_exit=py_exit, python_exit=python_exit)
                self.assertEqual(result.returncode, 2)
                self.assertIn("Python 3.10+", result.stderr)
                self.assertIn("python.org/downloads/windows/", result.stderr)
                self.assertIn("manually", result.stderr)
                self.assertTrue(all("-B" not in call["argv"] for call in calls))

    def test_invalid_options_never_start_python(self):
        """Validate commands, integer ranges and Windows absolute paths before launch."""
        for arguments in ("invalid-command", "-HistoryTurns 0", "-HistoryTurns 1001",
                          "-Port -1", "-Port 65536", "-DataDir /tmp/state",
                          "-DataDir 'C:relative'", "-ApiKeyEnv 'BAD; secret'", "-Model ''"):
            with self.subTest(arguments=arguments):
                result, calls, _ = self._mock(arguments)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(calls, [])
                self.assertNotIn("secret", result.stderr)

    def test_lower_bounds_and_upper_port_are_explicit(self):
        """Forward inclusive numeric endpoints instead of treating port zero as false."""
        result, calls, _ = self._mock("chat -HistoryTurns 1 -Port 65535")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[-1]["argv"][-4:], ["--history-turns", "1", "--port", "65535"])

    def test_cmd_launches_from_other_directory_and_preserves_exit(self):
        """The real double-click wrapper preserves Python's exit after its final pause."""
        target = self.checkout / "tools" / "lite_native.py"
        self.assertIn("placeholder", target.read_text(encoding="ascii"))
        target.write_text("import sys\nprint('CMD_NATIVE_LITE_OK')\nsys.exit(23)\n", encoding="ascii")
        # Execute a saved cmd script; pause receives EOF and must not replace the child's exit status.
        runner = self.work / "invoke.cmd"
        short_cmd = self.work / "lite.cmd"
        shutil.copy2(self.checkout / "lite.cmd", short_cmd)
        # Root cmd must still resolve its own PS wrapper; this path is entirely test-owned.
        shutil.copy2(self.checkout / "lite.ps1", self.work / "lite.ps1")
        (self.work / "tools").mkdir()
        shutil.copy2(self.checkout / "tools" / "windows-lite.ps1", self.work / "tools" / "windows-lite.ps1")
        shutil.copy2(target, self.work / "tools" / "lite_native.py")
        runner.write_text('@echo off\r\ncall "%~dp0lite.cmd" info\r\nexit /b %errorlevel%\r\n', encoding="ascii")
        result = subprocess.run([os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(runner)],
                                cwd=ROOT, input=b"", capture_output=True, timeout=25, check=False)
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertIn(b"CMD_NATIVE_LITE_OK", result.stdout)

    def test_real_python_preserves_redirected_stdin_unicode_and_secret_environment(self):
        """A real child, not only mocks, receives inherited stdin, exact paths and UTF-8."""
        target = self.checkout / "tools" / "lite_native.py"
        # Read the placeholder before replacing it, matching the repository edit observation rule.
        self.assertIn("placeholder", target.read_text(encoding="ascii"))
        target.write_text(
            "import json, os, sys\n"
            "print(json.dumps({'argv': sys.argv[1:], 'stdin': sys.stdin.read(), "
            "'cwd': os.getcwd(), 'key': os.environ.get('LITE_TEST_KEY'), "
            "'encoding': sys.stdout.encoding}, ensure_ascii=True))\n",
            encoding="ascii",
        )
        # Make the probe select this running Python without relying on py's host configuration.
        # The real native process still receives inherited stdin because no script pipeline is used.
        runner = (
            "$ErrorActionPreference = 'Stop'\n"
            "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)\n"
            "function Get-Command { param([string] $Name, [string] $ErrorAction)\n"
            "  if ($Name -eq 'py.exe') { throw 'No py in this test.' }\n"
            f"  [pscustomobject] @{{ Source = {_ps_quote(sys.executable)}; Name = 'python.exe'; CommandType = 'Application' }}\n"
            "}\n"
            f"& {_ps_quote(self.checkout / 'lite.ps1')} chat -DataDir {_ps_quote(str(self.checkout) + chr(92))} -ApiKeyEnv LITE_TEST_KEY\n"
            "exit $LASTEXITCODE\n"
        )
        environment = os.environ.copy()
        environment["LITE_TEST_KEY"] = "test-only-secret-never-an-argument"
        text = "native \u4e2d\u6587 stdin\nsecond line\n"
        result = self._run(runner, stdin=text, environment=environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["stdin"].splitlines(), text.splitlines())
        self.assertEqual(payload["argv"], ["chat", "--data-dir", str(self.checkout) + "\\", "--api-key-env", "LITE_TEST_KEY"])
        self.assertEqual(payload["cwd"], str(self.work))
        self.assertEqual(payload["key"], environment["LITE_TEST_KEY"])
        self.assertEqual(payload["encoding"], "utf-8")
        self.assertNotIn(environment["LITE_TEST_KEY"], repr(payload["argv"]))
        # Direct -File is how CMD launches the root wrapper; its redirected stdin must also survive.
        direct = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                 str(self.checkout / "lite.ps1"), "chat"], cwd=self.work,
                                input=text, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", env=environment, timeout=25, check=False)
        self.assertEqual(direct.returncode, 0, direct.stderr)
        self.assertEqual(json.loads(direct.stdout)["stdin"].splitlines(), text.splitlines())
        # Exercise a real native pipeline too: mock functions cannot detect PS5.1 ASCII encoding loss.
        prefix = "@(" + ",".join(_ps_quote(item) for item in ["pipeline \u4e2d\u6587", "", "last line"]) + ") | "
        piped_runner = runner.replace(f"& {_ps_quote(self.checkout / 'lite.ps1')} chat", prefix + f"& {_ps_quote(self.checkout / 'lite.ps1')} chat")
        piped = self._run(piped_runner, environment=environment)
        self.assertEqual(piped.returncode, 0, piped.stderr)
        self.assertEqual(json.loads(piped.stdout)["stdin"].splitlines(), ["pipeline \u4e2d\u6587", "", "last line"])


if __name__ == "__main__":
    unittest.main()
