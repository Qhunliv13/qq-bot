'use strict';

// Composition only: importing these dormant controllers never loads native code.
const { createLoginController } = require('./native_login');
const { createSessionController } = require('./native_session');
const { createMessageController } = require('./native_messages');
const { createNativeHost } = require('./native_host');
const { types } = require('node:util');

/** Fixed errors never retain trusted factory/native exception text or secrets. */
class NativeBackendError extends Error {
  constructor(code) { super(code); this.name = 'NativeBackendError'; this.code = code; }
}

/** Build an explicit owner-driven lifecycle, not a configuration or routing builder. */
function createNativeBackend({ wrapper, engineConfig, loginConfig, globalAdapter,
  listenerLifetime, sessionConfigFactory, sessionOptionsFactory, dependsAdapter,
  dispatcherAdapter, sendTimeoutMs, hostOptions }) {
  if (!['process', 'token'].includes(listenerLifetime)
      || typeof sessionConfigFactory !== 'function' || typeof sessionOptionsFactory !== 'function') {
    throw new NativeBackendError('INVALID_CONFIG');
  }
  let nativeHost = null;
  if (hostOptions !== undefined) {
    try {
      // Explicit own-data opt-in: reject accessors before extracting lifecycle fields.
      if (!hostOptions || typeof hostOptions !== 'object' || types.isProxy(hostOptions)) throw new NativeBackendError('INVALID_CONFIG');
      const keys = Reflect.ownKeys(hostOptions);
      if (keys.length !== 2 || keys.some(key => !['sessionName', 'lifecycleMode'].includes(key))) throw new NativeBackendError('INVALID_CONFIG');
      const descriptors = Object.getOwnPropertyDescriptors(hostOptions);
      if (!Object.hasOwn(descriptors.sessionName, 'value') || !Object.hasOwn(descriptors.lifecycleMode, 'value')) throw new NativeBackendError('INVALID_CONFIG');
      nativeHost = createNativeHost({wrapper, sessionName: descriptors.sessionName.value, lifecycleMode: descriptors.lifecycleMode.value});
    } catch (_) { throw new NativeBackendError('INVALID_CONFIG'); }
  }
  // Preserve generic-adapter construction behavior; the prepared facade is unavailable
  // until host.prepare(), so the opted-in login controller must be constructed later.
  let login = nativeHost ? null : createLoginController({ wrapper, engineConfig, loginConfig, globalAdapter, listenerLifetime });
  let initializeAttempted = false;
  let initializeFailure = null;
  let session = null;
  let messages = null;
  let identity = null;
  let sessionIdentity = null;
  let disposed = false;
  let sessionAttempted = false;
  let messagingAttempted = false;
  let sessionFailure = null;
  const subscribers = new Set();
  const qrSubscribers = new Set();
  const detach = [];

  /** No owner operation may enter once logical disposal has begun. */
  function active() { if (disposed) throw new NativeBackendError('DISPOSED'); }

  /** Construction may reenter owner code; never adopt a component born after dispose. */
  function retain(controller) {
    if (disposed) {
      // This dormant component was not visible to dispose's component sweep.
      controller.dispose();
      active();
    }
    return controller;
  }

  /** Require the same live native identity, including callbacks not yet forwarded. */
  function requireIdentity(expected) {
    active();
    if (identity !== expected || !login.getSnapshot().authenticated || login.getIdentity() !== expected) {
      throw new NativeBackendError('INVALID_STATE');
    }
  }

  /** Snapshot only lifecycle data; trusted factories must not smuggle executable getters. */
  function lifecycleOptions(options) {
    if (!options || typeof options !== 'object' || types.isProxy(options)) throw new NativeBackendError('INVALID_CONFIG');
    const result = {};
    for (const key of ['lifecycleMode', 'sessionName', 'legacyStartArgs']) {
      const descriptor = Object.getOwnPropertyDescriptor(options, key);
      if (!descriptor) continue; // The session controller validates required fields.
      if (!Object.hasOwn(descriptor, 'value')) throw new NativeBackendError('INVALID_CONFIG');
      result[key] = descriptor.value;
    }
    return result;
  }

  /** Frozen component projections only; consumer failures cannot leak or recurse. */
  function deliver(callbacks, envelope) {
    for (const callback of [...callbacks]) {
      if (disposed || !callbacks.has(callback)) continue;
      try { Promise.resolve(callback(envelope)).catch(() => {}); } catch (_) { /* Owner isolation. */ }
    }
  }

  /** Login QR/identity are separately scoped, not generic monitoring payloads. */
  function forward(source, event) {
    if (disposed) return;
    if (source === 'login') {
      if (event.type === 'authenticated') identity = login.getIdentity();
      if (['failed', 'disconnected'].includes(event.type)) identity = null;
      if (event.type === 'qr') deliver(qrSubscribers, event.payload);
      if (event.type === 'qr' || event.type === 'authenticated') {
        event = Object.freeze({ ...event, payload: Object.freeze({}) });
      }
    }
    deliver(subscribers, Object.freeze({ source, event }));
  }
  if (login) detach.push(login.subscribe(event => forward('login', event)));

  /** Prepare the explicit host before constructing/subscribing/initializing login. */
  function initialize() {
    active();
    if (initializeAttempted) throw new NativeBackendError('INVALID_STATE');
    initializeAttempted = true;
    try {
      if (nativeHost) {
        nativeHost.prepare();
        active();
        login = retain(createLoginController({wrapper: nativeHost.getLoginWrapper(), engineConfig, loginConfig, globalAdapter, listenerLifetime}));
        detach.push(login.subscribe(event => forward('login', event)));
      }
      active();
      return login.initialize();
    } catch (_) {
      initializeFailure = 'INITIALIZE_FAILED';
      throw new NativeBackendError(initializeFailure);
    }
  }
  /** Native connected callback, not this acknowledgement, proves connection. */
  function connect() { active(); if (!login || initializeFailure) throw new NativeBackendError('INVALID_STATE'); return login.connect(); }
  /** QR request does not fabricate callback correlation or authentication. */
  function requestQRCode() { active(); if (!login || initializeFailure) throw new NativeBackendError('INVALID_STATE'); return login.getQRCodePicture(); }

  /** Await trusted complete config/options once, checking disposal after each await. */
  async function initializeSession() {
    active();
    if (!identity || !login.getSnapshot().authenticated || sessionAttempted) throw new NativeBackendError('INVALID_STATE');
    sessionAttempted = true;
    const authenticatedIdentity = identity;
    try {
      const sessionConfig = await sessionConfigFactory(authenticatedIdentity);
      requireIdentity(authenticatedIdentity);
      const suppliedOptions = await sessionOptionsFactory(authenticatedIdentity);
      requireIdentity(authenticatedIdentity);
      const options = lifecycleOptions(suppliedOptions);
      // Only lifecycle fields are accepted from options: factories cannot replace
      // the authenticated identity, wrapper, adapters or supplied secret config.
      const preparedHandles = nativeHost ? nativeHost.getPreparedSessionHandles() : null;
      // Never substitute a differently named/mode session after trusted async work.
      if (preparedHandles && (options.lifecycleMode !== preparedHandles.lifecycleMode || options.sessionName !== preparedHandles.sessionName)) throw new NativeBackendError('INVALID_CONFIG');
      requireIdentity(authenticatedIdentity);
      session = retain(createSessionController({ wrapper, sessionConfig, accountIdentity: authenticatedIdentity,
        dependsAdapter, dispatcherAdapter, lifecycleMode: options.lifecycleMode,
        sessionName: options.sessionName, legacyStartArgs: options.legacyStartArgs,
        ...(preparedHandles ? {preparedHandles} : {}) }));
      sessionIdentity = authenticatedIdentity;
      detach.push(session.subscribe(event => forward('session', event)));
      requireIdentity(authenticatedIdentity);
      return session.initialize();
    } catch (_) {
      sessionFailure = disposed ? 'DISPOSED' : 'SESSION_INITIALIZE_FAILED';
      throw new NativeBackendError(sessionFailure);
    }
  }

  /** Synchronous login state closes authorization before queued event forwarding. */
  function authenticatedSession() {
    return !disposed && identity !== null && identity === sessionIdentity
      // Bind every operation to the live identity, not only queued composer state.
      && login !== null && login.getSnapshot().authenticated && login.getIdentity() === sessionIdentity;
  }

  /** Starting is a separate owner action; initialization cannot authorize it. */
  function startSession() {
    active();
    if (!session || !authenticatedSession()) throw new NativeBackendError('INVALID_STATE');
    return session.start();
  }

  /** Native readiness callback is necessary, but never auto-initializes messaging. */
  function initializeMessaging() {
    active();
    if (!session || !session.isReady() || messagingAttempted || !authenticatedSession()) throw new NativeBackendError('NOT_READY');
    messagingAttempted = true;
    messages = retain(createMessageController({ session: session.getSessionForMessaging(),
      accountIdentity: sessionIdentity, isReady: () => authenticatedSession() && session.isReady(),
      listenerLifetime, ...(sendTimeoutMs === undefined ? {} : { sendTimeoutMs }) }));
    detach.push(messages.subscribe(event => forward('messages', event)));
    active();
    return messages.initialize();
  }

  /** Ready describes the limited send path only, never full native capability parity. */
  function isReady() {
    return authenticatedSession() && session !== null && session.isReady()
      && messages !== null && messages.getSnapshot().phase === 'active';
  }

  /** Delegate once; accepted is not delivered and uncertain outcomes are not retried. */
  async function send(request) {
    active();
    if (!isReady()) throw new NativeBackendError('NOT_READY');
    return messages.send(request);
  }

  /** Subscribe locally; no subscription initiates native work. */
  function subscribe(callback) {
    active();
    if (typeof callback !== 'function') throw new NativeBackendError('INVALID_INPUT');
    subscribers.add(callback);
    return () => subscribers.delete(callback);
  }

  /** Only this explicit sensitive subscription can receive QR image/URL payloads. */
  function subscribeQRCode(callback) {
    active();
    if (typeof callback !== 'function') throw new NativeBackendError('INVALID_INPUT');
    qrSubscribers.add(callback);
    return () => qrSubscribers.delete(callback);
  }

  /** Component snapshots are already safe; identity is represented only as a boolean. */
  function getSnapshot() {
    const loginStatus = login ? login.getSnapshot() : null;
    const hostStatus = nativeHost ? nativeHost.getSnapshot() : null;
    const sessionStatus = session ? session.getSnapshot() : null;
    const messageStatus = messages ? messages.getSnapshot() : null;
    return Object.freeze({ disposed, identity_available: identity !== null,
      // Composition evidence does not establish complete independent QQ capability.
      independent_backend_ready: false, send_path_ready: isReady(),
      session_attempted: sessionAttempted, messaging_attempted: messagingAttempted,
      session_failure_code: sessionFailure, initialize_failure_code: initializeFailure,
      host: hostStatus, login: loginStatus, session: sessionStatus, messages: messageStatus });
  }

  /** Detach first, then attempt every component cleanup despite any earlier failure. */
  function dispose() {
    if (disposed) return getSnapshot();
    disposed = true;
    identity = null;
    subscribers.clear(); qrSubscribers.clear();
    let failed = false;
    for (const unsubscribe of detach.splice(0)) {
      try { unsubscribe(); } catch (_) { failed = true; }
    }
    for (const controller of [messages, session, login, nativeHost]) {
      if (!controller) continue;
      try { controller.dispose(); } catch (_) { failed = true; }
    }
    if (failed) throw new NativeBackendError('DISPOSE_FAILED');
    return getSnapshot();
  }
  return Object.freeze({ initialize, connect, requestQRCode, initializeSession, startSession,
    initializeMessaging, send, isReady, subscribe, subscribeQRCode, getSnapshot, dispose });
}

module.exports = Object.freeze({ createNativeBackend, NativeBackendError });
