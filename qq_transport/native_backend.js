'use strict';

// Composition only: importing these dormant controllers never loads native code.
const { createLoginController } = require('./native_login');
const { createSessionController } = require('./native_session');
const { createMessageController } = require('./native_messages');

/** Fixed errors never retain trusted factory/native exception text or secrets. */
class NativeBackendError extends Error {
  constructor(code) { super(code); this.name = 'NativeBackendError'; this.code = code; }
}

/** Build an explicit owner-driven lifecycle, not a configuration or routing builder. */
function createNativeBackend({ wrapper, engineConfig, loginConfig, globalAdapter,
  listenerLifetime, sessionConfigFactory, sessionOptionsFactory, dependsAdapter,
  dispatcherAdapter, sendTimeoutMs }) {
  if (!['process', 'token'].includes(listenerLifetime)
      || typeof sessionConfigFactory !== 'function' || typeof sessionOptionsFactory !== 'function') {
    throw new NativeBackendError('INVALID_CONFIG');
  }
  const login = createLoginController({ wrapper, engineConfig, loginConfig, globalAdapter, listenerLifetime });
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
  detach.push(login.subscribe(event => forward('login', event)));

  /** Only explicit initialize initializes engine/login; it cannot connect. */
  function initialize() { active(); return login.initialize(); }
  /** Native connected callback, not this acknowledgement, proves connection. */
  function connect() { active(); return login.connect(); }
  /** QR request does not fabricate callback correlation or authentication. */
  function requestQRCode() { active(); return login.getQRCodePicture(); }

  /** Await trusted complete config/options once, checking disposal after each await. */
  async function initializeSession() {
    active();
    if (!identity || !login.getSnapshot().authenticated || sessionAttempted) throw new NativeBackendError('INVALID_STATE');
    sessionAttempted = true;
    const authenticatedIdentity = identity;
    try {
      const sessionConfig = await sessionConfigFactory(authenticatedIdentity);
      active();
      if (identity !== authenticatedIdentity || !login.getSnapshot().authenticated) throw new NativeBackendError('INVALID_STATE');
      const options = await sessionOptionsFactory(authenticatedIdentity);
      active();
      if (identity !== authenticatedIdentity || !login.getSnapshot().authenticated) throw new NativeBackendError('INVALID_STATE');
      // Only lifecycle fields are accepted from options: factories cannot replace
      // the authenticated identity, wrapper, adapters or supplied secret config.
      sessionIdentity = authenticatedIdentity;
      session = createSessionController({ wrapper, sessionConfig, accountIdentity: authenticatedIdentity,
        dependsAdapter, dispatcherAdapter, lifecycleMode: options.lifecycleMode,
        sessionName: options.sessionName, legacyStartArgs: options.legacyStartArgs });
      detach.push(session.subscribe(event => forward('session', event)));
      active();
      return session.initialize();
    } catch (_) {
      sessionFailure = disposed ? 'DISPOSED' : 'SESSION_INITIALIZE_FAILED';
      throw new NativeBackendError(sessionFailure);
    }
  }

  /** Synchronous login state closes authorization before queued event forwarding. */
  function authenticatedSession() {
    return !disposed && identity !== null && identity === sessionIdentity
      && login.getSnapshot().authenticated;
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
    messages = createMessageController({ session: session.getSessionForMessaging(),
      accountIdentity: sessionIdentity, isReady: () => authenticatedSession() && session.isReady(),
      listenerLifetime, ...(sendTimeoutMs === undefined ? {} : { sendTimeoutMs }) });
    detach.push(messages.subscribe(event => forward('messages', event)));
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
    const loginStatus = login.getSnapshot();
    const sessionStatus = session ? session.getSnapshot() : null;
    const messageStatus = messages ? messages.getSnapshot() : null;
    return Object.freeze({ disposed, identity_available: identity !== null,
      // Composition evidence does not establish complete independent QQ capability.
      independent_backend_ready: false, send_path_ready: isReady(),
      session_attempted: sessionAttempted, messaging_attempted: messagingAttempted,
      session_failure_code: sessionFailure, login: loginStatus, session: sessionStatus, messages: messageStatus });
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
    for (const controller of [messages, session, login]) {
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
