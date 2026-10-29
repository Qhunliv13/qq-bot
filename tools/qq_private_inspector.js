'use strict';

/** Fixed, account-free inspector experiment. Never accepts a target or expression. */
const http = require('http');
const fs = require('fs');
const { URL } = require('url');
const { performance } = require('perf_hooks');

// These are hard limits, not options: the parent must additionally enforce RLIMIT/watchdogs.
const LIMITS = Object.freeze({ pollMs: 5000, socketMs: 5000, httpBytes: 65536,
  messageBytes: 65536, totalMessageBytes: 131072, messages: 64, outputBytes: 16384,
  exports: 64, attempts: 25 });
const MARKER = 'QQ_NATIVE_INSPECTOR:';
const VERSION_KEYS = Object.freeze(['node', 'electron', 'modules', 'napi', 'chrome', 'v8', 'uv']);
// Presence is only an observation; these names do not establish an API/session contract.
const CANDIDATES = Object.freeze(['NodeIQQNTWrapperEngine', 'NodeIKernelLoginService',
  'NodeIKernelWrapperSession', 'NodeIQQNTWrapperSession', 'NodeIQQNTStartupSessionWrapper']);
const ERRORS = new Set(['environment_refused', 'discovery_timeout', 'discovery_limit',
  'discovery_invalid', 'socket_unavailable', 'socket_timeout', 'socket_closed',
  'socket_error', 'message_limit', 'protocol_invalid', 'evaluation_failed',
  'runtime_mismatch', 'binding_load_failed', 'binding_invalid', 'internal_error', 'output_limit']);

// Both expressions are closed constants. No endpoint data, CLI text or environment enters them.
const RUNTIME_EXPRESSION = `(() => { 'use strict';
  const versions = {};
  for (const key of ['node','electron','modules','napi','chrome','v8','uv']) {
    const d = Object.getOwnPropertyDescriptor(process.versions, key);
    if (d && Object.prototype.hasOwnProperty.call(d, 'value') && typeof d.value === 'string' &&
        /^[0-9A-Za-z.+_-]{1,64}$/.test(d.value)) versions[key] = d.value;
  }
  return { versions, exec_path_matches: process.execPath === '/opt/QQ/qq' };
})()`;
const BINDING_EXPRESSION = `(() => { 'use strict';
  if (process.execPath !== '/opt/QQ/qq') return { loaded: false };
  const native={exports:{}};
  try { process.dlopen(native,'/opt/QQ/resources/app/wrapper.node'); }
  catch (_) { return { loaded: false }; }
  const target = native.exports;
  if (target === null || (typeof target !== 'object' && typeof target !== 'function'))
    return { loaded: true, invalid: true };
  const names = Object.getOwnPropertyNames(target);
  const exports = [];
  let truncated = false;
  for (const name of names) {
    if (!/^[A-Za-z_$][A-Za-z0-9_$]{0,95}$/.test(name) || exports.length >= 64) {
      truncated = true; continue;
    }
    const descriptor = Object.getOwnPropertyDescriptor(target, name);
    const data = Object.prototype.hasOwnProperty.call(descriptor, 'value');
    exports.push({ name, type: data ? typeof descriptor.value : 'accessor',
      kind: data ? 'data' : 'accessor' });
  }
  const candidates = {};
  for (const name of ['NodeIQQNTWrapperEngine','NodeIKernelLoginService',
      'NodeIKernelWrapperSession','NodeIQQNTWrapperSession','NodeIQQNTStartupSessionWrapper']) {
    const d = Object.getOwnPropertyDescriptor(target, name);
    candidates[name] = !!(d && Object.prototype.hasOwnProperty.call(d, 'value') &&
      typeof d.value === 'function');
  }
  return { loaded: true, exports, truncated, candidates };
})()`;

/** Create a data-only failure without propagating private exception messages. */
function failure(category) {
  const error = new Error('inspector_failure');
  error.category = ERRORS.has(category) ? category : 'internal_error';
  return error;
}

/** Start pessimistic: only the two directly observed gates may ever become true. */
function baseReport() {
  return { schema_version: 1, ok: false, error: null,
    runtime: { versions: {}, exec_path_matches: false },
    binding: { load_attempted: false, loaded: false, exports: [], truncated: false,
      candidates: Object.fromEntries(CANDIDATES.map(name => [name, false])) },
    readiness: { runtime_host_verified: false, native_binding_verified: false,
      session_initialized: false, safe_to_login: false, independent_backend_ready: false,
      production_cutover_ready: false } };
}

/** Check local prerequisites, not full isolation; the trusted outer supervisor owns that proof. */
function verifyEnvironment(proc = process, files = fs) {
  if (proc.platform !== 'linux' || proc.execPath !== '/usr/bin/node' ||
      !/^18\./.test(proc.versions.node) || proc.argv.length !== 2 ||
      proc.env.QQ_ISOLATED_NATIVE_INSPECTOR !== '1' ||
      !/^net:\[[0-9]{1,20}\]$/.test(proc.env.QQ_INSPECTOR_PARENT_NETNS || '') ||
      ['NODE_OPTIONS', 'NODE_PATH', 'LD_PRELOAD', 'LD_LIBRARY_PATH'].some(k => proc.env[k]))
    throw failure('environment_refused');
  try {
    const status = files.readFileSync('/proc/self/status', 'utf8');
    if (typeof status !== 'string' || status.length > 16384 ||
        !/^Uid:\s+65534\s+65534\s+65534\s+65534\s*$/m.test(status) ||
        !/^Gid:\s+65534\s+65534\s+65534\s+65534\s*$/m.test(status) ||
        !/^NoNewPrivs:\s+1\s*$/m.test(status) ||
        !/^CapEff:\s+0{1,16}\s*$/m.test(status)) throw failure('environment_refused');
    const ownNet = files.readlinkSync('/proc/self/ns/net');
    if (!/^net:\[[0-9]{1,20}\]$/.test(ownNet) || ownNet === proc.env.QQ_INSPECTOR_PARENT_NETNS)
      throw failure('environment_refused');
  } catch (_) { throw failure('environment_refused'); }
  return true;
}

/** Accept only the literal inspector authority and a single opaque identifier segment. */
function validateEndpoint(value) {
  if (typeof value !== 'string' ||
      !/^ws:\/\/127\.0\.0\.1:9229\/[A-Za-z0-9-]{1,128}$/.test(value))
    throw failure('discovery_invalid');
  const url = new URL(value);
  if (url.href !== value || url.protocol !== 'ws:' || url.hostname !== '127.0.0.1' ||
      url.port !== '9229' || url.username || url.password || url.search || url.hash)
    throw failure('discovery_invalid');
  return url.href;
}

/** Reject ambiguous/malformed discovery instead of falling back to another target. */
function parseDiscovery(body) {
  let value;
  try { value = JSON.parse(body); } catch (_) { throw failure('discovery_invalid'); }
  if (!Array.isArray(value) || value.length !== 1 || !value[0] ||
      typeof value[0] !== 'object' || typeof value[0].id !== 'string')
    throw failure('discovery_invalid');
  const endpoint = validateEndpoint(value[0].webSocketDebuggerUrl);
  if (new URL(endpoint).pathname !== '/' + value[0].id) throw failure('discovery_invalid');
  return endpoint;
}

/** GET only the fixed loopback discovery path; count bytes across retries in one budget. */
function discoveryRequest(httpClient, remainingMs, budget) {
  return new Promise((resolve, reject) => {
    let request;
    let response;
    let done = false;
    const timer = setTimeout(() => finish(null), remainingMs);
    /** Settle once and tear down sockets without exposing their raw errors. */
    function finish(value, error) {
      if (done) return;
      done = true;
      clearTimeout(timer);
      if (response) response.destroy();
      if (request) request.destroy();
      if (error) reject(error); else resolve(value);
    }
    try {
      request = httpClient.get({ hostname: '127.0.0.1', port: 9229, path: '/json/list',
        method: 'GET', agent: false, maxHeaderSize: 8192,
        headers: { Host: '127.0.0.1:9229', Accept: 'application/json', Connection: 'close' } }, res => {
        response = res;
        const chunks = [];
        res.on('data', chunk => {
          if (done) return;
          budget.bytes += chunk.length;
          if (budget.bytes > LIMITS.httpBytes) return finish(null, failure('discovery_limit'));
          chunks.push(chunk);
        });
        res.on('error', () => finish(null));
        res.on('aborted', () => finish(null));
        res.on('end', () => {
          if (res.statusCode !== 200) return finish(null);
          try { finish(parseDiscovery(Buffer.concat(chunks).toString('utf8'))); }
          catch (error) { finish(null, error); }
        });
      });
      request.on('error', () => finish(null));
    } catch (_) { finish(null); }
  });
}

/** Retry only unready HTTP, within a shared five-second and finite-attempt budget. */
async function discover(httpClient = http) {
  // Wall-clock adjustments cannot extend the monotonic discovery deadline.
  const deadline = performance.now() + LIMITS.pollMs;
  const budget = { bytes: 0 };
  for (let attempt = 0; attempt < LIMITS.attempts && performance.now() < deadline; attempt += 1) {
    const endpoint = await discoveryRequest(httpClient, deadline - performance.now(), budget);
    if (endpoint) return endpoint;
    // A discovery server commonly appears after QQ starts; never spin on connection refusal.
    const delay = Math.min(200, deadline - performance.now());
    if (delay > 0) await new Promise(resolve => setTimeout(resolve, delay));
  }
  throw failure('discovery_timeout');
}

/** Rebuild the narrow runtime schema and require genuine QQ/Electron host metadata. */
function sanitizeRuntime(value) {
  if (!value || typeof value !== 'object' || value.exec_path_matches !== true ||
      !value.versions || typeof value.versions !== 'object') throw failure('runtime_mismatch');
  const versions = {};
  for (const key of VERSION_KEYS) {
    const item = value.versions[key];
    if (item !== undefined) {
      if (typeof item !== 'string' || item.length > 64 ||
          !/^[0-9]+(?:\.[0-9]+){0,3}(?:[-+][0-9A-Za-z.-]+)?$/.test(item) ||
          (['modules', 'napi'].includes(key) && !/^[0-9]{1,6}$/.test(item)))
        throw failure('runtime_mismatch');
      versions[key] = item;
    }
  }
  if (!['node', 'electron', 'modules', 'napi'].every(key => versions[key]))
    throw failure('runtime_mismatch');
  return { versions, exec_path_matches: true };
}

/** Copy only bounded export metadata; never return values, native errors or private text. */
function sanitizeBinding(value) {
  if (!value || value.loaded !== true) throw failure('binding_load_failed');
  if (!Array.isArray(value.exports) || value.exports.length > LIMITS.exports ||
      typeof value.truncated !== 'boolean' || !value.candidates ||
      typeof value.candidates !== 'object') throw failure('binding_invalid');
  const types = new Set(['undefined', 'object', 'boolean', 'number', 'bigint', 'string',
    'symbol', 'function', 'accessor']);
  const names = new Set();
  const exports = value.exports.map(item => {
    if (!item || typeof item.name !== 'string' ||
        !/^[A-Za-z_$][A-Za-z0-9_$]{0,95}$/.test(item.name) || names.has(item.name) ||
        !types.has(item.type) || !['data', 'accessor'].includes(item.kind) ||
        ((item.kind === 'accessor') !== (item.type === 'accessor')))
      throw failure('binding_invalid');
    names.add(item.name);
    return { name: item.name, type: item.type, kind: item.kind };
  });
  const candidates = {};
  for (const name of CANDIDATES) {
    if (typeof value.candidates[name] !== 'boolean') throw failure('binding_invalid');
    candidates[name] = value.candidates[name];
    const exported = exports.find(item => item.name === name);
    if ((exported && candidates[name] !== (exported.kind === 'data' && exported.type === 'function')) ||
        (!exported && candidates[name] && !value.truncated))
      throw failure('binding_invalid');
  }
  return { load_attempted: true, loaded: true, exports, truncated: value.truncated, candidates };
}

/** Exchange at most two fixed evaluations with a bounded inspector connection. */
function inspect(endpoint, WebSocketClass, report) {
  validateEndpoint(endpoint);
  return new Promise((resolve, reject) => {
    let socket;
    let finished = false;
    let expected = 1;
    let count = 0;
    let bytes = 0;
    const timer = setTimeout(() => finish(failure('socket_timeout')), LIMITS.socketMs);
    /** Cleanup is best effort; the supervisor must kill the process on its outer deadline. */
    function finish(error) {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      if (socket) {
        try { socket.close(); } catch (_) { /* No raw close errors leave this experiment. */ }
      }
      if (error) reject(error); else resolve(report);
    }
    /** The only protocol command possible; its expression is selected from fixed constants. */
    function evaluate(id) {
      socket.send(JSON.stringify({ id, method: 'Runtime.evaluate', params: {
        expression: id === 1 ? RUNTIME_EXPRESSION : BINDING_EXPRESSION,
        returnByValue: true, silent: true, includeCommandLineAPI: false,
        awaitPromise: false, generatePreview: false, userGesture: false, timeout: 1000 } }));
    }
    try {
      socket = new WebSocketClass(endpoint);
      socket.addEventListener('open', () => {
        if (!finished) {
          try { evaluate(1); } catch (_) { finish(failure('socket_error')); }
        }
      });
      socket.addEventListener('error', () => finish(failure('socket_error')));
      socket.addEventListener('close', () => finish(failure('socket_closed')));
      socket.addEventListener('message', event => {
        if (finished) return;
        // undici assembles messages before dispatch; parent memory limits remain mandatory.
        count += 1;
        if (typeof event.data !== 'string') return finish(failure('protocol_invalid'));
        const size = Buffer.byteLength(event.data, 'utf8');
        bytes += size;
        if (count > LIMITS.messages || size > LIMITS.messageBytes || bytes > LIMITS.totalMessageBytes)
          return finish(failure('message_limit'));
        let message;
        try { message = JSON.parse(event.data); }
        catch (_) { return finish(failure('protocol_invalid')); }
        if (!message || typeof message !== 'object' || Array.isArray(message))
          return finish(failure('protocol_invalid'));
        // Unsolicited inspector events are ignored but consume all message budgets.
        if (!Object.prototype.hasOwnProperty.call(message, 'id')) {
          if (typeof message.method !== 'string') return finish(failure('protocol_invalid'));
          return;
        }
        if (message.id !== expected) return finish(failure('protocol_invalid'));
        if (message.error || !message.result || message.result.exceptionDetails ||
            !message.result.result || message.result.result.type !== 'object' ||
            !Object.prototype.hasOwnProperty.call(message.result.result, 'value'))
          return finish(failure('evaluation_failed'));
        try {
          if (expected === 1) {
            report.runtime = sanitizeRuntime(message.result.result.value);
            report.readiness.runtime_host_verified = true;
            report.binding.load_attempted = true;
            expected = 2;
            evaluate(2);
          } else {
            report.binding = sanitizeBinding(message.result.result.value);
            report.readiness.native_binding_verified = true;
            report.ok = true;
            finish();
          }
        } catch (error) { finish(error.category ? error : failure('protocol_invalid')); }
      });
    } catch (_) { finish(failure('socket_unavailable')); }
  });
}

/** Preserve a single bounded public marker even for unexpected internal failures. */
function serializeReport(report) {
  let json = JSON.stringify(report);
  if (Buffer.byteLength(json, 'utf8') > LIMITS.outputBytes) {
    const fallback = baseReport();
    fallback.error = 'output_limit';
    json = JSON.stringify(fallback);
  }
  return MARKER + json + '\n';
}

/** Gate first, lazy-load only system undici, and emit one fixed-schema result. */
async function main() {
  const report = baseReport();
  try {
    verifyEnvironment();
    // Node18 labels undici.WebSocket experimental; neither warnings nor stacks are public evidence.
    process.removeAllListeners('warning');
    process.on('warning', () => {});
    // Pin the WSL-verified system package: a writable /tmp/node_modules must not shadow it.
    if (require.resolve('undici') !== '/usr/share/nodejs/undici/index.js')
      throw failure('socket_unavailable');
    const { WebSocket } = require('undici');
    if (typeof WebSocket !== 'function') throw failure('socket_unavailable');
    const endpoint = await discover();
    await inspect(endpoint, WebSocket, report);
  } catch (error) {
    report.error = error && ERRORS.has(error.category) ? error.category : 'internal_error';
  }
  process.stdout.write(serializeReport(report), () => process.exit(report.ok ? 0 : 1));
}

// Importing helpers never opens a connection or loads QQ/native software.
module.exports = Object.freeze({ LIMITS, MARKER, VERSION_KEYS, CANDIDATES,
  RUNTIME_EXPRESSION, BINDING_EXPRESSION, baseReport, verifyEnvironment, validateEndpoint,
  parseDiscovery, discoveryRequest, discover, sanitizeRuntime, sanitizeBinding, inspect,
  serializeReport });
if (require.main === module) main();
