'use strict';
// Fixed initialize-only main, loaded only after the Python isolation gate.
const fs = require('node:fs');
const os = require('node:os');
const nonce = process.env.QQ_WRAPPER_HOST_NONCE;
const record = {schema_version: 1, nonce, ok: false, error: 'runtime_mismatch',
  runtime: {versions: {}, exec_path_matches: process.execPath === '/opt/QQ/qq'},
  binding: {load_attempted: false, loaded: false},
  initialization: {attempted: false, initialized: false, snapshot: null, listener_registered: false, disposed: false}};
for (const key of ['node', 'electron', 'modules', 'napi', 'chrome', 'v8', 'uv']) {
  const value = process.versions[key];
  if (typeof value === 'string' && /^[0-9A-Za-z.+_-]{1,64}$/.test(value)) record.runtime.versions[key] = value;
}
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
  // Version-specific public software metadata from pinned consumer ba0a53d;
  // no account identity, session, credential, or inferred version fallback.
  const versionEntry = {version: '3.2.34-53644', arch: 'x64', appid: 537391664,
    qua: 'V1_LNX_NQ_3.2.34_53644_GW_B'};
  const config = resolveNativeConfiguration({
    packageInfo: JSON.parse(fs.readFileSync('/tmp/qq-native-pristine-package.json', 'utf8')),
    versionEntry, platform: 'linux', arch: 'x64', osRelease: os.release(),
    hostname: 'qq-isolated-lab', dataPath: '/tmp/config/qq-native-init'});
  // Explicit observed callback keys are inert: never synthesize dispatcher success.
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
  controller.initialize();
  // Snapshot is the actual safe projection returned by the reusable controller.
  record.initialization.snapshot = controller.getSnapshot();
  record.initialization.initialized = record.initialization.snapshot.phase === 'initialized';
  record.initialization.listener_registered = record.initialization.snapshot.listener_registration_verified;
  record.error = 'dispose_failed';
  controller.dispose();
  record.initialization.disposed = controller.getSnapshot().disposed;
  record.ok = record.initialization.initialized && record.initialization.disposed;
  record.error = record.ok ? null : 'initialize_failed';
} catch (_error) {
  // Only the exact fixed stage and safe snapshot escape, never native messages.
  if (controller && record.initialization.attempted && !record.initialization.initialized) {
    record.initialization.snapshot = controller.getSnapshot();
  }
  record.ok = false;
}
const encoded = 'QQ_NATIVE_INIT_' + nonce + ':' + JSON.stringify(record) + '\n';
if (Buffer.byteLength(encoded) <= 16384) {
  fs.writeFileSync('/tmp/qq-wrapper-host-result.json', encoded, {encoding: 'utf8', flag: 'wx'});
}
// Child process exit cleans native threads; no guessed QQ-native teardown.
process.exitCode = record.ok ? 0 : 1;
require('electron').app.exit(process.exitCode);
