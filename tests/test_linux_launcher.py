"""Offline launcher contract, runnable with pytest or the stdlib unittest runner."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


# Locate the checkout from this file, never from a caller-controlled cwd.
REPO_ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash") if sys.platform.startswith("linux") else None


class LauncherSourceTests(unittest.TestCase):
    """Check portable source invariants even on Windows without Linux Bash."""

    def test_utf8_lf_and_thin_exec(self):
        """Keep the entry point executable as Bash without a BOM or CRLF."""
        source = (REPO_ROOT / "install.sh").read_bytes()
        self.assertTrue(source.startswith(b"#!/usr/bin/env bash\n"))
        self.assertNotIn(b"\r", source)
        text = source.decode("utf-8")
        self.assertIn('exec python3 "$repo_root/tools/bootstrap.py" "$@"', text)
        self.assertNotIn("eval ", text)
        self.assertNotIn("sudo ", text)


@unittest.skipUnless(BASH, "real Bash tests require Linux; run tools/test_linux_launcher.py in WSL")
class LinuxLauncherTests(unittest.TestCase):
    """Run the actual launcher with a fake python3 and no Docker or network."""

    def setUp(self):
        """Place executable fixtures in private Linux /tmp, not a Windows mount."""
        self.temporary = tempfile.TemporaryDirectory(prefix="qhunliv-launcher-", dir="/tmp")
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.checkout = root / "checkout with spaces"
        self.checkout.mkdir()
        (self.checkout / "tools").mkdir()
        self.launcher = self.checkout / "install.sh"
        shutil.copyfile(REPO_ROOT / "install.sh", self.launcher)
        self.caller = root / "unrelated working directory"
        self.caller.mkdir()
        binary = root / "private bin"
        binary.mkdir()
        fake_python = binary / "python3"
        # A file-backed executable records argv/stdin, avoiding shell inline code
        # and recursive lookup through the fake python3 at the front of PATH.
        fake_python.write_text(
            "#!" + sys.executable + "\n"
            "import json, os, sys\n"
            "print(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd(), "
            "'stdin': sys.stdin.read()}, ensure_ascii=True))\n"
            "raise SystemExit(int(os.environ.get('FAKE_PYTHON_EXIT', '0')))\n",
            encoding="utf-8",
            newline="\n",
        )
        fake_python.chmod(0o700)
        self.environment = os.environ.copy()
        self.environment["PATH"] = str(binary) + os.pathsep + os.environ.get("PATH", "")
        # A noisy CDPATH must not contaminate the resolved checkout path.
        self.environment["CDPATH"] = str(root)
        self.environment.pop("FAKE_PYTHON_EXIT", None)

    def run_launcher(self, arguments, stdin="", exit_code=0):
        """Execute Bash directly and return the fake Python's recorded contract."""
        self.environment["FAKE_PYTHON_EXIT"] = str(exit_code)
        result = subprocess.run(
            [BASH, str(self.launcher), *arguments],
            cwd=self.caller,
            env=self.environment,
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, exit_code, result.stderr)
        self.assertEqual(result.stderr, "")
        record = json.loads(result.stdout)
        self.assertEqual(record["argv"][0], str(self.checkout / "tools" / "bootstrap.py"))
        self.assertEqual(record["cwd"], str(self.caller))
        return record

    def test_default_wizard_from_other_cwd(self):
        """Open visual setup only for empty argv; explicit up still remains available."""
        record = self.run_launcher([])
        self.assertEqual(record["argv"][1:], ["wizard"])

    def test_explicit_up_is_unchanged(self):
        """Keep unattended and expert startup compatible with the original verb."""
        record = self.run_launcher(["up"])
        self.assertEqual(record["argv"][1:], ["up"])

    def test_exact_arguments_are_not_evaluated(self):
        """Preserve spaces, empty arguments, quotes, Unicode and shell syntax."""
        arguments = ["chat", "--prompt", "你好 'quoted' $HOME $(touch not-created); *", ""]
        record = self.run_launcher(arguments)
        self.assertEqual(record["argv"][1:], arguments)
        self.assertFalse((self.caller / "not-created").exists())

    def test_stdin_is_not_consumed(self):
        """Leave piped multi-line chat input available to the shared bootstrap."""
        prompt = "first line\n第二行 with spaces\n"
        record = self.run_launcher(["chat"], stdin=prompt)
        self.assertEqual(record["stdin"], prompt)

    def test_nonzero_exit_is_forwarded(self):
        """Expose child failure unchanged instead of reporting false success."""
        record = self.run_launcher(["doctor"], exit_code=37)
        self.assertEqual(record["argv"][1:], ["doctor"])


if __name__ == "__main__":
    unittest.main()
