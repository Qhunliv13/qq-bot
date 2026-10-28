"""Consistency checks for real detached-root observations, not live QQ execution."""
import json
from pathlib import Path


def test_detached_root_control_success_does_not_imply_qq_success():
    """Control availability, GUI failure and cutover permission remain separate facts."""
    path = Path(__file__).resolve().parents[1] / 'docs/qq-pivot-root-evidence.json'
    evidence = json.loads(path.read_text(encoding='utf-8'))
    control = evidence['trusted_control']
    assert control['control_exit_code'] == 0
    assert control['user_namespace_creation_verified'] is True
    assert control['runtime_executed'] is False
    assert all(value is True for value in control['isolation'].values())
    runtime = evidence['runtime_attempt']
    assert runtime['status'] == 'failed'
    assert runtime['errors'] == ['runtime_timeout']
    assert runtime['runtime_executed'] is True
    assert runtime['runtime_versions_verified'] is False and runtime['versions'] == {}
    assert runtime['stderr_observations']['missing_x_server_or_display'] is True
    assert runtime['stderr_observations']['suid_sandbox_configuration_fatal_observed'] is False
    assert evidence['cutover_requires_new_explicit_user_instruction'] is True
    for key in ('production_modified', 'account_credentials_copied', 'login_verified',
                'native_binding_verified', 'full_feature_parity_verified', 'production_cutover_authorized'):
        assert evidence[key] is False
