'use strict';
// Fixed host-dispatch observation: public scheduling APIs only, no application imports.
const fs = require('fs');
const nonce = process.env.QQ_WRAPPER_HOST_NONCE;
if (!/^[0-9a-f]{32}$/.test(nonce || '')) throw new Error('nonce_invalid');
const events = ['bootstrapEntered', 'synchronousEnd', 'nextTick', 'promiseThen',
  'queueMicrotask', 'setImmediate', 'setTimeout0', 'setTimeout1000', 'whenReady', 'onReady'];
const callbacks = events.slice(2);
const started = process.hrtime.bigint();
const versions = {};
for (const key of ['node', 'electron', 'modules', 'napi', 'chrome', 'v8', 'uv']) {
  const value = process.versions[key];
  if (typeof value === 'string' && /^[0-9A-Za-z.+_-]{1,64}$/.test(value)) versions[key] = value;
}
const record = {schema_version: 1, nonce, runtime: {versions,
  exec_path_matches: process.execPath === '/opt/QQ/qq'},
  flags: Object.fromEntries(events.map(key => [key, false])),
  registrations: Object.fromEntries(callbacks.map(key => [key, 'pending'])),
  history: [], observation_complete: false};
let app = null;
let finished = false;
/** Persist only the fixed bounded projection; never raw exceptions or objects. */
function write() {
  const encoded = JSON.stringify(record) + '\n';
  if (Buffer.byteLength(encoded, 'utf8') > 16384) throw new Error('record_bound');
  fs.writeFileSync('/tmp/qq-host-dispatch-progress.json', encoded, {mode: 0o600});
}
/** Mark each enum once, retaining actual dispatch order rather than assuming it. */
function mark(event) {
  if (finished || record.flags[event]) return;
  const elapsed = Number((process.hrtime.bigint() - started) / 1000000n);
  record.flags[event] = true;
  record.history.push({event, elapsed_ms: elapsed});
  write();
}
/** Catch registration failures without disclosing exception data or blocking main. */
function register(event, install) {
  try {
    install();
    record.registrations[event] = 'registered';
  } catch (_) {
    record.registrations[event] = 'threw';
  }
}
mark('bootstrapEntered');
register('nextTick', () => process.nextTick(() => mark('nextTick')));
register('promiseThen', () => Promise.resolve().then(() => mark('promiseThen')));
register('queueMicrotask', () => queueMicrotask(() => mark('queueMicrotask')));
register('setImmediate', () => setImmediate(() => mark('setImmediate')));
register('setTimeout0', () => setTimeout(() => mark('setTimeout0'), 0));
/** Rearm an early timer to its original deadline; never manufacture elapsed time. */
function completeObservation() {
  const elapsed = Number((process.hrtime.bigint() - started) / 1000000n);
  if (elapsed < 1000) { setTimeout(completeObservation, 1000 - elapsed); return; }
  mark('setTimeout1000');
  record.observation_complete = true;
  write();
  finished = true;
  // Completion means bounded observation only; ready is deliberately not required.
  if (app && typeof app.exit === 'function') app.exit(0);
  else process.exit(0);
}
register('setTimeout1000', () => setTimeout(completeObservation, 1000));
try { app = require('electron').app; } catch (_) { /* Timer remains independent. */ }
register('whenReady', () => app.whenReady().then(() => mark('whenReady'), () => {}));
register('onReady', () => app.on('ready', () => mark('onReady')));
mark('synchronousEnd');
