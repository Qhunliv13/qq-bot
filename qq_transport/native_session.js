'use strict';

// Consumer-side lifecycle only: never load native code or synthesize account config.
const path = require('node:path');
const { types } = require('node:util');
const CODES = Object.freeze({
  INVALID_CONFIG: 'INVALID_CONFIG', INVALID_WRAPPER: 'INVALID_WRAPPER',
  INVALID_STATE: 'INVALID_STATE', INITIALIZE_FAILED: 'INITIALIZE_FAILED',
  START_FAILED: 'START_FAILED', READINESS_FAILED: 'READINESS_FAILED',
  INVALID_EVENT: 'INVALID_EVENT'
});

/** Public failures discard native/caller text, causes, config and credentials. */
class NativeSessionError extends Error {
  constructor(code) {
    super(Object.hasOwn(CODES, code) ? code : CODES.INVALID_STATE);
    this.name = 'NativeSessionError';
    this.code = this.message;
  }
}

/** Throw only a fixed diagnostic code, never the offending value. */
function reject(code) { throw new NativeSessionError(code); }

/** Refuse accessors/proxies before reading a required own field. */
function data(object, key, code = CODES.INVALID_CONFIG) {
  if (!object || !['object', 'function'].includes(typeof object) || types.isProxy(object)) reject(code);
  const descriptor = Object.getOwnPropertyDescriptor(object, key);
  if (!descriptor || !Object.hasOwn(descriptor, 'value')) reject(code);
  return descriptor.value;
}

/** Bounded metadata validation is not a credential or authentication check. */
function text(value, max = 4096) {
  if (typeof value !== 'string' || !value.trim() || value.length > max || /[\x00-\x1f\x7f]/.test(value)) reject(CODES.INVALID_CONFIG);
  return value;
}

/** Copy plain JSON without invoking toJSON/getters; bounds prevent unbounded work. */
function cloneJSON(input) {
  let nodes = 0;
  let size = 0;
  const seen = new Set();
  /** Traverse own enumerable data descriptors, preserving unknown JSON fields. */
  function visit(value, depth) {
    if (++nodes > 20000 || depth > 32) reject(CODES.INVALID_CONFIG);
    if (typeof value === 'string') {
      size += value.length;
      if (size > 1048576) reject(CODES.INVALID_CONFIG);
      return value;
    }
    if (value === null || typeof value === 'boolean') return value;
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (!value || typeof value !== 'object' || types.isProxy(value) || seen.has(value)) reject(CODES.INVALID_CONFIG);
    const array = Array.isArray(value);
    const proto = Object.getPrototypeOf(value);
    if (!array && proto !== Object.prototype && proto !== null) reject(CODES.INVALID_CONFIG);
    const keys = Reflect.ownKeys(value);
    if (keys.length > 20000) reject(CODES.INVALID_CONFIG);
    const result = array ? [] : {};
    seen.add(value);
    for (const key of keys) {
      if (array && key === 'length') continue;
      if (typeof key !== 'string') reject(CODES.INVALID_CONFIG);
      size += key.length;
      if (size > 1048576 || (array && !/^(0|[1-9][0-9]*)$/.test(key))) reject(CODES.INVALID_CONFIG);
      const descriptor = Object.getOwnPropertyDescriptor(value, key);
      if (!descriptor.enumerable || !Object.hasOwn(descriptor, 'value')) reject(CODES.INVALID_CONFIG);
      Object.defineProperty(result, key, {value: visit(descriptor.value, depth + 1), enumerable: true, writable: true, configurable: true});
    }
    // Sparse arrays are not JSON records: JSON.stringify would silently add nulls.
    if (array && (value.length > 20000 || result.length !== value.length || keys.length !== value.length + 1)) reject(CODES.INVALID_CONFIG);
    seen.delete(value);
    return result;
  }
  return visit(input, 0);
}

/** Check only observed required fields; this is deliberately not a config builder. */
function validate(config, identity) {
  if (!config || typeof config !== 'object' || Array.isArray(config)) reject(CODES.INVALID_CONFIG);
  const uin = text(data(identity, 'uin'), 256);
  const uid = text(data(identity, 'uid'), 256);
  if (data(config, 'selfUin') !== uin || data(config, 'selfUid') !== uid) reject(CODES.INVALID_CONFIG);
  text(data(config, 'clientVer'));
  const machineId = data(config, 'machineId');
  if (typeof machineId !== 'string') reject(CODES.INVALID_CONFIG);
  const accountPath = text(data(data(config, 'desktopPathConfig'), 'account_path'));
  // Match either explicitly absolute host path style; never resolve relative paths.
  if (!(path.posix.isAbsolute(accountPath) || /^(?:[A-Za-z]:[\\/]|\\\\[^\\]+\\[^\\]+)/.test(accountPath))) reject(CODES.INVALID_CONFIG);
  if (Object.hasOwn(config, 'deviceInfo')) {
    const device = config.deviceInfo;
    if (!device || typeof device !== 'object' || Array.isArray(device)) reject(CODES.INVALID_CONFIG);
  }
}

/** Capture a method without invoking accessors; native prototypes are bounded. */
function method(instance, name) {
  let owner = instance;
  for (let depth = 0; owner && depth < 4; depth += 1) {
    if (!['object', 'function'].includes(typeof owner) || types.isProxy(owner)) reject(CODES.INVALID_WRAPPER);
    const descriptor = Object.getOwnPropertyDescriptor(owner, name);
    if (descriptor) {
      if (!Object.hasOwn(descriptor, 'value') || typeof descriptor.value !== 'function') reject(CODES.INVALID_WRAPPER);
      return (...args) => descriptor.value.apply(instance, args);
    }
    owner = Object.getPrototypeOf(owner);
  }
  reject(CODES.INVALID_WRAPPER);
}

/** Construct a dormant, single-attempt session; caller owns login and process exit. */
function createSessionController(options) {
  let config, wrapper, depends, dispatcher, mode, sessionName, startArgs;
  try {
    config = cloneJSON(data(options, 'sessionConfig'));
    validate(config, data(options, 'accountIdentity'));
    wrapper = data(options, 'wrapper');
    depends = data(options, 'dependsAdapter');
    dispatcher = data(options, 'dispatcherAdapter');
    for (const adapter of [depends, dispatcher]) {
      if (!adapter || !['object', 'function'].includes(typeof adapter)) reject(CODES.INVALID_CONFIG);
    }
    mode = data(options, 'lifecycleMode');
    if (!['startup', 'legacy'].includes(mode)) reject(CODES.INVALID_CONFIG);
    sessionName = text(data(options, 'sessionName'), 256);
    if (mode === 'legacy') {
      startArgs = cloneJSON(data(options, 'legacyStartArgs'));
      if (!Array.isArray(startArgs) || !(startArgs.length === 0 || (startArgs.length === 1 && startArgs[0] === 0))) reject(CODES.INVALID_CONFIG);
    }
  } catch (_) { reject(CODES.INVALID_CONFIG); }
  let phase = 'new';
  let nativeSession = null;
  let startNative = null;
  let cleanup = 'none';
  let failureCode = null;
  let startInFlight = false;
  let callbackReady = false;
  const subscribers = new Set();

  /** Deliver frozen fixed projections; consumer exceptions cannot escape native callbacks. */
  function publish(code, payload = Object.freeze({})) {
    const event = Object.freeze({code, phase, payload});
    const recipients = [...subscribers];
    queueMicrotask(() => {
      for (const callback of recipients) {
        if (!subscribers.has(callback)) continue;
        try { Promise.resolve(callback(event)).catch(() => {}); } catch (_) { /* No raw errors or recursive events. */ }
      }
    });
  }

  /** Terminal failure never initiates retries, cleanup guesses or service access. */
  function fail(code) {
    if (phase === 'disposed' || phase === 'failed') return;
    phase = 'failed'; failureCode = code; callbackReady = false;
    publish(code);
  }

  const listener = Object.freeze({
    /** Only an own exact boolean is consumed; every other raw field is discarded. */
    onOpentelemetryInit(raw) {
      if (phase === 'new' || phase === 'failed' || phase === 'disposed') return;
      let initialized;
      try {
        initialized = data(raw, 'is_init', CODES.INVALID_EVENT);
        if (typeof initialized !== 'boolean') reject(CODES.INVALID_EVENT);
      } catch (_) { fail(CODES.INVALID_EVENT); return; }
      if (!initialized) { fail(CODES.READINESS_FAILED); return; }
      // Pre-start success cannot authorize messaging; a post-start callback is required.
      if (phase !== 'starting') return;
      callbackReady = true;
      if (!startInFlight) { phase = 'ready'; publish('READY', Object.freeze({is_init: true})); }
    }
  });

  /** Safe status contains no identity, path, config, native return or credentials. */
  function getSnapshot() {
    return Object.freeze({phase, is_ready: phase === 'ready', failure_code: failureCode, native_cleanup_required: cleanup});
  }

  /** Obtain the explicitly chosen factories and initialize once, without starting. */
  function initialize() {
    if (phase !== 'new') reject(CODES.INVALID_STATE);
    try {
      const Session = data(wrapper, 'NodeIQQNTWrapperSession', CODES.INVALID_WRAPPER);
      let startup;
      // Resolve both startup factory methods before any potentially partial native work.
      const create = mode === 'startup'
        ? method(data(wrapper, 'NodeIQQNTStartupSessionWrapper', CODES.INVALID_WRAPPER), 'create')
        : method(Session, 'create');
      const get = mode === 'startup' ? method(Session, 'getNTWrapperSession') : null;
      cleanup = 'process_exit';
      if (mode === 'startup') {
        startup = create();
        nativeSession = get(sessionName);
        startNative = method(startup, 'start');
      } else {
        nativeSession = create();
        startNative = method(nativeSession, 'startNT');
      }
      const init = method(nativeSession, 'init');
      phase = 'initialized';
      const result = init(config, depends, dispatcher, listener);
      if (result !== undefined) reject(CODES.INITIALIZE_FAILED);
      if (phase === 'failed') reject(failureCode);
      publish('INITIALIZED');
      return getSnapshot();
    } catch (_) {
      fail(CODES.INITIALIZE_FAILED);
      reject(failureCode || CODES.INITIALIZE_FAILED);
    }
  }

  /** Start exactly once with the selected ABI; void return is not readiness proof. */
  function start() {
    if (phase !== 'initialized') reject(CODES.INVALID_STATE);
    phase = 'starting'; startInFlight = true; publish('STARTING');
    try {
      const result = mode === 'startup' ? startNative() : startNative(...startArgs);
      if (result !== undefined) reject(CODES.START_FAILED);
    } catch (_) { fail(CODES.START_FAILED); reject(failureCode || CODES.START_FAILED); }
    finally { startInFlight = false; }
    if (phase === 'starting' && callbackReady) { phase = 'ready'; publish('READY', Object.freeze({is_init: true})); }
    return getSnapshot();
  }

  /** Ready is callback evidence, not login success, init return or start return. */
  function isReady() { return phase === 'ready'; }

  /** Hand the exact session to the message adapter only after observed readiness. */
  function getSessionForMessaging() {
    if (!isReady()) reject(CODES.INVALID_STATE);
    return nativeSession;
  }

  /** Subscribe only to immutable safe events; never expose the native listener. */
  function subscribe(callback) {
    if (typeof callback !== 'function' || phase === 'disposed') reject(CODES.INVALID_STATE);
    subscribers.add(callback);
    return () => subscribers.delete(callback);
  }

  /** Logical disposal only: teardown ABI is unknown, so the owner must exit process. */
  function dispose() {
    if (phase === 'disposed') return getSnapshot();
    phase = 'disposed'; callbackReady = false; nativeSession = null; startNative = null;
    subscribers.clear();
    return getSnapshot();
  }
  return Object.freeze({initialize, start, isReady, getSessionForMessaging, getSnapshot, subscribe, dispose});
}

module.exports = {createSessionController, NativeSessionError, CODES};
