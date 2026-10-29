'use strict';
// Independently authored load-only main: no upstream loader or native method calls.
const fs = require('node:fs');
const CANDIDATES = ['NodeIQQNTWrapperEngine', 'NodeIKernelLoginService',
  'NodeIQQNTWrapperSession', 'NodeIQQNTStartupSessionWrapper'];
const VERSION_KEYS = ['node', 'electron', 'modules', 'napi', 'chrome', 'v8', 'uv'];
const nonce = process.env.QQ_WRAPPER_HOST_NONCE;
const record = {schema_version: 1, nonce, ok: false, error: 'runtime_mismatch',
  runtime: {versions: {}, exec_path_matches: process.execPath === '/opt/QQ/qq'},
  binding: {load_attempted: false, loaded: false, exports: [], truncated: false,
    candidates: Object.fromEntries(CANDIDATES.map(name => [name, false]))}};
// Only allowlisted bounded version strings may escape the jailed process.
for (const key of VERSION_KEYS) {
  const value = process.versions[key];
  if (typeof value === 'string' && /^[0-9A-Za-z.+_-]{1,64}$/.test(value)) {
    record.runtime.versions[key] = value;
  }
}
try {
  if (!/^[0-9a-f]{32}$/.test(nonce || '') || !record.runtime.exec_path_matches ||
      !['node', 'electron', 'modules', 'napi'].every(key => key in record.runtime.versions)) {
    record.error = 'runtime_mismatch';
  } else {
    record.binding.load_attempted = true;
    record.error = 'binding_load_failed';
    const mod = {exports: {}};
    // This exact built-in consumer call is the sole native operation.
    process.dlopen(mod, '/opt/QQ/resources/app/wrapper.node');
    record.binding.loaded = true;
    record.error = 'binding_invalid';
    if ((typeof mod.exports === 'object' && mod.exports !== null) || typeof mod.exports === 'function') {
      const names = Object.getOwnPropertyNames(mod.exports);
      record.binding.truncated = names.length > 64;
      for (const name of names.slice(0, 64)) {
        if (!/^[A-Za-z_$][A-Za-z0-9_$]{0,95}$/.test(name)) throw new Error('invalid descriptor name');
        const descriptor = Object.getOwnPropertyDescriptor(mod.exports, name);
        const data = Object.prototype.hasOwnProperty.call(descriptor, 'value');
        record.binding.exports.push({name, type: data ? typeof descriptor.value : 'accessor',
          kind: data ? 'data' : 'accessor'});
      }
      // Candidate presence checks never read an export value or invoke a getter.
      for (const name of CANDIDATES) record.binding.candidates[name] = names.includes(name);
      record.ok = true;
      record.error = null;
    }
  }
} catch (_error) {
  // Deliberately retain only the fixed current category, never native error text.
  record.ok = false;
}
const encoded = 'QQ_WRAPPER_HOST_' + nonce + ':' + JSON.stringify(record) + '\n';
if (Buffer.byteLength(encoded) <= 16384) {
  fs.writeFileSync('/tmp/qq-wrapper-host-result.json', encoded, {encoding: 'utf8', flag: 'w'});
}
// Public Electron shutdown is not a QQ-native export invocation.
process.exitCode = record.ok ? 0 : 1;
require('electron').app.exit(process.exitCode);
