"""Offline contract and real Linux subprocess tests for generated VM scripts.

Run with pytest, or ``python tests/test_vm_jobs.py`` on a Linux /proc host.
All managed jobs live in TemporaryDirectory and are cancelled during teardown.
"""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

# Import the helper directly: importing the plugin must not initialize AstrBot.
_TEST_ROOT = Path(__file__).resolve().parents[1]
_MODULE_PATH = _TEST_ROOT / "plugin" / "vm_jobs.py"
if not _MODULE_PATH.is_file():
    # The outer workspace can also collect these tests without plugin packaging.
    _MODULE_PATH = _TEST_ROOT / "_public_qqbot" / "plugin" / "vm_jobs.py"
_SPEC = importlib.util.spec_from_file_location("vm_jobs_test_subject", _MODULE_PATH)
vm_jobs = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(vm_jobs)


class BuilderTests(unittest.TestCase):
    """Validate builders without executing arbitrary code or requiring Linux."""

    def test_bootstraps_are_python(self):
        """Both supported languages still use the existing Python VM executor."""
        job_id = str(uuid.uuid4())
        for script in (vm_jobs.build_start("print('hello')", "python"),
                       vm_jobs.build_start("printf hello", "bash"),
                       vm_jobs.build_status(job_id), vm_jobs.build_cancel(job_id),
                       vm_jobs.build_list(), vm_jobs.build_list(offset=10, limit=100)):
            compile(script, "<vm-bootstrap>", "exec")

    def test_invalid_job_ids(self):
        """Never interpolate traversal paths, raw PIDs, noncanonical IDs or code."""
        for bad in (None, 1, "", "../escape", "/tmp/escape", "1", "x'\nimport os",
                    uuid.uuid4().hex, str(uuid.uuid4()).upper()):
            for builder in (vm_jobs.build_status, vm_jobs.build_cancel):
                with self.subTest(bad=bad, builder=builder.__name__):
                    with self.assertRaises(ValueError):
                        builder(bad)

    def test_invalid_list_inputs(self):
        """Reject invalid pagination before building or dispatching VM source."""
        for offset, limit in ((-1, 20), (True, 20), (0, 0), (0, 101),
                              (0, True), (0, 1.5), ("0", 20)):
            with self.subTest(offset=offset, limit=limit):
                with self.assertRaises(ValueError):
                    vm_jobs.build_list(offset=offset, limit=limit)

    def test_listing_shares_status_and_never_requests_output(self):
        """Keep one live identity validator and explicitly disable bulk log reads."""
        script = vm_jobs.build_list()
        self.assertEqual(script.count("def status(job, job_id):"), 1)
        self.assertIn("data = status(job, job_id)", script)
        self.assertIn("include_output=False", script)
        self.assertIn("data = status(job, JOB_ID)", vm_jobs.build_status(str(uuid.uuid4())))

    def test_listing_metadata_only_with_portable_owned_directory_stub(self):
        """Exercise generated pagination on Windows without claiming Linux PID coverage."""
        with tempfile.TemporaryDirectory(prefix="vm-list-contract-") as directory:
            root = Path(directory) / "jobs"
            root.mkdir()
            for unused in range(100):
                job_id = str(uuid.uuid4())
                job = root / job_id
                job.mkdir()
                (job / "status.json").write_text(json.dumps({
                    "job_id": job_id, "status": "completed", "exit_code": 0,
                    "created_at": 1234567890.123456, "started_at": 1234567890.123456,
                    "finished_at": 1234567890.123456}), encoding="utf-8")
                # A directory in place of a log proves listing never opens output.
                (job / "output.log").mkdir()
            script = vm_jobs.build_list(limit=100, _root=root)
            prefix, tail = script.rsplit("\ntry:\n    emit(listing())", 1)
            namespace = {}
            exec(compile(prefix, "<vm-list-contract>", "exec"), namespace)
            # POSIX ownership/permission enforcement is covered only by Linux tests.
            namespace["private_dir"] = lambda path: None
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exec(compile("try:\n    emit(listing())" + tail,
                             "<vm-list-contract>", "exec"), namespace)
            result = json.loads(output.getvalue())
            self.assertEqual(result["total"], 100)
            self.assertGreater(len(result["jobs"]), 0)
            self.assertEqual(result["next_offset"], len(result["jobs"]))
            self.assertLessEqual(len(output.getvalue().strip()), 7800)
            self.assertNotIn("output", output.getvalue())

    def test_invalid_start_inputs(self):
        """Reject unsupported interpreters and missing scripts before VM access."""
        for code, lang in (("", "python"), ("   ", "python"), (None, "python"),
                           ("echo hi", "sh"), ("echo hi", "ruby")):
            with self.assertRaises(ValueError):
                vm_jobs.build_start(code, lang)


@unittest.skipUnless(sys.platform.startswith("linux") and Path("/proc/self/stat").exists(),
                     "real job execution requires Linux /proc and POSIX process groups")
class LinuxJobTests(unittest.TestCase):
    """Exercise the exact returned scripts in fresh processes, without VM/network."""

    def setUp(self):
        """Use a fresh private root; no test touches /root/higan or production."""
        self.temporary = tempfile.TemporaryDirectory(prefix="vm-jobs-test-")
        self.root = Path(self.temporary.name) / "jobs"
        self.jobs = []
        self.calls = 0

    def tearDown(self):
        """Stop every registered job even when assertions fail, then release files."""
        for job_id in self.jobs:
            self.execute(vm_jobs.build_cancel(job_id, _root=self.root))
        self.temporary.cleanup()

    def execute(self, script):
        """Write audited scripts to files, with a strict management-call deadline."""
        self.calls += 1
        path = Path(self.temporary.name) / ("request-" + str(self.calls) + ".py")
        path.write_text(script, encoding="utf-8")
        result = subprocess.run([sys.executable, str(path)], capture_output=True,
                                text=True, timeout=8, check=True)
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertLess(len(result.stdout), 8000)
        # Simulate the VM's hard transport truncation before decoding.
        return json.loads(result.stdout[:8000])

    def start(self, code, lang="python"):
        """Record a job before assertions so teardown can always cancel it."""
        result = self.execute(vm_jobs.build_start(code, lang, _root=self.root))
        self.assertTrue(result["ok"], result)
        self.jobs.append(result["job_id"])
        return result

    def status(self, job_id):
        """Read only this test's explicitly registered private directory."""
        return self.execute(vm_jobs.build_status(job_id, _root=self.root))

    def wait_done(self, job_id):
        """Poll with a short deadline so broken wrappers cannot hang the suite."""
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            result = self.status(job_id)
            if result.get("status") in {"completed", "failed", "cancelled", "lost"}:
                return result
            time.sleep(0.03)
        self.fail("job did not finish within local test deadline")

    def test_listing_empty_root_is_read_only(self):
        """A first listing returns an empty page without creating the jobs root."""
        result = self.execute(vm_jobs.build_list(_root=self.root))
        self.assertEqual(result, {"ok": True, "jobs": [], "total": 0, "next_offset": None})
        self.assertFalse(self.root.exists())

    def test_listing_two_jobs_pagination_and_cancel(self):
        """List real jobs, preserve pagination, omit logs and reflect owned cancellation."""
        first = self.start("print('SECRET_LOG')")['job_id']
        second = self.start("import time\ntime.sleep(60)")['job_id']
        self.wait_done(first)
        page = self.execute(vm_jobs.build_list(limit=1, _root=self.root))
        self.assertEqual(page["total"], 2)
        self.assertEqual(page["next_offset"], 1)
        following = self.execute(vm_jobs.build_list(offset=1, limit=1, _root=self.root))
        self.assertIsNone(following["next_offset"])
        self.assertEqual([item["job_id"] for item in page["jobs"] + following["jobs"]],
                         sorted([first, second]))
        self.execute(vm_jobs.build_cancel(second, _root=self.root))
        rows = self.execute(vm_jobs.build_list(_root=self.root))
        self.assertEqual({item["job_id"]: item["status"] for item in rows["jobs"]},
                         {first: "completed", second: "cancelled"})
        encoded = json.dumps(rows)
        for secret in ("SECRET_LOG", "output", "owner", "pid", str(self.root)):
            self.assertNotIn(secret, encoded)

    def test_listing_skips_unsafe_and_corrupt_entries(self):
        """Do not enumerate foreign paths, symlinks, invalid UUIDs or malformed metadata."""
        good = self.start("print('done')")['job_id']
        self.wait_done(good)
        (self.root / str(uuid.uuid4())).symlink_to(self.temporary.name, target_is_directory=True)
        bad = self.root / str(uuid.uuid4())
        bad.mkdir(mode=0o700)
        (bad / "status.json").write_text("not-json", encoding="utf-8")
        (self.root / "private-secret-name").mkdir(mode=0o700)
        rows = self.execute(vm_jobs.build_list(_root=self.root))
        self.assertEqual(rows["total"], 1)
        self.assertEqual([item["job_id"] for item in rows["jobs"]], [good])
        self.assertNotIn("private-secret-name", json.dumps(rows))

    def test_listing_transport_cap_and_continuation(self):
        """One hundred summaries remain valid under the existing 8000-character transport."""
        self.root.mkdir(mode=0o700)
        for unused in range(100):
            job_id = str(uuid.uuid4())
            job = self.root / job_id
            job.mkdir(mode=0o700)
            data = {"job_id": job_id, "status": "completed", "exit_code": 0,
                    "created_at": 1234567890.123456, "started_at": 1234567890.123456,
                    "finished_at": 1234567890.123456}
            (job / "status.json").write_text(json.dumps(data), encoding="utf-8")
        page = self.execute(vm_jobs.build_list(limit=100, _root=self.root))
        self.assertEqual(page["total"], 100)
        self.assertGreater(len(page["jobs"]), 0)
        self.assertEqual(page["next_offset"], len(page["jobs"]))
        self.assertLessEqual(len(json.dumps(page, ensure_ascii=False)), 7800)
        following = self.execute(vm_jobs.build_list(offset=page["next_offset"], limit=100, _root=self.root))
        self.assertFalse({item["job_id"] for item in page["jobs"]} &
                         {item["job_id"] for item in following["jobs"]})

    def test_python_completion_permissions_and_private_response(self):
        """Persist success/timestamps without publishing source, PIDs or host paths."""
        started = self.start("import sys\nprint('hello 世界')\nprint('err', file=sys.stderr)")
        result = self.wait_done(started["job_id"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("hello 世界", result["output"])
        self.assertIn("err", result["output"])
        self.assertLessEqual(result["created_at"], result["started_at"])
        self.assertLessEqual(result["started_at"], result["finished_at"])
        job = self.root / started["job_id"]
        self.assertEqual(stat.S_IMODE(job.stat().st_mode), 0o700)
        for path in job.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o077, 0)
        self.assertNotIn("pid", result)
        self.assertNotIn(str(job), json.dumps(result))
        self.assertNotIn("import sys", json.dumps(result))
        self.assertEqual(result, self.status(started["job_id"]))

    def test_bash_completion(self):
        """Bash uses a code file rather than shell interpolation or inline -c."""
        result = self.wait_done(self.start("printf 'bash-ok\\n'; printf 'stderr-ok\\n' >&2", "bash")["job_id"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["output"], "bash-ok\nstderr-ok\n")

    def test_failed_exit(self):
        """A nonzero user exit is a managed failed job, not a VM request failure."""
        result = self.wait_done(self.start("print('before failure')\nraise SystemExit(7)")["job_id"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["exit_code"], 7)
        self.assertIn("before failure", result["output"])

    def test_script_quoting(self):
        """Newlines, quotes and non-ASCII source survive literal serialization."""
        text = "quotes ' \" \\ 中文\nnext"
        result = self.wait_done(self.start("print(" + repr(text) + ")")["job_id"])
        self.assertEqual(result["output"], text + "\n")

    def test_bounded_output_and_disk(self):
        """High output is drained but neither the file nor response grows unbounded."""
        job_id = self.start("import os\nos.write(1, b'x' * 2000000 + b'END')")["job_id"]
        result = self.wait_done(job_id)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["output"].encode("utf-8")), 6000)
        self.assertTrue(result["output"].endswith("END"))
        self.assertTrue(result["output_truncated"])
        self.assertLessEqual((self.root / job_id / "output.log").stat().st_size, 65536)

    def test_multibyte_output_cap(self):
        """Tail boundaries cannot expand replacement characters beyond the byte cap."""
        result = self.wait_done(self.start("print('界' * 5000, end='')")["job_id"])
        self.assertLessEqual(len(result["output"].encode("utf-8")), 6000)
        self.assertTrue(result["output_truncated"])

    def test_escaped_control_bytes_fit_transport(self):
        """Control-byte JSON escaping cannot truncate the whole management object."""
        result = self.wait_done(self.start("import os\nos.write(1, b'\\x01' * 10000 + b'END')")["job_id"])
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["output"].endswith("END"))
        self.assertLess(len(json.dumps(result, ensure_ascii=False)), 8000)
        self.assertTrue(result["output_truncated"])

    def test_start_and_status_do_not_wait_for_user_job(self):
        """A job longer than the VM's 30-second window returns immediately."""
        before = time.monotonic()
        job_id = self.start("import time\nprint('ready', flush=True)\ntime.sleep(45)")["job_id"]
        self.assertLess(time.monotonic() - before, 5)
        result = self.status(job_id)
        self.assertIn(result["status"], {"starting", "running"})
        self.assertIsNone(result["finished_at"])

    def test_cancel_is_idempotent(self):
        """Termination completes promptly and repeated cancellation changes nothing."""
        job_id = self.start("import time\ntime.sleep(60)")["job_id"]
        before = time.monotonic()
        result = self.execute(vm_jobs.build_cancel(job_id, _root=self.root))
        self.assertEqual(result["status"], "cancelled")
        self.assertLess(time.monotonic() - before, 5)
        again = self.execute(vm_jobs.build_cancel(job_id, _root=self.root))
        self.assertEqual(result, again)

    def test_cancel_kills_known_group_descendants(self):
        """A bash descendant in the registered session is stopped with its runner."""
        job_id = self.start("sleep 60 &\necho $!\nwait", "bash")["job_id"]
        deadline = time.monotonic() + 4
        output = ""
        while not output and time.monotonic() < deadline:
            output = self.status(job_id)["output"].strip()
        self.assertTrue(output)
        child_pid = int(output)
        self.execute(vm_jobs.build_cancel(job_id, _root=self.root))
        proc = Path("/proc") / str(child_pid) / "stat"
        # Some local PID1 implementations retain zombies briefly; these are dead.
        if proc.exists():
            self.assertEqual(proc.read_text().rsplit(")", 1)[1].split()[0], "Z")

    def test_cancel_escalates_for_term_ignoring_job(self):
        """An uncooperative child cannot consume the 30-second management window."""
        job_id = self.start("import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nprint('ready', flush=True)\ntime.sleep(60)")["job_id"]
        deadline = time.monotonic() + 4
        while "ready" not in self.status(job_id)["output"]:
            self.assertLess(time.monotonic(), deadline)
        before = time.monotonic()
        result = self.execute(vm_jobs.build_cancel(job_id, _root=self.root))
        self.assertEqual(result["status"], "cancelled")
        self.assertLess(time.monotonic() - before, 5)
        self.assertEqual(self.status(job_id)["status"], "cancelled")

    def test_completed_cancel_preserves_result(self):
        """Cancelling finished work never rewrites its exit code or timestamps."""
        job_id = self.start("print('done')")["job_id"]
        result = self.wait_done(job_id)
        self.assertEqual(result, self.execute(vm_jobs.build_cancel(job_id, _root=self.root)))

    def test_unknown_uuid(self):
        """Unknown valid IDs return a stable machine-readable not-found response."""
        self.root.mkdir(mode=0o700)
        for builder in (vm_jobs.build_status, vm_jobs.build_cancel):
            result = self.execute(builder(str(uuid.uuid4()), _root=self.root))
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], "job_not_found")

    def test_symlink_directory_refused(self):
        """Even valid UUIDs cannot redirect status/cancel outside the private root."""
        self.root.mkdir(mode=0o700)
        job_id = str(uuid.uuid4())
        (self.root / job_id).symlink_to(self.temporary.name, target_is_directory=True)
        result = self.status(job_id)
        self.assertEqual(result["error"], "job_metadata_invalid")

    def test_unrelated_pid_never_signalled(self):
        """Forged registration with a real owned unrelated PID cannot trigger killpg."""
        unrelated_script = Path(self.temporary.name) / "unrelated.py"
        unrelated_script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
        unrelated = subprocess.Popen([sys.executable, str(unrelated_script)],
                                     start_new_session=True)
        job_id = self.start("import time\ntime.sleep(60)")["job_id"]
        owner_path = self.root / job_id / "owner.json"
        original = owner_path.read_text(encoding="utf-8")
        try:
            # Wait until the genuine runner has consumed registration before tampering.
            deadline = time.monotonic() + 4
            while self.status(job_id)["status"] != "running":
                self.assertLess(time.monotonic(), deadline)
            owner = json.loads(original)
            fields = (Path("/proc") / str(unrelated.pid) / "stat").read_text().rsplit(")", 1)[1].split()
            owner.update(pid=unrelated.pid, starttime=fields[19],
                         pgrp=unrelated.pid, session=unrelated.pid)
            owner_path.write_text(json.dumps(owner), encoding="utf-8")
            result = self.execute(vm_jobs.build_cancel(job_id, _root=self.root))
            self.assertEqual(result["status"], "lost")
            self.assertIsNone(unrelated.poll())
            # Even spoofing argv metadata cannot bypass the live /proc cmdline check.
            owner["argv"] = [sys.executable, str(self.root / job_id / "runner.py")]
            owner_path.write_text(json.dumps(owner), encoding="utf-8")
            self.execute(vm_jobs.build_cancel(job_id, _root=self.root))
            self.assertIsNone(unrelated.poll())
        finally:
            owner_path.write_text(original, encoding="utf-8")
            os.killpg(unrelated.pid, signal.SIGKILL)
            unrelated.wait(timeout=3)

    def test_reused_starttime_refused(self):
        """Matching PID/argv is insufficient when the registered birth tick differs."""
        job_id = self.start("import time\ntime.sleep(60)")["job_id"]
        owner_path = self.root / job_id / "owner.json"
        original = owner_path.read_text(encoding="utf-8")
        try:
            # Corrupt only an already-running registration, not the startup handshake.
            deadline = time.monotonic() + 4
            while self.status(job_id)["status"] != "running":
                self.assertLess(time.monotonic(), deadline)
            owner = json.loads(original)
            owner["starttime"] = "0"
            owner_path.write_text(json.dumps(owner), encoding="utf-8")
            result = self.execute(vm_jobs.build_cancel(job_id, _root=self.root))
            self.assertEqual(result["status"], "lost")
            os.kill(owner["pid"], 0)
        finally:
            owner_path.write_text(original, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
