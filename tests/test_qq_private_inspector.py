"""Mock-only inspector regressions: never start QQ, native code or real sockets."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "qq_private_inspector.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node required for mocked JS helpers")


@pytest.fixture
def run_js(tmp_path):
    """Execute a saved Node harness; requiring the client cannot start its main path."""
    def run(body):
        """Save auditable mock code before subprocess execution, with bounded output/time."""
        harness = tmp_path / "inspector-test.cjs"
        harness.write_text(
            "'use strict';\nconst assert=require('assert/strict');\n"
            "const vm=require('vm');\nconst {EventEmitter}=require('events');\n"
            f"const client=require({json.dumps(str(SCRIPT))});\n"
            "(async()=>{\n" + body +
            "\n})().catch(()=>{process.exitCode=1;console.error('mock assertion failed');});\n",
            encoding="utf-8",
        )
        result = subprocess.run([NODE, str(harness)], check=False, capture_output=True,
                                text=True, timeout=8)
        assert result.returncode == 0, result.stdout + result.stderr
        assert len(result.stdout) <= 20000
        return result.stdout
    return run


@pytest.mark.parametrize("endpoint", [
    "ws://127.0.0.1:9229/00000000-0000-4000-8000-000000000000",
    "ws://127.0.0.1:9229/a",
    "ws://127.0.0.1:9229/" + "a" * 128,
])
def test_only_exact_fixed_loopback_endpoint_is_accepted(run_js, endpoint):
    """Accept the inspector UUID while preserving the explicit single-segment contract."""
    run_js(f"assert.equal(client.validateEndpoint({json.dumps(endpoint)}),{json.dumps(endpoint)});")


@pytest.mark.parametrize("endpoint", [
    None, 12, "http://127.0.0.1:9229/id", "wss://127.0.0.1:9229/id",
    "ws://localhost:9229/id", "ws://127.1:9229/id", "ws://2130706433:9229/id",
    "ws://127.0.0.1:9230/id", "ws://[::1]:9229/id", "ws://user@127.0.0.1:9229/id",
    "ws://127.0.0.1:9229/id?x", "ws://127.0.0.1:9229/id#x",
    "ws://127.0.0.1:9229/id?", "ws://127.0.0.1:9229/id#",
    "ws://127.0.0.1:9229/", "ws://127.0.0.1:9229/a/b",
    "ws://127.0.0.1:9229/%61", "ws://127.0.0.1:9229/../id",
    "ws://127.0.0.1:9229/" + "a" * 129, "ws://127.0.0.1:9229/a_b",
    " ws://127.0.0.1:9229/id", "ws://127.0.0.1:9229/id\n",
])
def test_endpoint_rejects_normalization_credentials_and_extra_parts(run_js, endpoint):
    """Reject alternate spellings before WHATWG URL normalization can hide their origin."""
    run_js(f"assert.throws(()=>client.validateEndpoint({json.dumps(endpoint)}),"
           "e=>e.category==='discovery_invalid');")


@pytest.mark.parametrize("body", ["{}", "[]", "null", "private malformed text", '[{"id":"x"}]',
    '[{"id":"x","webSocketDebuggerUrl":"ws://127.0.0.1:9229/y"}]',
    '[{"id":"x","webSocketDebuggerUrl":"ws://127.0.0.1:9229/x"},{}]'])
def test_discovery_rejects_ambiguous_or_malformed_targets(run_js, body):
    """Never choose an alternate target or expose untrusted discovery text."""
    run_js(f"assert.throws(()=>client.parseDiscovery({json.dumps(body)}),"
           "e=>e.category==='discovery_invalid');")


ENVIRONMENT = r"""
const proc={platform:'linux',execPath:'/usr/bin/node',versions:{node:'18.19.1'},
  argv:['/usr/bin/node','/tmp/qq-private-inspector.js'],
  env:{QQ_ISOLATED_NATIVE_INSPECTOR:'1',QQ_INSPECTOR_PARENT_NETNS:'net:[1]'}};
let status='Uid:\t65534\t65534\t65534\t65534\nGid:\t65534\t65534\t65534\t65534\nNoNewPrivs:\t1\nCapEff:\t0000000000000000\n';
let net='net:[2]';
const files={readFileSync:(path,encoding)=>{assert.equal(path,'/proc/self/status');
  assert.equal(encoding,'utf8');return status;},readlinkSync:path=>{
  assert.equal(path,'/proc/self/ns/net');return net;}};
"""


def test_child_proves_local_prerequisites_only(run_js):
    """A different netns and least privilege permit the local gate, not full certification."""
    run_js(ENVIRONMENT + "assert.equal(client.verifyEnvironment(proc,files),true);")


@pytest.mark.parametrize("mutation", [
    "proc.platform='win32';", "proc.execPath='/usr/local/bin/node';",
    "proc.versions.node='20.0.0';", "proc.argv.push('ws://elsewhere');",
    "delete proc.env.QQ_ISOLATED_NATIVE_INSPECTOR;", "delete proc.env.QQ_INSPECTOR_PARENT_NETNS;",
    "proc.env.QQ_INSPECTOR_PARENT_NETNS='unproven';", "net='net:[1]';",
    "status=status.replace('NoNewPrivs:\\t1','NoNewPrivs:\\t0');",
    "status=status.replace('0000000000000000','0000000000000001');",
    "status=status.replace('Uid:\\t65534','Uid:\\t0');",
    "status=status.replace('Gid:\\t65534','Gid:\\t0');",
    "files.readlinkSync=()=>{throw Error('private path');};",
    "proc.env.NODE_OPTIONS='--require=private';", "proc.env.NODE_PATH='/private';",
    "proc.env.LD_PRELOAD='/private';", "proc.env.LD_LIBRARY_PATH='/private';",
])
def test_gate_refuses_unproven_or_unsafe_environment(run_js, mutation):
    """Fail before HTTP/undici access when local least-privilege checks are not proven."""
    run_js(ENVIRONMENT + mutation.replace("\\t", "\t") +
           "assert.throws(()=>client.verifyEnvironment(proc,files),e=>e.category==='environment_refused');")


RUNTIME = "{versions:{node:'22.14.0',electron:'35.1.0',modules:'133',napi:'10'},exec_path_matches:true}"
BINDING = """{loaded:true,exports:[{name:'NodeIQQNTWrapperEngine',type:'function',kind:'data'}],
 truncated:false,candidates:Object.fromEntries(client.CANDIDATES.map(n=>[n,n==='NodeIQQNTWrapperEngine']))}"""


def test_runtime_expression_is_closed_and_returns_allowlist_only(run_js):
    """The fixed first expression never leaks paths or disallowed process version fields."""
    run_js("const fake={versions:{node:'22.14.0',electron:'35.1.0',modules:'133',napi:'10',"
           "private:'secret'},execPath:'/opt/QQ/qq'};"
           "const value=vm.runInNewContext(client.RUNTIME_EXPRESSION,{process:fake});"
           "assert.equal(value.exec_path_matches,true);assert.equal(value.versions.private,undefined);"
           "assert.deepEqual(Object.keys(client.sanitizeRuntime(value).versions),['node','electron','modules','napi']);")


@pytest.mark.parametrize("change", [
    "value.exec_path_matches=false;", "delete value.versions.electron;",
    "value.versions.node='private token /path';", "value.versions.modules=133;",
    "value.versions.napi='';", "value.versions.uv='x'.repeat(65);",
])
def test_runtime_metadata_must_be_proven_before_dlopen(run_js, change):
    """Missing host/Electron metadata must not advance the native gate."""
    run_js("const value=" + RUNTIME + ";" + change +
           "assert.throws(()=>client.sanitizeRuntime(value),e=>e.category==='runtime_mismatch');")


def test_binding_expression_loads_only_once_and_never_calls_getters_or_exports(run_js):
    """Execute fixed code in a VM with a fake dlopen; no actual native module is loaded."""
    run_js(r"""
let loads=0,calls=0;
const fake={execPath:'/opt/QQ/qq',dlopen:(native,path)=>{
  assert.equal(path,'/opt/QQ/resources/app/wrapper.node');loads++;
  native.exports.NodeIQQNTWrapperEngine=()=>{calls++;throw Error('must not call');};
  Object.defineProperty(native.exports,'getter',{enumerable:true,get(){calls++;throw Error('secret');}});
  native.exports.secretString='must not appear';
}};
const value=vm.runInNewContext(client.BINDING_EXPRESSION,{process:fake});
const clean=client.sanitizeBinding(value);
assert.equal(loads,1);assert.equal(calls,0);
assert.equal(clean.candidates.NodeIQQNTWrapperEngine,true);
assert.equal(clean.exports.find(e=>e.name==='getter').type,'accessor');
assert(!JSON.stringify(clean).includes('must not appear'));
assert(!client.BINDING_EXPRESSION.includes('require('));
""")


def test_binding_expression_is_bounded_and_does_not_retry_failed_dlopen(run_js):
    """Load failure never exposes exception text or retries via a different loader."""
    run_js(r"""
let loads=0;
let fake={execPath:'/opt/QQ/qq',dlopen:()=>{loads++;throw Error('SECRET /private');}};
let value=vm.runInNewContext(client.BINDING_EXPRESSION,{process:fake});
assert.equal(JSON.stringify(value),'{"loaded":false}');assert.equal(loads,1);
fake={execPath:'/opt/QQ/qq',dlopen:n=>{for(let i=0;i<100;i++)n.exports['name'+i]=()=>{};}};
value=vm.runInNewContext(client.BINDING_EXPRESSION,{process:fake});
assert.equal(value.exports.length,64);assert.equal(value.truncated,true);
fake={execPath:'/wrong',dlopen:()=>{throw Error('must not load');}};
assert.equal(vm.runInNewContext(client.BINDING_EXPRESSION,{process:fake}).loaded,false);
""")


@pytest.mark.parametrize("mutation", [
    "value.loaded=false;", "value.exports[0].name='/private path';",
    "value.exports[0].type='secret-value';", "value.exports[0].kind='getter';",
    "value.exports[0].type='accessor';", "value.exports.push(value.exports[0]);",
    "value.candidates.NodeIQQNTWrapperEngine='yes';", "delete value.candidates.NodeIKernelLoginService;",
    "value.exports=Array(65).fill(value.exports[0]);", "value.candidates.NodeIQQNTWrapperEngine=false;",
])
def test_binding_rejects_malformed_descriptors_and_candidate_claims(run_js, mutation):
    """Never affirm a binding gate on malformed native export metadata."""
    run_js("const value=" + BINDING + ";" + mutation +
           "assert.throws(()=>client.sanitizeBinding(value),e=>['binding_invalid','binding_load_failed'].includes(e.category));")


SOCKET_MOCK = """
let sent=[];let closed=0;
class FakeSocket {
 constructor(url){assert.equal(url,'ws://127.0.0.1:9229/id');this.handlers={};
   setImmediate(()=>this.handlers.open());}
 addEventListener(name,fn){this.handlers[name]=fn;}
 close(){closed++;}
 send(text){const request=JSON.parse(text);sent.push(request);
   assert.equal(request.method,'Runtime.evaluate');
   assert.equal(request.params.includeCommandLineAPI,false);
   assert.equal(request.params.returnByValue,true);assert.equal(request.params.timeout,1000);
   setImmediate(()=>this.deliver(request));}
 emit(value){this.handlers.message({data:typeof value==='string'?value:JSON.stringify(value)});}
}
const report=client.baseReport();
"""


def test_protocol_sends_two_fixed_commands_and_only_proves_host_and_binding(run_js):
    """Successful mocked evidence never claims a session/login/backend is usable."""
    run_js(SOCKET_MOCK + "FakeSocket.prototype.deliver=function(r){"
           "this.emit({id:r.id,result:{result:{type:'object',value:r.id===1?" + RUNTIME + ":" + BINDING +
           "}}});};await client.inspect('ws://127.0.0.1:9229/id',FakeSocket,report);"
           "assert.equal(sent.length,2);assert.equal(sent[0].params.expression,client.RUNTIME_EXPRESSION);"
           "assert.equal(sent[1].params.expression,client.BINDING_EXPRESSION);assert.equal(closed,1);"
           "assert.equal(report.ok,true);assert.equal(report.readiness.native_binding_verified,true);"
           "assert.equal(report.readiness.runtime_host_verified,true);"
           "for(const k of ['session_initialized','safe_to_login','independent_backend_ready','production_cutover_ready'])"
           "assert.equal(report.readiness[k],false);")


@pytest.mark.parametrize("message,category", [
    ("'malformed secret'", "protocol_invalid"),
    ("{id:2,result:{}}", "protocol_invalid"),
    ("{id:1,error:{message:'SECRET STACK'}}", "evaluation_failed"),
    ("{id:1,result:{exceptionDetails:{text:'SECRET'}}}", "evaluation_failed"),
    ("{id:1,result:{result:{type:'object',value:{exec_path_matches:false}}}}", "runtime_mismatch"),
    ("'x'.repeat(65537)", "message_limit"),
    ("[]", "protocol_invalid"),
])
def test_protocol_redacts_failures_and_never_reaches_native_after_bad_host(run_js, message, category):
    """Reject unsafe first responses before sending the load-only expression."""
    run_js(SOCKET_MOCK + f"FakeSocket.prototype.deliver=function(){{this.emit({message});}};"
           "await assert.rejects(client.inspect('ws://127.0.0.1:9229/id',FakeSocket,report),"
           f"e=>e.category==={json.dumps(category)});"
           "assert.equal(sent.length,1);assert.equal(report.binding.load_attempted,false);"
           "assert.equal(report.readiness.native_binding_verified,false);")


def test_socket_message_count_and_aggregate_bytes_are_bounded(run_js):
    """Ignored events cannot bypass the finite count or aggregate byte budgets."""
    run_js(SOCKET_MOCK + "FakeSocket.prototype.deliver=function(){for(let i=0;i<65;i++)"
           "this.emit({method:'Runtime.private',params:{secret:'not returned'}});};"
           "await assert.rejects(client.inspect('ws://127.0.0.1:9229/id',FakeSocket,report),"
           "e=>e.category==='message_limit');assert.equal(sent.length,1);")
    run_js(SOCKET_MOCK + "FakeSocket.prototype.deliver=function(){for(let i=0;i<3;i++)"
           "this.emit({method:'Runtime.private',params:{secret:'x'.repeat(50000)}});};"
           "await assert.rejects(client.inspect('ws://127.0.0.1:9229/id',FakeSocket,report),"
           "e=>e.category==='message_limit');")


def test_native_load_failure_keeps_host_evidence_without_native_readiness(run_js):
    """Separate successful QQ host evidence from failed or malformed native evidence."""
    run_js(SOCKET_MOCK + "FakeSocket.prototype.deliver=function(r){this.emit({id:r.id,result:{"
           "result:{type:'object',value:r.id===1?" + RUNTIME + ":{loaded:false}}}});};"
           "await assert.rejects(client.inspect('ws://127.0.0.1:9229/id',FakeSocket,report),"
           "e=>e.category==='binding_load_failed');assert.equal(report.binding.load_attempted,true);"
           "assert.equal(report.readiness.runtime_host_verified,true);"
           "assert.equal(report.readiness.native_binding_verified,false);assert.equal(report.ok,false);")


HTTP_MOCK = """
let optionsSeen;let destroyed=0;
const response=new EventEmitter();response.destroy=()=>{destroyed++;};response.statusCode=200;
const request=new EventEmitter();request.destroy=()=>{destroyed++;};
const fakeHttp={get:(options,callback)=>{optionsSeen=options;setImmediate(()=>{
 callback(response);deliver();});return request;}};
"""


def test_discovery_http_uses_fixed_address_path_and_bounded_body(run_js):
    """HTTP mock verifies the actual options; tests cannot touch a local inspector."""
    run_js(HTTP_MOCK + "function deliver(){response.emit('data',Buffer.from(JSON.stringify([{"
           "id:'id',webSocketDebuggerUrl:'ws://127.0.0.1:9229/id',private:'discard'}])));"
           "response.emit('end');}"
           "assert.equal(await client.discoveryRequest(fakeHttp,1000,{bytes:0}),'ws://127.0.0.1:9229/id');"
           "assert.equal(optionsSeen.hostname,'127.0.0.1');assert.equal(optionsSeen.port,9229);"
           "assert.equal(optionsSeen.path,'/json/list');assert.equal(optionsSeen.method,'GET');"
           "assert.equal(optionsSeen.agent,false);assert.equal(optionsSeen.maxHeaderSize,8192);"
           "assert.equal(destroyed,2);")
    run_js(HTTP_MOCK + "function deliver(){response.emit('data',Buffer.alloc(65537));}"
           "await assert.rejects(client.discoveryRequest(fakeHttp,1000,{bytes:0}),"
           "e=>e.category==='discovery_limit');assert.equal(destroyed,2);")
    run_js(HTTP_MOCK + "function deliver(){response.emit('data',Buffer.alloc(10));}"
           "await assert.rejects(client.discoveryRequest(fakeHttp,1000,{bytes:65530}),"
           "e=>e.category==='discovery_limit');")


def test_http_redirect_is_not_followed_and_errors_are_not_printed(run_js):
    """Non-success discovery can retry but never follows a Location or emits raw text."""
    run_js(HTTP_MOCK + "function deliver(){response.statusCode=302;response.headers={location:'http://private'};"
           "response.emit('data',Buffer.from('SECRET'));response.emit('end');}"
           "assert.equal(await client.discoveryRequest(fakeHttp,1000,{bytes:0}),null);")


def test_socket_timeout_uses_fixed_five_second_budget_and_closes(run_js):
    """Drive the real timer callback immediately without waiting or opening a socket."""
    run_js(SOCKET_MOCK + "const oldSet=global.setTimeout,oldClear=global.clearTimeout;"
           "global.setTimeout=(fn,ms)=>{assert.equal(ms,5000);setImmediate(fn);return 123;};"
           "global.clearTimeout=id=>assert.equal(id,123);"
           "try{await assert.rejects(client.inspect('ws://127.0.0.1:9229/id',FakeSocket,report),"
           "e=>e.category==='socket_timeout');assert.equal(sent.length,0);assert.equal(closed,1);}"
           "finally{global.setTimeout=oldSet;global.clearTimeout=oldClear;}")


@pytest.mark.parametrize("event,category", [("close", "socket_closed"), ("error", "socket_error")])
def test_socket_closure_and_error_are_fixed_categories(run_js, event, category):
    """Socket lifecycle failures cannot expose a private event message or a stack."""
    run_js(SOCKET_MOCK + f"FakeSocket.prototype.deliver=function(){{this.handlers.{event}("
           "{message:'SECRET PRIVATE TEXT'});};"
           "await assert.rejects(client.inspect('ws://127.0.0.1:9229/id',FakeSocket,report),"
           f"e=>e.category==={json.dumps(category)});assert.equal(closed,1);")


def test_binary_message_is_refused_without_native_evaluation(run_js):
    """Binary frames are not coerced into inspector JSON or forwarded publicly."""
    run_js(SOCKET_MOCK + "FakeSocket.prototype.deliver=function(){"
           "this.handlers.message({data:Buffer.from('private payload')});};"
           "await assert.rejects(client.inspect('ws://127.0.0.1:9229/id',FakeSocket,report),"
           "e=>e.category==='protocol_invalid');assert.equal(sent.length,1);")


def test_http_request_timer_bounds_a_stalled_response(run_js):
    """A server that never emits a body/end cannot retain the discovery socket."""
    run_js(HTTP_MOCK + "function deliver(){};"
           "assert.equal(await client.discoveryRequest(fakeHttp,5,{bytes:0}),null);"
           "assert.equal(destroyed,2);")


def test_output_is_one_bounded_marker_line_with_no_stack_or_endpoint(run_js):
    """The serializer and refusal CLI have a narrow public output surface."""
    run_js("const r=client.baseReport();r.error='environment_refused';"
           "let text=client.serializeReport(r);assert(text.startsWith('QQ_NATIVE_INSPECTOR:'));"
           "assert.equal(text.split('\\n').length,2);assert(!text.includes('ws://'));"
           "assert(!text.includes('stack'));assert(Buffer.byteLength(text)<=16384+22);"
           "r.binding.exports=Array(10000).fill({name:'private'});"
           "text=client.serializeReport(r);assert.equal(JSON.parse(text.slice(client.MARKER.length)).error,'output_limit');")
    result = subprocess.run([NODE, str(SCRIPT), "--arbitrary-expression"], check=False,
                            capture_output=True, text=True, timeout=5,
                            env={**os.environ, "QQ_ISOLATED_NATIVE_INSPECTOR": "0"})
    assert result.returncode == 1
    assert result.stderr == ""
    assert result.stdout.count("\n") == 1
    report = json.loads(result.stdout.removeprefix("QQ_NATIVE_INSPECTOR:"))
    assert report["error"] == "environment_refused"
    assert not any(report["readiness"].values())
