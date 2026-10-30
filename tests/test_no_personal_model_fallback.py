"""Unconfigured model requests must not silently select a developer's provider/model."""
import ast
import asyncio
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / 'plugin/main.py'


@pytest.mark.parametrize('name,args,expected', [
    ('_llm_chat', ([],), ''),
    ('_llm_plain', ('system', 'prompt', []), ''),
    ('_llm_tools', ('system', 'prompt', []), ('', [])),
])
def test_missing_model_never_requests_hardcoded_provider(monkeypatch, name, args, expected):
    """Run actual request methods with a missing model and a network-refusing edge."""
    import urllib.request
    def forbidden(*_args, **_kwargs):
        """A missing configuration may not trigger an external request."""
        raise AssertionError('unconfigured request attempted')
    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'HiganPlugin')
    method = next(node for node in cls.body if isinstance(node, ast.AsyncFunctionDef) and node.name == name)
    namespace = {}
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), method], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(SOURCE), 'exec'), namespace)
    bot = type('Bot', (), {name: namespace[name], '_her_llm_config': lambda self: ('https://example.invalid/v1', 'fixture-only', '')})()
    assert asyncio.run(getattr(bot, name)(*args)) == expected
