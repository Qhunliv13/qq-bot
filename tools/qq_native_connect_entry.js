'use strict';
// Fixed offline observation only; Python owns every isolation and privilege gate.
const fs = require('node:fs');
const os = require('node:os');
const nonce = process.env.QQ_WRAPPER_HOST_NONCE;
const record = {schema_version: 1, nonce, ok: false, error: 'runtime_mismatch',
  runtime: {versions: {}, exec_path_matches: process.execPath === '/opt/QQ/qq'},
  binding: {load_attempted: false, loaded: false},
  initialization: {attempted: false, initialized: false, snapshot: null, listener_registered: false, disposed: false},
  connection: {attempted: false, call_threw: false, wait_completed: false, wait_limit_ms: 8000,
    snapshot: null, disposed_snapshot: null}};
for (const key of ['node', 'electron', 'modules', 'napi', 'chrome', 'v8', 'uv']) {
  const value = process.versions[key];
  if (typeof value === 'string' && /^[0-9A-Za-z.+_-]{1,64}$/.test(value)) record.runtime.versions[key] = value;
}
/** Synchronous fixed checkpoints distinguish blocked native calls from timer stalls. */
function checkpoint(stage, snapshot = null) {
  const progress = JSON.stringify({schema_version: 1, nonce, stage, snapshot}) + '\n';
  if (Buffer.byteLength(progress) <= 16384) {
    fs.writeFileSync('/tmp/qq-native-connect-progress.json', progress, {encoding: 'utf8', flag: 'w', mode: 0o600});
  }
}
/** Observe one authorized connect; never ask for any account or image operation. */
async function main() {
  let controller = null;
  try {
    if (!/^[0-9a-f]{32}$/.test(nonce || '') || !record.runtime.exec_path_matches ||
        !['node', 'electron', 'modules', 'napi'].every(key => key in record.runtime.versions)) throw new Error();
    record.error = 'binding_load_failed';
    record.binding.load_attempted = true;
    const mod = {exports: {}};
    process.dlopen(mod, '/opt/QQ/resources/app/wrapper.node');
    record.binding.loaded = true;
    record.error = 'metadata_invalid';
    const {resolveNativeConfiguration} = require('/tmp/native_metadata.js');
    const {createLoginController} = require('/tmp/native_login.js');
    // Same pinned public metadata as the measured initialize control, unchanged.
    const versionEntry = {version: '3.2.34-53644', arch: 'x64', appid: 537391664,
      qua: 'V1_LNX_NQ_3.2.34_53644_GW_B'};
    const config = resolveNativeConfiguration({
      packageInfo: JSON.parse(fs.readFileSync('/tmp/qq-native-pristine-package.json', 'utf8')),
      versionEntry, platform: 'linux', arch: 'x64', osRelease: os.release(),
      hostname: 'qq-isolated-lab', dataPath: '/tmp/config/qq-native-init'});
    // No callback arguments are read, retained or serialized by this adapter.
    const globalAdapter = Object.freeze({
      onLog() {}, onGetSrvCalTime() {}, onShowErrUITips() {}, fixPicImgType() {},
      getAppSetting() {}, onInstallFinished() {}, onUpdateGeneralFlag() {}, onGetOfflineMsg() {}
    });
    record.error = 'controller_invalid';
    controller = createLoginController({wrapper: mod.exports,
      engineConfig: config.engineConfig, loginConfig: config.loginConfig, globalAdapter,
      listenerLifetime: 'process'});
    record.error = 'initialize_failed';
    record.initialization.attempted = true;
    checkpoint('before_initialize');
    controller.initialize();
    record.initialization.snapshot = controller.getSnapshot();
    record.initialization.initialized = record.initialization.snapshot.phase === 'initialized';
    record.initialization.listener_registered = record.initialization.snapshot.listener_registration_verified;
    record.error = 'connect_observation_failed';
    record.connection.attempted = true;
    // Exactly one call: even an ABI return-type rejection must retain its snapshot.
    checkpoint('before_connect', controller.getSnapshot());
    try { controller.connect(); } catch (_error) { record.connection.call_threw = true; }
    checkpoint('after_connect', controller.getSnapshot());
    // A fixed bounded window also permits callbacks following a strict ABI rejection.
    await new Promise(resolve => setTimeout(resolve, record.connection.wait_limit_ms));
    checkpoint('after_wait', controller.getSnapshot());
    record.connection.wait_completed = true;
    record.connection.snapshot = controller.getSnapshot();
    record.error = 'dispose_failed';
    controller.dispose();
    record.connection.disposed_snapshot = controller.getSnapshot();
    record.initialization.disposed = record.connection.disposed_snapshot.disposed;
    record.ok = record.initialization.initialized && record.connection.wait_completed && record.initialization.disposed;
    record.error = record.ok ? null : 'connect_observation_failed';
  } catch (_error) {
    // Never emit native errors, raw arguments, identity or other private state.
    if (controller && record.initialization.attempted && !record.initialization.initialized) {
      record.initialization.snapshot = controller.getSnapshot();
    }
  }
  const encoded = 'QQ_NATIVE_CONNECT_' + nonce + ':' + JSON.stringify(record) + '\n';
  if (Buffer.byteLength(encoded) <= 16384) {
    fs.writeFileSync('/tmp/qq-wrapper-host-result.json', encoded, {encoding: 'utf8', flag: 'wx'});
  }
  // Public Electron process exit retires the entire listener owner; no native teardown.
  require('electron').app.exit(record.ok ? 0 : 1);
}
main();
