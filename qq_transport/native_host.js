'use strict';

// Preparation owns factory identity only. It never loads QQ, initializes services,
// connects, starts sessions, constructs native classes or guesses native cleanup.
const { types } = require('node:util');
const CODES = Object.freeze({
  INVALID_CONFIG: 'INVALID_CONFIG', INVALID_WRAPPER: 'INVALID_WRAPPER',
  INVALID_STATE: 'INVALID_STATE', PREPARE_FAILED: 'PREPARE_FAILED'
});

/** Fixed diagnostics never retain native exceptions, arguments or caller config. */
class NativeHostError extends Error {
  constructor(code) {
    super(Object.hasOwn(CODES, code) ? code : CODES.INVALID_STATE);
    this.name = 'NativeHostError';
    this.code = this.message;
  }
}

/** Fail with a fixed code rather than reflecting trusted-owner input or native text. */
function reject(code) { throw new NativeHostError(code); }

/** Inspect own data descriptors only; this is validation, not a security boundary. */
function data(object, key, code) {
  if (!object || !['object', 'function'].includes(typeof object) || types.isProxy(object)) reject(code);
  const descriptor = Object.getOwnPropertyDescriptor(object, key);
  if (!descriptor || !Object.hasOwn(descriptor, 'value')) reject(code);
  return descriptor.value;
}

/** Capture only an own static data method; inherited factories are not this contract. */
function factory(wrapper, className, methodName) {
  const owner = data(wrapper, className, CODES.INVALID_WRAPPER);
  const call = data(owner, methodName, CODES.INVALID_WRAPPER);
  if (typeof call !== 'function') reject(CODES.INVALID_WRAPPER);
  return (...args) => call.apply(owner, args);
}

/** Refuse empty factory returns without inspecting or serializing native handles. */
function handle(value) {
  if (!value || !['object', 'function'].includes(typeof value) || types.isProxy(value)) reject(CODES.PREPARE_FAILED);
  return value;
}

/** Create a dormant single-attempt prerequisite; only prepare() invokes factories. */
function createNativeHost(options) {
  let wrapper, sessionName, lifecycleMode;
  try {
    // Reject unrelated account/config fields instead of retaining a config bag.
    const keys = Reflect.ownKeys(options);
    if (keys.length !== 3 || keys.some(key => !['wrapper', 'sessionName', 'lifecycleMode'].includes(key))) reject(CODES.INVALID_CONFIG);
    wrapper = data(options, 'wrapper', CODES.INVALID_CONFIG);
    sessionName = data(options, 'sessionName', CODES.INVALID_CONFIG);
    lifecycleMode = data(options, 'lifecycleMode', CODES.INVALID_CONFIG);
    if (typeof sessionName !== 'string' || !sessionName.trim() || sessionName.length > 256 || /[\x00-\x1f\x7f]/.test(sessionName)) reject(CODES.INVALID_CONFIG);
    if (!['startup', 'legacy'].includes(lifecycleMode)) reject(CODES.INVALID_CONFIG);
  } catch (_) { reject(CODES.INVALID_CONFIG); }
  let phase = 'new';
  let failureCode = null;
  let cleanupRequired = false;
  let engine = null, login = null, startupSession = null, session = null;
  let loginWrapper = null;

  /** Status is the only telemetry surface; names, configs and handles are omitted. */
  function getSnapshot() {
    return Object.freeze({phase, engine_available: engine !== null, login_available: login !== null,
      startup_available: startupSession !== null, session_available: session !== null,
      failure_code: failureCode, process_cleanup_required: cleanupRequired,
      cleanup_required: cleanupRequired ? 'process_exit' : 'none'});
  }

  /** Prevent duplicate preparation, retry after uncertainty and stale facade use. */
  function requirePrepared() { if (phase !== 'prepared') reject(CODES.INVALID_STATE); }

  /** Return the original cached engine without a second native static get call. */
  function getEngine() { requirePrepared(); return engine; }

  /** Return the original cached login without a second native static get call. */
  function getLogin() { requirePrepared(); return login; }

  /** Disposal during a synchronous native callback irrevocably ends preparation. */
  function acquireFactory(call, ...args) {
    if (phase === 'disposed') reject(CODES.INVALID_STATE);
    const value = call(...args);
    // A native factory can synchronously call owner code which disposes this host.
    // Check before assigning its returned capability; disposal must not resurrect it.
    if (phase === 'disposed') reject(CODES.INVALID_STATE);
    return handle(value);
  }

  /** Validate every selected factory before entering the irreversible native order. */
  function prepare() {
    if (phase !== 'new') reject(CODES.INVALID_STATE);
    let getEngineNative, getLoginNative, createSession, getSession;
    try {
      getEngineNative = factory(wrapper, 'NodeIQQNTWrapperEngine', 'get');
      getLoginNative = factory(wrapper, 'NodeIKernelLoginService', 'get');
      if (lifecycleMode === 'startup') {
        createSession = factory(wrapper, 'NodeIQQNTStartupSessionWrapper', 'create');
        getSession = factory(wrapper, 'NodeIQQNTWrapperSession', 'getNTWrapperSession');
      } else {
        // Legacy is an explicit mode, never a fallback when startup fails.
        createSession = factory(wrapper, 'NodeIQQNTWrapperSession', 'create');
      }
    } catch (_) {
      phase = 'failed'; failureCode = CODES.INVALID_WRAPPER; reject(failureCode);
    }
    // Mark terminal before the first native call so reentrant prepare cannot retry.
    phase = 'failed'; failureCode = CODES.PREPARE_FAILED;
    cleanupRequired = true;
    try {
      engine = acquireFactory(getEngineNative);
      login = acquireFactory(getLoginNative);
      if (lifecycleMode === 'startup') {
        startupSession = acquireFactory(createSession);
        session = acquireFactory(getSession, sessionName);
      } else {
        session = acquireFactory(createSession);
      }
      // Plain own-get facade, no Proxy, subclass construction or global patching.
      loginWrapper = Object.freeze({
        NodeIQQNTWrapperEngine: Object.freeze({get: getEngine}),
        NodeIKernelLoginService: Object.freeze({get: getLogin})
      });
      phase = 'prepared'; failureCode = null;
      return getSnapshot();
    } catch (_) {
      // Disposal wins even if the native call throws after disposing the owner.
      // dispose() already cleared all earlier handles; do not change its phase.
      if (phase === 'disposed') reject(CODES.INVALID_STATE);
      // Native uncertainty is terminal. Never retry another factory or retain cause.
      reject(CODES.PREPARE_FAILED);
    }
  }

  /** Trusted-owner capability, not a telemetry object; callers must not log handles. */
  function getLoginWrapper() { requirePrepared(); return loginWrapper; }

  /** Explicit raw-handle read for prepared session injection by the trusted owner. */
  function getPreparedSessionHandles() {
    requirePrepared();
    return Object.freeze({startupSession, session, lifecycleMode, sessionName});
  }

  /** Logical release only; process exit remains required after any native attempt. */
  function dispose() {
    phase = 'disposed';
    wrapper = null; engine = null; login = null; startupSession = null; session = null;
    loginWrapper = null; sessionName = null;
    return getSnapshot();
  }

  return Object.freeze({prepare, getLoginWrapper, getPreparedSessionHandles, getSnapshot, dispose});
}

/** Explicit convenience invocation; unlike construction, this authorizes factories. */
function prepareNativeHost(options) {
  const host = createNativeHost(options);
  host.prepare();
  return host;
}

module.exports = {createNativeHost, prepareNativeHost, NativeHostError};
