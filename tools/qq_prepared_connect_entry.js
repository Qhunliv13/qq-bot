'use strict';
// One fixed offline experiment. The Python owner supplies all isolation gates.
const fs = require('node:fs');
const os = require('node:os');
const nonce = process.env.QQ_WRAPPER_HOST_NONCE;
// Only the shared fixed-mode runner sets this flag; all other modes delete it.
const phaseControl = process.env.QQ_PREPARED_PHASE_CONTROL === '1';
const progressPath = phaseControl ? '/tmp/qq-prepared-phase-progress.json' : '/tmp/qq-prepared-connect-progress.json';
const started = process.hrtime.bigint();
const history = [];
let host = null;
let controller = null;
let progress = null;
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
/** Monotonic elapsed milliseconds never contain native data or wall-clock identity. */
function elapsed() { return Number((process.hrtime.bigint() - started) / 1000000n); }
/** A bounded synchronous prefix survives later native/event-loop stalls. */
function checkpoint(stage) {
  history.push({stage, elapsed_ms: elapsed()});
  progress = {schema_version: 1, nonce, stage, history: history.map(item => ({...item})),
    host: host ? host.getSnapshot() : null, login: controller ? controller.getSnapshot() : null};
  const raw = JSON.stringify(progress) + '\n';
  if (Buffer.byteLength(raw) > 16384) throw new Error('progress_bound');
  fs.writeFileSync(progressPath, raw, {encoding: 'utf8', flag: 'w', mode: 0o600});
}
/** One timer implementation proves dispatch, rearming only an early deadline. */
function waitForCheckpoint(delay, stage, origin = process.hrtime.bigint()) {
  return new Promise((resolve, reject) => {
    /** Never advance an irreversible native stage before its full monotonic wait. */
    function tick() {
      const remaining = delay - Number((process.hrtime.bigint() - origin) / 1000000n);
      if (remaining > 0) { setTimeout(tick, remaining); return; }
      try { checkpoint(stage); resolve(); } catch (error) { reject(error); }
    }
    setTimeout(tick, delay);
  });
}
/** Preserve original connect deadlines and order when the phase flag is absent. */
function waitForTicks() {
  const origin = process.hrtime.bigint();
  return Promise.all([[1000, 'tick_1s'], [5000, 'tick_5s'], [8000, 'tick_8s']].map(
    ([delay, stage]) => waitForCheckpoint(delay, stage, origin)));
}
/** Reuse actual prepared factories once; no session, account, QR or native teardown. */
async function main() {
  try {
    checkpoint('runtime');
    if (!/^[0-9a-f]{32}$/.test(nonce || '') || !record.runtime.exec_path_matches ||
        !['node', 'electron', 'modules', 'napi'].every(key => key in record.runtime.versions)) throw new Error();
    if (phaseControl) await waitForCheckpoint(1000, 'phase_baseline_1s');
    record.error = 'binding_load_failed';
    record.binding.load_attempted = true;
    checkpoint('before_binding_load');
    const mod = {exports: {}};
    process.dlopen(mod, '/opt/QQ/resources/app/wrapper.node');
    record.binding.loaded = true;
    checkpoint('after_binding_load');
    if (phaseControl) await waitForCheckpoint(1000, 'phase_loaded_1s');
    record.error = 'metadata_invalid';
    const {resolveNativeConfiguration} = require('/tmp/native_metadata.js');
    const {createNativeHost} = require('/tmp/native_host.js');
    const {createLoginController} = require('/tmp/native_login.js');
    const config = resolveNativeConfiguration({
      packageInfo: JSON.parse(fs.readFileSync('/tmp/qq-native-pristine-package.json', 'utf8')),
      versionEntry: {version: '3.2.34-53644', arch: 'x64', appid: 537391664, qua: 'V1_LNX_NQ_3.2.34_53644_GW_B'},
      platform: 'linux', arch: 'x64', osRelease: os.release(), hostname: 'qq-isolated-lab',
      dataPath: '/tmp/config/qq-native-init'});
    record.error = 'prepare_failed';
    host = createNativeHost({wrapper: mod.exports, sessionName: 'nt_1', lifecycleMode: 'startup'});
    checkpoint('before_prepare');
    host.prepare();
    checkpoint('after_prepare');
    if (phaseControl) await waitForCheckpoint(1000, 'phase_prepared_1s');
    // No arguments are read or retained by this explicit inert adapter.
    const globalAdapter = Object.freeze({onLog() {}, onGetSrvCalTime() {}, onShowErrUITips() {},
      fixPicImgType() {}, getAppSetting() {}, onInstallFinished() {}, onUpdateGeneralFlag() {}, onGetOfflineMsg() {}});
    record.error = 'controller_invalid';
    controller = createLoginController({wrapper: host.getLoginWrapper(), engineConfig: config.engineConfig,
      loginConfig: config.loginConfig, globalAdapter, listenerLifetime: 'process'});
    record.error = 'initialize_failed';
    record.initialization.attempted = true;
    checkpoint('before_initialize');
    controller.initialize();
    record.initialization.snapshot = controller.getSnapshot();
    record.initialization.initialized = record.initialization.snapshot.phase === 'initialized';
    record.initialization.listener_registered = record.initialization.snapshot.listener_registration_verified;
    checkpoint('after_initialize');
    if (phaseControl) await waitForCheckpoint(1000, 'phase_initialized_1s');
    // Only the observed void registration permits logical-only controller disposal.
    record.error = 'listener_contract_unexpected';
    if (record.initialization.snapshot.listener_id_type !== 'undefined') throw new Error();
    record.error = 'connect_observation_failed';
    record.connection.attempted = true;
    checkpoint('before_connect');
    try { controller.connect(); } catch (_) { record.connection.call_threw = true; }
    checkpoint('after_connect');
    await waitForTicks();
    record.connection.wait_completed = true;
    record.connection.snapshot = controller.getSnapshot();
    record.error = 'dispose_failed';
    checkpoint('before_dispose');
    controller.dispose();
    host.dispose();
    record.connection.disposed_snapshot = controller.getSnapshot();
    record.initialization.disposed = record.connection.disposed_snapshot.disposed;
    checkpoint('after_dispose');
    record.ok = record.initialization.initialized && record.initialization.disposed;
    record.error = record.ok ? null : 'connect_observation_failed';
  } catch (_) {
    // Raw exceptions, callback arguments and factory handles never cross this boundary.
    if (controller && record.initialization.attempted && !record.initialization.initialized) {
      record.initialization.snapshot = controller.getSnapshot();
    }
  }
  const raw = (phaseControl ? 'QQ_PREPARED_PHASE_' : 'QQ_PREPARED_CONNECT_') + nonce + ':' + JSON.stringify({...record, progress}) + '\n';
  if (Buffer.byteLength(raw) <= 16384) fs.writeFileSync('/tmp/qq-wrapper-host-result.json', raw, {encoding: 'utf8', flag: 'wx'});
  // Process retirement is the only native cleanup, including all failed paths.
  require('electron').app.exit(record.ok ? 0 : 1);
}
main();
