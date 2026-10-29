'use strict';

// This module never loads a native library. The owner supplies the matching host's
// exports/configuration and explicitly authorizes each native lifecycle operation.
const path = require('node:path');
const MAX_PNG_BYTES = 1024 * 1024;
const MAX_URL_LENGTH = 4096;
const CODES = Object.freeze({
  INVALID_CONFIG: 'INVALID_CONFIG', INVALID_WRAPPER: 'INVALID_WRAPPER',
  INVALID_STATE: 'INVALID_STATE', INITIALIZE_FAILED: 'INITIALIZE_FAILED',
  CONNECT_FAILED: 'CONNECT_FAILED', CONNECT_REJECTED: 'CONNECT_REJECTED',
  QR_REQUEST_FAILED: 'QR_REQUEST_FAILED', QR_REQUEST_REJECTED: 'QR_REQUEST_REJECTED',
  INVALID_EVENT: 'INVALID_EVENT', LOGIN_FAILED: 'LOGIN_FAILED',
  QR_SESSION_FAILED: 'QR_SESSION_FAILED', REMOVE_LISTENER_FAILED: 'REMOVE_LISTENER_FAILED',
  LISTENER_FAILED: 'LISTENER_FAILED'
});

/** Fixed public errors deliberately discard native messages, causes and arguments. */
class NativeLoginError extends Error {
  constructor(code) {
    super(Object.hasOwn(CODES, code) ? code : CODES.INVALID_STATE);
    this.name = 'NativeLoginError';
    this.code = this.message;
  }
}

/** Fail without embedding any caller-controlled or native diagnostic text. */
function reject(code) { throw new NativeLoginError(code); }

/** Read own data only: accessors and inherited fields cannot trigger user code. */
function data(object, key, code = CODES.INVALID_CONFIG) {
  if ((typeof object !== 'object' && typeof object !== 'function') || object === null) reject(code);
  const descriptor = Object.getOwnPropertyDescriptor(object, key);
  if (!descriptor || !Object.hasOwn(descriptor, 'value')) reject(code);
  return descriptor.value;
}

/** Project the exact supplied schema; unknown keys cannot sneak into native calls. */
function fields(object, keys) {
  if (!object || typeof object !== 'object' || Array.isArray(object)) reject(CODES.INVALID_CONFIG);
  const own = Reflect.ownKeys(object);
  if (own.length !== keys.length || own.some(key => !keys.includes(key))) reject(CODES.INVALID_CONFIG);
  return Object.fromEntries(keys.map(key => [key, data(object, key)]));
}

/** Bound textual inputs, rejecting blank/control-bearing metadata instead of guessing. */
function text(value, max = 4096) {
  if (typeof value !== 'string' || !value.trim() || value.length > max || /[\x00-\x1f\x7f]/.test(value)) reject(CODES.INVALID_CONFIG);
  return value;
}

/** Container paths are explicit absolute POSIX paths; this module creates no files. */
function containerPath(value) {
  text(value);
  if (!path.posix.isAbsolute(value) || value.includes('\\') || value.split('/').includes('..')) reject(CODES.INVALID_CONFIG);
  return value;
}

/** Copy/freeze only observed same-version fields; never normalize appid's type. */
function configs(engineInput, loginInput) {
  const engine = fields(engineInput, ['base_path_prefix', 'platform_type', 'app_type', 'app_version', 'os_version', 'use_xlog', 'qua', 'global_path_config', 'thumb_config']);
  // Pinned source explicitly uses an empty prefix; nonempty prefixes must still
  // remain inside the caller's absolute container-path contract.
  if (engine.base_path_prefix !== '') containerPath(engine.base_path_prefix);
  // Only the explicitly observed Linux=5 / desktop app=4 contract is supported.
  if (engine.platform_type !== 5 || engine.app_type !== 4 || typeof engine.use_xlog !== 'boolean') reject(CODES.INVALID_CONFIG);
  for (const key of ['app_version', 'os_version', 'qua']) text(engine[key]);
  const globalPath = fields(engine.global_path_config, ['desktopGlobalPath']);
  containerPath(globalPath.desktopGlobalPath);
  const thumb = fields(engine.thumb_config, ['maxSide', 'minSide', 'longLimit', 'density']);
  for (const value of Object.values(thumb)) {
    if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) reject(CODES.INVALID_CONFIG);
  }
  if (thumb.minSide > thumb.maxSide) reject(CODES.INVALID_CONFIG);
  engine.global_path_config = Object.freeze(globalPath);
  engine.thumb_config = Object.freeze(thumb);
  const login = fields(loginInput, ['machineId', 'appid', 'platVer', 'commonPath', 'clientVer', 'hostName', 'externalVersion']);
  if (login.machineId !== '' || typeof login.externalVersion !== 'boolean') reject(CODES.INVALID_CONFIG);
  const appidOK = (typeof login.appid === 'number' && Number.isSafeInteger(login.appid) && login.appid > 0)
    || (typeof login.appid === 'string' && /^[0-9]{1,32}$/.test(login.appid));
  if (!appidOK) reject(CODES.INVALID_CONFIG);
  for (const key of ['platVer', 'clientVer', 'hostName']) text(login[key]);
  containerPath(login.commonPath);
  return { engine: Object.freeze(engine), login: Object.freeze(login) };
}

/** Verify the wrapper's own exports and own static get without invoking either. */
function getter(wrapper, name) {
  const exported = data(wrapper, name, CODES.INVALID_WRAPPER);
  const get = data(exported, 'get', CODES.INVALID_WRAPPER);
  if (typeof get !== 'function') reject(CODES.INVALID_WRAPPER);
  return { exported, get };
}

/** Capture methods without invoking getter properties, including native prototypes. */
function method(instance, name) {
  let owner = instance;
  // Native classes may put methods on their prototype; bound traversal avoids
  // treating an arbitrary distant prototype as a supported native contract.
  let depth = 0;
  while (owner !== null && depth <= 3 && (typeof owner === 'object' || typeof owner === 'function')) {
    depth += 1;
    const descriptor = Object.getOwnPropertyDescriptor(owner, name);
    if (descriptor) {
      if (!Object.hasOwn(descriptor, 'value') || typeof descriptor.value !== 'function') reject(CODES.INVALID_WRAPPER);
      return (...args) => descriptor.value.apply(instance, args);
    }
    owner = Object.getPrototypeOf(owner);
  }
  reject(CODES.INVALID_WRAPPER);
}

/** Validate canonical bounded PNG bytes and HTTPS URL, never logging the payload. */
function qrPayload(raw) {
  const png = data(raw, 'pngBase64QrcodeData', CODES.INVALID_EVENT);
  const url = data(raw, 'qrcodeUrl', CODES.INVALID_EVENT);
  // Accept plain base64 or PNG's exact data-URI representation; never accept
  // arbitrary image/media MIME types or strip an unvalidated prefix.
  if (typeof png !== 'string') reject(CODES.INVALID_EVENT);
  const encoded = png.startsWith('data:image/png;base64,') ? png.slice(22) : png;
  if (encoded.length > Math.ceil(MAX_PNG_BYTES / 3) * 4 || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(encoded)) reject(CODES.INVALID_EVENT);
  const bytes = Buffer.from(encoded, 'base64');
  // Signature plus IHDR and IEND bound the PNG container; image decoding belongs
  // to the consuming UI (never execute/decode untrusted images in this module).
  if (bytes.length < 45 || bytes.length > MAX_PNG_BYTES || bytes.toString('base64') !== encoded
      || bytes.subarray(0, 8).toString('hex') !== '89504e470d0a1a0a'
      || bytes.readUInt32BE(8) !== 13 || bytes.toString('ascii', 12, 16) !== 'IHDR'
      || bytes.readUInt32BE(16) === 0 || bytes.readUInt32BE(20) === 0
      || bytes.readUInt32BE(bytes.length - 12) !== 0 || bytes.toString('ascii', bytes.length - 8, bytes.length - 4) !== 'IEND') reject(CODES.INVALID_EVENT);
  // Walk the bounded chunk container, refusing truncation, missing image data,
  // trailing content and giant dimensions without inflating attacker input.
  if (bytes.readUInt32BE(16) > 8192 || bytes.readUInt32BE(20) > 8192) reject(CODES.INVALID_EVENT);
  let offset = 8;
  let imageData = false;
  let ended = false;
  while (offset + 12 <= bytes.length) {
    const length = bytes.readUInt32BE(offset);
    const type = bytes.toString('ascii', offset + 4, offset + 8);
    if (length > bytes.length - offset - 12 || !/^[A-Za-z]{4}$/.test(type)) reject(CODES.INVALID_EVENT);
    if (offset > 8 && type === 'IHDR') reject(CODES.INVALID_EVENT);
    if (type === 'IDAT') imageData = true;
    offset += length + 12;
    if (type === 'IEND') { ended = true; break; }
  }
  if (!ended || !imageData || offset !== bytes.length) reject(CODES.INVALID_EVENT);
  if (typeof url !== 'string' || url.length > MAX_URL_LENGTH || /[\s\x00-\x1f\x7f]/.test(url)) reject(CODES.INVALID_EVENT);
  let parsed;
  try { parsed = new URL(url); } catch (_) { reject(CODES.INVALID_EVENT); }
  if (parsed.protocol !== 'https:' || !parsed.hostname || parsed.username || parsed.password) reject(CODES.INVALID_EVENT);
  return Object.freeze({ pngBase64QrcodeData: png, qrcodeUrl: url });
}

/** Require actual callback identity, discarding every other raw field. */
function identityPayload(raw) {
  const uin = data(raw, 'uin', CODES.INVALID_EVENT);
  const uid = data(raw, 'uid', CODES.INVALID_EVENT);
  for (const value of [uin, uid]) {
    if (typeof value !== 'string' || !value.trim() || value.length > 256 || /[\x00-\x1f\x7f]/.test(value)) reject(CODES.INVALID_EVENT);
  }
  return Object.freeze({ uin, uid });
}

/** Create a dormant single-use lifecycle; only explicit methods call native code. */
function createLoginController({ wrapper, engineConfig, loginConfig, globalAdapter, clock = Date.now, listenerLifetime = 'token' }) {
  // Void listener handles are accepted only under an explicit process-owner
  // contract; there is no automatic compatibility fallback or removal guess.
  if (!['token', 'process'].includes(listenerLifetime)) reject(CODES.INVALID_CONFIG);
  const config = configs(engineConfig, loginConfig);
  const engineGet = getter(wrapper, 'NodeIQQNTWrapperEngine');
  const loginGet = getter(wrapper, 'NodeIKernelLoginService');
  if (!globalAdapter || (typeof globalAdapter !== 'object' && typeof globalAdapter !== 'function') || typeof clock !== 'function') reject(CODES.INVALID_CONFIG);
  let phase = 'new';
  let disposed = false;
  let initAttempted = false;
  let connectAttempted = false;
  let connected = false;
  let qrAttempted = false;
  let qrAvailable = false;
  let identity = null;
  let listenerId = null;
  let native = null;
  let lastCode = null;
  // Fixed milestones expose where initialization stopped, never native details.
  let initializationStep = null;
  let failedStage = null;
  // Diagnostic enums distinguish a thrown call from a returned invalid ID;
  // neither the actual ID nor any native exception details are exposed.
  let registrationCallReturned = false;
  let listenerIdType = 'not_attempted';
  let listenerRegistrationVerified = false;
  let nativeListenerRemoved = false;
  let cleanupRequired = 'none';
  let connectCallReturned = false;
  let connectResultType = 'not_attempted';
  // Fixed callback names and saturated counters reveal delivery only, never args.
  const nativeCallbackCounts = { onLoginConnected: 0, onLoginConnecting: 0, onLoginDisConnected: 0, onQRCodeGetPicture: 0, onQRCodeLoginSucceed: 0, onLoginFailed: 0, onQRCodeSessionFailed: 0 };
  const subscribers = new Set();

  /** Deliver frozen projections asynchronously; reporting failures never recurses. */
  function publish(type, payload = Object.freeze({}), excluded = null) {
    let at = null;
    try { const value = clock(); if (Number.isFinite(value)) at = value; } catch (_) { /* A clock cannot break native callback handling. */ }
    const event = Object.freeze({ type, phase, at, payload });
    const recipients = [...subscribers];
    queueMicrotask(() => {
      for (const callback of recipients) {
        if (callback === excluded || !subscribers.has(callback)) continue;
        /** Drop all thrown/rejected details, including QR-bearing error messages. */
        const report = () => { if (type !== 'listener_error') publish('listener_error', Object.freeze({ code: CODES.LISTENER_FAILED }), callback); };
        try { Promise.resolve(callback(event)).catch(report); } catch (_) { report(); }
      }
    });
  }

  /** Terminal failure prevents hidden retries and clears sensitive state. */
  function fail(code) {
    phase = 'failed'; connected = false; qrAvailable = false; identity = null;
    lastCode = code;
    publish('failed', Object.freeze({ code }));
  }

  /** Count known native deliveries, never arguments, before lifecycle gating. */
  function observed(name) {
    nativeCallbackCounts[name] = Math.min(100, nativeCallbackCounts[name] + 1);
    if (!disposed) listenerRegistrationVerified = true;
  }

  /** Ignore late/out-of-order callbacks; no event grants authorization to connect. */
  function active() {
    return !disposed && connectAttempted && phase !== 'failed' && phase !== 'disconnected';
  }

  /** Validate the payload synchronously before exposing any immutable projection. */
  function receive(project, raw, type, next) {
    if (!active()) return;
    if (!connected || !qrAttempted || phase === 'authenticated') { fail(CODES.INVALID_EVENT); return; }
    let payload;
    try { payload = project(raw); } catch (_) { fail(CODES.INVALID_EVENT); return; }
    if (next === 'authenticated') { identity = payload; qrAvailable = false; }
    else qrAvailable = true;
    phase = next;
    publish(type, payload);
  }

  // Only observed callback names are registered; arbitrary raw native fields are
  // never forwarded, even on failure or disconnect callbacks.
  const listener = Object.freeze({
    /** Native callback, not connect's acknowledgement, proves connection. */
    onLoginConnected() {
      observed('onLoginConnected');
      if (!active() || connected) return;
      connected = true; phase = 'connected'; publish('connected');
    },
    /** Native transport progress is not authentication. */
    onLoginConnecting() {
      observed('onLoginConnecting');
      if (!active() || connected) return;
      phase = 'connecting'; publish('connecting');
    },
    /** Disconnect does not call offline/destroy and cannot trigger reconnect. */
    onLoginDisConnected() {
      observed('onLoginDisConnected');
      if (!active()) return;
      connected = false; qrAvailable = false; identity = null;
      phase = 'disconnected'; publish('disconnected');
    },
    /** QR callback alone contains the sensitive image and URL. */
    onQRCodeGetPicture(raw) { observed('onQRCodeGetPicture'); receive(qrPayload, raw, 'qr', 'qr_pending'); },
    /** Preserve observed identity only after a valid explicit QR request. */
    onQRCodeLoginSucceed(raw) { observed('onQRCodeLoginSucceed'); receive(identityPayload, raw, 'authenticated', 'authenticated'); },
    /** Native diagnostic arguments are intentionally discarded. */
    onLoginFailed() { observed('onLoginFailed'); if (active()) fail(CODES.LOGIN_FAILED); },
    /** Session failure is terminal; a fresh attempt requires external policy. */
    onQRCodeSessionFailed() { observed('onQRCodeSessionFailed'); if (active()) fail(CODES.QR_SESSION_FAILED); },
    // These additional exact consumer-declared callbacks intentionally discard
    // arguments. They cannot grant authentication, trigger retries or expose raw
    // records; the controller only supports the explicit QR lifecycle above.
    /** Polling notification is not a QR image or authentication proof. */
    onQRCodeLoginPollingStarted() {},
    /** Scan notification alone does not prove login succeeded. */
    onQRCodeSessionUserScaned() {},
    /** Logout notifications do not initiate another native operation. */
    onLogoutSucceed() {},
    /** Native error arguments are deliberately not forwarded. */
    onLogoutFailed() {},
    /** An unrelated logged-in notification cannot substitute for QR success. */
    onUserLoggedIn() {},
    /** Unsupported quick-login paths never enable an automatic fallback. */
    onQRCodeSessionQuickLoginFailed() {},
    /** Password login is outside this controller's explicit QR-only contract. */
    onPasswordLoginFailed() {},
    /** Preserve the vendor consumer's unusual capitalization without guessing. */
    OnConfirmUnusualDeviceFailed() {},
    /** Account-count notifications do not alter authorization or account choice. */
    onQQLoginNumLimited() {},
    /** Raw state objects are not treated as authenticated identity. */
    onLoginState() {},
    /** Account history is neither retained nor exposed by this controller. */
    onLoginRecordUpdate() {}
  });

  /** Expose only safe status; identity requires its own explicit read. */
  function getSnapshot() {
    return Object.freeze({ phase, disposed, qr_available: qrAvailable, authenticated: phase === 'authenticated', failure_code: lastCode, initialization_step: initializationStep, failed_stage: failedStage, registration_call_returned: registrationCallReturned, listener_id_type: listenerIdType, listener_handle_available: listenerId !== null, listener_registration_verified: listenerRegistrationVerified, native_listener_removed: nativeListenerRemoved, cleanup_required: cleanupRequired, listener_lifetime: listenerLifetime, connect_call_returned: connectCallReturned, connect_result_type: connectResultType, native_callback_counts: Object.freeze({ ...nativeCallbackCounts }) });
  }

  /** A synchronous native callback may dispose; never continue its outer request. */
  function requireNotDisposed() {
    if (disposed) reject(CODES.INVALID_STATE);
  }

  /** Obtain native singletons and register once, without connecting or QR work. */
  function initialize() {
    if (disposed || initAttempted || phase !== 'new') reject(CODES.INVALID_STATE);
    initAttempted = true;
    // Even a factory can perform native work before throwing or disposing us.
    // Until a live controller owns a numeric token, only its owner can retire it.
    cleanupRequired = 'process_exit';
    try {
      initializationStep = 'factory_engine';
      const engine = engineGet.get.call(engineGet.exported);
      requireNotDisposed();
      initializationStep = 'factory_login';
      const login = loginGet.get.call(loginGet.exported);
      requireNotDisposed();
      initializationStep = 'validate_methods';
      const engineInit = method(engine, 'initWithDeskTopConfig');
      native = {
        init: method(login, 'initConfig'), add: method(login, 'addKernelLoginListener'),
        remove: method(login, 'removeKernelLoginListener'), connect: method(login, 'connect'),
        qr: method(login, 'getQRCodePicture')
      };
      initializationStep = 'engine_config';
      engineInit(config.engine, globalAdapter);
      requireNotDisposed();
      initializationStep = 'login_config';
      native.init(config.login);
      requireNotDisposed();
      initializationStep = 'register_listener';
      listenerIdType = 'threw';
      const id = native.add(listener);
      registrationCallReturned = true;
      const returnedType = typeof id;
      listenerIdType = ['number', 'string', 'undefined'].includes(returnedType) ? returnedType : 'other';
      // Safe return-type telemetry survives disposal, but a late token must not
      // resurrect ownership or trigger an extra native removal after disposal.
      requireNotDisposed();
      // A thrown/invalid registration may still have attached a native listener.
      // Only an actual numeric handle authorizes exact-ID removal.
      if (Number.isSafeInteger(id) && id >= 0) {
        listenerId = id;
        cleanupRequired = 'remove_listener';
      } else if (id === undefined && listenerLifetime === 'process') {
        cleanupRequired = 'process_exit';
      } else {
        cleanupRequired = 'process_exit';
        reject(CODES.INITIALIZE_FAILED);
      }
      initializationStep = 'initialized';
      phase = 'initialized'; publish('initialized');
      return getSnapshot();
    } catch (_) {
      // Disposal wins over both a returned result and a later native exception.
      requireNotDisposed();
      failedStage = initializationStep;
      fail(CODES.INITIALIZE_FAILED); reject(CODES.INITIALIZE_FAILED);
    }
  }

  /** One explicit connect request; a boolean is never a success identity. */
  function connect() {
    if (disposed || phase !== 'initialized' || connectAttempted) reject(CODES.INVALID_STATE);
    connectAttempted = true;
    phase = 'connecting'; publish('connecting');
    let acknowledged;
    connectResultType = 'threw';
    try {
      acknowledged = native.connect();
      connectCallReturned = true;
      connectResultType = ['boolean', 'undefined'].includes(typeof acknowledged) ? typeof acknowledged : 'other';
    } catch (_) {
      requireNotDisposed();
      fail(CODES.CONNECT_FAILED); reject(CODES.CONNECT_FAILED);
    }
    // Only diagnostic return enums may change after synchronous disposal.
    requireNotDisposed();
    if (typeof acknowledged !== 'boolean') { fail(CODES.CONNECT_FAILED); reject(CODES.CONNECT_FAILED); }
    if (!acknowledged) fail(CODES.CONNECT_REJECTED);
    return acknowledged;
  }

  /** Request once only after actual connected callback; never fabricate QR data. */
  function getQRCodePicture() {
    if (disposed || !connected || phase !== 'connected' || qrAttempted) reject(CODES.INVALID_STATE);
    qrAttempted = true;
    phase = 'qr_pending'; publish('qr_pending');
    let acknowledged;
    try { acknowledged = native.qr(); }
    catch (_) {
      requireNotDisposed();
      fail(CODES.QR_REQUEST_FAILED); reject(CODES.QR_REQUEST_FAILED);
    }
    requireNotDisposed();
    if (typeof acknowledged !== 'boolean') { fail(CODES.QR_REQUEST_FAILED); reject(CODES.QR_REQUEST_FAILED); }
    if (!acknowledged) fail(CODES.QR_REQUEST_REJECTED);
    return acknowledged;
  }

  /** Subscribe without native work; unsubscribe is repeat-safe. */
  function subscribe(callback) {
    if (disposed || typeof callback !== 'function') reject(CODES.INVALID_STATE);
    subscribers.add(callback);
    return () => subscribers.delete(callback);
  }

  /** Explicitly read the last validated callback identity, never a guessed ID. */
  function getIdentity() { return identity; }

  /** Dispose this controller only; void registrations require owner process exit. */
  function dispose() {
    if (disposed) return getSnapshot();
    disposed = true; connected = false; qrAvailable = false; identity = null;
    phase = 'disconnected'; subscribers.clear();
    if (listenerId !== null) {
      const id = listenerId;
      listenerId = null;
      try {
        native.remove(id);
        nativeListenerRemoved = true;
        cleanupRequired = 'none';
      } catch (_) {
        cleanupRequired = 'process_exit';
        lastCode = CODES.REMOVE_LISTENER_FAILED; reject(CODES.REMOVE_LISTENER_FAILED);
      }
    }
    return getSnapshot();
  }

  return Object.freeze({ initialize, connect, getQRCodePicture, subscribe, getSnapshot, getIdentity, dispose });
}

module.exports = Object.freeze({ createLoginController, NativeLoginError });
