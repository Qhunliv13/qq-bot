"""Offline integration of the real three native controllers with one fake wrapper."""

from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
PRELUDE = r"""
'use strict';
const assert = require('node:assert/strict');
const {createNativeBackend} = require(process.argv[2]);
const tick = () => new Promise(resolve => setImmediate(resolve));
const png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=';
const identity = Object.freeze({uin:'10001', uid:'actual-test-uid'});
const secret = 'never-expose-config-secret';
function fixture(lifetime = 'process', hostOptions) {
  const calls = []; let loginListener, sessionListener, messageListener;
  const handle = lifetime === 'process' ? undefined : 7;
  const login = {
    initConfig() { calls.push('login.init'); },
    addKernelLoginListener(value) { calls.push('login.add'); loginListener = value; return handle; },
    removeKernelLoginListener(id) { calls.push('login.remove'); assert.equal(id, 7); },
    connect() { calls.push('connect'); return true; },
    getQRCodePicture() { calls.push('qr'); return true; }
  };
  const msg = {
    addKernelMsgListener(value) { calls.push('msg.add'); messageListener = value; return handle; },
    removeKernelMsgListener(id) { calls.push('msg.remove'); assert.equal(id, 7); },
    generateMsgUniqueId() { calls.push('allocate'); return 'native-only-id'; },
    sendMsg() { calls.push('send'); return Promise.resolve({result:0,errMsg:''}); }
  };
  const nativeSession = {
    init(config, depends, dispatcher, value) {
      calls.push('session.init'); assert.equal(config.selfUin, identity.uin);
      assert.equal(config.selfUid, identity.uid); assert.equal(config.privateSecret, secret);
      assert(!Object.hasOwn(config, 'nonce'));
      sessionListener = value;
    },
    getMsgService() { calls.push('msg.get'); return msg; }
  };
  const wrapper = {
    NodeIQQNTWrapperEngine: {get() { calls.push('engine.get'); return {initWithDeskTopConfig() { calls.push('engine.init'); }}; }},
    NodeIKernelLoginService: {get() { calls.push('login.get'); return login; }},
    NodeIQQNTStartupSessionWrapper: {create() { calls.push('startup.create'); return {start() { calls.push('start'); }}; }},
    NodeIQQNTWrapperSession: {getNTWrapperSession(name) { calls.push('session.get'); assert.equal(name, 'explicit-test'); return nativeSession; }}
  };
  const options = {wrapper, listenerLifetime:lifetime, globalAdapter:{}, dependsAdapter:{}, dispatcherAdapter:{}, sendTimeoutMs:5,
    engineConfig:{base_path_prefix:'/test',platform_type:5,app_type:4,app_version:'test',os_version:'test',use_xlog:false,qua:'test',global_path_config:{desktopGlobalPath:'/test/global'},thumb_config:{maxSide:100,minSide:10,longLimit:100,density:1}},
    loginConfig:{machineId:'',appid:'123',platVer:'test',commonPath:'/test/common',clientVer:'test',hostName:'test',externalVersion:false},
    sessionConfigFactory:async account => { calls.push('config.factory'); assert.deepEqual(account, identity); return {selfUin:account.uin,selfUid:account.uid,clientVer:'test',machineId:'',desktopPathConfig:{account_path:'/test/account'},privateSecret:secret}; },
    sessionOptionsFactory:async account => { calls.push('options.factory'); assert.deepEqual(account, identity); return {lifecycleMode:'startup',sessionName:'explicit-test'}; }
  };
  if (hostOptions !== undefined) options.hostOptions = hostOptions;
  const backend = createNativeBackend(options);
  return {calls,login,msg,options,backend, get loginListener() {return loginListener;}, get sessionListener() {return sessionListener;}, get messageListener() {return messageListener;}};
}
async function authenticate(f) {
  f.backend.initialize(); f.backend.connect();
  f.loginListener.onLoginConnected(); await tick();
  f.backend.requestQRCode();
  f.loginListener.onQRCodeGetPicture({pngBase64QrcodeData:png,qrcodeUrl:'https://example.invalid/private-qr'});
  f.loginListener.onQRCodeLoginSucceed({...identity, privateSecret:secret}); await tick();
}
async function ready(f) {
  await authenticate(f); await f.backend.initializeSession();
  f.backend.startSession(); f.sessionListener.onOpentelemetryInit({is_init:true});
  await tick(); f.backend.initializeMessaging(); await tick();
}
const request = {peer:{chatType:1,peerUid:'explicit-peer'},timestamp:'1',elements:[{elementType:1,textElement:{content:'hello',atType:0,atUid:'',atTinyId:'',atNtUid:''}}]};
"""


def run_node(tmp_path, source):
    """Execute a stored offline test; the wrapper is never a native library."""
    if not NODE:
        pytest.skip("Node.js is required")
    script = tmp_path / "backend_case.cjs"
    script.write_text(PRELUDE + "\n(async () => {\n" + source + "\n})().catch(error => {console.error(error);process.exitCode=1;});\n", encoding="utf-8")
    result = subprocess.run([NODE, str(script), str(ROOT / "qq_transport/native_backend.js")], cwd=ROOT, capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("field", ["lifecycleMode", "sessionName", "legacyStartArgs"])
def test_options_accessors_never_execute_or_resurrect(tmp_path, field):
    """Reject executable lifecycle options before construction, even from trusted factories."""
    run_node(tmp_path, "const field = " + repr(field) + ";" + r"""
const f = fixture(); let reads = 0;
const options = {lifecycleMode:'startup', sessionName:'explicit-test'};
Object.defineProperty(options, field, {get() { reads++; b.dispose(); return field === 'lifecycleMode' ? 'startup' : 'explicit-test'; }});
const b = createNativeBackend({...f.options, sessionOptionsFactory:async () => options});
await authenticate({...f, backend:b, get loginListener() { return f.loginListener; }});
await assert.rejects(b.initializeSession(), {code:'SESSION_INITIALIZE_FAILED'});
assert.equal(reads, 0); assert.equal(b.getSnapshot().session, null);
assert(!f.calls.includes('startup.create')); assert(!f.calls.includes('session.init'));
b.dispose(); assert.equal(b.getSnapshot().session, null);
""")


def test_prepared_login_construction_disposal_never_adopts(tmp_path):
    """A config descriptor trap can dispose during real login construction, not after init."""
    run_node(tmp_path, r"""
const f = fixture('process', {sessionName:'explicit-test', lifecycleMode:'startup'});
let fired = false, b;
const engineConfig = new Proxy(f.options.engineConfig, {
  getOwnPropertyDescriptor(target, key) {
    if (!fired) { fired = true; b.dispose(); }
    return Reflect.getOwnPropertyDescriptor(target, key);
  }
});
b = createNativeBackend({...f.options, engineConfig});
assert.throws(b.initialize, {code:'INITIALIZE_FAILED'});
assert(fired); assert.equal(b.getSnapshot().disposed, true);
assert.equal(b.getSnapshot().login, null);
assert.equal(b.getSnapshot().host.phase, 'disposed');
assert.equal(b.getSnapshot().host.cleanup_required, 'process_exit');
assert.deepEqual(f.calls, ['engine.get','login.get','startup.create','session.get']);
b.dispose(); assert.equal(f.calls.length, 4);
""")


@pytest.mark.parametrize("stage", ["config", "options"])
@pytest.mark.parametrize("invalidation", ["disconnect", "dispose"])
def test_async_factory_identity_loss_before_continuation(tmp_path, stage, invalidation):
    """Real disconnect or disposal invalidates pending factory work before event fanout."""
    run_node(tmp_path, "const stage = " + repr(stage) + "; const invalidation = " + repr(invalidation) + ";" + r"""
const f = fixture(); let release, entered;
const waiting = new Promise(resolve => { entered = resolve; });
const key = stage === 'config' ? 'sessionConfigFactory' : 'sessionOptionsFactory';
const b = createNativeBackend({...f.options, [key]:account => new Promise(resolve => {
  release = async () => resolve(await f.options[key](account)); entered();
})});
await authenticate({...f, backend:b, get loginListener() { return f.loginListener; }});
const pending = b.initializeSession(); await waiting;
if (invalidation === 'dispose') b.dispose(); else f.loginListener.onLoginDisConnected();
release();
await assert.rejects(pending, {code:invalidation === 'dispose' ? 'DISPOSED' : 'SESSION_INITIALIZE_FAILED'});
assert.equal(b.getSnapshot().session, null); assert(!f.calls.includes('session.get'));
if (stage === 'config') assert(!f.calls.includes('options.factory'));
b.dispose();
""")


@pytest.mark.parametrize("stage", ["host", "login", "session", "messaging"])
def test_composed_native_reentrant_disposal_stops_next_call(tmp_path, stage):
    """Dispose inside a real native boundary, preserving process ownership and no late work."""
    run_node(tmp_path, "const stage = " + repr(stage) + ";" + r"""
const f = fixture('token', stage === 'host' ? {sessionName:'explicit-test', lifecycleMode:'startup'} : undefined);
const b = f.backend;
if (stage === 'host') {
  f.options.wrapper.NodeIQQNTWrapperEngine.get = () => {f.calls.push('engine.get'); b.dispose(); return {};};
  assert.throws(b.initialize, {code:'INITIALIZE_FAILED'});
  assert.deepEqual(f.calls, ['engine.get']); assert.equal(b.getSnapshot().login, null);
} else if (stage === 'login') {
  f.login.initConfig = () => {f.calls.push('login.init'); b.dispose();};
  assert.throws(b.initialize, {code:'INITIALIZE_FAILED'});
  assert(!f.calls.includes('login.add'));
} else {
  await authenticate(f);
  if (stage === 'session') {
    f.options.wrapper.NodeIQQNTStartupSessionWrapper.create = () => {f.calls.push('startup.create'); b.dispose(); return {start(){}};};
    await assert.rejects(b.initializeSession(), {code:'DISPOSED'});
    assert(!f.calls.includes('session.get')); assert(!f.calls.includes('session.init'));
  } else {
    await b.initializeSession(); b.startSession(); f.sessionListener.onOpentelemetryInit({is_init:true}); await tick();
    f.msg.addKernelMsgListener = listener => {f.calls.push('msg.add'); b.dispose(); listener.onRecvMsg([]); return 7;};
    assert.throws(b.initializeMessaging, {code:'INVALID_STATE'});
    assert.equal(b.getSnapshot().messages.phase, 'disposed');
    assert.equal(b.getSnapshot().messages.cleanup_required, 'process_exit');
    assert(!f.calls.includes('msg.remove')); // Returned token arrived after logical disposal.
  }
}
assert.equal(b.getSnapshot().disposed, true); const count = f.calls.length;
b.dispose(); await tick(); assert.equal(f.calls.length, count); assert.equal(b.isReady(), false);
assert(f.calls.filter(x => x === 'login.remove').length <= 1);
""")


def test_subscriber_disposal_drains_late_callbacks_and_uncertain_send(tmp_path):
    """Actual authentication/message callbacks cannot revive disposed composed controllers."""
    run_node(tmp_path, r"""
const f = fixture('token'); const events = [];
f.backend.subscribe(event => {if (event.event.type === 'authenticated') f.backend.dispose();});
f.backend.subscribe(event => events.push(event));
await authenticate(f);
assert.equal(f.backend.getSnapshot().disposed, true);
assert(!events.some(event => event.event.type === 'authenticated'));
f.loginListener.onQRCodeLoginSucceed(identity); await tick();
await assert.rejects(f.backend.initializeSession(), {code:'DISPOSED'});
assert(!f.calls.includes('config.factory')); assert.equal(f.calls.filter(x => x === 'login.remove').length, 1);
let complete;
// A separate valid fixture captures the deferred native method during initialize.
const h = fixture('token'); h.msg.sendMsg = () => {h.calls.push('send'); return new Promise(resolve => {complete=resolve;});};
await ready(h); const pending = h.backend.send(request); h.backend.dispose(); complete({result:0,errMsg:''});
await assert.rejects(pending, {code:'SEND_UNCERTAIN'});
h.messageListener.onRecvMsg([{msgId:'late',chatType:1,peerUid:'p',elements:[]}]);
h.sessionListener.onOpentelemetryInit({is_init:true}); await tick();
assert.equal(h.backend.isReady(), false); assert.equal(h.calls.filter(x => x === 'send').length, 1);
h.backend.dispose(); assert.equal(h.calls.filter(x => x === 'msg.remove').length, 1);
""")


def test_real_controllers_explicit_lifecycle(tmp_path):
    """Connect/QR/auth/session/start/message operations require separate owner calls."""
    run_node(tmp_path, r"""
const f = fixture(), b = f.backend, events = [], qr = [];
b.subscribe(e => {assert(Object.isFrozen(e)); assert(Object.isFrozen(e.event)); events.push(e);});
b.subscribeQRCode(e => {assert(Object.isFrozen(e)); qr.push(e);});
assert.deepEqual(f.calls, []); assert.equal(b.getSnapshot().identity_available, false);
await assert.rejects(b.initializeSession(), {code:'INVALID_STATE'});
assert.throws(() => b.initializeMessaging(), {code:'NOT_READY'});
await assert.rejects(b.send(request), {code:'NOT_READY'});
await authenticate(f);
assert.equal(qr.length, 1); assert.equal(qr[0].pngBase64QrcodeData, png);
assert(!JSON.stringify(events).includes(png)); assert(!JSON.stringify(events).includes(identity.uid));
assert(!f.calls.includes('session.get')); assert(!f.calls.includes('config.factory'));
assert.equal(b.getSnapshot().identity_available, true);
await b.initializeSession(); assert(!f.calls.includes('start')); assert(!f.calls.includes('msg.get'));
assert.throws(() => b.initializeMessaging(), {code:'NOT_READY'});
f.sessionListener.onOpentelemetryInit({is_init:true}); assert.equal(b.isReady(), false);
b.startSession(); assert.equal(b.isReady(), false);
f.sessionListener.onOpentelemetryInit({is_init:true}); await tick();
assert(!f.calls.includes('msg.get')); assert.equal(b.isReady(), false);
b.initializeMessaging(); assert.equal(b.isReady(), true);
f.messageListener.onRecvMsg([{msgId:'r1',chatType:1,peerUid:'p',elements:[]}]); await tick();
assert(events.some(e => e.source === 'messages' && e.event.type === 'recv'));
assert.deepEqual(await b.send(request), {state:'accepted',native_msg_id:'native-only-id',result:0});
assert.equal(f.calls.filter(x => x === 'send').length, 1);
const snapshot = JSON.stringify(b.getSnapshot());
for (const value of [png, secret, identity.uid, identity.uin, '/test/account']) assert(!snapshot.includes(value));
assert.equal(b.getSnapshot().send_path_ready, true);
assert.equal(b.getSnapshot().independent_backend_ready, false);
b.dispose(); assert.equal(b.isReady(), false);
assert.equal(b.getSnapshot().session.native_cleanup_required, 'process_exit');
assert.equal(b.getSnapshot().messages.cleanup_required, 'process_exit');
assert.equal(b.getSnapshot().login.cleanup_required, 'process_exit');
assert(!f.calls.some(x => x.endsWith('.remove')));
""")


def test_async_factory_disposal_and_double_call(tmp_path):
    """Dispose while awaiting trusted config blocks all late native initialization."""
    run_node(tmp_path, r"""
let release;
const g = fixture();
// The owner supplies a deferred factory; no controller internals are replaced.
const options = {...g.options, sessionConfigFactory:() => new Promise(resolve => {release=resolve;})};
const b = createNativeBackend(options);
b.initialize(); b.connect(); g.loginListener.onLoginConnected(); await tick(); b.requestQRCode();
g.loginListener.onQRCodeLoginSucceed(identity); await tick();
const pending = b.initializeSession();
await assert.rejects(b.initializeSession(), {code:'INVALID_STATE'});
b.dispose(); release({}); await assert.rejects(pending, {code:'DISPOSED'});
assert(!g.calls.includes('startup.create'));
assert.throws(() => b.initialize(), {code:'DISPOSED'});
assert.throws(() => b.subscribe(() => {}), {code:'DISPOSED'});
""")


@pytest.mark.parametrize("stage", ["start", "messaging", "send"])
def test_duplicate_authentication_blocks_operations_before_forwarding(tmp_path, stage):
    """Real duplicate QR success fails synchronously; it never authenticates a second account."""
    run_node(tmp_path, "const stage = " + repr(stage) + ";" + r"""
const f = fixture(); const b = f.backend;
await authenticate(f); await b.initializeSession();
if (stage !== 'start') {
  b.startSession(); f.sessionListener.onOpentelemetryInit({is_init:true}); await tick();
}
if (stage === 'send') b.initializeMessaging();
const count = f.calls.length;
f.loginListener.onQRCodeLoginSucceed({uin:'20002',uid:'different-test-uid'});
// No await: composer still caches A, but actual login rejects B and clears authorization.
const snapshot = b.getSnapshot();
assert.equal(snapshot.identity_available, true);
assert.equal(snapshot.login.phase, 'failed');
assert.equal(snapshot.login.authenticated, false);
assert.equal(snapshot.login.failure_code, 'INVALID_EVENT');
assert.equal(b.isReady(), false);
if (stage === 'start') assert.throws(b.startSession, {code:'INVALID_STATE'});
if (stage === 'messaging') assert.throws(b.initializeMessaging, {code:'NOT_READY'});
const pending = b.send(request);
assert.equal(f.calls.length, count); // send enters synchronously, before forwarding drains.
await assert.rejects(pending, {code:'NOT_READY'});
assert.equal(b.getSnapshot().identity_available, false);
assert(!f.calls.includes('allocate')); assert(!f.calls.includes('send'));
b.dispose();
""")


def test_uncertain_send_no_retry_and_wrong_readiness(tmp_path):
    """Only native acknowledgements accept; failed readiness never dispatches again."""
    run_node(tmp_path, r"""
const f = fixture(); await ready(f);
f.loginListener.onLoginDisConnected();
// Do not flush queued forwarding: synchronous native login status must gate sends.
assert.equal(f.backend.isReady(), false);
await assert.rejects(f.backend.send(request), {code:'NOT_READY'});
assert(!f.calls.includes('allocate')); assert(!f.calls.includes('send'));
const g = fixture(); g.msg.sendMsg = () => {g.calls.push('send'); return new Promise(() => {});};
await ready(g); await assert.rejects(g.backend.send(request), {code:'SEND_UNCERTAIN'});
assert.equal(g.calls.filter(x => x === 'send').length, 1);
g.sessionListener.onOpentelemetryInit({is_init:false}); await tick();
assert.equal(g.backend.isReady(), false);
await assert.rejects(g.backend.send(request), {code:'NOT_READY'});
assert.equal(g.calls.filter(x => x === 'send').length, 1);
g.backend.dispose(); f.backend.dispose();
""")


def test_factory_failure_identity_mismatch_and_no_nonce_fusion(tmp_path):
    """Factory failures/mismatches are sanitized; callback fields never become config."""
    run_node(tmp_path, r"""
const f = fixture();
const options = {...f.options,sessionConfigFactory:async () => {throw Error(secret);}};
const b = createNativeBackend(options);
b.initialize(); b.connect(); f.loginListener.onLoginConnected(); await tick(); b.requestQRCode();
f.loginListener.onQRCodeLoginSucceed({...identity,nonce:'not-config'}); await tick();
await assert.rejects(b.initializeSession(), error => error.code === 'SESSION_INITIALIZE_FAILED' && !String(error).includes(secret));
await assert.rejects(b.initializeSession(), {code:'INVALID_STATE'}); assert(!f.calls.includes('session.get')); b.dispose();
const g = fixture(); const bad = createNativeBackend({...g.options,sessionConfigFactory:async () => ({selfUin:'wrong',selfUid:identity.uid})});
bad.initialize(); bad.connect(); g.loginListener.onLoginConnected(); await tick(); bad.requestQRCode();
g.loginListener.onQRCodeLoginSucceed(identity); await tick();
await assert.rejects(bad.initializeSession(), {code:'SESSION_INITIALIZE_FAILED'});
assert(!g.calls.includes('startup.create')); bad.dispose();
""")


def test_cleanup_attempts_every_component_and_is_repeat_safe(tmp_path):
    """One removal failure does not skip session or login cleanup or leak native text."""
    run_node(tmp_path, r"""
const f = fixture('token');
f.msg.removeKernelMsgListener = () => {f.calls.push('msg.remove'); throw Error(secret);};
await ready(f);
assert.throws(() => f.backend.dispose(), error => error.code === 'DISPOSE_FAILED' && !String(error).includes(secret));
assert(f.calls.includes('login.remove'));
const snapshot = f.backend.getSnapshot();
assert.equal(snapshot.session.phase, 'disposed'); assert.equal(snapshot.messages.phase, 'disposed');
assert.equal(snapshot.messages.cleanup_required, 'process_exit');
assert.equal(snapshot.login.cleanup_required, 'none');
f.backend.dispose(); assert.equal(f.calls.filter(x => x === 'msg.remove').length, 1);
""")


def test_prepared_host_exact_factory_order_and_callback_gates(tmp_path):
    """Host factories precede init; prepared session is reused without reconnect/start."""
    run_node(tmp_path, r"""
const f = fixture('process', {sessionName:'explicit-test', lifecycleMode:'startup'});
const b = f.backend; b.subscribe(() => {}); b.subscribeQRCode(() => {});
assert.deepEqual(f.calls, []); assert.equal(b.getSnapshot().login, null);
assert.equal(b.getSnapshot().host.phase, 'new'); assert.equal(b.isReady(), false);
assert.throws(b.connect, {code:'INVALID_STATE'}); assert.throws(b.requestQRCode, {code:'INVALID_STATE'});
await assert.rejects(b.initializeSession(), {code:'INVALID_STATE'});
b.initialize();
assert.deepEqual(f.calls, ['engine.get','login.get','startup.create','session.get','engine.init','login.init','login.add']);
assert.throws(b.initialize, {code:'INVALID_STATE'});
assert(!f.calls.includes('connect')); assert(!f.calls.includes('start')); assert(!f.calls.includes('msg.get'));
b.connect(); f.loginListener.onLoginConnected(); await tick(); b.requestQRCode();
f.loginListener.onQRCodeLoginSucceed(identity); await tick();
await b.initializeSession();
for (const name of ['engine.get','login.get','startup.create','session.get','session.init']) assert.equal(f.calls.filter(x => x === name).length, 1);
assert(!f.calls.includes('start')); assert(!f.calls.includes('msg.get'));
f.sessionListener.onOpentelemetryInit({is_init:true});
b.startSession(); assert.equal(b.isReady(), false);
assert.throws(b.initializeMessaging, {code:'NOT_READY'});
f.sessionListener.onOpentelemetryInit({is_init:true}); await tick();
assert(!f.calls.includes('msg.get')); b.initializeMessaging(); assert(b.isReady());
f.loginListener.onLoginDisConnected(); assert.equal(b.isReady(), false);
await assert.rejects(b.send(request), {code:'NOT_READY'});
b.dispose(); assert.equal(b.getSnapshot().host.phase, 'disposed');
assert.equal(b.getSnapshot().host.cleanup_required, 'process_exit');
""")


def test_prepared_host_failures_are_terminal_and_cleanup_survives(tmp_path):
    """No partial factory failure, mismatch or login construction failure is retried."""
    run_node(tmp_path, r"""
for (const stage of ['engine.get','login.get','startup.create','session.get','login.construct']) {
  const f = fixture('process', {sessionName:'explicit-test',lifecycleMode:'startup'});
  const broken = () => {f.calls.push('broken'); throw Error(secret);};
  if (stage === 'engine.get') f.options.wrapper.NodeIQQNTWrapperEngine.get = broken;
  if (stage === 'login.get') f.options.wrapper.NodeIKernelLoginService.get = broken;
  if (stage === 'startup.create') f.options.wrapper.NodeIQQNTStartupSessionWrapper.create = broken;
  if (stage === 'session.get') f.options.wrapper.NodeIQQNTWrapperSession.getNTWrapperSession = broken;
  const b = stage === 'login.construct' ? createNativeBackend({...f.options,engineConfig:{}}) : f.backend;
  assert.throws(b.initialize, e => e.code === 'INITIALIZE_FAILED' && !String(e).includes(secret));
  const count = f.calls.length; assert.throws(b.initialize, {code:'INVALID_STATE'});
  assert.equal(f.calls.length, count); assert(!f.calls.includes('session.init'));
  assert.throws(b.connect, {code:'INVALID_STATE'}); b.dispose(); b.dispose();
  assert.equal(b.getSnapshot().host.phase, 'disposed');
  assert.equal(b.getSnapshot().host.cleanup_required, 'process_exit');
}
for (const options of [{sessionName:'wrong',lifecycleMode:'startup'}, {sessionName:'explicit-test',lifecycleMode:'legacy',legacyStartArgs:[]}]) {
  const f = fixture('process', {sessionName:'explicit-test',lifecycleMode:'startup'});
  const b = createNativeBackend({...f.options,sessionOptionsFactory:async () => options});
  b.initialize(); b.connect(); f.loginListener.onLoginConnected(); await tick(); b.requestQRCode();
  f.loginListener.onQRCodeLoginSucceed(identity); await tick();
  await assert.rejects(b.initializeSession(), {code:'SESSION_INITIALIZE_FAILED'});
  await assert.rejects(b.initializeSession(), {code:'INVALID_STATE'});
  assert.equal(f.calls.filter(x => x === 'session.get').length, 1);
  assert(!f.calls.includes('session.init')); b.dispose();
}
const f = fixture('token', {sessionName:'explicit-test',lifecycleMode:'startup'});
f.msg.removeKernelMsgListener = () => {throw Error(secret);}; await ready(f);
assert.throws(f.backend.dispose, {code:'DISPOSE_FAILED'});
assert(f.calls.includes('login.remove')); assert.equal(f.backend.getSnapshot().session.phase, 'disposed');
assert.equal(f.backend.getSnapshot().host.phase, 'disposed');
""")
