"""Use the configured AstrBot model/source pair, not a bundled provider identity."""
import ast
import json
import logging
import os
from pathlib import Path

import pytest


def _provider_adapter():
    """Compile the real selected-source reader without importing AstrBot or credentials."""
    source_path = Path(__file__).resolve().parents[1] / 'plugin/main.py'
    tree = ast.parse(source_path.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'HiganPlugin')
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == '_her_llm_config')
    namespace = {'__package__': 'plugin', 'json': json, 'os': os, 'Path': Path,
                 'CHAT_TIMEOUT': 60, 'logger': logging.getLogger(__name__)}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source_path), 'exec'), namespace)
    return type('SourceAdapter', (), {'_her_llm_config': namespace['_her_llm_config']})()


def test_model_selection_uses_source_id_instead_of_first_source(tmp_path, monkeypatch):
    """A fresh embedded installation must use its operator-selected endpoint/model."""
    plugin = _provider_adapter()
    (tmp_path / 'data').mkdir()
    config = {'provider_sources': [{'id': 'unselected', 'api_base': 'https://wrong.invalid', 'key': ['not-used']},
                                  {'id': 'selected-source', 'api_base': 'https://chosen.invalid/v1', 'key': ['fixture']}],
              'provider': [{'id': 'chosen-model', 'provider_source_id': 'selected-source', 'model': 'operator-model'}],
              'provider_settings': {'default_provider_id': 'chosen-model'}}
    (tmp_path / 'data/cmd_config.json').write_text(json.dumps(config), encoding='utf-8')
    monkeypatch.setenv('ASTRBOT_ROOT', str(tmp_path))
    assert plugin._her_llm_config() == ('https://chosen.invalid/v1', 'fixture', 'operator-model')


@pytest.mark.parametrize('config', [
    {},
    {'provider': [{'id': 'first'}, {'id': 'second'}]},
    {'provider': [{'id': 'only'}], 'provider_settings': {'default_provider_id': 'missing'}},
    {'provider': [{'id': 'only', 'provider_source_id': 'missing'}],
     'provider_sources': [{'id': 'other', 'api_base': 'https://wrong.invalid', 'key': ['wrong']}]},
    {'provider': [{'id': 'only'}],
     'provider_sources': [{'id': 'one'}, {'id': 'two'}]},
])
def test_missing_or_ambiguous_selection_fails_closed(tmp_path, monkeypatch, config):
    """No bundled brand/provider identity or first-source guess rescues invalid selection."""
    (tmp_path / 'data').mkdir()
    (tmp_path / 'data/cmd_config.json').write_text(json.dumps(config), encoding='utf-8')
    monkeypatch.setenv('ASTRBOT_ROOT', str(tmp_path))
    assert _provider_adapter()._her_llm_config() == ('', '', '')


@pytest.mark.parametrize('canonical,legacy,expected_model', [
    ('second', None, 'second-model'),
    ('second', 'first', 'second-model'),
    ('missing', 'first', None),
    ('', 'first', None),
])
def test_canonical_runner_binding_survives_upstream_migration(tmp_path, monkeypatch,
                                                             canonical, legacy, expected_model):
    """Actual v3 runner selection wins; invalid explicit bindings never use legacy defaults."""
    (tmp_path / 'data').mkdir()
    config = {
        'provider_sources': [{'id': 'source', 'api_base': 'https://offline.invalid/v1',
                              'key': ['fixture']}],
        'provider': [{'id': name, 'provider_source_id': 'source', 'model': name + '-model'}
                     for name in ('first', 'second')],
        'agent_runner': {'runner_type': 'local', 'config': {'model': {'provider_id': canonical}}},
        'provider_settings': {'default_provider_id': legacy} if legacy else {},
    }
    (tmp_path / 'data/cmd_config.json').write_text(json.dumps(config), encoding='utf-8')
    monkeypatch.setenv('ASTRBOT_ROOT', str(tmp_path))
    expected = ('https://offline.invalid/v1', 'fixture', expected_model) if expected_model else ('', '', '')
    assert _provider_adapter()._her_llm_config() == expected


def test_unique_private_legacy_source_is_preserved_without_fixed_brand(tmp_path, monkeypatch):
    """A sole operator-defined provider/source is unambiguous existing private configuration."""
    (tmp_path / 'data').mkdir()
    config = {'provider': [{'id': 'private-selection', 'model': 'private-model'}],
              'provider_sources': [{'api_base': 'https://private.invalid/v1', 'key': ['fixture']}]}
    (tmp_path / 'data/cmd_config.json').write_text(json.dumps(config), encoding='utf-8')
    monkeypatch.delenv('ASTRBOT_ROOT', raising=False)
    monkeypatch.chdir(tmp_path)
    assert _provider_adapter()._her_llm_config() == ('https://private.invalid/v1', 'fixture', 'private-model')
