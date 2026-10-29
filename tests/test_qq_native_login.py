"""Offline Node subprocess tests: injected fakes only, never load native QQ."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "qq_transport" / "native_login.js"
NODE = shutil.which("node")

# The values below are intentionally synthetic test inputs, not deployment metadata.
PRELUDE = r"""
'use strict';
const assert = require('node:assert/strict');
const {createLoginController, NativeLoginError} = require(process.argv[2]);
const png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=';
const secret = 'private-do-not-report';
const tick = () => new Promise(resolve => setImmediate(resolve));
function fixture(overrides = {}) {
  const calls = [];
  let listener;
  const login = {
    initConfig(config) { calls.push(['login.init', config]); },
    addKernelLoginListener(value) { calls.push(['add']); listener = value; return 17; },
    removeKernelLoginListener(id) { calls.push(['remove', id]); },
    connect() { calls.push(['connect']); return true; },
    getQRCodePicture() { calls.push(['qr']); return true; },
    ...overrides
  };
  const engine = {initWithDeskTopConfig(config, adapter) { calls.push(['engine.init', config, adapter]); }};
  const wrapper = {
    NodeIQQNTWrapperEngine: {get() { calls.push(['engine.get']); return engine; }},
    NodeIKernelLoginService: {get() { calls.push(['login.get']); return login; }}
  };
  const options = {
    wrapper,
    engineConfig: {base_path_prefix:'/test',platform_type:5,app_type:4,app_version:'test-version',os_version:'test-os',use_xlog:false,qua:'test-qua',global_path_config:{desktopGlobalPath:'/test/global'},thumb_config:{maxSide:100,minSide:10,longLimit:100,density:1}},
    loginConfig: {machineId:'',appid:'123',platVer:'test-os',commonPath:'/test/common',clientVer:'test-version',hostName:'test-host',externalVersion:false},
    globalAdapter: {}, clock: () => 123
  };
  return {calls,engine,login,wrapper,options,get listener() { return listener; }};
}
function code(fn, expected) {
  assert.throws(fn, error => error instanceof NativeLoginError && error.code === expected && error.message === expected && !('cause' in error));
}
"""


def run_node(tmp_path, source):
    """Run a stored JS test with only fake exports and expose no native payloads."""
    if not NODE:
        pytest.skip("Node.js is required for injected-wrapper tests")
    script = tmp_path / "native_login_case.cjs"
    script.write_text(PRELUDE + "\n(async () => {\n" + source + "\n})().catch(error => { console.error(error); process.exitCode = 1; });\n", encoding="utf-8")
    result = subprocess.run(
        [NODE, str(script), str(MODULE)], cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_explicit_lifecycle_and_sensitive_projections(tmp_path):
    """Only actual callbacks produce QR/identity; snapshots never contain either."""
    run_node(tmp_path, r"""
const f = fixture();
const c = createLoginController(f.options);
assert.deepEqual(f.calls, []);
assert.equal(c.getSnapshot().phase, 'new');
code(() => c.connect(), 'INVALID_STATE');
code(() => c.getQRCodePicture(), 'INVALID_STATE');
assert.deepEqual(f.calls, []);
const events = [];
c.subscribe(event => { assert(Object.isFrozen(event)); assert(Object.isFrozen(event.payload)); events.push(event); });
c.initialize();
assert.deepEqual(f.calls.map(x => x[0]), ['engine.get','login.get','engine.init','login.init','add']);
assert.equal(f.calls[3][1].appid, '123');
assert(Object.isFrozen(f.calls[2][1].thumb_config));
code(() => c.initialize(), 'INVALID_STATE');
assert.equal(c.connect(), true);
assert.equal(c.getSnapshot().phase, 'connecting');
assert.equal(c.getIdentity(), null);
code(() => c.getQRCodePicture(), 'INVALID_STATE');
f.listener.onLoginConnected();
assert.equal(c.getQRCodePicture(), true);
assert.equal(c.getSnapshot().qr_available, false);
await tick();
assert.equal(events.filter(e => e.type === 'qr').length, 0);
f.listener.onQRCodeGetPicture({pngBase64QrcodeData:png,qrcodeUrl:'https://example.invalid/private-qr',extra:secret});
assert.equal(c.getSnapshot().qr_available, true);
f.listener.onQRCodeLoginSucceed({uin:'10001',uid:'observed-test-uid',extra:secret});
assert.deepEqual(c.getIdentity(), {uin:'10001',uid:'observed-test-uid'});
assert(Object.isFrozen(c.getIdentity()));
const snapshot = JSON.stringify(c.getSnapshot());
for (const value of [png,'private-qr','10001','observed-test-uid',secret]) assert(!snapshot.includes(value));
await tick();
assert.deepEqual(events.find(e => e.type === 'authenticated').payload, {uin:'10001',uid:'observed-test-uid'});
assert(!JSON.stringify(events).includes(secret));
c.dispose(); c.dispose();
assert.deepEqual(f.calls.filter(x => x[0] === 'remove'), [['remove',17]]);
f.listener.onLoginConnected();
assert.equal(c.getSnapshot().phase, 'disconnected');
""")


def test_reentrant_native_callback_order(tmp_path):
    """Synchronous connected/QR/auth callbacks cannot be overwritten by returns."""
    run_node(tmp_path, r"""
const f = fixture();
f.login.connect = () => { f.calls.push(['connect']); f.listener.onLoginConnecting(); f.listener.onLoginConnected(); return true; };
f.login.getQRCodePicture = () => {
  f.calls.push(['qr']);
  f.listener.onQRCodeGetPicture({pngBase64QrcodeData:png,qrcodeUrl:'https://example.invalid/qr'});
  f.listener.onQRCodeLoginSucceed({uin:'observed',uid:'observed-uid'});
  return true;
};
const c = createLoginController(f.options);
const seen = [];
c.subscribe(event => seen.push([event.type,event.phase]));
c.initialize(); c.connect();
assert.equal(c.getSnapshot().phase, 'connected');
c.getQRCodePicture();
assert.equal(c.getSnapshot().phase, 'authenticated');
await tick();
assert.deepEqual(seen.map(e => e[0]), ['initialized','connecting','connecting','connected','qr_pending','qr','authenticated']);
assert.equal(seen[seen.length-1][1], 'authenticated');
c.dispose();
""")


@pytest.mark.parametrize("stage", ["engine.get", "login.get", "engine.init", "login.init", "add"])
@pytest.mark.parametrize("throws", [False, True])
@pytest.mark.parametrize("lifetime", ["token", "process"])
def test_initialization_disposal_stops_at_native_boundary(tmp_path, stage, throws, lifetime):
    """Disposal inside any native call is terminal, even if it returns a numeric ID."""
    run_node(tmp_path, "const [stage, throws, lifetime] = " + json.dumps([stage, throws, lifetime]) + r""";
const f = fixture();
f.options.listenerLifetime = lifetime;
const targets = {
  'engine.get': [f.wrapper.NodeIQQNTWrapperEngine, 'get'],
  'login.get': [f.wrapper.NodeIKernelLoginService, 'get'],
  'engine.init': [f.engine, 'initWithDeskTopConfig'],
  'login.init': [f.login, 'initConfig'],
  add: [f.login, 'addKernelLoginListener']
};
const [target, name] = targets[stage];
const original = target[name];
let c;
target[name] = function (...args) {
  const result = original.apply(this, args);
  c.dispose();
  assert.equal(c.getSnapshot().cleanup_required, 'process_exit');
  if (throws) throw Error(secret);
  return result;
};
c = createLoginController(f.options);
const events = [];
c.subscribe(event => events.push(event));
code(() => c.initialize(), 'INVALID_STATE');
const order = ['engine.get', 'login.get', 'engine.init', 'login.init', 'add'];
assert.deepEqual(f.calls.map(call => call[0]), order.slice(0, order.indexOf(stage) + 1));
const snapshot = c.getSnapshot();
assert.equal(snapshot.disposed, true);
assert.equal(snapshot.phase, 'disconnected');
assert.equal(snapshot.cleanup_required, 'process_exit');
assert.equal(snapshot.listener_handle_available, false);
assert.equal(snapshot.native_listener_removed, false);
assert.equal(snapshot.authenticated, false);
assert.equal(snapshot.qr_available, false);
assert.equal(c.getIdentity(), null);
assert.equal(snapshot.initialization_step, {'engine.get':'factory_engine', 'login.get':'factory_login', 'engine.init':'engine_config', 'login.init':'login_config', add:'register_listener'}[stage]);
assert.equal(snapshot.failed_stage, null);
assert.equal(snapshot.failure_code, null);
assert.equal(snapshot.registration_call_returned, stage === 'add' && !throws);
assert.equal(snapshot.listener_id_type, stage !== 'add' ? 'not_attempted' : throws ? 'threw' : 'number');
assert(!JSON.stringify(snapshot).includes(secret));
c.dispose(); c.dispose();
code(() => c.initialize(), 'INVALID_STATE');
code(() => c.connect(), 'INVALID_STATE');
code(() => c.getQRCodePicture(), 'INVALID_STATE');
assert.deepEqual(f.calls.map(call => call[0]), order.slice(0, order.indexOf(stage) + 1));
await tick();
assert.deepEqual(events, []);
""")


def test_disposed_registration_keeps_only_safe_return_type(tmp_path):
    """Late void/invalid registration results retain telemetry, never ownership."""
    run_node(tmp_path, r"""
for (const result of [undefined, '17', null, {}, true, NaN, -1]) {
  const f = fixture();
  f.options.listenerLifetime = 'process';
  let c;
  f.login.addKernelLoginListener = () => { f.calls.push(['add']); c.dispose(); return result; };
  c = createLoginController(f.options);
  code(() => c.initialize(), 'INVALID_STATE');
  const snapshot = c.getSnapshot();
  assert.equal(snapshot.registration_call_returned, true);
  assert.equal(snapshot.listener_id_type, ['number', 'string', 'undefined'].includes(typeof result) ? typeof result : 'other');
  assert.equal(snapshot.listener_handle_available, false);
  assert.equal(snapshot.native_listener_removed, false);
  assert.equal(snapshot.cleanup_required, 'process_exit');
  assert.equal(snapshot.disposed, true);
  assert.equal(snapshot.phase, 'disconnected');
  assert.equal(snapshot.initialization_step, 'register_listener');
  assert.equal(snapshot.failed_stage, null);
  assert.equal(snapshot.failure_code, null);
  c.dispose();
  assert.deepEqual(f.calls.map(call => call[0]), ['engine.get', 'login.get', 'engine.init', 'login.init', 'add']);
}
""")


@pytest.mark.parametrize("operation", ["connect", "qr"])
@pytest.mark.parametrize("outcome", ["true", "false", "undefined", "throw"])
def test_request_disposal_cannot_report_success_or_resurrect(tmp_path, operation, outcome):
    """Synchronous callback disposal clears identity and dominates returns/exceptions."""
    run_node(tmp_path, "const [operation, outcome] = " + json.dumps([operation, outcome]) + r""";
const f = fixture();
let c;
const name = operation === 'connect' ? 'connect' : 'getQRCodePicture';
f.login[name] = () => {
  f.calls.push([operation]);
  if (operation === 'connect') f.listener.onLoginConnected();
  else {
    f.listener.onQRCodeGetPicture({pngBase64QrcodeData:png,qrcodeUrl:'https://example.invalid/qr'});
    f.listener.onQRCodeLoginSucceed({uin:'observed',uid:'observed-uid'});
    assert.notEqual(c.getIdentity(), null);
  }
  c.dispose();
  if (outcome === 'throw') throw Error(secret);
  return {true:true, false:false, undefined:undefined}[outcome];
};
c = createLoginController(f.options);
c.initialize();
if (operation === 'qr') { c.connect(); f.listener.onLoginConnected(); }
await tick();
const events = [];
c.subscribe(event => events.push(event));
code(() => operation === 'connect' ? c.connect() : c.getQRCodePicture(), 'INVALID_STATE');
const snapshot = c.getSnapshot();
assert.equal(snapshot.disposed, true);
assert.equal(snapshot.phase, 'disconnected');
assert.equal(snapshot.authenticated, false);
assert.equal(snapshot.qr_available, false);
assert.equal(snapshot.failure_code, null);
assert.equal(c.getIdentity(), null);
assert.equal(snapshot.cleanup_required, 'none');
assert.equal(snapshot.listener_handle_available, false);
assert.equal(snapshot.native_listener_removed, true);
assert(!JSON.stringify(snapshot).includes(secret));
if (operation === 'connect') {
  assert.equal(snapshot.connect_call_returned, outcome !== 'throw');
  assert.equal(snapshot.connect_result_type, outcome === 'throw' ? 'threw' : outcome === 'undefined' ? 'undefined' : 'boolean');
}
c.dispose(); c.dispose();
code(() => c.connect(), 'INVALID_STATE');
code(() => c.getQRCodePicture(), 'INVALID_STATE');
assert.deepEqual(f.calls.map(call => call[0]), ['engine.get', 'login.get', 'engine.init', 'login.init', 'add', 'connect', ...(operation === 'qr' ? ['qr'] : []), 'remove']);
assert.deepEqual(f.calls.filter(call => call[0] === 'remove'), [['remove', 17]]);
await tick();
assert.deepEqual(events, []);
""")


@pytest.mark.parametrize("stage", ["engine.get", "login.get", "engine.init", "login.init"])
def test_early_native_failure_requires_process_exit(tmp_path, stage):
    """Factory/config uncertainty requires owner cleanup even before registration."""
    run_node(tmp_path, "const stage = " + json.dumps(stage) + r""";
const f = fixture();
const targets = {
  'engine.get': [f.wrapper.NodeIQQNTWrapperEngine, 'get'],
  'login.get': [f.wrapper.NodeIKernelLoginService, 'get'],
  'engine.init': [f.engine, 'initWithDeskTopConfig'],
  'login.init': [f.login, 'initConfig']
};
const [target, name] = targets[stage];
target[name] = () => { f.calls.push([stage]); throw Error(secret); };
const c = createLoginController(f.options);
code(() => c.initialize(), 'INITIALIZE_FAILED');
assert.equal(c.getSnapshot().cleanup_required, 'process_exit');
assert.equal(c.getSnapshot().listener_id_type, 'not_attempted');
c.dispose(); c.dispose();
assert.equal(c.getSnapshot().cleanup_required, 'process_exit');
assert.equal(f.calls.filter(call => call[0] === 'remove').length, 0);
""")


@pytest.mark.parametrize("operation", ["connect", "qr", "init", "add", "remove"])
def test_uncertain_native_outcome_never_retries(tmp_path, operation):
    """A thrown native outcome is sanitized and consumed, including cleanup."""
    run_node(tmp_path, "const operation = " + json.dumps(operation) + r""";
const f = fixture();
const methodName = {connect:'connect',qr:'getQRCodePicture',init:'initConfig',add:'addKernelLoginListener',remove:'removeKernelLoginListener'}[operation];
f.login[methodName] = () => { f.calls.push(['uncertain']); throw Error(secret); };
const c = createLoginController(f.options);
if (operation === 'init' || operation === 'add') {
  code(() => c.initialize(), 'INITIALIZE_FAILED');
  assert.equal(c.getSnapshot().failed_stage, operation === 'init' ? 'login_config' : 'register_listener');
  assert.equal(c.getSnapshot().initialization_step, c.getSnapshot().failed_stage);
  assert.equal(c.getSnapshot().registration_call_returned, false);
  assert.equal(c.getSnapshot().listener_id_type, operation === 'add' ? 'threw' : 'not_attempted');
  code(() => c.initialize(), 'INVALID_STATE');
  c.dispose();
} else {
  c.initialize();
  if (operation === 'connect') {
    code(() => c.connect(), 'CONNECT_FAILED');
    code(() => c.connect(), 'INVALID_STATE');
    c.dispose();
  } else {
    c.connect(); f.listener.onLoginConnected();
    if (operation === 'qr') {
      code(() => c.getQRCodePicture(), 'QR_REQUEST_FAILED');
      code(() => c.getQRCodePicture(), 'INVALID_STATE');
      c.dispose();
    } else {
      code(() => c.dispose(), 'REMOVE_LISTENER_FAILED');
      c.dispose();
    }
  }
}
assert.equal(f.calls.filter(x => x[0] === 'uncertain').length, 1);
assert(!JSON.stringify(c.getSnapshot()).includes(secret));
""")


@pytest.mark.parametrize("bad", ["platform", "appid", "path", "unknown", "getter", "inherited", "static_accessor"])
def test_invalid_input_cannot_escalate_to_native(tmp_path, bad):
    """Invalid config/export descriptors are rejected without getters or calls."""
    run_node(tmp_path, "const bad = " + json.dumps(bad) + r""";
const f = fixture();
let expected = 'INVALID_CONFIG';
if (bad === 'platform') f.options.engineConfig.platform_type = 1;
if (bad === 'appid') f.options.loginConfig.appid = true;
if (bad === 'path') f.options.loginConfig.commonPath = 'C:\\host-secret';
if (bad === 'unknown') f.options.engineConfig.extra = secret;
if (bad === 'getter') Object.defineProperty(f.options.engineConfig, 'qua', {get() { throw Error('accessor executed'); }});
if (bad === 'inherited') {
  f.options.wrapper = Object.create(f.wrapper); expected = 'INVALID_WRAPPER';
}
if (bad === 'static_accessor') {
  Object.defineProperty(f.wrapper.NodeIKernelLoginService, 'get', {get() { throw Error('accessor executed'); }}); expected = 'INVALID_WRAPPER';
}
code(() => createLoginController(f.options), expected);
assert.deepEqual(f.calls, []);
""")


@pytest.mark.parametrize("payload", ["boolean", "bad_png", "http", "credentials", "empty_identity", "numeric_identity", "oversized"])
def test_invalid_events_fail_without_payload_or_retry(tmp_path, payload):
    """Reject malformed sensitive events rather than generating replacements."""
    run_node(tmp_path, "const variant = " + json.dumps(payload) + r""";
const f = fixture(); const c = createLoginController(f.options); const events = [];
c.subscribe(event => events.push(event));
c.initialize(); c.connect(); f.listener.onLoginConnected(); c.getQRCodePicture();
let raw = {pngBase64QrcodeData:png,qrcodeUrl:'https://example.invalid/qr'};
if (variant === 'boolean') raw = true;
if (variant === 'bad_png') raw.pngBase64QrcodeData = Buffer.from(secret).toString('base64');
if (variant === 'http') raw.qrcodeUrl = 'http://example.invalid/qr';
if (variant === 'credentials') raw.qrcodeUrl = 'https://private:secret@example.invalid/qr';
if (variant === 'oversized') raw.pngBase64QrcodeData = 'A'.repeat(2*1024*1024);
if (variant === 'empty_identity') f.listener.onQRCodeLoginSucceed({uin:'',uid:'uid'});
else if (variant === 'numeric_identity') f.listener.onQRCodeLoginSucceed({uin:1,uid:'uid'});
else f.listener.onQRCodeGetPicture(raw);
assert.equal(c.getSnapshot().phase, 'failed');
assert.equal(c.getIdentity(), null);
code(() => c.connect(), 'INVALID_STATE'); code(() => c.getQRCodePicture(), 'INVALID_STATE');
await tick();
assert(!events.some(e => e.type === 'qr' || e.type === 'authenticated'));
assert.equal(events.find(e => e.type === 'failed').payload.code, 'INVALID_EVENT');
c.dispose();
""")


def test_listener_exceptions_are_async_sanitized_and_isolated(tmp_path):
    """Throwing/rejecting consumers never propagate secrets or recursive failures."""
    run_node(tmp_path, r"""
const f = fixture(); const c = createLoginController(f.options); const good = [];
c.subscribe(() => { throw Error(secret); });
c.subscribe(async () => { throw Error(secret + png); });
c.subscribe(event => good.push(event));
c.initialize(); c.connect();
assert.equal(good.length, 0);
await tick(); await tick();
assert(good.some(e => e.type === 'initialized'));
assert(good.some(e => e.type === 'listener_error' && e.payload.code === 'LISTENER_FAILED'));
assert(!JSON.stringify(good).includes(secret));
assert.equal(c.getSnapshot().phase, 'connecting');
c.dispose();
""")


def test_data_uri_prototype_methods_and_numeric_appid(tmp_path):
    """Support native prototype methods and preserve numeric metadata without coercion."""
    run_node(tmp_path, r"""
const f = fixture();
f.options.loginConfig.appid = 123;
f.options.engineConfig.base_path_prefix = '';
const inheritedLogin = Object.create(f.login);
f.wrapper.NodeIKernelLoginService.get = () => inheritedLogin;
const c = createLoginController(f.options);
const events = []; c.subscribe(event => events.push(event));
c.initialize();
assert.equal(f.calls.find(x => x[0] === 'login.init')[1].appid, 123);
c.connect(); f.listener.onLoginConnected(); c.getQRCodePicture();
f.listener.onQRCodeGetPicture({pngBase64QrcodeData:'data:image/png;base64,'+png,qrcodeUrl:'https://example.invalid/qr'});
assert.equal(c.getSnapshot().qr_available, true);
await tick();
assert.equal(events.find(e => e.type === 'qr').payload.pngBase64QrcodeData, 'data:image/png;base64,'+png);
c.dispose();
const untouched = fixture(); const dormant = createLoginController(untouched.options);
dormant.dispose(); dormant.dispose();
assert.deepEqual(untouched.calls, []);
code(() => dormant.initialize(), 'INVALID_STATE');
""")


def test_explicit_process_lifetime_void_handle_policy(tmp_path):
    """Void registration is opt-in and never claims native listener cleanup."""
    run_node(tmp_path, r"""
const f = fixture();
f.login.addKernelLoginListener = listener => { f.login.observedListener = listener; return undefined; };
f.options.listenerLifetime = 'process';
const c = createLoginController(f.options);
c.initialize();
assert.equal(c.getSnapshot().phase, 'initialized');
assert.equal(c.getSnapshot().listener_handle_available, false);
assert.equal(c.getSnapshot().listener_registration_verified, false);
assert.equal(c.getSnapshot().registration_call_returned, true);
assert.equal(c.getSnapshot().listener_id_type, 'undefined');
assert.equal(c.getSnapshot().cleanup_required, 'process_exit');
c.connect();
assert.equal(c.getSnapshot().listener_registration_verified, false);
f.login.observedListener.onLoginConnected();
assert.equal(c.getSnapshot().listener_registration_verified, true);
assert.equal(c.getSnapshot().phase, 'connected');
const disposition = c.dispose(); c.dispose();
assert.equal(disposition.cleanup_required, 'process_exit');
assert.equal(disposition.native_listener_removed, false);
assert.equal(disposition.connect_call_returned, true);
assert.equal(disposition.connect_result_type, 'boolean');
assert.equal(disposition.native_callback_counts.onLoginConnected, 1);
assert(Object.isFrozen(disposition.native_callback_counts));
for (let i = 0; i < 110; i++) f.login.observedListener.onLoginConnected();
assert.equal(c.getSnapshot().native_callback_counts.onLoginConnected, 100);
assert.equal(c.getSnapshot().phase, 'disconnected');
assert.equal(f.calls.filter(x => x[0] === 'remove').length, 0);
for (const value of ['17', {}, null, true]) {
  const bad = fixture({addKernelLoginListener() { return value; }});
  bad.options.listenerLifetime = 'process';
  const controller = createLoginController(bad.options);
  code(() => controller.initialize(), 'INITIALIZE_FAILED');
  controller.dispose();
  assert.equal(bad.calls.filter(x => x[0] === 'remove').length, 0);
}
const invalid = fixture(); invalid.options.listenerLifetime = 'fallback';
code(() => createLoginController(invalid.options), 'INVALID_CONFIG');
assert.deepEqual(invalid.calls, []);
""")


def test_false_ack_and_invalid_listener_id(tmp_path):
    """False acknowledgements and unknown listener IDs do not imply success."""
    run_node(tmp_path, r"""
for (const ack of [false, undefined, {}, 1]) {
  const f = fixture({connect() { return ack; }}); const c = createLoginController(f.options);
  c.initialize();
  if (ack === false) assert.equal(c.connect(), false);
  else code(() => c.connect(), 'CONNECT_FAILED');
  assert.equal(c.getSnapshot().phase, 'failed');
  assert.equal(c.getSnapshot().connect_call_returned, true);
  assert.equal(c.getSnapshot().connect_result_type, ack === false ? 'boolean' : ack === undefined ? 'undefined' : 'other');
  f.listener.onLoginConnected();
  assert.equal(c.getSnapshot().native_callback_counts.onLoginConnected, 1);
  assert.equal(c.getSnapshot().phase, 'failed');
  code(() => c.connect(), 'INVALID_STATE'); c.dispose();
}
for (const id of [undefined, '17', NaN, -1, 1.5, null, {}, true]) {
  const f = fixture({addKernelLoginListener() { return id; }});
  const c = createLoginController(f.options);
  code(() => c.initialize(), 'INITIALIZE_FAILED'); c.dispose(); c.dispose();
  assert.equal(c.getSnapshot().registration_call_returned, true);
  assert.equal(c.getSnapshot().listener_id_type, ['number','string','undefined'].includes(typeof id) ? typeof id : 'other');
  assert(!Object.hasOwn(c.getSnapshot(), 'listener_id'));
  assert.equal(f.calls.filter(x => x[0] === 'remove').length, 0);
}
""")
