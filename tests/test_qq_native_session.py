"""Offline session lifecycle tests: only injected Node fakes, no QQ native loading."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "qq_transport" / "native_session.js"
NODE = shutil.which("node")
PRELUDE = r"""
'use strict';
const assert = require('node:assert/strict');
const {createSessionController, NativeSessionError} = require(process.argv[2]);
const secret = 'private-never-expose';
const tick = () => new Promise(resolve => setImmediate(resolve));
function fixture(mode = 'startup', legacyStartArgs = []) {
  const calls = [];
  let listener;
  const startup = {start(...args) { calls.push(['startup.start', args]); }};
  const session = {
    init(config, depends, dispatcher, value) { calls.push(['init', config, depends, dispatcher]); listener = value; },
    startNT(...args) { calls.push(['session.startNT', args]); },
    getMsgService() { throw new Error('must-never-getMsgService'); },
    close() { throw new Error('must-never-close'); }
  };
  const wrapper = {
    NodeIQQNTStartupSessionWrapper: {create(...args) { calls.push(['startup.create', args]); return startup; }},
    NodeIQQNTWrapperSession: {
      getNTWrapperSession(...args) { calls.push(['session.get', args]); return session; },
      create(...args) { calls.push(['session.create', args]); return session; }
    }
  };
  const options = {
    wrapper, dependsAdapter: {}, dispatcherAdapter: {},
    sessionConfig: {selfUin: '10001', selfUid: 'test-uid', clientVer: 'test-version', machineId: '', desktopPathConfig: {account_path: '/test/account'}, deviceInfo: {}, extra: {a2: secret, d2: secret, d2Key: secret, nested: [1, false, null]}},
    accountIdentity: {uin: '10001', uid: 'test-uid'},
    lifecycleMode: mode, sessionName: 'caller-chosen-name', legacyStartArgs
  };
  return {calls, startup, session, wrapper, options, get listener() { return listener; }};
}
function code(fn, expected) {
  assert.throws(fn, error => error instanceof NativeSessionError && error.code === expected && error.message === expected && !('cause' in error) && !JSON.stringify(error).includes(secret));
}
"""


def run_node(tmp_path, source):
    """Execute a saved fake-only JS program and report assertion failures."""
    if not NODE:
        pytest.skip("Node.js is required for injected session tests")
    script = tmp_path / "native_session_case.cjs"
    script.write_text(PRELUDE + "\n(async () => {\n" + source + "\n})().catch(error => { console.error(error); process.exitCode = 1; });\n", encoding="utf-8")
    result = subprocess.run([NODE, str(script), str(MODULE)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=15, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_explicit_startup_readiness_and_safe_events(tmp_path):
    """Factory order and post-start exact callback alone authorize messaging."""
    run_node(tmp_path, r"""
const f = fixture();
const c = createSessionController(f.options);
const events = [];
c.subscribe(event => { assert(Object.isFrozen(event)); assert(Object.isFrozen(event.payload)); events.push(event); });
assert.deepEqual(f.calls, []);
assert.equal(c.getSnapshot().phase, 'new');
assert.equal(c.isReady(), false);
code(c.start, 'INVALID_STATE'); code(c.getSessionForMessaging, 'INVALID_STATE');
c.initialize();
assert.deepEqual(f.calls.map(x => x[0]), ['startup.create', 'session.get', 'init']);
assert.deepEqual(f.calls[0][1], []);
assert.deepEqual(f.calls[1][1], ['caller-chosen-name']);
assert.equal(f.calls[2][2], f.options.dependsAdapter);
assert.equal(f.calls[2][3], f.options.dispatcherAdapter);
assert.equal(c.getSnapshot().phase, 'initialized');
f.listener.onOpentelemetryInit({is_init: true});
assert.equal(c.isReady(), false);
code(c.getSessionForMessaging, 'INVALID_STATE');
code(c.initialize, 'INVALID_STATE');
c.start();
assert.deepEqual(f.calls[3], ['startup.start', []]);
assert.equal(c.getSnapshot().phase, 'starting');
code(c.getSessionForMessaging, 'INVALID_STATE');
f.listener.onOpentelemetryInit({is_init: true, a2: secret, d2Key: secret});
assert.equal(c.isReady(), true);
assert.equal(c.getSessionForMessaging(), f.session);
code(c.start, 'INVALID_STATE');
await tick();
assert.deepEqual(events.map(e => e.code), ['INITIALIZED', 'STARTING', 'READY']);
const publicData = JSON.stringify([events, c.getSnapshot()]);
for (const value of [secret, '10001', 'test-uid', '/test/account', 'd2Key']) assert(!publicData.includes(value));
assert(Object.isFrozen(c.getSnapshot()));
assert.equal(c.dispose().native_cleanup_required, 'process_exit');
f.listener.onOpentelemetryInit({is_init: true});
assert.equal(c.isReady(), false);
assert.equal(c.dispose().phase, 'disposed');
assert.equal(f.calls.length, 4);
""")


@pytest.mark.parametrize("args", [[], [0]])
def test_legacy_fixed_contract(tmp_path, args):
    """Legacy requires selected argument overload and never touches startup factory."""
    run_node(tmp_path, "const f = fixture('legacy', " + json.dumps(args) + r""");
const c = createSessionController(f.options); c.initialize(); c.start();
assert.deepEqual(f.calls.map(x => x[0]), ['session.create', 'init', 'session.startNT']);
assert.deepEqual(f.calls[0][1], []);
assert.deepEqual(f.calls[2][1], f.options.legacyStartArgs);
f.listener.onOpentelemetryInit({is_init:true});
assert.equal(c.getSessionForMessaging(), f.session);
""")


@pytest.mark.parametrize("expression", ["false", "1", "'true'", "undefined", "null", "{}"])
def test_readiness_false_and_malformed_terminal(tmp_path, expression):
    """Invalid/negative callback data cannot authorize or retry the native start."""
    run_node(tmp_path, r"""
const f = fixture(); const c = createSessionController(f.options); c.initialize(); c.start();
""" + "f.listener.onOpentelemetryInit({is_init:" + expression + r"""});
assert.equal(c.getSnapshot().phase, 'failed');
f.listener.onOpentelemetryInit({is_init:true});
assert.equal(c.isReady(), false);
code(c.start, 'INVALID_STATE'); code(c.initialize, 'INVALID_STATE');
code(c.getSessionForMessaging, 'INVALID_STATE');
assert.equal(f.calls.length, 4);
""")


def test_no_fallback_for_factory_or_start_throw(tmp_path):
    """Partial native failures require process cleanup and never ABI retries."""
    run_node(tmp_path, r"""
for (const stage of ['startup.create','session.get','init','startup.start','session.startNT']) {
  const f = fixture(stage === 'session.startNT' ? 'legacy' : 'startup', [0]);
  const broken = () => { f.calls.push(['throw']); throw new Error(secret); };
  if (stage === 'startup.create') f.wrapper.NodeIQQNTStartupSessionWrapper.create = broken;
  if (stage === 'session.get') f.wrapper.NodeIQQNTWrapperSession.getNTWrapperSession = broken;
  if (stage === 'init') f.session.init = broken;
  if (stage === 'startup.start') f.startup.start = broken;
  if (stage === 'session.startNT') f.session.startNT = broken;
  const c = createSessionController(f.options);
  if (stage.endsWith('start') || stage.endsWith('startNT')) { c.initialize(); code(c.start, 'START_FAILED'); }
  else code(c.initialize, 'INITIALIZE_FAILED');
  assert.equal(c.getSnapshot().phase, 'failed');
  assert.equal(c.getSnapshot().native_cleanup_required, 'process_exit');
  const count = f.calls.length;
  code(c.initialize, 'INVALID_STATE'); code(c.start, 'INVALID_STATE');
  assert.equal(f.calls.length, count);
  assert.equal(f.calls.filter(x => x[0] === 'throw').length, 1);
}
const f = fixture(); delete f.wrapper.NodeIQQNTStartupSessionWrapper;
const c = createSessionController(f.options); code(c.initialize, 'INITIALIZE_FAILED');
assert.deepEqual(f.calls, []);
""")


def test_config_json_detached_identity_and_no_defaults(tmp_path):
    """Reject invalid bounded JSON/metadata before factory access; preserve unknown data."""
    run_node(tmp_path, r"""
const f = fixture(); const c = createSessionController(f.options);
f.options.sessionConfig.extra.nested[0] = 99;
f.options.sessionConfig.selfUin = 'changed';
c.initialize();
assert.equal(f.calls[2][1].selfUin, '10001');
assert.deepEqual(f.calls[2][1].extra.nested, [1,false,null]);
assert.equal(f.calls[2][1].extra.d2Key, secret);
assert.notEqual(f.calls[2][1].extra, f.options.sessionConfig.extra);
const mutations = [
  o => o.accountIdentity.uin = 'wrong', o => o.sessionConfig.selfUid = 'wrong',
  o => delete o.sessionConfig.clientVer, o => o.sessionConfig.machineId = 1,
  o => o.sessionConfig.desktopPathConfig.account_path = 'relative',
  o => o.sessionConfig.deviceInfo = [], o => o.sessionConfig.extra = undefined,
  o => o.sessionConfig.extra = NaN, o => o.sessionConfig.extra = new Date(),
  o => o.sessionConfig.extra = [1,,3], o => o.sessionConfig.extra = o.sessionConfig,
  o => o.sessionConfig.extra = 'x'.repeat(1048577),
  o => { let v = {}; o.sessionConfig.extra = v; for (let i=0; i<40; i++) { v.next={}; v=v.next; } },
  o => Object.defineProperty(o.sessionConfig, 'bad', {enumerable:true, get() { throw new Error(secret); }}),
  o => Object.defineProperty(o.accountIdentity, 'uid', {get() { throw new Error(secret); }}),
  o => o.sessionConfig.extra = new Proxy({}, {ownKeys() { throw new Error(secret); }}),
  o => delete o.sessionName, o => o.sessionName = '', o => delete o.lifecycleMode,
  o => o.lifecycleMode = 'guess', o => {o.lifecycleMode='legacy'; delete o.legacyStartArgs;},
  o => {o.lifecycleMode='legacy'; o.legacyStartArgs=[1];}
];
for (const mutate of mutations) {
  const f = fixture(); mutate(f.options);
  code(() => createSessionController(f.options), 'INVALID_CONFIG');
  assert.deepEqual(f.calls, []);
}
""")


def test_synchronous_callback_does_not_mask_start_failure(tmp_path):
    """Callback delivered within start is committed only after its void return."""
    run_node(tmp_path, r"""
for (const bad of [false, true, 0]) {
  const f = fixture(); f.startup.start = () => { f.listener.onOpentelemetryInit({is_init:true}); return bad; };
  const c = createSessionController(f.options); c.initialize(); code(c.start, 'START_FAILED');
  assert.equal(c.isReady(), false);
}
const f = fixture(); f.startup.start = () => { f.listener.onOpentelemetryInit({is_init:true}); };
const c = createSessionController(f.options); c.initialize(); c.start(); assert(c.isReady());
""")


def test_accessor_events_and_dormant_disposal(tmp_path):
    """Neither diagnostic getters nor late callbacks can execute hidden native calls."""
    run_node(tmp_path, r"""
const f = fixture(); const c = createSessionController(f.options);
assert.equal(c.dispose().native_cleanup_required, 'none'); code(c.initialize, 'INVALID_STATE');
assert.deepEqual(f.calls, []);
const g = fixture(); const d = createSessionController(g.options); d.initialize(); d.start();
let touched = false;
g.listener.onOpentelemetryInit(Object.defineProperty({}, 'is_init', {get() {touched=true; return true;}}));
assert.equal(touched, false); assert.equal(d.getSnapshot().failure_code, 'INVALID_EVENT');
const h = fixture(); const e = createSessionController(h.options); e.initialize(); e.start();
const raw = {is_init:true}; Object.defineProperty(raw, 'secret', {get() { throw new Error('never'); }});
h.listener.onOpentelemetryInit(raw); assert(e.isReady());
e.subscribe(() => {throw new Error(secret);});
e.dispose(); await tick();
""")


def test_prepared_handles_reuse_exact_objects_without_factory_access(tmp_path):
    """Prepared startup/legacy paths only init/start captured objects, never factories."""
    run_node(tmp_path, r"""
for (const mode of ['startup', 'legacy']) {
  const f = fixture(mode, [0]);
  const preparedHandles = Object.freeze({startupSession:mode === 'startup' ? f.startup : null,
    session:f.session,lifecycleMode:mode,sessionName:f.options.sessionName});
  const c = createSessionController({...f.options,wrapper:{},preparedHandles});
  assert.deepEqual(f.calls, []); assert(!c.isReady());
  c.initialize(); assert.deepEqual(f.calls.map(x => x[0]), ['init']);
  assert(!c.isReady()); c.start(); f.listener.onOpentelemetryInit({is_init:true});
  assert.equal(c.getSessionForMessaging(), f.session);
  assert.deepEqual(f.calls.map(x => x[0]), ['init',mode === 'startup' ? 'startup.start' : 'session.startNT']);
  c.dispose(); assert.equal(c.getSnapshot().native_cleanup_required, 'process_exit');
}
""")


def test_prepared_handles_reject_mismatch_and_accessors_before_native(tmp_path):
    """Prepared handles are own-data capabilities, not a bypass for config/identity."""
    run_node(tmp_path, r"""
const mutations = [
  h => h.sessionName = 'wrong', h => h.lifecycleMode = 'legacy',
  h => h.session = null, h => h.session = 1, h => h.startupSession = null,
  h => h.extra = true, h => delete h.session,
  h => Object.defineProperty(h, 'session', {get() {throw Error(secret);}}),
  h => h.session = new Proxy({}, {}), h => h.startupSession = new Proxy({}, {})
];
for (const mutate of mutations) {
  const f = fixture(); const h = {startupSession:f.startup,session:f.session,lifecycleMode:'startup',sessionName:f.options.sessionName};
  mutate(h); code(() => createSessionController({...f.options,preparedHandles:h}), 'INVALID_CONFIG');
  assert.deepEqual(f.calls, []);
}
const f = fixture('legacy', []);
code(() => createSessionController({...f.options,preparedHandles:{startupSession:f.startup,session:f.session,lifecycleMode:'legacy',sessionName:f.options.sessionName}}), 'INVALID_CONFIG');
const g = fixture(); const h = {startupSession:g.startup,session:g.session,lifecycleMode:'startup',sessionName:g.options.sessionName};
g.options.sessionConfig.selfUid = 'wrong';
code(() => createSessionController({...g.options,preparedHandles:h}), 'INVALID_CONFIG'); assert.deepEqual(g.calls, []);
const j = fixture(); j.session.init = () => {j.calls.push(['broken']); throw Error(secret);};
const c = createSessionController({...j.options,preparedHandles:{startupSession:j.startup,session:j.session,lifecycleMode:'startup',sessionName:j.options.sessionName}});
code(c.initialize, 'INITIALIZE_FAILED'); code(c.initialize, 'INVALID_STATE');
assert.deepEqual(j.calls.map(x => x[0]), ['broken']); c.dispose();
""")


@pytest.mark.parametrize("stage", ["startup.create", "session.get", "session.create"])
@pytest.mark.parametrize("throws", [False, True])
def test_factory_disposal_stops_all_later_native_calls(tmp_path, stage, throws):
    """A factory may synchronously dispose; its returned handle cannot revive us."""
    run_node(tmp_path, "const [stage, throws] = " + json.dumps([stage, throws]) + r""";
const f = fixture(stage === 'session.create' ? 'legacy' : 'startup');
const c = createSessionController(f.options);
const events = []; c.subscribe(event => events.push(event));
const owner = stage === 'startup.create' ? f.wrapper.NodeIQQNTStartupSessionWrapper : f.wrapper.NodeIQQNTWrapperSession;
const key = stage === 'session.get' ? 'getNTWrapperSession' : 'create';
const original = owner[key];
owner[key] = (...args) => {
  const result = original(...args); c.dispose();
  if (throws) throw Error(secret);
  return result;
};
code(c.initialize, 'INVALID_STATE');
assert.deepEqual(f.calls.map(x => x[0]), stage === 'session.get' ? ['startup.create', 'session.get'] : [stage]);
assert.deepEqual(c.getSnapshot(), {phase:'disposed', is_ready:false, failure_code:null, native_cleanup_required:'process_exit'});
code(c.initialize, 'INVALID_STATE'); code(c.start, 'INVALID_STATE'); code(c.getSessionForMessaging, 'INVALID_STATE');
assert.equal(c.dispose().phase, 'disposed');
await tick(); assert.deepEqual(events, []);
""")


@pytest.mark.parametrize("mode", ["startup", "legacy"])
@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("stage", ["init", "start"])
@pytest.mark.parametrize("throws", [False, True])
def test_init_start_disposal_is_not_a_success(tmp_path, mode, prepared, stage, throws):
    """Disposal during native init/start stays terminal for direct and prepared paths."""
    run_node(tmp_path, "const [mode, prepared, stage, throws] = " + json.dumps([mode, prepared, stage, throws]) + r""";
const f = fixture(mode, [0]);
if (prepared) {
  f.options.preparedHandles = {startupSession:mode === 'startup' ? f.startup : null,
    session:f.session,lifecycleMode:mode,sessionName:f.options.sessionName};
  // Prepared ownership must not touch either factory, even on disposal paths.
  f.options.wrapper = new Proxy({}, {get() {throw Error(secret);}});
}
const c = createSessionController(f.options);
const events = []; c.subscribe(event => events.push(event));
if (stage === 'init') {
  const original = f.session.init;
  f.session.init = (...args) => {
    original(...args); c.dispose(); f.listener.onOpentelemetryInit({is_init:true});
    if (throws) throw Error(secret);
  };
  code(c.initialize, 'INVALID_STATE');
} else {
  const target = mode === 'startup' ? f.startup : f.session;
  const key = mode === 'startup' ? 'start' : 'startNT';
  const original = target[key];
  target[key] = (...args) => {
    original(...args); f.listener.onOpentelemetryInit({is_init:true});
    c.dispose(); f.listener.onOpentelemetryInit({is_init:true});
    if (throws) throw Error(secret);
  };
  c.initialize(); await tick(); events.length = 0;
  code(c.start, 'INVALID_STATE');
}
const expected = prepared ? [] : mode === 'startup' ? ['startup.create','session.get'] : ['session.create'];
expected.push('init');
if (stage === 'start') expected.push(mode === 'startup' ? 'startup.start' : 'session.startNT');
assert.deepEqual(f.calls.map(x => x[0]), expected);
assert.deepEqual(c.getSnapshot(), {phase:'disposed', is_ready:false, failure_code:null, native_cleanup_required:'process_exit'});
code(c.initialize, 'INVALID_STATE'); code(c.start, 'INVALID_STATE'); code(c.getSessionForMessaging, 'INVALID_STATE');
await tick(); assert.deepEqual(events, []);
""")


@pytest.mark.parametrize("prepared", [False, True])
@pytest.mark.parametrize("mode", ["startup", "legacy"])
@pytest.mark.parametrize("method_name", ["init", "start"])
def test_method_accessors_never_execute_disposal(tmp_path, prepared, mode, method_name):
    """Method validation rejects accessors without executing their hidden callbacks."""
    run_node(tmp_path, "const [prepared, mode, name] = " + json.dumps([prepared, mode, method_name]) + r""";
const f = fixture(mode);
if (prepared) f.options.preparedHandles = {startupSession:mode === 'startup' ? f.startup : null,
  session:f.session,lifecycleMode:mode,sessionName:f.options.sessionName};
const c = createSessionController(f.options);
const target = name === 'init' || mode === 'legacy' ? f.session : f.startup;
const key = name === 'start' && mode === 'legacy' ? 'startNT' : name;
let getterCalls = 0;
Object.defineProperty(target, key, {get() {getterCalls += 1; c.dispose(); return () => {};}});
code(c.initialize, 'INITIALIZE_FAILED');
assert.equal(getterCalls, 0);
assert.equal(c.getSnapshot().phase, 'failed');
assert.equal(c.getSnapshot().native_cleanup_required, 'process_exit');
assert.deepEqual(f.calls.map(x => x[0]), prepared ? [] : mode === 'startup' ? ['startup.create','session.get'] : ['session.create']);
code(c.start, 'INVALID_STATE'); code(c.getSessionForMessaging, 'INVALID_STATE');
""")
