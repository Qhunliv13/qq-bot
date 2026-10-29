'use strict';
// Fixed offline preparation only; observed consumer order is not a vendor ABI promise.
const fs = require('node:fs');
const os = require('node:os');
const nonce = process.env.QQ_WRAPPER_HOST_NONCE;
const factory = {stage: 'runtime', validated: false, engine_type: 'not_attempted',
  login_type: 'not_attempted', startup_type: 'not_attempted', session_type: 'not_attempted',
  startup_factory_created: false, session_factory_available: false};
const record = {schema_version: 1, nonce, ok: false, error: 'runtime_mismatch',
  runtime: {versions: {}, exec_path_matches: process.execPath === '/opt/QQ/qq'},
  binding: {load_attempted: false, loaded: false}, factories: factory,
  initialization: {attempted: false, initialized: false, snapshot: null, listener_registered: false, disposed: false}};
for (const key of ['node', 'electron', 'modules', 'napi', 'chrome', 'v8', 'uv']) {
  const value = process.versions[key];
  if (typeof value === 'string' && /^[0-9A-Za-z.+_-]{1,64}$/.test(value)) record.runtime.versions[key] = value;
}
/** Persist a synchronous fixed checkpoint so watchdog retirement retains actual progress. */
function checkpoint(stage) {
  factory.stage = stage;
  fs.writeFileSync('/tmp/qq-native-factory-progress.json', JSON.stringify({schema_version: 1, nonce, stage, factories: factory}) + '\n', {encoding: 'utf8', mode: 0o600});
}
/** Require own data properties only: validation must never invoke native accessors. */
function data(object, key, expected) {
  const descriptor = Object.getOwnPropertyDescriptor(object, key);
  if (!descriptor || !Object.prototype.hasOwnProperty.call(descriptor, 'value') || typeof descriptor.value !== expected) throw new Error();
  return descriptor.value;
}
/** Store only an object-kind descriptor; never inspect account or returned native data. */
function resultType(value) {
  return value !== null && ['object', 'function'].includes(typeof value) ? typeof value : 'other';
}
let controller = null;
try {
  if (!/^[0-9a-f]{32}$/.test(nonce || '') || !record.runtime.exec_path_matches ||
      !['node', 'electron', 'modules', 'napi'].every(key => key in record.runtime.versions)) throw new Error();
  record.error = 'binding_load_failed';
  checkpoint('before_binding_load');
  record.binding.load_attempted = true;
  const mod = {exports: {}};
  process.dlopen(mod, '/opt/QQ/resources/app/wrapper.node');
  record.binding.loaded = true;
  checkpoint('after_binding_load');
  record.error = 'factory_contract_invalid';
  // Static exports/methods are validated before any getter or factory invocation.
  const engineClass = data(mod.exports, 'NodeIQQNTWrapperEngine', 'function');
  const loginClass = data(mod.exports, 'NodeIKernelLoginService', 'function');
  const startupClass = data(mod.exports, 'NodeIQQNTStartupSessionWrapper', 'function');
  const sessionClass = data(mod.exports, 'NodeIQQNTWrapperSession', 'function');
  const engineGet = data(engineClass, 'get', 'function');
  const loginGet = data(loginClass, 'get', 'function');
  const startupCreate = data(startupClass, 'create', 'function');
  const sessionGet = data(sessionClass, 'getNTWrapperSession', 'function');
  factory.validated = true;
  record.error = 'factory_engine_failed';
  checkpoint('before_engine_get');
  const engine = engineGet.call(engineClass);
  factory.engine_type = resultType(engine);
  checkpoint('after_engine_get');
  if (factory.engine_type === 'other') throw new Error();
  record.error = 'factory_login_failed';
  checkpoint('before_login_get');
  const login = loginGet.call(loginClass);
  factory.login_type = resultType(login);
  checkpoint('after_login_get');
  if (factory.login_type === 'other') throw new Error();
  record.error = 'factory_startup_failed';
  checkpoint('before_startup_create');
  const startup = startupCreate.call(startupClass);
  factory.startup_type = resultType(startup);
  factory.startup_factory_created = factory.startup_type !== 'other';
  checkpoint('after_startup_create');
  if (!factory.startup_factory_created) throw new Error();
  record.error = 'factory_session_failed';
  checkpoint('before_session_get');
  // Literal nt_1 is observed in pinned NapCat base.ts, not a Tencent guarantee.
  const session = sessionGet.call(sessionClass, 'nt_1');
  factory.session_type = resultType(session);
  factory.session_factory_available = factory.session_type !== 'other';
  checkpoint('after_session_get');
  if (!factory.session_factory_available) throw new Error();
  record.error = 'metadata_invalid';
  const {resolveNativeConfiguration} = require('/tmp/native_metadata.js');
  const {createLoginController} = require('/tmp/native_login.js');
  const versionEntry = {version: '3.2.34-53644', arch: 'x64', appid: 537391664,
    qua: 'V1_LNX_NQ_3.2.34_53644_GW_B'};
  const config = resolveNativeConfiguration({
    packageInfo: JSON.parse(fs.readFileSync('/tmp/qq-native-pristine-package.json', 'utf8')),
    versionEntry, platform: 'linux', arch: 'x64', osRelease: os.release(),
    hostname: 'qq-isolated-lab', dataPath: '/tmp/config/qq-native-init'});
  const globalAdapter = Object.freeze({
    onLog() {}, onGetSrvCalTime() {}, onShowErrUITips() {}, fixPicImgType() {},
    getAppSetting() {}, onInstallFinished() {}, onUpdateGeneralFlag() {}, onGetOfflineMsg() {}
  });
  record.error = 'controller_invalid';
  // Trusted own-get facade returns actual cached instances once; no Proxy, globals,
  // native class patches, duplicate native get, or new reusable-controller ABI.
  const cachedWrapper = Object.freeze({
    NodeIQQNTWrapperEngine: Object.freeze({get() { return engine; }}),
    NodeIKernelLoginService: Object.freeze({get() { return login; }})
  });
  controller = createLoginController({wrapper: cachedWrapper,
    engineConfig: config.engineConfig, loginConfig: config.loginConfig, globalAdapter,
    listenerLifetime: 'process'});
  record.error = 'initialize_failed';
  checkpoint('before_initialize');
  record.initialization.attempted = true;
  controller.initialize();
  record.initialization.snapshot = controller.getSnapshot();
  record.initialization.initialized = record.initialization.snapshot.phase === 'initialized';
  record.initialization.listener_registered = record.initialization.snapshot.listener_registration_verified;
  checkpoint('after_initialize');
  record.error = 'dispose_failed';
  checkpoint('before_dispose');
  controller.dispose();
  record.initialization.disposed = controller.getSnapshot().disposed;
  checkpoint('after_dispose');
  record.ok = record.initialization.initialized && record.initialization.disposed;
  record.error = record.ok ? null : 'initialize_failed';
} catch (_error) {
  // Fixed codes only: never disclose native error messages or fall back to old init.
  if (controller && record.initialization.attempted && !record.initialization.initialized) record.initialization.snapshot = controller.getSnapshot();
  record.ok = false;
}
const encoded = 'QQ_NATIVE_FACTORY_' + nonce + ':' + JSON.stringify(record) + '\n';
if (Buffer.byteLength(encoded) <= 16384) fs.writeFileSync('/tmp/qq-wrapper-host-result.json', encoded, {encoding: 'utf8', flag: 'wx'});
// app.exit is fixed process cleanup, not an invented native session teardown.
require('electron').app.exit(record.ok ? 0 : 1);
