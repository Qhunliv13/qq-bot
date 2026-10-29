"""Pure metadata resolver regressions: no QQ, native code, account or network use."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


MODULE = Path(__file__).resolve().parents[1] / "qq_transport" / "native_metadata.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node is required for JS metadata tests")

# Deliberately synthetic table values: tests are not a distributable production manifest.
FIXTURE = {
    "packageInfo": {"name": "QQ", "version": "3.2.34-53644", "buildVersion": 53644,
                    "platform": "linux", "eleArch": "x64"},
    "versionEntry": {"version": "3.2.34-53644", "arch": "x64", "appid": 123456,
                     "qua": "synthetic-test-qua"},
    "platform": "linux", "arch": "x64", "osRelease": "6.8.0-test",
    "hostname": "isolated-test-host", "dataPath": "/explicit/test-state",
}


@pytest.fixture
def run_js(tmp_path):
    """Save each auditable harness before invoking Node with bounded execution time."""
    def run(body):
        """Use the real module without invoking any native engine or production controller."""
        script = tmp_path / "metadata-test.cjs"
        script.write_text(
            "'use strict';\nconst assert=require('node:assert/strict');\n"
            f"const {{resolveNativeConfiguration:resolve}}=require({json.dumps(str(MODULE))});\n"
            f"const input={json.dumps(FIXTURE)};\n" + body + "\n", encoding="utf-8",
        )
        result = subprocess.run([NODE, str(script)], check=False, capture_output=True,
                                text=True, timeout=8)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout == ""
    return run


def test_exact_engine_and_login_contract(run_js):
    """Translate only explicit inputs, sharing exactly one path between both consumers."""
    run_js("""
const out=resolve(input);
assert.deepEqual(out, {
  engineConfig:{base_path_prefix:'',platform_type:5,app_type:4,
    app_version:input.packageInfo.version,os_version:input.osRelease,use_xlog:false,
    qua:input.versionEntry.qua,global_path_config:{desktopGlobalPath:input.dataPath},
    thumb_config:{maxSide:324,minSide:48,longLimit:6,density:2}},
  loginConfig:{machineId:'',appid:123456,platVer:input.osRelease,commonPath:input.dataPath,
    clientVer:input.packageInfo.version,hostName:input.hostname,externalVersion:false}
});
assert.equal(out.engineConfig.global_path_config.desktopGlobalPath,out.loginConfig.commonPath);
""")


@pytest.mark.parametrize("appid", [1, 537246140, "537246140", "000123", "9" * 40])
def test_appid_type_and_digits_are_preserved(run_js, appid):
    """Avoid lossy string-to-number conversion or numeric-to-string normalization."""
    run_js(f"input.versionEntry.appid={json.dumps(appid)};"
           "assert.equal(resolve(input).loginConfig.appid,input.versionEntry.appid);")


@pytest.mark.parametrize("appid", [None, True, False, 0, -1, 1.5, "", "0", "000", "+1",
                                   " 1", "1 ", "1\n", "1e4", "1.0", "１２", [], {}])
def test_invalid_appid_fails_closed(run_js, appid):
    """Require positive safe numeric integers or literal positive decimal digit strings."""
    run_js(f"input.versionEntry.appid={json.dumps(appid)};"
           "assert.throws(()=>resolve(input),e=>e.code==='ERR_QQ_NATIVE_METADATA');")


@pytest.mark.parametrize("mutation", [
    "delete input.packageInfo", "delete input.versionEntry", "delete input.platform",
    "delete input.arch", "delete input.osRelease", "delete input.hostname", "delete input.dataPath",
    "input.packageInfo={name:'foreign',version:'3.2.34-53644'}",
    "input.packageInfo.name='foreign'", "delete input.packageInfo.version",
    "delete input.packageInfo.buildVersion", "delete input.packageInfo.platform",
    "delete input.packageInfo.eleArch", "input.packageInfo.buildVersion=53643",
    "input.packageInfo.version='3.2.34'", "input.packageInfo.version='v3.2.34-53644'",
    "input.packageInfo.version='3.2.34-053644'", "input.packageInfo.version='3.2.34-53644\\n'",
    "input.versionEntry.version='3.2.34-53645'", "delete input.versionEntry.version",
    "input.versionEntry.arch='arm64'", "delete input.versionEntry.arch",
    "delete input.versionEntry.appid", "delete input.versionEntry.qua",
    "input.versionEntry.qua=''", "input.versionEntry.qua='  '",
    "input.versionEntry.qua='test\\nqua'", "input.arch='riscv64'", "input.arch='amd64'",
    "input.platform='win32'", "input.packageInfo.platform='darwin'",
    "input.packageInfo.eleArch='arm64'", "input.hostname=''", "input.osRelease=null",
])
def test_missing_unknown_or_mismatched_metadata_is_rejected(run_js, mutation):
    """Never guess from host properties, a nearby version, a foreign package or a fallback."""
    run_js(mutation + ";assert.throws(()=>resolve(input),e=>e.code==='ERR_QQ_NATIVE_METADATA');")


@pytest.mark.parametrize("data_path", [None, "", "/", "relative", "~/qq", "$HOME/qq", "./qq",
    "C:\\qq", "\\\\server\\qq", "/tmp/../qq", "/tmp/./qq", "/tmp//qq", "//tmp/qq",
    "/tmp/qq/", "/tmp/qq\n", "/tmp/qq\0", "/tmp\\qq"])
def test_unsafe_or_implicit_linux_paths_are_rejected(run_js, data_path):
    """Require one explicit canonical lexical Linux directory, without filesystem access."""
    run_js(f"input.dataPath={json.dumps(data_path)};"
           "assert.throws(()=>resolve(input),e=>e.code==='ERR_QQ_NATIVE_METADATA');")


def test_supplied_exact_new_version_and_arm64_are_supported(run_js):
    """Unknown versions work only with a matching explicit entry, not an embedded allowlist."""
    run_js("""
input.arch=input.packageInfo.eleArch=input.versionEntry.arch='arm64';
input.packageInfo.version=input.versionEntry.version='8.14.2-987654';
input.packageInfo.buildVersion='987654';
assert.equal(resolve(input).engineConfig.app_version,'8.14.2-987654');
input.versionEntry.version='8.14.2-987655';
assert.throws(()=>resolve(input));
""")


def test_output_is_deep_frozen_detached_and_repeatable(run_js):
    """Mutation of inputs or another result cannot change an already resolved snapshot."""
    run_js("""
const before=JSON.stringify(input), first=resolve(input), second=resolve(input);
assert.equal(JSON.stringify(input),before);
assert.deepEqual(first,second); assert.notEqual(first,second);
for(const value of [first,first.engineConfig,first.loginConfig,
    first.engineConfig.global_path_config,first.engineConfig.thumb_config]) assert(Object.isFrozen(value));
assert.throws(()=>{first.engineConfig.thumb_config.maxSide=1000;},TypeError);
input.versionEntry.qua='changed'; input.dataPath='/other/state';
assert.equal(first.engineConfig.qua,'synthetic-test-qua');
assert.equal(first.loginConfig.commonPath,'/explicit/test-state');
""")


def test_optional_explicit_presentation_profile_is_validated_and_copied(run_js):
    """Permit deliberate thumbnail settings without sharing caller-owned nested objects."""
    run_js("""
input.desktopSettings={base_path_prefix:'',app_type:4,use_xlog:true,
  thumb_config:{maxSide:600,minSide:40,longLimit:8,density:1.5}};
const out=resolve(input); input.desktopSettings.thumb_config.maxSide=700;
assert.equal(out.engineConfig.thumb_config.maxSide,600);
assert.equal(out.engineConfig.use_xlog,true);
assert(!Object.isFrozen(input.desktopSettings.thumb_config));
""")


@pytest.mark.parametrize("settings", [None, {}, {"base_path_prefix": ""},
    {"base_path_prefix": "", "app_type": 5, "use_xlog": False,
     "thumb_config": {"maxSide": 324, "minSide": 48, "longLimit": 6, "density": 2}},
    {"base_path_prefix": "", "app_type": 4, "use_xlog": "false",
     "thumb_config": {"maxSide": 324, "minSide": 48, "longLimit": 6, "density": 2}},
    {"base_path_prefix": "", "app_type": 4, "use_xlog": False,
     "thumb_config": {"maxSide": 1, "minSide": 48, "longLimit": 6, "density": 2}},
])
def test_bad_explicit_profile_is_not_silently_defaulted(run_js, settings):
    """Fail on malformed explicit settings instead of quietly applying another profile."""
    run_js(f"input.desktopSettings={json.dumps(settings)};assert.throws(()=>resolve(input));")


def test_missing_host_inputs_do_not_read_host_and_import_is_inert(run_js):
    """A VM with no process, require, fs, timers or network can load and run the module."""
    run_js(f"""
const fs=require('node:fs'), vm=require('node:vm');
const source=fs.readFileSync({json.dumps(str(MODULE))},'utf8');
const sandbox={{module:{{exports:{{}}}}}};
vm.runInNewContext(source,sandbox,{{timeout:1000}});
const isolated=sandbox.module.exports.resolveNativeConfiguration;
// Serialize in the context to retain its own JSON-object prototypes.
sandbox.serialized=JSON.stringify(input);
const result=vm.runInNewContext('module.exports.resolveNativeConfiguration(JSON.parse(serialized))',sandbox);
assert.equal(result.loginConfig.commonPath,input.dataPath);
for(const key of ['osRelease','hostname','dataPath','platform','arch']) {{
  sandbox.serialized=JSON.stringify(Object.fromEntries(Object.entries(input).filter(([k])=>k!==key)));
  assert.throws(()=>vm.runInNewContext('module.exports.resolveNativeConfiguration(JSON.parse(serialized))',sandbox));
}}
assert.equal(typeof isolated,'function');
""")


def test_accessors_and_inherited_required_fields_cannot_supply_metadata(run_js):
    """Do not execute getters or resolve required fields through caller prototype state."""
    run_js("""
Object.defineProperty(input,'hostname',{get(){throw Error('getter executed');}});
assert.throws(()=>resolve(input),e=>e.code==='ERR_QQ_NATIVE_METADATA');
assert.throws(()=>resolve(Object.create({platform:'linux'})),e=>e.code==='ERR_QQ_NATIVE_METADATA');
""")


@pytest.mark.parametrize("mutation", [
    "input.versionEntry.appid=Number.MAX_SAFE_INTEGER+1",
    "input.versionEntry.appid=Infinity", "input.versionEntry.appid=NaN",
    "input.desktopSettings={base_path_prefix:'',app_type:4,use_xlog:false,thumb_config:{maxSide:324,minSide:48,longLimit:6,density:NaN}}",
    "input.desktopSettings={base_path_prefix:'',app_type:4,use_xlog:false,thumb_config:{maxSide:324,minSide:48,longLimit:6,density:Infinity}}",
    "input.desktopSettings={base_path_prefix:'',app_type:4,use_xlog:false,extra:true,thumb_config:{maxSide:324,minSide:48,longLimit:6,density:2}}",
])
def test_non_json_numbers_and_unknown_profile_keys_are_rejected(run_js, mutation):
    """Do not permit precision loss, nonfinite native fields or misspelled explicit keys."""
    run_js(mutation + ";assert.throws(()=>resolve(input));")


def test_validation_errors_do_not_echo_sensitive_values(run_js):
    """Error messages identify fields without leaking private paths or metadata contents."""
    run_js("""
input.dataPath='/private/secret/../state';
assert.throws(()=>resolve(input),e=>!e.message.includes('secret')&&e.message.endsWith('dataPath'));
""")
