"""Use the configured AstrBot model/source pair, not a bundled provider identity."""
import ast
import json
import logging
import os
from pathlib import Path


def test_model_selection_uses_source_id_instead_of_first_source(tmp_path, monkeypatch):
    """A fresh embedded installation must use its operator-selected endpoint/model."""
    source_path = Path(__file__).resolve().parents[1] / 'plugin/main.py'
    tree = ast.parse(source_path.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'HiganPlugin')
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == '_her_llm_config')
    namespace = {'__package__': 'plugin', 'json': json, 'os': os, 'Path': Path,
                 'CHAT_TIMEOUT': 60, 'logger': logging.getLogger(__name__)}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source_path), 'exec'), namespace)
    plugin = type('SourceAdapter', (), {'_her_llm_config': namespace['_her_llm_config']})()
    (tmp_path / 'data').mkdir()
    config = {'provider_sources': [{'id': 'unselected', 'api_base': 'https://wrong.invalid', 'key': ['not-used']},
                                  {'id': 'selected-source', 'api_base': 'https://chosen.invalid/v1', 'key': ['fixture']}],
              'provider': [{'id': 'chosen-model', 'provider_source_id': 'selected-source', 'model': 'operator-model'}],
              'provider_settings': {'default_provider_id': 'chosen-model'}}
    (tmp_path / 'data/cmd_config.json').write_text(json.dumps(config), encoding='utf-8')
    monkeypatch.setenv('ASTRBOT_ROOT', str(tmp_path))
    assert plugin._her_llm_config() == ('https://chosen.invalid/v1', 'fixture', 'operator-model')
