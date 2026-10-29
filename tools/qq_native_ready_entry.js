'use strict';
// Readiness is observed before any wrapper or native-controller module is loaded.
const fs = require('node:fs');
const os = require('node:os');
const {app} = require('electron');
const nonce = process.env.QQ_WRAPPER_HOST_NONCE;
const record = {schema_version: 1, nonce, ok: false, error: 'runtime_mismatch',
  runtime: {versions: {}, exec_path_matches: process.execPath === '/opt/QQ/qq'},
  binding: {load_attempted: false, loaded: false},
  initialization: {attempted: false, initialized: false, snapshot: null, listener_registered: false, disposed: false},
  connection: {attempted: false, call_threw: false, wait_completed: false, wait_limit_ms: 8000,
    snapshot: null, disposed_snapshot: null},
  readiness: {when_ready_resolved: false, app_is_ready: app.isReady(), pure_timer_fired: false}};
let controller = null;
let lastStage = 'before_when_ready';
let written = false;
for (const key of ['node', 'electron', 'modules', 'napi', 'chrome', 'v8', 'uv']) {
  const value = process.versions[key];
  if (typeof value === 'string' && /^[0-9A-Za-z.+_-]{1,64}$/.test(value)) record.runtime.versions[key] = value;
}
/** Write actual safe readiness state synchronously, never callback arguments. */
function checkpoint(stage, snapshot = null) {
  lastStage = stage;
  record.readiness.app_is_ready = app.isReady();
  const progress = JSON.stringify({schema_version: 1, nonce, stage, snapshot,
    app_is_ready: record.readiness.app_is_ready, pure_timer_fired: record.readiness.pure_timer_fired}) + '\n';
  if (Buffer.byteLength(progress) <= 16384) {
    fs.writeFileSync('/tmp/qq-native-ready-progress.json', progress, {encoding: 'utf8', flag: 'w', mode: 0o600});
  }
}
/** Persist one bounded record then use standard Electron exit, not native shutdown. */
function finish() {
  if (written) return;
  written = true;
  record.readiness.app_is_ready = app.isReady();
  const encoded = 'QQ_NATIVE_READY_' + nonce + ':' + JSON.stringify(record) + '\n';
  if (Buffer.byteLength(encoded) <= 16384) {
    fs.writeFileSync('/tmp/qq-wrapper-host-result.json', encoded, {encoding: 'utf8', flag: 'wx', mode: 0o600});
  }
  app.exit(record.ok ? 0 : 1);
}
/** Fail fast without allowing Electron's default error dialog to hide a loop failure. */
function fatal(category) {
  // Fixed separate fatal projection; no error message, stack, class or payload retained.
  fs.writeFileSync('/tmp/qq-native-ready-fatal.json', JSON.stringify({schema_version: 1, nonce, category}) + '\n', {encoding: 'utf8', mode: 0o600});
  checkpoint(lastStage, controller && ['before_connect', 'after_connect', 'after_wait'].includes(lastStage) ? controller.getSnapshot() : null);
  app.exit(1);
}
process.once('uncaughtException', () => fatal('uncaught_exception'));
process.once('unhandledRejection', () => fatal('unhandled_rejection'));
/** Await the official Electron lifecycle, then exactly one isolated offline connect. */
async function main() {
  try {
    if (!/^[0-9a-f]{32}$/.test(nonce || '') || !record.runtime.exec_path_matches ||
        !['node', 'electron', 'modules', 'napi'].every(key => key in record.runtime.versions)) throw new Error();
    record.error = 'ready_wait_failed';
    checkpoint('before_when_ready');
    // This pure timer is enqueued before wrapper load and while awaiting app readiness.
    setTimeout(() => {
      record.readiness.pure_timer_fired = true;
      checkpoint(lastStage, controller && ['before_connect', 'after_connect', 'after_wait'].includes(lastStage) ? controller.getSnapshot() : null);
    }, 0);
    await app.whenReady();
    record.readiness.when_ready_resolved = true;
    checkpoint('after_when_ready');
    if (!app.isReady()) throw new Error();
    record.error = 'binding_load_failed';
    record.binding.load_attempted = true;
    const mod = {exports: {}};
    process.dlopen(mod, '/opt/QQ/resources/app/wrapper.node');
    record.binding.loaded = true;
    record.error = 'metadata_invalid';
    const {resolveNativeConfiguration} = require('/tmp/native_metadata.js');
    const {createLoginController} = require('/tmp/native_login.js');
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
    checkpoint('before_connect', controller.getSnapshot());
    try { controller.connect(); } catch (_error) { record.connection.call_threw = true; }
    checkpoint('after_connect', controller.getSnapshot());
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
    // Fixed codes preserve the failed milestone without leaking native exceptions.
    if (controller && record.initialization.attempted && !record.initialization.initialized) {
      record.initialization.snapshot = controller.getSnapshot();
    }
  }
  finish();
}
main();
