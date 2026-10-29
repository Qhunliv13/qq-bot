'use strict';

// Independently authored adapter: no native loading, login, networking or persistence.
// Limits are adapter safety policy, not claims about QQ's native maxima.
const LIMITS = Object.freeze({ batch: 100, elements: 100, text: 16384, messageText: 65536, id: 256, pending: 100, subscribers: 100 });
const TEXT_FIELDS = Object.freeze(['content', 'atType', 'atUid', 'atTinyId', 'atNtUid']);

/** Public failures discard native text, stack causes and caller-controlled details. */
class NativeMessageError extends Error {
  constructor(code, nativeMsgId = null) {
    super(code);
    this.name = 'NativeMessageError';
    this.code = code;
    if (code === 'SEND_UNCERTAIN') {
      this.state = 'uncertain';
      this.native_msg_id = nativeMsgId;
    }
  }
}

/** Throw only fixed adapter codes; never forward native errors. */
function fail(code) { throw new NativeMessageError(code); }

/** Read a data descriptor without executing accessors, including native prototypes. */
function read(object, key, required = true) {
  if (!object || (typeof object !== 'object' && typeof object !== 'function')) fail('INVALID_INPUT');
  const descriptor = Object.getOwnPropertyDescriptor(object, key);
  if (!descriptor) { if (required) fail('INVALID_INPUT'); return undefined; }
  if (!Object.hasOwn(descriptor, 'value')) fail('INVALID_INPUT');
  return descriptor.value;
}

/** Resolve bounded prototype methods without accidentally invoking property getters. */
function method(object, name) {
  let owner = object;
  for (let depth = 0; owner !== null && depth < 4; depth += 1) {
    if (!owner || (typeof owner !== 'object' && typeof owner !== 'function')) break;
    const descriptor = Object.getOwnPropertyDescriptor(owner, name);
    if (descriptor) {
      if (!Object.hasOwn(descriptor, 'value') || typeof descriptor.value !== 'function') fail('INVALID_SERVICE');
      return descriptor.value.bind(object);
    }
    owner = Object.getPrototypeOf(owner);
  }
  fail('INVALID_SERVICE');
}

/** Reject ambiguous extra outgoing fields instead of claiming unsupported features. */
function shape(object, allowed, required = allowed) {
  if (!object || typeof object !== 'object' || Array.isArray(object)) fail('INVALID_INPUT');
  if (Reflect.ownKeys(object).some(key => !allowed.includes(key))) fail('INVALID_INPUT');
  for (const key of required) read(object, key);
}

/** Keep identifiers as exact bounded strings; no UIN/UID or numeric conversion. */
function string(value, max = LIMITS.id, empty = false) {
  if (typeof value !== 'string' || value.length > max || (!empty && !value.length)) fail('INVALID_INPUT');
  return value;
}

/** Copy a bounded dense array by descriptor, never running indexed getters. */
function array(value, max, minimum = 0) {
  if (!Array.isArray(value)) fail('INVALID_INPUT');
  const length = read(value, 'length');
  if (length > max || length < minimum) fail('INVALID_INPUT');
  const result = [];
  for (let index = 0; index < length; index += 1) result.push(read(value, String(index)));
  return result;
}

/** Preserve the five observed text fields; empty mention IDs must be explicit. */
function textElement(raw, strict) {
  if (strict) shape(raw, TEXT_FIELDS);
  const projected = {};
  for (const key of TEXT_FIELDS) {
    const value = read(raw, key);
    if (key === 'atType') {
      if (!Number.isSafeInteger(value) || value < 0) fail('INVALID_INPUT');
      projected[key] = value;
    } else projected[key] = string(value, key === 'content' ? LIMITS.text : LIMITS.id, true);
  }
  return Object.freeze(projected);
}

/** Project text only; unknown native elements expose type/ID, not private blobs. */
function elements(raw, outgoing) {
  let total = 0;
  const projected = array(raw, LIMITS.elements, outgoing ? 1 : 0).map(element => {
    if (outgoing) shape(element, ['elementType', 'textElement', 'elementId'], ['elementType', 'textElement']);
    const elementType = read(element, 'elementType');
    if (!Number.isSafeInteger(elementType) || elementType < 0 || (outgoing && elementType !== 1)) fail('INVALID_INPUT');
    const result = { elementType };
    const elementId = read(element, 'elementId', false);
    if (elementId !== undefined) result.elementId = string(elementId, LIMITS.id, true);
    if (elementType === 1) {
      result.textElement = textElement(read(element, 'textElement'), outgoing);
      total += result.textElement.content.length;
      if (total > LIMITS.messageText) fail('INVALID_INPUT');
    }
    return Object.freeze(result);
  });
  return Object.freeze(projected);
}

/** Snapshot only named message fields; unknown raw data is intentionally not cloned. */
function projectMessage(raw, account) {
  const msgId = string(read(raw, 'msgId'));
  const chatType = read(raw, 'chatType');
  if (!Number.isSafeInteger(chatType) || chatType < 0) fail('INVALID_INPUT');
  const peerUid = string(read(raw, 'peerUid'));
  const result = { account, msgId, chatType, peerUid };
  for (const key of ['senderUid', 'senderUin', 'peerUin', 'msgTime', 'msgSeq', 'msgRandom']) {
    const value = read(raw, key, false);
    if (value !== undefined) result[key] = string(value, LIMITS.id, true);
  }
  result.elements = elements(read(raw, 'elements'), false);
  // Tuple identity avoids delimiter collisions, but does not assert global uniqueness.
  result.key = Object.freeze([account.uin, account.uid, chatType, peerUid, msgId]);
  return Object.freeze(result);
}

/** Validate and snapshot the exact supported send schema before any allocation. */
function outgoing(raw) {
  shape(raw, ['peer', 'elements', 'timestamp']);
  const peer = read(raw, 'peer');
  shape(peer, ['chatType', 'peerUid', 'guildId'], ['chatType', 'peerUid']);
  const chatType = read(peer, 'chatType');
  if (chatType !== 1 && chatType !== 2) fail('INVALID_INPUT');
  const projectedPeer = { chatType, peerUid: string(read(peer, 'peerUid')) };
  const guildId = read(peer, 'guildId', false);
  if (guildId !== undefined) projectedPeer.guildId = string(guildId);
  return { peer: Object.freeze(projectedPeer), elements: elements(read(raw, 'elements'), true), timestamp: string(read(raw, 'timestamp')) };
}

/** Create an inert controller; only initialize explicitly touches the native session. */
function createMessageController({ session, accountIdentity, isReady, listenerLifetime = 'token', removalIdEncoder, sendTimeoutMs = 30000 }) {
  if (typeof isReady !== 'function' || !['token', 'process'].includes(listenerLifetime)
      || (removalIdEncoder !== undefined && typeof removalIdEncoder !== 'function')
      || !Number.isSafeInteger(sendTimeoutMs) || sendTimeoutMs < 1 || sendTimeoutMs > 300000) fail('INVALID_CONFIG');
  const account = Object.freeze({ uin: string(read(accountIdentity, 'uin')), uid: string(read(accountIdentity, 'uid')) });
  const subscribers = new Set();
  let phase = 'new';
  let native = null;
  let handle;
  let cleanupRequired = 'none';
  let pending = 0;
  let droppedBatches = 0;

  /** Authorization is exclusively the supplied session lifecycle, never login state. */
  function ready() {
    try { return isReady() === true; } catch (_) { return false; }
  }

  /** Bound asynchronous fanout and sanitize both synchronous and rejected callbacks. */
  function publish(type, payload, excluded = null) {
    if (phase !== 'active') return;
    if (pending >= LIMITS.pending) { droppedBatches += 1; return; }
    pending += 1;
    const event = Object.freeze({ type, payload });
    const recipients = [...subscribers];
    queueMicrotask(() => {
      pending -= 1;
      if (phase !== 'active') return;
      for (const callback of recipients) {
        if (!subscribers.has(callback) || callback === excluded) continue;
        /** Error reports cannot recursively produce more error reports. */
        const report = () => {
          if (type !== 'listener_error') publish('listener_error', Object.freeze({ code: 'LISTENER_FAILED' }), callback);
        };
        try { Promise.resolve(callback(event)).catch(report); } catch (_) { report(); }
      }
    });
  }

  /** One callback becomes one immutable array event; invalid/oversize batches fail closed. */
  function receive(type, rawList) {
    if (phase !== 'active' || !ready()) return;
    try {
      const batch = Object.freeze(array(rawList, LIMITS.batch).map(raw => projectMessage(raw, account)));
      publish(type, batch);
    } catch (_) { publish('invalid_event', Object.freeze({ code: 'INVALID_EVENT' })); }
  }

  const listener = Object.freeze({
    /** Exact observed receive callback, not one invocation per message. */
    onRecvMsg(rawList) { receive('recv', rawList); },
    /** Native update batches share the same projection and preserve ordering. */
    onMsgInfoListUpdate(rawList) { receive('update', rawList); }
  });

  /** Register once after strict readiness; failed registrations are never auto-retried. */
  function initialize() {
    if (phase !== 'new') fail('INVALID_STATE');
    if (!ready()) fail('NOT_READY');
    phase = 'initializing';
    try {
      const service = method(session, 'getMsgService')();
      native = Object.fromEntries(['generateMsgUniqueId', 'addKernelMsgListener', 'removeKernelMsgListener', 'sendMsg'].map(name => [name, method(service, name)]));
      // The service lookup could synchronously change lifecycle authorization.
      if (!ready()) fail('NOT_READY');
      cleanupRequired = 'process_exit';
      handle = native.addKernelMsgListener(listener);
      if (listenerLifetime === 'token') {
        if (!((typeof handle === 'number' && Number.isSafeInteger(handle))
          || (typeof handle === 'string' && /^(0|[1-9][0-9]{0,255})$/.test(handle)))) fail('INVALID_LISTENER_HANDLE');
        cleanupRequired = 'token';
      }
      phase = 'active';
    } catch (error) {
      phase = 'failed';
      if (error instanceof NativeMessageError) throw error;
      fail('INITIALIZE_FAILED');
    }
    return getSnapshot();
  }

  /** Subscribe locally without causing native work; unsubscribe never guesses a token. */
  function subscribe(callback) {
    if (typeof callback !== 'function' || phase === 'disposed' || subscribers.size >= LIMITS.subscribers) fail('INVALID_INPUT');
    subscribers.add(callback);
    return () => subscribers.delete(callback);
  }

  /** Allocate exactly once, dispatch once, and report only strict native acknowledgements. */
  async function send(request) {
    if (phase !== 'active') fail('INVALID_STATE');
    const validated = outgoing(request);
    if (!ready()) fail('NOT_READY');
    let msgId = null;
    let timer;
    try {
      msgId = string(native.generateMsgUniqueId(validated.peer.chatType, validated.timestamp));
      // Native allocation may synchronously invalidate the supplied lifecycle.
      if (!ready()) fail('NOT_READY');
      const resultPromise = native.sendMsg(msgId, validated.peer, validated.elements, new Map());
      // The pinned ABI is a promise, not a synchronous object or truthy success flag.
      if (!(resultPromise instanceof Promise)) throw new NativeMessageError('INVALID_ACK');
      const response = await Promise.race([resultPromise, new Promise((resolve, reject) => {
        timer = setTimeout(() => reject(new NativeMessageError('TIMEOUT')), sendTimeoutMs);
      })]);
      const result = read(response, 'result');
      const errMsg = read(response, 'errMsg');
      if ((result !== 0 && result !== -1) || typeof errMsg !== 'string' || errMsg.length > LIMITS.text) throw new NativeMessageError('INVALID_ACK');
      return Object.freeze({ state: result === 0 ? 'accepted' : 'rejected', native_msg_id: msgId, result });
    } catch (error) {
      if (error instanceof NativeMessageError && error.code === 'NOT_READY') throw error;
      // A native exception, invalid response or timeout never licenses an automatic retry.
      throw new NativeMessageError('SEND_UNCERTAIN', typeof msgId === 'string' ? msgId : null);
    } finally { if (timer !== undefined) clearTimeout(timer); }
  }

  /** Dispose locally first; native removal is exact-type unless explicitly encoded. */
  function dispose() {
    if (phase === 'disposed') return getSnapshot();
    phase = 'disposed';
    subscribers.clear();
    if (cleanupRequired === 'token') {
      try {
        const removalId = removalIdEncoder === undefined ? handle : removalIdEncoder(handle);
        if (!((typeof removalId === 'number' && Number.isSafeInteger(removalId))
          || (typeof removalId === 'string' && /^(0|[1-9][0-9]{0,255})$/.test(removalId)))) fail('INVALID_LISTENER_HANDLE');
        native.removeKernelMsgListener(removalId);
        cleanupRequired = 'none';
      } catch (_) { cleanupRequired = 'process_exit'; fail('REMOVE_LISTENER_FAILED'); }
    }
    return getSnapshot();
  }

  /** Diagnostics contain fixed state/counts only, not message content or account IDs. */
  function getSnapshot() {
    return Object.freeze({ phase, listener_lifetime: listenerLifetime, cleanup_required: cleanupRequired, dropped_batches: droppedBatches });
  }

  return Object.freeze({ initialize, subscribe, send, dispose, getSnapshot });
}

module.exports = { createMessageController, NativeMessageError, LIMITS };
