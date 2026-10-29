"""Fake-only phase deadlines, strict prefixes and fixed-mode regression tests."""
import copy
import hashlib
import json
import io
import sys
from unittest import mock
import test_qq_prepared_stall_integration as integration

# Reuse the actual runner fixtures without duplicating isolation-boundary mocks.
imports = integration.imports
observer = integration.observer

import pytest

from tools import qq_prepared_phase_control as phase
from tools import qq_ephemeral_host_entry as shared
from test_qq_prepared_connect_control import fake_run, NONCE


def test_phase_entry_and_all_prefixes(tmp_path):
    """Native calls stay once-only; all five waits precede subsequent native work."""
    result = fake_run(tmp_path, phase=True)
    value = phase.parse_record(result['raw'].encode(), NONCE)
    for raw in result['checkpoints']:
        phase.parse_progress(raw.encode(), NONCE)
    assert [item['stage'] for item in value['progress']['history']] == list(phase.STAGES)
    assert value['progress']['history'][-1]['elapsed_ms'] == 12000
    assert result['calls'] == ['dlopen', 'engine.get', 'login.get', 'startup.create', 'session.get',
                               'engine.init', 'login.init', 'listener.add', 'connect']
    report = shared.base_report('prepared_phase_offline')
    phase.project_report(report, value, 0)
    assert all(report['phase_timers_verified'].values())
    assert not report['safe_to_login'] and not report['native_login_verified']


@pytest.mark.parametrize('stage,count', [('runtime', 0), ('after_binding_load', 1),
    ('after_prepare', 5), ('after_initialize', 8), ('after_connect', 9)])
def test_stall_never_advances_next_native_stage(tmp_path, stage, count):
    """Stopping dispatch at each origin leaves a valid prefix and no later call."""
    result = fake_run(tmp_path, phase=True, stall=stage)
    value = phase.parse_progress(result['progress'].encode(), NONCE)
    assert value['stage'] == stage
    assert len(result['calls']) == count
    assert 'raw' not in result


@pytest.mark.parametrize('change', ['early', 'skip', 'nonce', 'extra', 'boolean', 'duplicate'])
def test_reject_invalid_phase_history(tmp_path, change):
    """Reject premature timers, skipped stages and malformed/secret evidence."""
    value = json.loads(fake_run(tmp_path, phase=True)['progress'])
    value = copy.deepcopy(value)
    if change == 'early':
        value['history'][1]['elapsed_ms'] = 999
    elif change == 'skip':
        value['history'].pop(1)
    elif change == 'nonce':
        value['nonce'] = 'a' * 32
    elif change == 'extra':
        value['secret'] = 'forbidden'
    elif change == 'boolean':
        value['history'][1]['elapsed_ms'] = True
    else:
        value['history'][1]['stage'] = 'runtime'
    with pytest.raises(shared.runtime.ProbeError):
        phase.parse_progress((json.dumps(value) + '\n').encode(), NONCE)


def test_old_mode_preserves_exact_progress(tmp_path):
    """Absent flag preserves old history, schema, deadlines and result marker."""
    result = fake_run(tmp_path)
    value = phase.prepared.parse_record(result['raw'].encode(), NONCE)
    assert [item['stage'] for item in value['progress']['history']] == list(phase.prepared.STAGES)
    assert [item['elapsed_ms'] for item in value['progress']['history']] == [0]*9 + [1000, 5000, 8000, 8000, 8000]
    assert 'phase_' not in result['progress']


def test_fixed_sources_and_delegation(monkeypatch):
    """No new entry/source override, no budget changes and only one fixed mode."""
    assert shared.fixed_sources('prepared_phase_offline') == shared.fixed_sources('prepared_connect_offline')
    calls = []
    def capture(root, **kwargs):
        """Record delegation without launching QQ."""
        calls.append((root, kwargs))
        return {'status': 'not_executed'}
    monkeypatch.setattr(shared, 'observe', capture)
    phase.observe('root', execute=True)
    assert calls == [('root', {'execute': True, 'control_mode': 'prepared_phase_offline'})]
    assert (shared.LIMIT, shared.QQ_TIMEOUT, shared.OUTER_TIMEOUT) == (16384, 30, 45)


@pytest.mark.parametrize('mode', ['prepared_phase_offline', 'prepared_connect_offline', 'wrapper', 'initialize'])
def test_runner_controls_flag(monkeypatch, observer, mode):
    """Caller flags cannot enable waits outside the fixed mode or disable its waits."""
    monkeypatch.setitem(sys.modules, 'qq_prepared_phase_control', phase)
    sampler = sys.modules['qq_vendor_stall_observation']
    monkeypatch.setattr(sampler, 'observe_wait', mock.Mock())
    monkeypatch.setattr(sampler, 'capture_categories', mock.Mock())
    report = shared.base_report(mode)
    report['isolation'] = {}
    shared.observe_host(['/opt/QQ/qq'], {'QQ_PREPARED_PHASE_CONTROL': 'caller'}, io.BytesIO(), io.BytesIO(),
                        30, report, source=b'fixed', control=b'fixed', mount='mount', parent_ids={},
                        nonce=NONCE, control_mode=mode)
    env = shared.subprocess.Popen.call_args.kwargs['env']
    assert env.get('QQ_PREPARED_PHASE_CONTROL') == ('1' if mode == 'prepared_phase_offline' else None)


def test_frozen_actual_evidence_stopped_before_dlopen():
    """Validate the single approved run without executing software or rewriting evidence."""
    report = json.loads(phase.ARTIFACT.read_text(encoding='utf-8'), object_pairs_hook=shared.unique_object)
    progress = report['connect_progress']
    phase.parse_progress((json.dumps(progress) + '\n').encode(), progress['nonce'])
    assert progress['history'] == [{'stage': 'runtime', 'elapsed_ms': 0}]
    assert progress['host'] is None and progress['login'] is None
    assert report['completion'] == 'watchdog_30s' and report['exit_code'] == -9
    assert report['status'] == 'failed' and report['process_retired']
    assert report['host_record'] is None and not report['runtime_versions_verified']
    assert not any(report['phase_timers_verified'].values())
    assert report['host_software_unchanged']
    assert len(report['host_software_sha256_before']) == 5
    assert report['host_software_sha256_before'] == report['host_software_sha256_after']
    assert all(report['offline_network'].values())
    assert all(report['isolation'][key] for key in ('network', 'mount', 'pid', 'ipc', 'pivotRoot',
        'oldroot_detached', 'unprivileged', 'effective_capabilities_zero', 'no_new_privileges'))
    source = shared.fixed_sources('prepared_phase_offline')[shared.MAIN]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == report['authored_source_sha256']


def test_artifact_is_exclusive_before_execution(tmp_path, monkeypatch):
    """An existing immutable artifact refuses another execution before observe."""
    artifact = tmp_path / 'evidence.json'
    artifact.write_text('existing', encoding='utf-8')
    monkeypatch.setattr(phase, 'ARTIFACT', artifact)
    with pytest.raises(FileExistsError):
        phase.main(['--qq-root', 'unused', '--execute'])
    assert artifact.read_text(encoding='utf-8') == 'existing'
