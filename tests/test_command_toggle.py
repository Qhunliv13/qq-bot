"""Native command toggles target one actual descriptor and preserve unrelated commands."""
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def command_tool():
    """Import administration without service imports, credentials or network effects."""
    spec=importlib.util.spec_from_file_location('command_toggle',Path(__file__).parents[1]/'tools/command_toggle.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_disable_exact_builtin_preserves_others(command_tool,tmp_path,monkeypatch):
    """Resolve the real identifier from GET; issue one PATCH and retain rollback metadata."""
    rows=[{'original_command':'sid','module_path':'astrbot.builtin_stars.builtin_commands.main',
           'handler_full_name':'actual_sid','enabled':True},
          {'original_command':'help','module_path':'builtin_commands','handler_full_name':'help','enabled':True}]
    calls=[]
    def fake(method,path,credential,payload=None):
        """Model native hot persistence without any public QQ test message."""
        calls.append((method,path,payload))
        if method=='PATCH':rows[0]['enabled']=payload['enabled'];return rows[0]
        return {'items':[dict(row) for row in rows]}
    monkeypatch.setattr(command_tool,'request',fake)
    result=command_tool.apply(tmp_path/'key',tmp_path/'backup.json',tmp_path/'report.json',False)
    assert result['enabled'] is False and result['other_commands_unchanged']
    assert calls[1]==('PATCH','/commands/actual_sid',{'enabled':False})
    assert json.loads((tmp_path/'backup.json').read_text())[0]['enabled'] is True


def test_refuse_unknown_or_duplicate_handler(command_tool):
    """Never substitute another plugin or disable an entire system plugin."""
    with pytest.raises(RuntimeError):command_tool.select([], 'sid')
    row={'original_command':'sid','module_path':'unrelated'}
    with pytest.raises(RuntimeError):command_tool.select([row], 'sid')
    row['module_path']='builtin_commands'
    with pytest.raises(RuntimeError):command_tool.select([row,row], 'sid')


def test_plugin_access_default_scope_remains_narrow():
    """The additive command key choice leaves every existing release call plugin-only."""
    spec=importlib.util.spec_from_file_location('release_access',Path(__file__).parents[1]/'tools/release_access.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    assert module.SCOPES==('plugin',)
    assert module.NAME=='context-tools-20261025'
