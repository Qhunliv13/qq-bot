"""Guard metadata-only offline defaults and native usage semantics."""
import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('prompt_structure_cache_probe', ROOT / 'tools' / 'prompt_structure_cache_probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_offline_never_calls_provider():
    """Default analysis must have no provider network side effect."""
    with patch.object(probe.urllib.request, 'urlopen', side_effect=AssertionError('network forbidden')):
        report = probe.run_probe(ROOT / 'plugin')
    assert report['provider_request_count'] == 0
    assert report['requests'] == []
    assert report['qq_messages_sent'] == report['tool_actions_executed'] == 0
    assert 'cached_tokens' not in json.dumps(report)


def test_lossless_layout_with_unknown_fields():
    """Reordering must retain unknown nested data rather than silently dropping it."""
    context = probe.load_context(ROOT / 'plugin')
    for text in probe.fixture_pair(context.build_context):
        data = json.loads(text)
        data['future_field'] = {'keep': ['all', 12]}
        data['conversation']['extra'] = {'keep': True}
        for layout in (probe.prior_layout, probe.history_first_layout):
            result = layout(json.dumps(data))
            assert json.loads(result) == data
            assert len(json.loads(result)['conversation']['history']) == 40
            assert len(result.encode('utf-8')) == len(json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def test_only_history_first_reuses_cross_scope_history_prefix():
    """The exact fixture varies early observations and scope but keeps all history."""
    report = probe.run_probe(ROOT / 'plugin')
    assert report['parsed_data_equal']
    assert set(report['layouts']) == {'prior', 'history_first', 'observations_first'}
    assert report['layouts']['history_first']['common_user_prefix_bytes'] > report['layouts']['prior']['common_user_prefix_bytes'] + 10000
    # This counterexample changes observations: the chosen production order is
    # not universally better, and the old control must not be relabeled as new.
    assert report['layouts']['history_first']['common_user_prefix_bytes'] > report['layouts']['observations_first']['common_user_prefix_bytes'] + 10000
    assert report['layouts']['history_first']['user_bytes'] == report['layouts']['observations_first']['user_bytes']
    assert report['causal_improvement_asserted'] is False


def test_native_missing_cache_is_unknown():
    """Missing provider evidence must never become a fabricated zero-cache hit."""
    value = probe.native_usage({'usage': {'prompt_tokens': 100}})
    assert value['cached_tokens'] is None
    assert value['cache_valid'] is False


def test_weighted_cache_is_not_mean_percent():
    """A large prompt receives its token weight, not the weight of one request."""
    rows = [{'usage': probe.native_usage({'usage': {'prompt_tokens': 100, 'prompt_tokens_details': {'cached_tokens': 100}}})},
            {'usage': probe.native_usage({'usage': {'prompt_tokens': 900, 'prompt_cache_hit_tokens': 0}})}]
    assert probe.weighted(rows)['weighted_hit_percent'] == 10


def test_output_contains_no_fixture_or_persona_content():
    """Safe report never serializes fixture strings, persona, payloads or raw response."""
    serialized = json.dumps(probe.run_probe(ROOT / 'plugin'))
    assert 'Synthetic public discussion' not in serialized
    assert 'synthetic-scope-' not in serialized
    assert 'Synthetic observation' not in serialized
    assert 'Authorization' not in serialized
