"""Respect explicit provider transport settings rather than the plugin's45s fallback."""
import ast
import json
import logging
from pathlib import Path

import pytest

from plugin.model_transport import request_timeout


@pytest.mark.parametrize("configured,expected", [(300,300), (1.5,1.5), ("600",600), (0,None)])
def test_configured_deadline_is_preserved(configured, expected):
    """A valid setting is neither clamped to45 nor replaced by another hard limit."""
    assert request_timeout({"timeout":configured},45) == expected


@pytest.mark.parametrize("source", [{}, {"timeout":None}, {"timeout":True}, {"timeout":-1},
                                     {"timeout":"invalid"}, {"timeout":float("inf")}])
def test_invalid_or_missing_deadline_uses_existing_fallback(source):
    """Only absent or invalid deployment settings use the existing transport fallback."""
    assert request_timeout(source,45) == 45


def test_actual_provider_loader_sets_timeout_without_changing_credentials(tmp_path, monkeypatch):
    """Compile the real loader and provide a local config, not a mocked timeout method."""
    source = Path(__file__).resolve().parents[1]/"plugin/main.py"
    tree=ast.parse(source.read_text(encoding="utf-8"))
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=="HiganPlugin")
    method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=="_her_llm_config")
    import os
    ns={"__name__":"plugin.timeout_test", "__package__":"plugin", "json":json,
        "os":os,"Path":Path,"CHAT_TIMEOUT":45,"logger":logging.getLogger(__name__)}
    (tmp_path / "data").mkdir()
    config=tmp_path/"data"/"cmd_config.json"
    config.write_text(json.dumps({"provider_sources":[{"api_base":"https://offline.invalid/v1", "key":["offline-key"],"timeout":300}],
                                  "provider":[{"id":"operator-provider","model":"offline-model"}]}),encoding="utf-8")
    monkeypatch.setenv("ASTRBOT_ROOT", str(tmp_path))
    exec(compile(ast.Module(body=[method],type_ignores=[]),str(source),"exec"),ns)
    bot=type("Bot",(),{"_her_llm_config":ns["_her_llm_config"]})()
    assert bot._her_llm_config()==("https://offline.invalid/v1","offline-key","offline-model")
    assert bot._provider_request_timeout==300
