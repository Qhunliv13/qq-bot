"""Verify exact failed-plugin recovery never falls through to a framework-wide reload."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def release():
    """Load the sole reviewed deploy implementation without running its command-line entry."""
    # Preserve historical recovery coverage without exposing this private deployer as active tooling.
    path=Path(__file__).resolve().parents[1]/'trash/20261030-personal-operations/tools/release_apply.py'
    spec=importlib.util.spec_from_file_location('tested_release_apply',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('failed,expected',[
    ({'astrbot_plugin_higan':{'error':'old timeout'}},'/plugins/failed/astrbot_plugin_higan/reload'),
    ({},'/plugins/astrbot_plugin_higan/reload'),
    ({'other':{'error':'mentions astrbot_plugin_higan only in text'}},'/plugins/astrbot_plugin_higan/reload'),
])
def test_exact_registry_selects_only_target_reload(release,tmp_path,monkeypatch,failed,expected):
    """Model the installed registry shape, verify backups/hashes and preserve process identities."""
    target=tmp_path/'target';target.mkdir()
    stage=tmp_path/'stage';(stage/'plugin').mkdir(parents=True)
    (target/'main.py').write_bytes(b'old')
    (stage/'plugin/main.py').write_bytes(b'new')
    original=Path.resolve
    def resolve(path,*args,**kwargs):
        """Substitute only this test target for the explicit production-path safety check."""
        if path==target:return Path('/root/astrbot-src/data/plugins/astrbot_plugin_higan')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'resolve',resolve)
    manifest=tmp_path/'manifest.json'
    manifest.write_text(json.dumps({'commit':'f'*40,'files':[{'name':'main.py','sha256':hashlib.sha256(b'new').hexdigest(),'baseline_sha256':hashlib.sha256(b'old').hexdigest()}]}))
    calls=[];reloaded=False
    def dashboard(path,credential,payload=None):
        """Emulate only exact plugin health and registry operations, not a bulk fallback."""
        nonlocal reloaded
        calls.append(path)
        if path=='/plugins/failed':return {'status':'ok','data':{} if reloaded else failed}
        if path.endswith('/reload'):
            assert path==expected
            reloaded=True
            return {'status':'ok'}
        assert path=='/plugins/astrbot_plugin_higan'
        return {'status':'ok','data':{'name':'astrbot_plugin_higan'}}
    monkeypatch.setattr(release,'dashboard',dashboard)
    monkeypatch.setattr(release,'command',lambda args:'714988' if args[0]=='systemctl' else ('unchanged-napcat' if args[0]=='docker' else ''))
    report=tmp_path/'report.json'
    release.apply(stage,target,'f'*40,manifest,tmp_path/'backup',report,tmp_path/'credential')
    result=json.loads(report.read_text())
    assert result['status']=='active'
    assert result['reload_path']==expected
    assert calls.count(expected)==1
    assert result['hashes_verified']==1
    assert (tmp_path/'backup/main.py').read_bytes()==b'old'
    assert (target/'main.py').read_bytes()==b'new'
