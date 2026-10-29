"""Fake-only actual authored-entry execution and strict offline dispatch contracts."""
import copy
import io
import json
import subprocess
import sys
from unittest import mock

import pytest
import test_qq_prepared_stall_integration as integration
from tools import qq_host_dispatch_control as dispatch
from tools import qq_ephemeral_host_entry as shared

imports = integration.imports
observer = integration.observer
NONCE = 'a' * 32


def fake_run(tmp_path, reverse=False, throws=False, stalled=False, early=False):
    """Run actual entry under a VM with fake public APIs, never QQ or Electron."""
    script = tmp_path / 'fake-dispatch.cjs'
    script.write_text(r'''
const fs = require('fs'), vm = require('vm');
const jobs = [], writes = [], exits = [];
let now = 0;
const enqueue = (name, cb) => { jobs.push({name, cb}); };
const app = {whenReady() { if (THROWS) throw Error('private'); return {then(cb) {enqueue('whenReady', cb);}}; },
  on(event, cb) {if (THROWS) throw Error('private'); enqueue('onReady', cb);}, exit(code) {exits.push(code);}};
const context = {Buffer, require(name) {if (name === 'fs') return {writeFileSync(path, value) {writes.push(value);}};
  if (name === 'electron') return {app}; throw Error('forbidden import');},
  process: {env: {QQ_WRAPPER_HOST_NONCE: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'},
    execPath: '/opt/QQ/qq', versions: {node:'22.0.0',electron:'39.0.0',modules:'1',napi:'1',secret:'hidden'},
    hrtime: {bigint() {return BigInt(now)*1000000n;}}, nextTick(cb) {enqueue('nextTick',cb);}, exit(code) {exits.push(code);}},
  Promise: {resolve() {return {then(cb) {enqueue('promiseThen', cb);}};}},
  queueMicrotask(cb) {enqueue('queueMicrotask', cb);}, setImmediate(cb) {enqueue('setImmediate',cb);},
  setTimeout(cb, delay) {enqueue('setTimeout'+delay, cb);}};
vm.runInNewContext(fs.readFileSync(SOURCE, 'utf8'), context);
if (!STALLED) {
  const short = jobs.filter(job => job.name !== 'setTimeout1000');
  if (REVERSE) short.reverse();
  for (const job of short) {job.cb(); job.cb();}
  if (EARLY) {now = 999; jobs.find(job => job.name === 'setTimeout1000').cb();}
  now = 1000; jobs.find(job => job.name === (EARLY ? 'setTimeout1' : 'setTimeout1000')).cb();
}
console.log(JSON.stringify({writes, exits}));
'''.replace('SOURCE', json.dumps(str(shared.fixed_sources('host_dispatch_offline')[shared.MAIN])))
        .replace('THROWS', str(throws).lower()).replace('REVERSE', str(reverse).lower())
        .replace('STALLED', str(stalled).lower()).replace('EARLY', str(early).lower()), encoding='utf-8')
    result = subprocess.run(['node', str(script)], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize('reverse', [False, True])
@pytest.mark.parametrize('throws', [False, True])
def test_actual_entry_arbitrary_order_and_throw(tmp_path, reverse, throws):
    """All prefixes validate and duplicate callbacks never produce duplicate events."""
    result = fake_run(tmp_path, reverse, throws)
    records = [dispatch.parse_progress(raw.encode(), NONCE) for raw in result['writes']]
    final = records[-1]
    assert final['observation_complete'] and result['exits'] == [0]
    assert final['flags']['whenReady'] is not throws
    assert final['flags']['onReady'] is not throws
    assert len(final['history']) == (8 if throws else 10)
    assert 'secret' not in final['runtime']['versions']
    report = shared.base_report('host_dispatch_offline')
    dispatch.project_report(report, final, 0)
    assert report['status'] == 'observed'
    assert not report['native_binding_verified'] and not report['native_login_verified']
    assert not report['session_initialized'] and not report['safe_to_login']


def test_early_timer_rearms_without_fabricated_time(tmp_path):
    """An early timer publishes no false one-second checkpoint and uses remaining delay."""
    result = fake_run(tmp_path, early=True)
    records = [dispatch.parse_progress(raw.encode(), NONCE) for raw in result['writes']]
    assert records[-1]['history'][-1] == {'event': 'setTimeout1000', 'elapsed_ms': 1000}
    assert result['exits'] == [0]


def test_frozen_actual_evidence():
    """Verify the single approved observation without rerunning or altering its source."""
    import hashlib
    report = json.loads(dispatch.ARTIFACT.read_text(encoding='utf-8'), object_pairs_hook=shared.unique_object)
    progress = report['dispatch_progress']
    dispatch.parse_progress((json.dumps(progress) + '\n').encode(), progress['nonce'])
    assert [item['event'] for item in progress['history']] == ['bootstrapEntered', 'synchronousEnd',
        'nextTick', 'promiseThen', 'queueMicrotask', 'setImmediate', 'setTimeout0']
    assert not progress['observation_complete']
    assert report['completion'] == 'watchdog_30s' and report['exit_code'] == -9
    assert report['process_retired'] and report['runtime_versions_verified']
    assert report['host_software_unchanged'] and len(report['host_software_sha256_before']) == 5
    assert report['host_software_sha256_before'] == report['host_software_sha256_after']
    source = shared.fixed_sources('host_dispatch_offline')[shared.MAIN]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == report['authored_source_sha256']
    assert all(report['offline_network'].values())
    assert not report['stall_observation']['window_roles_queried']
    assert report['private_output_categories'] == {}
    assert not report['native_binding_verified'] and not report['native_login_verified']


def test_stalled_sync_prefix(tmp_path):
    """No dispatched callbacks is valid incomplete observation, not inferred ready."""
    result = fake_run(tmp_path, stalled=True)
    record = dispatch.parse_progress(result['writes'][-1].encode(), NONCE)
    assert [item['event'] for item in record['history']] == ['bootstrapEntered', 'synchronousEnd']
    assert not record['observation_complete'] and not result['exits']


@pytest.mark.parametrize('change', ['nonce', 'extra', 'flags', 'duplicate', 'early', 'timebool', 'bound', 'backwards', 'runtime', 'registration', 'complete'])
def test_reject_malformed_progress(tmp_path, change):
    """Reject payload expansion and contradictions rather than trusting flags."""
    value = copy.deepcopy(json.loads(fake_run(tmp_path)['writes'][-1]))
    if change == 'nonce':
        value['nonce'] = 'bad'
    elif change == 'extra':
        value['account'] = 'forbidden'
    elif change == 'flags':
        value['flags']['nextTick'] = False
    elif change == 'duplicate':
        value['history'][2]['event'] = 'bootstrapEntered'
    elif change == 'early':
        value['history'][-1]['elapsed_ms'] = 999
    elif change == 'timebool':
        value['history'][0]['elapsed_ms'] = True
    elif change == 'bound':
        value['history'][-1]['elapsed_ms'] = 60001
    elif change == 'backwards':
        value['history'][1]['elapsed_ms'] = 1
    elif change == 'runtime':
        value['runtime']['versions']['node'] = 'private/path'
    elif change == 'registration':
        value['registrations']['nextTick'] = 'threw'
    else:
        value['history'][-1]['event'] = 'unknown'
    with pytest.raises((ValueError, shared.runtime.ProbeError)):
        dispatch.parse_progress((json.dumps(value) + '\n').encode(), NONCE)


def test_duplicate_keys_nonce_and_size(tmp_path):
    """Reject duplicate keys, malformed verifier nonce and oversized evidence."""
    raw = fake_run(tmp_path)['writes'][-1].encode()
    for candidate, nonce in [(raw.replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1'), NONCE),
                             (raw, 'bad'), (raw + b' ' * shared.LIMIT, NONCE)]:
        with pytest.raises((ValueError, shared.runtime.ProbeError)):
            dispatch.parse_progress(candidate, nonce)


def test_route_and_flag_delete(monkeypatch, observer):
    """Use shared offline gate/count sampler; never capture errors or inherit phase flag."""
    monkeypatch.setitem(sys.modules, 'qq_host_dispatch_control', dispatch)
    sampler = sys.modules['qq_vendor_stall_observation']
    waiter, capture = mock.Mock(), mock.Mock()
    monkeypatch.setattr(sampler, 'observe_wait', waiter)
    monkeypatch.setattr(sampler, 'capture_categories', capture)
    report = shared.base_report('host_dispatch_offline')
    report['isolation'] = {}
    shared.observe_host(['/opt/QQ/qq'], {'QQ_PREPARED_PHASE_CONTROL':'1'}, io.BytesIO(), io.BytesIO(), 30,
        report, source=b'fixed', control=b'fixed', mount='mount', parent_ids={}, nonce=NONCE,
        control_mode='host_dispatch_offline')
    assert 'QQ_PREPARED_PHASE_CONTROL' not in shared.subprocess.Popen.call_args.kwargs['env']
    waiter.assert_called_once()
    assert 'window_sampler' not in waiter.call_args.kwargs
    capture.assert_not_called()
    assert report['private_output_categories'] == {} and report['status'] == 'failed'
    integration.connect.verify_offline_network.assert_called_once_with(report)


def test_fixed_sources_no_native_and_exclusive(tmp_path, monkeypatch):
    """Pin minimal source set and reserve immutable evidence before execution."""
    sources = shared.fixed_sources('host_dispatch_offline')
    assert len(sources) == 1
    source = sources[shared.MAIN].read_text(encoding='utf-8')
    for forbidden in ('dlopen', 'binding(', 'wrapper.node', 'native_login', 'native_host', 'BrowserWindow', 'setInterval', 'await ', 'while (', 'process.nextTick ='):
        assert forbidden not in source
    artifact = tmp_path / 'evidence.json'
    artifact.write_text('existing', encoding='utf-8')
    monkeypatch.setattr(dispatch, 'ARTIFACT', artifact)
    invoke = mock.Mock()
    monkeypatch.setattr(dispatch, 'observe', invoke)
    with pytest.raises(FileExistsError):
        dispatch.main(['--qq-root', 'unused', '--execute'])
    invoke.assert_not_called()
    for invalid in ('host_dispatch', 'host_dispatch_offline --flag', '../entry.js'):
        with pytest.raises(shared.runtime.ProbeError):
            shared.fixed_sources(invalid)
