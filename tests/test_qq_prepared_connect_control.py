"""Fake-only prepared-host entry, strict evidence, and fixed shared-mode tests."""

import copy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from tools import qq_ephemeral_host_entry as shared
from tools import qq_prepared_connect_control as control

ROOT = Path(__file__).resolve().parents[1]
NONCE = "c" * 32


def encode(value):
    """Encode a final record with its fixed experiment marker."""
    return ("QQ_PREPARED_CONNECT_" + NONCE + ":" + json.dumps(value) + "\n").encode()


def fake_run(tmp_path, case="normal", *, phase=False, stall=None):
    """Run the actual authored entry with reusable modules and injected native fakes."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for fake-only entry validation")
    script = tmp_path / "prepared_case.cjs"
    # Function injection keeps all reusable modules in the same JS realm; no dlopen occurs.
    script.write_text(r"""
'use strict';
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const root = process.argv[2], scenario = process.argv[3], calls = [], writes = {}, timers = [], checkpoints = [];
let now = 0, exitCode = null;
const forbidden = () => { throw Error('forbidden native operation'); };
const engine = {initWithDeskTopConfig() { calls.push('engine.init'); }};
const login = {initConfig() { calls.push('login.init'); },
  addKernelLoginListener() { calls.push('listener.add'); return scenario === 'numeric' ? 7 : undefined; },
  removeKernelLoginListener: forbidden, getQRCodePicture: forbidden,
  connect() { calls.push('connect'); if (scenario === 'throw') throw Error('secret'); return true; }};
const wrapper = {
 NodeIQQNTWrapperEngine: {get() { calls.push('engine.get'); return engine; }},
 NodeIKernelLoginService: {get() { calls.push('login.get'); return login; }},
 NodeIQQNTStartupSessionWrapper: {create() { calls.push('startup.create'); if (scenario === 'prepare_fail') throw Error('secret'); return {}; }},
 NodeIQQNTWrapperSession: {getNTWrapperSession(name) { assert.equal(name, 'nt_1'); calls.push('session.get'); return {}; }}
};
const pkg = {name:'QQ',version:'3.2.34-53644',buildVersion:'53644',platform:'linux',eleArch:'x64'};
const injectedFs = {
 readFileSync() { return JSON.stringify(pkg); },
 writeFileSync(name, raw) { assert(Buffer.byteLength(raw) <= 16384); writes[name] = raw;
   if (name === '/tmp/qq-prepared-connect-progress.json' || name === '/tmp/qq-prepared-phase-progress.json') checkpoints.push(raw); }
};
function localRequire(name) {
 if (name === 'node:fs') return injectedFs;
 if (name === 'node:os') return {release: () => '6.6.0'};
 if (name === 'electron') return {app: {exit(code) { exitCode = code; }}};
 if (name.startsWith('/tmp/native_')) return require(path.join(root, 'qq_transport', path.basename(name)));
 throw Error('unexpected import');
}
const fakeProcess = {env:{QQ_WRAPPER_HOST_NONCE:'c'.repeat(32)},execPath:'/opt/QQ/qq',
 versions:{node:'22.19.0',electron:'39.0.0',modules:'140',napi:'10'},
 hrtime:{bigint:() => BigInt(now) * 1000000n},dlopen(mod) { calls.push('dlopen'); mod.exports = wrapper; }};
const phase = process.argv[4] === 'phase';
if (phase) fakeProcess.env.QQ_PREPARED_PHASE_CONTROL = '1';
const source = fs.readFileSync(path.join(root,'tools/qq_prepared_connect_entry.js'),'utf8');
new Function('require','process','setTimeout',source)(localRequire,fakeProcess,(fn,delay) => timers.push({fn,at:now+delay}));
(async () => {
 for (let spin=0; spin<100; spin++) {
  await Promise.resolve();
  const last = checkpoints.length ? JSON.parse(checkpoints.at(-1)).stage : '';
  if (process.argv[5] === last) break;
  if (timers.length) { timers.sort((a,b) => a.at-b.at); const timer=timers.shift(); now=timer.at; timer.fn(); }
 }
 const raw = writes['/tmp/qq-wrapper-host-result.json'];
 if (process.argv[5] === 'none') assert(raw, 'final result missing');
 console.log(JSON.stringify({raw, progress:writes[phase ? '/tmp/qq-prepared-phase-progress.json' : '/tmp/qq-prepared-connect-progress.json'], checkpoints, calls, exitCode}));
})().catch(error => { console.error(error); process.exitCode=1; });
""", encoding="utf-8")
    result = subprocess.run([node, str(script), str(ROOT), case, "phase" if phase else "old", stall or "none"], capture_output=True,
                            text=True, encoding="utf-8", timeout=15, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("case", ["normal", "throw", "numeric", "prepare_fail"])
def test_actual_entry_fake_native_order_and_failure(tmp_path, case):
    """Real host/login modules use factories once and never escalate a failed contract."""
    result = fake_run(tmp_path, case)
    value = control.parse_record(result["raw"].encode(), NONCE)
    progress = control.parse_progress(result["progress"].encode(), NONCE)
    for checkpoint in result["checkpoints"]:
        control.parse_progress(checkpoint.encode(), NONCE)
    assert progress == value["progress"]
    assert result["calls"][:4] == ["dlopen", "engine.get", "login.get", "startup.create"]
    if case == "prepare_fail":
        assert result["calls"] == ["dlopen", "engine.get", "login.get", "startup.create"]
        assert value["error"] == "prepare_failed"
        assert not value["initialization"]["attempted"]
    else:
        assert result["calls"][4:] == ["session.get", "engine.init", "login.init", "listener.add"] + ([] if case == "numeric" else ["connect"])
        assert value["ok"] == (case != "numeric")
    report = shared.base_report("prepared_connect_offline")
    control.project_report(report, value, result["exitCode"])
    for field in ("native_login_verified", "session_initialized", "full_sendrecv_verified", "safe_to_login", "production_cutover", "independent_backend_ready", "native_listener_disposed"):
        assert report[field] is False
    if case in ("normal", "throw"):
        assert report["native_host_prepared_verified"] and report["bounded_progress_verified"]
        assert report["native_connect_observation_verified"]
        assert value["connection"]["call_threw"] == (case == "throw")


@pytest.fixture
def successful(tmp_path):
    """Use an actual fake-run artifact rather than maintaining a second snapshot schema."""
    return json.loads(fake_run(tmp_path)["raw"].split(":", 1)[1])


@pytest.mark.parametrize("change", ["nonce", "secret", "host_bool", "host_phase", "history_skip", "history_time", "early_tick", "bool_time", "auth", "qr", "native_remove", "connect_not_attempted", "wait", "error", "disposed", "binding"])
def test_strict_final_records(successful, change):
    """Reject lies, additional native data, invalid time bounds and stage incoherence."""
    value = copy.deepcopy(successful)
    progress = value["progress"]
    if change == "nonce":
        value["nonce"] = "a" * 32
    elif change == "secret":
        progress["host"]["raw"] = "forbidden"
    elif change == "host_bool":
        progress["host"]["process_cleanup_required"] = 1
    elif change == "host_phase":
        progress["host"]["phase"] = "prepared"
    elif change == "history_skip":
        progress["history"].pop(3)
    elif change == "history_time":
        progress["history"][-1]["elapsed_ms"] = 0
    elif change == "early_tick":
        progress["history"][9]["elapsed_ms"] = 999
    elif change == "bool_time":
        progress["history"][0]["elapsed_ms"] = False
    elif change in ("auth", "qr", "native_remove"):
        progress["login"][{"auth": "authenticated", "qr": "qr_available", "native_remove": "native_listener_removed"}[change]] = True
    elif change == "connect_not_attempted":
        progress["login"].update(connect_result_type="not_attempted", connect_call_returned=False)
    elif change == "wait":
        value["connection"]["wait_completed"] = False
    elif change == "error":
        value["error"] = "native secret"
    elif change == "disposed":
        value["initialization"]["disposed"] = False
    else:
        value["binding"]["loaded"] = False
    with pytest.raises(shared.runtime.ProbeError):
        control.parse_record(encode(value), NONCE)


def test_bounds_duplicates_and_watchdog_progress(successful):
    """A last valid prefix is partial evidence, never a completed observation."""
    raw = encode(successful)
    for bad in (raw + b"\n", raw + b"x" * shared.LIMIT,
                raw.replace(b'"ok": true', b'"ok": false, "ok": true')):
        with pytest.raises(shared.runtime.ProbeError):
            control.parse_record(bad, NONCE)
    progress = successful["progress"]
    report = shared.base_report("prepared_connect_offline")
    control.project_progress(report, progress)
    assert report["bounded_progress_verified"]
    assert not report["native_connect_observation_verified"]
    assert report["status"] == "not_executed"


@pytest.mark.parametrize("mode,extra,missing,accepted", [
    ("prepared_connect_offline", True, False, True),
    ("prepared_connect_offline", False, False, False),
    ("prepared_connect_offline", True, True, False),
    ("initialize", True, False, False),
    ("initialize", False, False, True)])
def test_exact_auxiliary_whitelist_before_writes(monkeypatch, mode, extra, missing, accepted):
    """Only the prepared mode admits its mandatory extra module after namespace gates."""
    class WriteReached(Exception):
        """Stop before writing any filesystem object in this staging-gate test."""
    class FakePath:
        """Provide only observations required to reach the exact staging whitelist."""
        def __init__(self, path):
            self.path = path
        def exists(self):
            """The detached old root must not exist."""
            return False
        def read_bytes(self):
            """Return a fixed manifest accepted by the injected pure validator."""
            return b"manifest"
        def open(self, mode):
            """Signal that validation completed without actually creating a file."""
            raise WriteReached()
    monkeypatch.setattr(shared, "Path", FakePath)
    monkeypatch.setattr(shared.os, "getpid", lambda: 1)
    monkeypatch.setattr(shared.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(shared.os, "readlink", lambda path: "private-ipc")
    monkeypatch.setattr(shared.runtime, "namespace_ids", lambda: {"net": "private-net", "mnt": "private-mnt", "pid": "private-pid"})
    monkeypatch.setattr(shared, "validate_manifest", lambda raw: b"control")
    auxiliary = {key: b"module" for key in ("/tmp/native_login.js", "/tmp/native_metadata.js", "/tmp/qq-native-pristine-package.json")}
    if extra:
        auxiliary["/tmp/native_host.js"] = b"host"
    if missing:
        auxiliary.pop("/tmp/native_login.js")
    report = {"isolation": {key: True for key in ("network", "mount", "pid", "ipc", "pivotRoot", "oldroot_detached")}}
    parents = {key: "parent" for key in ("net", "mnt", "pid", "ipc")}
    with pytest.raises(WriteReached if accepted else shared.runtime.ProbeError):
        shared.stage_control(b"source", b"control", "mount", parents, report, auxiliary=auxiliary, control_mode=mode)


def test_recorded_actual_attempt_remains_incomplete():
    """Validate frozen native evidence without re-running QQ or assuming new-source parity."""
    artifact = ROOT / 'docs' / 'qq-prepared-connect-evidence.json'
    report = json.loads(artifact.read_text(encoding='utf-8'), object_pairs_hook=shared.unique_object)
    progress = report['connect_progress']
    parsed = control.parse_progress((json.dumps(progress) + '\n').encode(), progress['nonce'])
    assert parsed['stage'] == 'after_connect'
    assert report['completion'] == 'watchdog_30s' and report['exit_code'] == -9
    assert report['status'] == 'failed' and report['process_retired']
    assert report['native_host_prepared_verified']
    assert not report['bounded_progress_verified']
    assert report['host_record'] is None
    assert not report['native_login_verified'] and not report['full_sendrecv_verified']
    assert not any(progress['login']['native_callback_counts'].values())
    assert report['host_software_unchanged']
    assert len(report['host_software_sha256_before']) == 5
    assert report['host_software_sha256_before'] == report['host_software_sha256_after']
    assert all(report['offline_network'].values())
    for flag in ('network', 'mount', 'pid', 'ipc', 'pivotRoot', 'oldroot_detached',
                 'unprivileged', 'effective_capabilities_zero', 'no_new_privileges'):
        assert report['isolation'][flag]
    # Historical hashes identify that run, not necessarily the current source tree.
    assert set(report['staged_source_sha256']) == {
        '/tmp/native_host.js', '/tmp/native_login.js', '/tmp/native_metadata.js',
        '/tmp/qq-native-pristine-package.json'}


def test_fixed_mode_sources_and_no_sensitive_calls(monkeypatch):
    """Only fixed sources enter the unchanged shared namespace/privilege executor."""
    calls = []
    def capture(root, **kwargs):
        """Record delegation without invoking software."""
        calls.append((root, kwargs))
        return {"status": "not_executed"}
    monkeypatch.setattr(shared, "observe", capture)
    control.observe("root", execute=True)
    assert calls == [("root", {"execute": True, "control_mode": "prepared_connect_offline"})]
    sources = shared.fixed_sources("prepared_connect_offline")
    assert set(sources) == {shared.MAIN, "/tmp/native_host.js", "/tmp/native_login.js", "/tmp/native_metadata.js"}
    assert shared.LIMIT == 16384 and shared.QQ_TIMEOUT == 30 and shared.OUTER_TIMEOUT == 45
    source = sources[shared.MAIN].read_text(encoding="utf-8")
    assert source.count("controller.connect();") == 1
    assert source.count("host.prepare();") == 1
    assert "wrapper: host.getLoginWrapper()" in source
    for forbidden in ("getQRCodePicture(", "startNT(", "session.init(", "removeKernelLoginListener(", "process.argv", "new Proxy", "--no-sandbox"):
        assert forbidden not in source
