"""Fake-only factory preparation tests; never load QQ or invoke real native code."""

from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "qq_transport" / "native_host.js"
NODE = shutil.which("node")
PRELUDE = r"""
'use strict';
const assert = require('node:assert/strict');
const {createNativeHost, prepareNativeHost, NativeHostError} = require(process.argv[2]);
const secret = 'sensitive-native-config';
function fixture(mode = 'startup') {
  const calls = [];
  const forbidden = () => { throw Error('unexpected-native-lifecycle'); };
  const engine = {init: forbidden, destroy: forbidden, config: secret};
  const login = {init: forbidden, connect: forbidden, removeKernelLoginListener: forbidden, account: secret};
  const startup = {start: forbidden, destroy: forbidden};
  const session = {init: forbidden, startNT: forbidden, close: forbidden};
  const wrapper = {
    NodeIQQNTWrapperEngine: {get(...args) { assert.equal(this, wrapper.NodeIQQNTWrapperEngine); calls.push(['engine.get', args]); return engine; }},
    NodeIKernelLoginService: {get(...args) { assert.equal(this, wrapper.NodeIKernelLoginService); calls.push(['login.get', args]); return login; }},
    NodeIQQNTStartupSessionWrapper: {create(...args) { calls.push(['startup.create', args]); return startup; }},
    NodeIQQNTWrapperSession: {
      getNTWrapperSession(...args) { calls.push(['session.get', args]); return session; },
      create(...args) { calls.push(['session.create', args]); return session; }
    }
  };
  return {calls, engine, login, startup, session, wrapper,
    options: {wrapper, sessionName: 'explicit-owner-session', lifecycleMode: mode}};
}
function code(fn, expected) {
  assert.throws(fn, error => error instanceof NativeHostError && error.code === expected && error.message === expected
    && !('cause' in error) && !JSON.stringify(error).includes(secret) && !String(error.stack).includes(secret));
}
"""


def run_node(tmp_path, source):
    """Execute saved injected fakes only, reporting assertion output on failure."""
    if not NODE:
        pytest.skip("Node.js is required for injected native host tests")
    script = tmp_path / "native_host_case.cjs"
    script.write_text(PRELUDE + "\n" + source, encoding="utf-8")
    result = subprocess.run([NODE, str(script), str(MODULE)], cwd=ROOT, capture_output=True,
                            text=True, encoding="utf-8", timeout=15, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_dormant_exact_order_and_cached_facade(tmp_path):
    """Construction is dormant; all native identities are acquired once in order."""
    run_node(tmp_path, r"""
const f = fixture(); const host = createNativeHost(f.options);
assert.deepEqual(f.calls, []); assert.equal(host.getSnapshot().phase, 'new');
code(host.getLoginWrapper, 'INVALID_STATE'); code(host.getPreparedSessionHandles, 'INVALID_STATE');
const snap = host.prepare();
assert.deepEqual(f.calls, [['engine.get', []], ['login.get', []], ['startup.create', []], ['session.get', ['explicit-owner-session']]]);
assert.equal(snap.phase, 'prepared'); assert.equal(snap.cleanup_required, 'process_exit');
assert.equal(snap.process_cleanup_required, true);
const facade = host.getLoginWrapper();
assert.equal(facade, host.getLoginWrapper()); assert(Object.isFrozen(facade));
for (let i = 0; i < 3; i++) {
  assert.equal(facade.NodeIQQNTWrapperEngine.get(), f.engine);
  assert.equal(facade.NodeIKernelLoginService.get(), f.login);
}
assert.equal(f.calls.length, 4);
assert(Object.hasOwn(facade.NodeIQQNTWrapperEngine, 'get'));
const handles = host.getPreparedSessionHandles();
assert(Object.isFrozen(handles));
assert.deepEqual(Object.keys(handles), ['startupSession', 'session', 'lifecycleMode', 'sessionName']);
assert.equal(handles.startupSession, f.startup); assert.equal(handles.session, f.session);
assert.equal(handles.sessionName, f.options.sessionName); assert.equal(handles.lifecycleMode, 'startup');
code(host.prepare, 'INVALID_STATE'); assert.equal(f.calls.length, 4);
""")


def test_explicit_legacy_and_convenience_invocation(tmp_path):
    """Explicit legacy uses only Session.create; convenience function really prepares."""
    run_node(tmp_path, r"""
const f = fixture('legacy'); delete f.wrapper.NodeIQQNTStartupSessionWrapper;
const host = prepareNativeHost(f.options);
assert.deepEqual(f.calls, [['engine.get', []], ['login.get', []], ['session.create', []]]);
assert.equal(host.getPreparedSessionHandles().startupSession, null);
assert.equal(host.getPreparedSessionHandles().session, f.session);
assert.equal(host.getSnapshot().startup_available, false);
""")


@pytest.mark.parametrize("field", ["engine", "login", "startup", "session"])
def test_all_static_methods_validated_before_any_factory(tmp_path, field):
    """Invalid later factories cannot leave earlier native factories partially run."""
    run_node(tmp_path, "const bad = " + repr(field) + r""";
const f = fixture();
const targets = {engine: ['NodeIQQNTWrapperEngine','get'], login: ['NodeIKernelLoginService','get'],
  startup: ['NodeIQQNTStartupSessionWrapper','create'], session: ['NodeIQQNTWrapperSession','getNTWrapperSession']};
const [owner, method] = targets[bad];
Object.defineProperty(f.wrapper[owner], method, {get() { throw Error(secret); }});
const host = createNativeHost(f.options); code(host.prepare, 'INVALID_WRAPPER');
assert.deepEqual(f.calls, []); assert.equal(host.getSnapshot().cleanup_required, 'none');
assert.equal(host.getSnapshot().phase, 'failed'); code(host.prepare, 'INVALID_STATE');
""")


@pytest.mark.parametrize("stage", [0, 1, 2, 3])
def test_no_retry_or_factory_fallback_after_throw(tmp_path, stage):
    """Uncertain throws are fixed errors and terminal, including first native get."""
    run_node(tmp_path, "const stage = " + str(stage) + r""";
const f = fixture();
const targets = [['NodeIQQNTWrapperEngine','get'], ['NodeIKernelLoginService','get'],
  ['NodeIQQNTStartupSessionWrapper','create'], ['NodeIQQNTWrapperSession','getNTWrapperSession']];
const [owner, method] = targets[stage];
f.wrapper[owner][method] = function() { f.calls.push(['throw']); throw Error(secret); };
const host = createNativeHost(f.options); code(host.prepare, 'PREPARE_FAILED');
assert.equal(f.calls.length, stage + 1); code(host.prepare, 'INVALID_STATE');
assert.equal(f.calls.length, stage + 1);
assert.equal(host.getSnapshot().cleanup_required, 'process_exit');
assert.equal(host.getSnapshot().process_cleanup_required, true);
assert.equal(host.getSnapshot().failure_code, 'PREPARE_FAILED');
code(host.getLoginWrapper, 'INVALID_STATE'); code(host.getPreparedSessionHandles, 'INVALID_STATE');
host.dispose(); assert.equal(f.calls.length, stage + 1);
""")


@pytest.mark.parametrize("mode,stage", [("startup", stage) for stage in range(4)]
                         + [("legacy", stage) for stage in range(3)])
@pytest.mark.parametrize("throws", [False, True])
def test_reentrant_dispose_is_terminal_at_every_factory(tmp_path, mode, stage, throws):
    """Disposal inside any factory clears prior handles and prevents subsequent calls."""
    run_node(tmp_path, "const mode = " + repr(mode) + "; const stage = " + str(stage)
             + "; const throws = " + str(throws).lower() + r""";
const f = fixture(mode);
const targets = [['NodeIQQNTWrapperEngine','get'], ['NodeIKernelLoginService','get'],
  ...(mode === 'startup' ? [['NodeIQQNTStartupSessionWrapper','create'],
    ['NodeIQQNTWrapperSession','getNTWrapperSession']] : [['NodeIQQNTWrapperSession','create']])];
const [owner, method] = targets[stage];
const original = f.wrapper[owner][method];
let host;
f.wrapper[owner][method] = function(...args) {
  const result = original.apply(this, args);
  const disposed = host.dispose();
  assert.equal(disposed.phase, 'disposed');
  assert.equal(disposed.cleanup_required, 'process_exit');
  if (throws) throw Error(secret);
  return result;
};
host = createNativeHost(f.options);
code(host.prepare, 'INVALID_STATE');
assert.equal(f.calls.length, stage + 1);
const snapshot = host.getSnapshot();
assert.equal(snapshot.phase, 'disposed');
assert.equal(snapshot.process_cleanup_required, true);
assert.equal(snapshot.cleanup_required, 'process_exit');
for (const field of ['engine_available', 'login_available', 'startup_available', 'session_available']) {
  assert.equal(snapshot[field], false);
}
code(host.prepare, 'INVALID_STATE');
code(host.getLoginWrapper, 'INVALID_STATE');
code(host.getPreparedSessionHandles, 'INVALID_STATE');
assert.deepEqual(host.dispose(), snapshot);
assert.equal(f.calls.length, stage + 1);
assert(!JSON.stringify(snapshot).includes(secret));
""")


@pytest.mark.parametrize("value", ["undefined", "''", "' '", "'x'.repeat(257)", "'a\\nb'", "null", "1"])
def test_session_name_has_no_default(tmp_path, value):
    """Only an explicit bounded nonempty name is accepted; no account name default."""
    run_node(tmp_path, "const f = fixture(); f.options.sessionName = " + value + r""";
code(() => createNativeHost(f.options), 'INVALID_CONFIG'); assert.deepEqual(f.calls, []);
""")


def test_no_account_fields_snapshot_leaks_or_native_cleanup(tmp_path):
    """Safe snapshots/errors omit names and native content; disposal is logical only."""
    run_node(tmp_path, r"""
const f = fixture();
code(() => createNativeHost({...f.options, accountIdentity: {uin: secret}}), 'INVALID_CONFIG');
code(() => createNativeHost({...f.options, lifecycleMode: undefined}), 'INVALID_CONFIG');
const unused = createNativeHost(f.options); assert.equal(unused.dispose().cleanup_required, 'none');
code(unused.prepare, 'INVALID_STATE'); assert.deepEqual(f.calls, []);
const host = prepareNativeHost(f.options); const facade = host.getLoginWrapper();
const snapshots = [host.getSnapshot(), host.dispose(), host.dispose()];
for (const snapshot of snapshots) {
  assert(Object.isFrozen(snapshot));
  assert.deepEqual(Object.keys(snapshot).sort(), ['phase','engine_available','login_available','startup_available','session_available',
    'failure_code','process_cleanup_required','cleanup_required'].sort());
  const raw = JSON.stringify(snapshot);
  for (const forbidden of [secret, f.options.sessionName, 'accountIdentity', 'config', 'sessionName']) assert(!raw.includes(forbidden));
}
assert.equal(host.getSnapshot().phase, 'disposed'); assert.equal(host.getSnapshot().engine_available, false);
assert.equal(host.getSnapshot().cleanup_required, 'process_exit'); assert.equal(f.calls.length, 4);
code(host.prepare, 'INVALID_STATE'); code(host.getLoginWrapper, 'INVALID_STATE');
code(host.getPreparedSessionHandles, 'INVALID_STATE'); code(facade.NodeIQQNTWrapperEngine.get, 'INVALID_STATE');
""")


def test_inherited_factories_and_getters_are_not_executed(tmp_path):
    """Own export/static descriptors are required, without invoking accessors."""
    run_node(tmp_path, r"""
const f = fixture();
f.wrapper.NodeIQQNTWrapperEngine = Object.create({get() { throw Error(secret); }});
const host = createNativeHost(f.options); code(host.prepare, 'INVALID_WRAPPER'); assert.deepEqual(f.calls, []);
const g = fixture(); Object.defineProperty(g.wrapper, 'NodeIKernelLoginService', {get() { throw Error(secret); }});
code(createNativeHost(g.options).prepare, 'INVALID_WRAPPER'); assert.deepEqual(g.calls, []);
const h = fixture(); h.wrapper.NodeIQQNTWrapperSession.getNTWrapperSession = () => null;
const last = createNativeHost(h.options); code(last.prepare, 'PREPARE_FAILED');
assert.equal(last.getSnapshot().process_cleanup_required, true); code(last.prepare, 'INVALID_STATE');
""")
