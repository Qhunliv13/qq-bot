"""Build self-contained Linux VM scripts; never execute or log supplied code here.

Call the returned Python with ``vm_exec`` (``lang='python'``) and decode its
single JSON stdout object. These helpers need only the Python standard library.
Jobs run as the VM account, NOT in a security sandbox. No secrets are published
by the management protocol; a job's explicitly printed output is returned.
"""

import uuid

# Private override permits isolated local tests without touching the VM root.
_ROOT = "/root/higan/jobs"
# One shared runtime keeps ownership checks identical for status and cancellation.
_COMMON = r'''
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time

# Leave space for JSON metadata inside vm_exec's 8000-character stdout cap.
OUTPUT_LIMIT = 6000
LOG_LIMIT = 65536
TERMINAL = {"completed", "failed", "cancelled", "lost"}


def save(path, data):
    """Atomically replace private metadata, so readers cannot observe half JSON."""
    temporary = path.with_name(path.name + "." + str(os.getpid()) + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(data, handle)
    os.replace(temporary, path)


def load(path):
    """Read small management files, rejecting corrupt or oversized metadata."""
    with path.open("r", encoding="utf-8") as handle:
        data = handle.read(16385)
    if len(data) > 16384:
        raise ValueError("oversized metadata")
    return json.loads(data)


def private_dir(path):
    """Refuse symlinks or directories writable/readable by other VM accounts."""
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("unsafe job directory")
    if info.st_mode & 0o077:
        raise ValueError("unsafe job permissions")


def process_info(pid):
    """Bind a Linux process to its UID, birth tick, session and exact argv."""
    if type(pid) is not int or pid <= 1:
        raise ValueError("invalid managed process")
    proc = Path("/proc") / str(pid)
    # comm can contain spaces or parentheses; fields follow the final ')'.
    fields = (proc / "stat").read_text().rsplit(")", 1)[1].split()
    argv = (proc / "cmdline").read_bytes().split(b"\0")
    return {"pid": pid, "uid": proc.stat().st_uid,
            "starttime": fields[19], "pgrp": int(fields[2]),
            "session": int(fields[3]), "state": fields[0],
            "argv": [os.fsdecode(arg) for arg in argv if arg]}


def owned(job, owner):
    """Never signal a reused PID or an unrelated command, even with fake argv metadata."""
    try:
        current = process_info(owner["pid"])
        expected = [owner["python"], str(job / "runner.py")]
        return (current["uid"] == os.getuid() == owner["uid"]
                and current["starttime"] == owner["starttime"]
                and current["argv"] == owner["argv"] == expected
                and current["pgrp"] == current["session"] == owner["pid"]
                and current["state"] != "Z")
    except (OSError, ValueError, KeyError, TypeError):
        return False


def report(job, data):
    """Whitelist public metadata and decode at most the final 6000 output bytes."""
    result = {key: data.get(key) for key in (
        "job_id", "status", "exit_code", "created_at", "started_at", "finished_at")}
    result["ok"] = True
    try:
        with (job / "output.log").open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - OUTPUT_LIMIT))
            # Ignore incomplete boundary characters, keeping encoded output <=6000.
            result["output"] = handle.read(OUTPUT_LIMIT).decode("utf-8", "ignore")
        result["output_truncated"] = size > OUTPUT_LIMIT
    except FileNotFoundError:
        result.update(output="", output_truncated=False)
    return result


def emit(result):
    """Keep complete JSON below transport limits, including escaped control bytes."""
    encoded = json.dumps(result, ensure_ascii=False)
    if len(encoded) > 7800 and "output" in result:
        # JSON escaping can expand even ASCII control characters up to sixfold.
        output = result["output"]
        low, high = 0, len(output)
        result["output_truncated"] = True
        while low < high:
            count = (low + high + 1) // 2
            result["output"] = output[-count:] if count else ""
            if len(json.dumps(result, ensure_ascii=False)) <= 7800:
                low = count
            else:
                high = count - 1
        result["output"] = output[-low:] if low else ""
        encoded = json.dumps(result, ensure_ascii=False)
    print(encoded)


def resolve(root, job_id):
    """Use only a validated UUID beneath a private non-symlink root."""
    private_dir(root)
    job = root / job_id
    private_dir(job)
    return job
'''

# A detached supervisor owns the session throughout execution, including bash
# descendants; bounded pipe draining avoids unlimited on-disk redirect files.
_RUNNER = _COMMON + r'''
import selectors


def run():
    """Capture output independently of the 30-second management request lifetime."""
    os.umask(0o077)
    job = Path(__file__).resolve().parent
    cancelled = False

    def mark_cancelled(signum, frame):
        """Keep the supervisor alive long enough to persist child termination."""
        nonlocal cancelled
        cancelled = True

    signal.signal(signal.SIGTERM, mark_cancelled)
    data = load(job / "status.json")
    child = None
    try:
        # Bootstrap registers the detached PID before the child is allowed to run.
        deadline = time.monotonic() + 3
        while not (job / "owner.json").exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("registration timeout")
            time.sleep(0.01)
        owner = load(job / "owner.json")
        if not owned(job, owner):
            raise RuntimeError("registration mismatch")
        command = ([sys.executable, "-u", str(job / "code.py")]
                   if data["lang"] == "python"
                   else ["/bin/bash", str(job / "code.sh")])
        data.update(status="running", started_at=time.time())
        save(job / "status.json", data)
        # Neither child nor supervisor inherits the VM tool's stdout/stderr pipe.
        child = subprocess.Popen(command, cwd=str(job), stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        tail = b""
        selector = selectors.DefaultSelector()
        selector.register(child.stdout, selectors.EVENT_READ)
        with (job / "output.log").open("wb") as output:
            while selector.get_map():
                for key, events in selector.select(0.1):
                    chunk = os.read(key.fileobj.fileno(), 16384)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    tail = (tail + chunk)[-LOG_LIMIT:]
                    output.seek(0)
                    output.write(tail)
                    output.truncate()
                    output.flush()
        selector.close()
        child.stdout.close()
        code = child.wait()
        data.update(exit_code=code,
                    status="cancelled" if cancelled or (job / "cancel.request").exists()
                    else ("completed" if code == 0 else "failed"))
    except Exception:
        # Do not expose execution source or exception text through management logs.
        if child is not None and child.poll() is None:
            child.kill()
            child.wait(timeout=2)
        data.update(status="cancelled" if cancelled else "failed", exit_code=None)
    data["finished_at"] = time.time()
    save(job / "status.json", data)


run()
'''


def _job_id(job_id):
    """Validate canonical UUIDs locally before any remote path can be generated."""
    if not isinstance(job_id, str):
        raise ValueError("job_id must be a canonical UUID")
    try:
        valid = str(uuid.UUID(job_id)) == job_id
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("job_id must be a canonical UUID")
    return job_id


def build_start(code, lang, *, _root=_ROOT):
    """Return a quick Python bootstrap for a detached Python or bash job.

    ``_root`` is a private test seam. Public results contain no source, process
    credentials, or filesystem paths. The VM user must have a Linux /proc mount.
    """
    if not isinstance(code, str) or not code.strip():
        raise ValueError("code must be a non-empty string")
    if lang not in ("python", "bash"):
        raise ValueError("lang must be python or bash")
    return _COMMON + "\nROOT = Path(" + repr(str(_root)) + ")\nCODE = " + repr(code) + "\nLANG = " + repr(lang) + "\nRUNNER = " + repr(_RUNNER) + r'''
import uuid


def start():
    """Return immediately after registering a detached, private job directory."""
    os.umask(0o077)
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    private_dir(ROOT)
    job_id = str(uuid.uuid4())
    job = ROOT / job_id
    job.mkdir(mode=0o700)
    job = job.resolve()
    (job / ("code.py" if LANG == "python" else "code.sh")).write_text(CODE, encoding="utf-8")
    (job / "runner.py").write_text(RUNNER, encoding="utf-8")
    data = {"job_id": job_id, "lang": LANG, "status": "starting",
            "created_at": time.time(), "started_at": None,
            "finished_at": None, "exit_code": None}
    save(job / "status.json", data)
    runner = subprocess.Popen([sys.executable, str(job / "runner.py")],
                              cwd=str(job), stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              start_new_session=True, close_fds=True)
    # Popen can return before execve replaces the forked bootstrap's argv.
    # Register only after /proc confirms the exact detached runner command.
    deadline = time.monotonic() + 2
    while True:
        owner = process_info(runner.pid)
        owner["python"] = sys.executable
        if owned(job, owner):
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("runner launch timeout")
        time.sleep(0.01)
    save(job / "owner.json", owner)
    return report(job, data)


try:
    emit(start())
except Exception:
    print(json.dumps({"ok": False, "error": "job_start_failed"}))
'''


def _manage(job_id, _root, cancel):
    """Share bounded status reads and cancellation ownership verification."""
    job_id = _job_id(job_id)
    return (_COMMON + "\nROOT = Path(" + repr(str(_root)) + ")\nJOB_ID = "
            + repr(job_id) + "\nCANCEL = " + repr(cancel) + r'''


def manage():
    """Status is read-only; cancellation signals only a registered session leader."""
    job = resolve(ROOT, JOB_ID)
    # Match the absolute runner path used during bootstrap, including test roots.
    job = job.resolve()
    data = load(job / "status.json")
    if data["job_id"] != JOB_ID:
        raise ValueError("job metadata mismatch")
    if data["status"] not in TERMINAL:
        owner = load(job / "owner.json")
        if not owned(job, owner):
            # A missing runner is not permission to signal another process.
            result = report(job, data)
            result.update(status="lost", error="job_process_not_owned")
            return result
        if CANCEL:
            (job / "cancel.request").touch(mode=0o600, exist_ok=True)
            # Recheck immediately before each group signal, including escalation.
            if owned(job, owner):
                try:
                    os.killpg(owner["pid"], signal.SIGTERM)
                except ProcessLookupError:
                    pass
            deadline = time.monotonic() + 1
            while owned(job, owner) and time.monotonic() < deadline:
                time.sleep(0.02)
            if owned(job, owner):
                try:
                    os.killpg(owner["pid"], signal.SIGKILL)
                except ProcessLookupError:
                    pass
            # Prefer the wrapper's exit code if it had time to reap its child.
            data = load(job / "status.json")
            if data["status"] not in TERMINAL:
                data.update(status="cancelled", finished_at=time.time())
                save(job / "status.json", data)
    return report(job, data)


try:
    emit(manage())
except FileNotFoundError:
    print(json.dumps({"ok": False, "error": "job_not_found", "job_id": JOB_ID}))
except Exception:
    print(json.dumps({"ok": False, "error": "job_metadata_invalid", "job_id": JOB_ID}))
''')


def build_status(job_id, *, _root=_ROOT):
    """Return a fast read-only Python status script with an 6000-byte output tail."""
    return _manage(job_id, _root, False)


def build_cancel(job_id, *, _root=_ROOT):
    """Return an idempotent Python cancellation script; never accept a raw PID."""
    return _manage(job_id, _root, True)
