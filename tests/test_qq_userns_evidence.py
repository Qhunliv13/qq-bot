"""Consistency checks for actual account-free user namespace controls, not QQ parity."""
import json
from pathlib import Path


def test_controls_separate_probe_limitations_from_qq_feature_claims():
    """A denied trusted namespace inside chroot must not become an impossible-QQ claim."""
    path = Path(__file__).resolve().parents[1] / 'docs/evidence/qq-userns-control-evidence.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    outside = data['without_chroot_control']
    inside = data['inside_existing_chroot_control']
    assert outside['exit_code'] == 0
    assert outside['uid'] == outside['gid'] == 65534
    assert outside['no_new_privileges'] is True
    assert outside['qq_executed'] is False
    assert inside['control_executed'] is True
    assert inside['control_exit_code'] == 1
    assert inside['control_failure_category'] == 'user_namespace_creation_denied'
    assert inside['runtime_executed'] is False
    assert inside['runtime_versions_verified'] is False
    assert all(inside['isolation'].values())
    assert data['runtime_mode_static_scan']['alternative_environment_name_found'] is False
    assert data['runtime_mode_static_scan']['byte_presence_proves_runtime_acceptance'] is False
    for field in ('system_policy_modified', 'production_modified', 'native_binding_verified',
                  'login_verified', 'full_feature_parity_verified', 'production_cutover_authorized'):
        assert data[field] is False
