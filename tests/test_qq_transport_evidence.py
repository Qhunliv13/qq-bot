"""Validate checked-in static evidence consistency, not a live QQ implementation.

These tests do not rerun readelf, connect to a server, or execute any QQ software.
A snapshot's internal consistency is not proof that the current runtime matches it.
"""
import json
from pathlib import Path


DOCS = Path(__file__).resolve().parents[1] / 'docs'


def read_document(name):
    """Read only public, checked-in facts; never open the referenced private evidence."""
    return json.loads((DOCS / name).read_text(encoding='utf-8'))


def test_abi_snapshot_records_host_coupling_without_runtime_claims():
    """A custom unresolved symbol and host definition are not a usable JS binding."""
    evidence = read_document('qq-native-abi-evidence.json')
    assert evidence['schema'] == 'qq-native-static-abi-evidence-v1'
    assert evidence['wrapper_symbols']['exit'] == 0
    assert evidence['wrapper_symbols']['capture_truncated'] is False
    assert evidence['wrapper_symbols']['standard_registration_exports_found'] == []
    registration = evidence['wrapper_symbols']['custom_undefined_registration_symbol']
    assert registration == 'qq_magic_napi_register'
    assert any(line.split()[-1] == registration
               for line in evidence['qq_host_registration']['matching_symbol_rows'])
    assert evidence['qq_host_registration']['exit'] == 0
    assert evidence['wrapper_symbols']['napi_prefixed_undefined_symbol_count'] > 0
    assert 'runtime N-API version' in evidence['unknown']
    assert 'full JavaScript export object and method signatures' in evidence['unknown']


def test_bootstrap_snapshot_keeps_original_entry_and_native_api_unknown():
    """Launcher filenames or binary ASAR entries cannot establish native API signatures."""
    bootstrap = read_document('qq-native-bootstrap-evidence.json')
    abi = read_document('qq-native-abi-evidence.json')
    assert bootstrap['installed_package']['version'] == abi['software']['package_version']
    assert bootstrap['installed_package']['current_main'] == abi['software']['package_main']
    assert bootstrap['installed_package']['original_main'] is None
    assert bootstrap['asar']['package_json_entry_present'] is False
    assert all(not value for value in bootstrap['boundary'].values())
    assert len(bootstrap['official_outer_launcher_metadata']) == 2
    assert all(item['literal_require_targets'] == ['../major.node']
               for item in bootstrap['official_outer_launcher_metadata'])
    prefix = bootstrap['asar']['prefix_little_endian_uint32']
    assert prefix[0] == 4
    assert prefix[1] == 4 + prefix[2]
    assert prefix[3] == bootstrap['asar']['header_json_bytes']
    assert all(item['utf8_decode_replacements'] > 0
               for item in bootstrap['asar']['selected_payload_metadata'])


def test_installed_actions_are_static_not_a_verified_native_backend():
    """Retain explicit unknown flags even when the static registry is fully resolved."""
    evidence = read_document('qq-installed-action-inventory.json')
    flags = evidence['completeness']
    assert flags['static_constructor_and_decorator_inventory'] is True
    for key in ('full_runtime_registry_verified', 'full_event_inventory_complete',
                'all_personal_qq_features_verified', 'independent_authentication_protocol_verified',
                'candidate_coverage_verified'):
        assert flags[key] is False
    abi = read_document('qq-native-abi-evidence.json')
    assert evidence['snapshot']['qq_version'] == abi['software']['package_version']


def test_event_snapshot_matches_installed_bundle_without_schema_claims():
    """Class candidates and enum literals are not a proven full event/message schema."""
    events = read_document('qq-installed-event-inventory.json')
    actions = read_document('qq-installed-action-inventory.json')
    assert events['schema_version'] == 1
    assert events['source']['sha256'] == actions['source']['sha256']
    assert events['source']['bytes'] == actions['source']['bytes']
    coverage = events['coverage']
    assert coverage['static_only'] is True
    assert coverage['source_bundle_verified_against_action_inventory'] is True
    for flag in ('constructors_executed', 'runtime_complete', 'requiredness_inferred',
                 'inheritance_resolved', 'local.member_list_delta_native',
                 'full_event_inventory_complete', 'dynamic_runtime_registry_complete_claim'):
        assert coverage[flag] is False
    assert coverage['event_record_count'] == len(events['events'])
    definitions = events['message_type_enum_candidates']
    assert coverage['message_type_enum_entry_count'] == sum(len(item['entries']) for item in definitions)
    assert all(item['runtime_registered_support_verified'] is False for item in definitions)
    types = {entry['type'] for definition in definitions for entry in definition['entries']}
    assert {'text', 'image', 'record', 'reply', 'node', 'forward'} <= types
    assert all(type(item['class_line']) is int and item['class_line'] > 0 for item in events['events'])
