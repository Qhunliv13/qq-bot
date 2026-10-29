'use strict';
// Observe only the vendor-owned bootstrap in its original outer module identity.
const fs = require('fs');
const app = require('electron').app;
const nonce = process.env.QQ_WRAPPER_HOST_NONCE;
const progressPath = '/tmp/qq-vendor-bootstrap-progress.json';
const resultPath = '/tmp/qq-wrapper-host-result.json';
const fatalPath = '/tmp/qq-vendor-bootstrap-fatal.json';
const versions = {};
for (const key of ['node', 'electron', 'modules', 'napi', 'chrome', 'v8', 'uv']) {
  if (typeof process.versions[key] === 'string') versions[key] = process.versions[key];
}
let returned = false;
let readyFired = false;
let timer0 = false;
let timer5s = false;
let timer8s = false;
/** Synchronously persist fixed lifecycle facts, never QQ data or exception text. */
function checkpoint(stage) {
  const record = {
    schema_version: 1, nonce, stage,
    runtime: {versions, exec_path_matches: process.execPath === '/opt/QQ/qq'},
    vendor_bootstrap_returned: returned, ready_callback_fired: readyFired,
    app_is_ready: app.isReady(), timer0_fired: timer0,
    timer5s_fired: timer5s, timer8s_fired: timer8s
  };
  fs.writeFileSync(progressPath, JSON.stringify(record) + '\n', {mode: 0o600});
  return record;
}
/** Fixed fatal categories terminate the process; they never suppress or retry. */
function fatal(category) {
  fs.writeFileSync(fatalPath, JSON.stringify({schema_version: 1, nonce, category}) + '\n', {mode: 0o600});
  process.exit(1);
}
process.once('uncaughtException', () => fatal('uncaught_exception'));
process.once('unhandledRejection', () => fatal('unhandled_rejection'));
if (!/^[a-f0-9]{32}$/.test(nonce || '')) process.exit(1);
checkpoint('before_vendor_bootstrap');
app.once('ready', () => {
  readyFired = true;
  checkpoint('ready_callback');
});
setTimeout(() => {
  timer0 = true;
  checkpoint('timer0');
}, 0);
setTimeout(() => {
  timer5s = true;
  checkpoint('timer5s');
}, 5000);
setTimeout(() => {
  timer8s = true;
  const record = checkpoint('timer8s');
  fs.writeFileSync(resultPath, 'QQ_VENDOR_BOOTSTRAP_' + nonce + ':' + JSON.stringify(record) + '\n', {mode: 0o600});
  // Observation complete does not mean Electron-ready, QQ login, or send/receive.
  app.exit(0);
}, 8000);
// Exact 56-byte vendor expression; same overlaid filename and module, not a wrapper.
require('../major.node').load('internal_index', module);
returned = true;
checkpoint('after_vendor_bootstrap');
