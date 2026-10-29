"""Offline fake-native message contract tests; no QQ library, login or network."""

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "qq_transport" / "native_messages.js"
NODE = shutil.which("node")

# Synthetic metadata only. Stored subprocess scripts avoid multiline shell execution.
PRELUDE = r"""
'use strict';
const assert = require('node:assert/strict');
const {createMessageController, NativeMessageError, LIMITS} = require(process.argv[2]);
const tick = () => new Promise(resolve => setImmediate(resolve));
const text = () => ({elementType:1,textElement:{content:'hello',atType:0,atUid:'',atTinyId:'',atNtUid:''}});
const request = () => ({peer:{chatType:1,peerUid:'explicit-native-uid'},elements:[text()],timestamp:'1780000000'});
const message = () => ({msgId:'18446744073709551615',chatType:1,peerUid:'peer-uid',peerUin:'10002',senderUid:'sender-uid',senderUin:'10003',msgTime:'1780000000',msgSeq:'18446744073709551614',msgRandom:'18446744073709551613',elements:[text()]});
function fixture(overrides = {}, options = {}) {
  const calls = [];
  let listener;
  let ready = true;
  const service = {
    generateMsgUniqueId(chatType, time) { calls.push(['generate',chatType,time]); return '18446744073709551615'; },
    addKernelMsgListener(value) { calls.push(['add']); listener = value; return 19; },
    removeKernelMsgListener(id) { calls.push(['remove',id]); },
    sendMsg(...args) { calls.push(['send',...args]); return Promise.resolve({result:0,errMsg:''}); },
    ...overrides
  };
  const session = {getMsgService() { calls.push(['service']); return service; }};
  const config = {session,accountIdentity:{uin:'account-uin',uid:'account-uid'},isReady:() => ready,...options};
  return {calls,service,session,config,set ready(value) { ready = value; },get listener() { return listener; }};
}
function code(fn, expected) {
  assert.throws(fn, error => error instanceof NativeMessageError && error.code === expected && error.message === expected && !('cause' in error));
}
async function rejected(promise, expected) {
  await assert.rejects(promise, error => error instanceof NativeMessageError && error.code === expected && error.message === expected && !('cause' in error));
}
"""


def run_node(tmp_path, source):
    """Run an auditable stored script against injected fakes, never native exports."""
    if not NODE:
        pytest.skip("Node.js is required for offline fake-native contract tests")
    script = tmp_path / "native_messages_case.cjs"
    script.write_text(PRELUDE + "\n(async () => {\n" + source + "\n})().catch(error => { console.error(error); process.exitCode = 1; });\n", encoding="utf-8")
    result = subprocess.run([NODE, str(script), str(MODULE)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=15, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_dormant_readiness_and_no_login_claim(tmp_path):
    """Only explicit readiness authorizes initialization, allocation and dispatch."""
    run_node(tmp_path, r"""
const f = fixture(); const c = createMessageController(f.config);
const events = []; c.subscribe(event => events.push(event));
assert.deepEqual(f.calls, []); assert.equal(c.getSnapshot().phase,'new');
await rejected(c.send(request()),'INVALID_STATE');
f.ready = false; code(() => c.initialize(),'NOT_READY'); assert.deepEqual(f.calls,[]);
f.ready = 1; code(() => c.initialize(),'NOT_READY'); assert.deepEqual(f.calls,[]);
f.ready = true; c.initialize(); code(() => c.initialize(),'INVALID_STATE');
f.ready = false; await rejected(c.send(request()),'NOT_READY');
f.listener.onRecvMsg([message()]); await tick(); assert.deepEqual(events,[]);
assert.deepEqual(f.calls.map(x => x[0]),['service','add']);
assert(!JSON.stringify(c.getSnapshot()).includes('account-uid'));
c.dispose(); c.dispose(); assert.deepEqual(f.calls.at(-1),['remove',19]);
assert(!('sessionReady' in c.getSnapshot())); assert(!('authenticated' in c.getSnapshot()));
""")


def test_exact_event_batches_projection_and_immutability(tmp_path):
    """Receive/update remain single array events with stable tuple keys and exact IDs."""
    run_node(tmp_path, r"""
const f = fixture(); const c = createMessageController(f.config); const events=[];
c.subscribe(event => events.push(event)); c.initialize();
assert.deepEqual(Object.keys(f.listener).sort(),['onMsgInfoListUpdate','onRecvMsg']);
const raw = message(); let unknownRead = 0;
Object.defineProperty(raw,'privateUnknown',{get() { unknownRead++; throw Error('secret'); }});
raw.elements.push({elementType:2,elementId:'picture-id',privatePath:'secret'});
f.listener.onRecvMsg([raw, message()]); f.listener.onMsgInfoListUpdate([raw]);
raw.msgId = 'changed'; raw.elements[0].textElement.content = 'changed'; await tick();
assert.equal(events.length,2); assert.equal(events[0].type,'recv'); assert.equal(events[1].type,'update');
assert.equal(events[0].payload.length,2); assert.equal(events[1].payload.length,1);
const item=events[0].payload[0]; assert.equal(item.msgId,'18446744073709551615');
assert.equal(item.msgSeq,'18446744073709551614'); assert.equal(item.msgRandom,'18446744073709551613');
assert.equal(item.msgTime,'1780000000'); assert.equal(item.peerUin,'10002');
assert.equal(item.senderUid,'sender-uid'); assert.equal(item.senderUin,'10003');
assert.deepEqual(item.key,['account-uin','account-uid',1,'peer-uid','18446744073709551615']);
assert.deepEqual(item.key,events[1].payload[0].key);
assert.deepEqual(item.elements[1],{elementType:2,elementId:'picture-id'});
assert.equal(item.elements[0].textElement.content,'hello'); assert.equal(unknownRead,0);
for (const obj of [events[0],events[0].payload,item,item.account,item.key,item.elements,item.elements[0],item.elements[0].textElement]) assert(Object.isFrozen(obj));
assert(!JSON.stringify(events).includes('secret'));
c.dispose(); f.listener.onRecvMsg([message()]); await tick(); assert.equal(events.length,2);
""")


def test_bounds_descriptor_safety_and_sanitized_subscriber_errors(tmp_path):
    """Native getters never execute; oversized/invalid batches and callback errors are safe."""
    run_node(tmp_path, r"""
const f=fixture(); const c=createMessageController(f.config); const events=[];
c.subscribe(() => { throw Error('secret-message'); });
c.subscribe(() => Promise.reject(Error('secret-rejection')));
c.subscribe(event => events.push(event)); c.initialize();
const raw=message(); Object.defineProperty(raw,'msgId',{get() { throw Error('getter-ran'); }});
f.listener.onRecvMsg([raw]); f.listener.onRecvMsg(Array(LIMITS.batch+1).fill(message()));
const indexed=[]; Object.defineProperty(indexed,'0',{get() { throw Error('index-ran'); }}); indexed.length=1;
f.listener.onRecvMsg(indexed);
const long=message(); long.elements[0].textElement.content='x'.repeat(LIMITS.text+1); f.listener.onRecvMsg([long]);
f.listener.onRecvMsg([message()]); await tick(); await tick();
assert.equal(events.filter(e=>e.type==='invalid_event').length,4);
assert.equal(events.filter(e=>e.type==='recv').length,1);
assert(events.some(e=>e.type==='listener_error')); assert(!JSON.stringify(events).includes('secret'));
assert(events.length<30); c.dispose();
""")


def test_prototype_service_methods_and_getter_rejection(tmp_path):
    """Native prototype methods retain this; accessor-backed contracts fail without invocation."""
    run_node(tmp_path, r"""
const f=fixture(); const prototype={getMsgService() { assert.equal(this.marker,17); return f.service; }};
f.config.session=Object.assign(Object.create(prototype),{marker:17});
const c=createMessageController(f.config); c.initialize(); c.dispose();
for (const name of ['getMsgService','generateMsgUniqueId','addKernelMsgListener','removeKernelMsgListener','sendMsg']) {
 const g=fixture(); let reads=0;
 Object.defineProperty(name==='getMsgService'?g.session:g.service,name,{get() { reads++; throw Error('private'); }});
 const d=createMessageController(g.config); code(()=>d.initialize(),'INVALID_SERVICE'); assert.equal(reads,0);
 assert(!g.calls.some(call=>call[0]==='add')); d.dispose();
}
""")


def test_exact_outgoing_call_map_and_no_uid_defaults(tmp_path):
    """All five text fields, string ID/time and optional explicit fields pass unchanged."""
    run_node(tmp_path, r"""
const f=fixture(); const c=createMessageController(f.config); c.initialize();
const input=request(); input.peer.guildId='explicit-guild'; input.elements[0].elementId='explicit-element';
input.elements[0].textElement={content:'hi',atType:2,atUid:'given-uin',atTinyId:'given-tiny',atNtUid:'given-native-uid'};
const result=await c.send(input);
assert.deepEqual(result,{state:'accepted',native_msg_id:'18446744073709551615',result:0}); assert(Object.isFrozen(result));
const call=f.calls.find(x=>x[0]==='send'); assert.equal(call.length,5);
assert.equal(call[1],'18446744073709551615'); assert.deepEqual(call[2],input.peer); assert.deepEqual(call[3],input.elements);
assert(call[4] instanceof Map); assert.equal(call[4].size,0);
assert.deepEqual(f.calls.filter(x=>x[0]==='generate'),[['generate',1,'1780000000']]);
const plain=request(); await c.send(plain); assert(!('elementId' in f.calls.filter(x=>x[0]==='send')[1][3][0]));
for (const field of ['content','atType','atUid','atTinyId','atNtUid']) {
 const bad=request(); delete bad.elements[0].textElement[field]; await rejected(c.send(bad),'INVALID_INPUT');
}
const bad=request(); bad.peer={chatType:1,peerUin:'10001'}; await rejected(c.send(bad),'INVALID_INPUT');
const badChat=request(); badChat.peer.chatType=3; await rejected(c.send(badChat),'INVALID_INPUT');
const badTime=request(); badTime.timestamp=123; await rejected(c.send(badTime),'INVALID_INPUT');
const unsupported=request(); unsupported.elements=[{elementType:2}]; await rejected(c.send(unsupported),'INVALID_INPUT');
assert.equal(f.calls.filter(x=>x[0]==='generate').length,2); c.dispose();
""")


@pytest.mark.parametrize("response", ["{result:-1,errMsg:'private rejection'}", "{result:true,errMsg:''}", "{result:1,errMsg:''}", "{result:0}", "{result:0,errMsg:19}", "Object.defineProperty({},'result',{get() { throw Error('secret'); }})"])
def test_strict_native_ack_and_sanitized_uncertain(tmp_path, response):
    """Only actual result 0 accepts, -1 rejects; malformed native acknowledgements are uncertain."""
    run_node(tmp_path, "const response=" + response + r""";
const f=fixture({sendMsg(...args) { f.calls.push(['send',...args]); return Promise.resolve(response); }});
const c=createMessageController(f.config); c.initialize();
if (Object.getOwnPropertyDescriptor(response,'result')?.value===-1) assert.deepEqual(await c.send(request()),{state:'rejected',native_msg_id:'18446744073709551615',result:-1});
else {
 await assert.rejects(c.send(request()),error => error.code==='SEND_UNCERTAIN' && error.state==='uncertain' && error.native_msg_id==='18446744073709551615' && error.message==='SEND_UNCERTAIN' && !('cause' in error));
}
assert.equal(f.calls.filter(x=>x[0]==='send').length,1); assert.equal(f.calls.filter(x=>x[0]==='generate').length,1); c.dispose();
""")


def test_throw_timeout_no_retry_and_lifecycle_recheck(tmp_path):
    """Thrown calls/timeouts never retry; readiness loss during allocation prevents dispatch."""
    run_node(tmp_path, r"""
for (const behavior of ['throw','reject','timeout','sync']) {
 const f=fixture({sendMsg(...args) { f.calls.push(['send',...args]);
   if(behavior==='throw') throw Error('private-native');
   if(behavior==='reject') return Promise.reject(Error('private-native'));
   if(behavior==='sync') return {result:0,errMsg:''};
   return new Promise(()=>{});
 }},{sendTimeoutMs:5});
 const c=createMessageController(f.config); c.initialize(); await rejected(c.send(request()),'SEND_UNCERTAIN');
 assert.equal(f.calls.filter(x=>x[0]==='send').length,1); assert.equal(f.calls.filter(x=>x[0]==='generate').length,1); c.dispose();
}
const f=fixture(); f.service.generateMsgUniqueId=()=> {f.calls.push(['generate']);f.ready=false;return '18446744073709551615';};
const c=createMessageController(f.config); c.initialize(); await rejected(c.send(request()),'NOT_READY');
assert.equal(f.calls.filter(x=>x[0]==='send').length,0); assert.equal(f.calls.filter(x=>x[0]==='generate').length,1); c.dispose();
const g=fixture({generateMsgUniqueId() {return 18446744073709551615;}}); const d=createMessageController(g.config); d.initialize();
await rejected(d.send(request()),'SEND_UNCERTAIN'); assert(!g.calls.some(x=>x[0]==='send')); d.dispose();
""")


@pytest.mark.parametrize("boundary", ["get", "add"])
@pytest.mark.parametrize("behavior", ["return", "throw"])
@pytest.mark.parametrize("lifetime", ["process", "token"])
def test_startup_disposal_is_terminal(tmp_path, boundary, behavior, lifetime):
    """Native startup callbacks cannot resurrect state or adopt/remove late handles."""
    run_node(tmp_path, f"const boundary='{boundary}', behavior='{behavior}', lifetime='{lifetime}';" + r"""
const f=fixture({}, {listenerLifetime:lifetime}); let c;
const stop=() => { c.dispose(); if(behavior==='throw') throw Error('private-native'); };
if(boundary==='get') f.session.getMsgService=() => { f.calls.push(['service']); stop(); return f.service; };
else f.service.addKernelMsgListener=() => { f.calls.push(['add']); stop(); return 19; };
c=createMessageController(f.config);
code(()=>c.initialize(),'INVALID_STATE');
assert.equal(c.getSnapshot().phase,'disposed');
assert.equal(c.getSnapshot().cleanup_required,boundary==='add'?'process_exit':'none');
code(()=>c.initialize(),'INVALID_STATE'); await rejected(c.send(request()),'INVALID_STATE'); c.dispose();
assert.deepEqual(f.calls.map(call=>call[0]),boundary==='get'?['service']:['service','add']);
""")


@pytest.mark.parametrize("boundary", ["allocate", "dispatch", "pending"])
def test_send_disposal_is_uncertain_without_retry(tmp_path, boundary):
    """Disposal during native work suppresses later dispatch and acknowledgement claims."""
    run_node(tmp_path, f"const boundary='{boundary}';" + r"""
let settle;
const g=fixture(); let d;
g.service.generateMsgUniqueId=() => {g.calls.push(['generate']); if(boundary==='allocate') d.dispose(); return '18446744073709551615';};
g.service.sendMsg=() => {g.calls.push(['send']); if(boundary==='dispatch') d.dispose(); return new Promise(resolve=>{settle=resolve;});};
d=createMessageController(g.config); d.initialize(); const result=d.send(request());
if(boundary==='pending') d.dispose();
if(settle) settle({result:0,errMsg:''});
await assert.rejects(result,error=>error.code==='SEND_UNCERTAIN' && error.state==='uncertain' && error.native_msg_id==='18446744073709551615');
assert.equal(d.getSnapshot().phase,'disposed'); d.dispose();
assert.deepEqual(g.calls.map(call=>call[0]),boundary==='allocate'?['service','add','generate','remove']:['service','add','generate','send','remove']);
""")


def test_readiness_disposal_preserves_preflight_semantics(tmp_path):
    """False preflight keeps NOT_READY; true callbacks cannot start disposed controllers."""
    run_node(tmp_path, r"""
for(const ready of [false,true]) {
 const f=fixture(); let c;
 f.config.isReady=()=>{c.dispose();return ready;}; c=createMessageController(f.config);
 code(()=>c.initialize(),ready?'INVALID_STATE':'NOT_READY');
 assert.equal(c.getSnapshot().phase,'disposed'); assert.deepEqual(f.calls,[]);
}
""")


def test_token_type_process_lifetime_and_explicit_encoder(tmp_path):
    """Token types are not coerced; process ownership never fakes native unregister."""
    run_node(tmp_path, r"""
for(const token of [19,'19',0,'0']) {
 const f=fixture({addKernelMsgListener(value) {f.calls.push(['add']);return token;}});
 const c=createMessageController(f.config); c.initialize(); c.dispose(); assert.deepEqual(f.calls.at(-1),['remove',token]);
}
for(const token of [undefined,null,true,NaN,{},'019','invalid']) {
 const f=fixture({addKernelMsgListener() {return token;}}); const c=createMessageController(f.config);
 code(()=>c.initialize(),'INVALID_LISTENER_HANDLE'); c.dispose(); assert.equal(c.getSnapshot().cleanup_required,'process_exit');
 assert(!f.calls.some(x=>x[0]==='remove'));
}
const f=fixture({addKernelMsgListener() {}},{listenerLifetime:'process'}); const c=createMessageController(f.config);
c.initialize(); c.dispose(); assert.equal(c.getSnapshot().cleanup_required,'process_exit'); assert(!f.calls.some(x=>x[0]==='remove'));
const g=fixture({}, {removalIdEncoder:id => String(id)}); const d=createMessageController(g.config); d.initialize(); d.dispose();
assert.deepEqual(g.calls.at(-1),['remove','19']);
const h=fixture({removeKernelMsgListener() {throw Error('private-removal');}}); const e=createMessageController(h.config); e.initialize();
code(()=>e.dispose(),'REMOVE_LISTENER_FAILED'); assert.equal(e.getSnapshot().cleanup_required,'process_exit'); e.dispose();
""")
