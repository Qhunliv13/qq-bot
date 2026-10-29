"""Explicit stdlib-only POSIX smoke tests; never import or launch real xdotool.

Run this file explicitly after review. Temporary executable is a fixed Python
fixture, has no GUI imports, and receives an empty environment. Not auto-collected
by pytest. Linux /proc checks allow a killed orphan zombie but never a live child.
"""

import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest import mock


_PACKAGE = "posix_pointer_subject"
_package = types.ModuleType(_PACKAGE)
_package.__path__ = [str(Path(__file__).resolve().parents[1] / "plugin")]
sys.modules[_PACKAGE] = _package
_SPEC = importlib.util.spec_from_file_location(
    _PACKAGE + ".vm_pointer_adapter", Path(_package.__path__[0]) / "vm_pointer_adapter.py")
subject = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(subject)

# This fixture accepts only the adapter's existing argv. It does no desktop work.
_FIXTURE = """#!/usr/bin/python3
import os
import sys
import time
if sys.argv[1] == 'mouseup':
    print('private stdout')
    print('private stderr', file=sys.stderr)
    sys.exit(7)
if sys.argv[1] == 'mousedown':
    sys.exit(0)
child = os.fork()
if child == 0:
    time.sleep(30)
    os._exit(0)
with open('child.pid', 'w') as target:
    target.write(str(child))
time.sleep(30)
"""


@unittest.skipUnless(os.name == "posix", "POSIX process groups required")
class PosixProcessTests(unittest.TestCase):
    """Use only a temporary fixed harmless executable with an empty environment."""

    def setUp(self):
        """Create a real nonsymlink executable fixture, never a GUI executable."""
        self.temp = tempfile.TemporaryDirectory(prefix="pointer-adapter-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.executable = self.root / "fixed-harmless-fixture"
        self.executable.write_text(_FIXTURE, encoding="utf-8")
        self.executable.chmod(0o700)
        self.adapter = subject.VmPointerAdapter(
            executable_path=self.executable, env={}, cwd=self.root)

    def test_success_nonzero_no_output(self):
        """Exercise real zero/nonzero exit waits while all streams stay DEVNULL."""
        self.assertEqual(self.adapter.runner(("xdotool", "mousedown", "1"), 1), 0)
        self.assertEqual(self.adapter.runner(("xdotool", "mouseup", "1"), 1), 7)

    def test_timeout_group_kill_reap(self):
        """Prove a timed-out parent is reaped and its same-group descendant killed."""
        processes = []
        original = subject.subprocess.Popen

        def record(*args, **kwargs):
            """Retain the actual child handle solely for post-cleanup assertions."""
            process = original(*args, **kwargs)
            processes.append(process)
            return process

        started = time.monotonic()
        with mock.patch.object(subject.subprocess, "Popen", side_effect=record):
            with self.assertRaisesRegex(RuntimeError, "pointer_command_timeout"):
                self.adapter.runner(("xdotool", "mousemove", "1", "2"), 0.4)
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(processes[0].returncode, -9)
        with self.assertRaises(ChildProcessError):
            os.waitpid(processes[0].pid, os.WNOHANG)
        child = int((self.root / "child.pid").read_text(encoding="utf-8"))
        # A signal may need one scheduler tick; bounded sleeps are not busy-spin.
        for _ in range(50):
            status = Path('/proc') / str(child) / 'stat'
            if not status.exists() or status.read_text().split()[2] == 'Z':
                break
            time.sleep(0.01)
        else:
            self.fail("descendant survived managed-group teardown")


if __name__ == "__main__":
    unittest.main()
