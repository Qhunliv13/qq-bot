"""Protect installed NapCat HTTP dispatch against successful no-op root responses."""
import ast
import json
from pathlib import Path
import re
from types import SimpleNamespace

import pytest


@pytest.fixture
def native_http(monkeypatch):
    """Load the actual shared bridge without importing or initializing a live plugin."""
    source=Path(__file__).resolve().parents[1]/'plugin/main.py'
    tree=ast.parse(source.read_text(encoding='utf-8'))
    cls=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='HiganPlugin')
    node=next(node for node in cls.body if isinstance(node,ast.FunctionDef) and node.name=='_onebot_http')
    namespace={'re':re,'logger':SimpleNamespace(warning=lambda *args:None)}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(source),'exec'),namespace)
    calls=[]
    class Response:
        """Simulate a native account envelope, not a root-path no-op."""
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def read(self):
            """Preserve complete business response for callers to verify."""
            return b'{"status":"ok","retcode":0,"data":{"user_id":123}}'
    def open_request(request,timeout):
        """Capture exact host/action/body rather than returning success for every URL."""
        calls.append((request.full_url,json.loads(request.data),timeout))
        return Response()
    monkeypatch.setattr('urllib.request.urlopen',open_request)
    return namespace['_onebot_http'],calls


def test_action_is_path_parameters_are_body(native_http):
    """Use /get_stranger_info with parameters directly, not action/params at POST /."""
    function,calls=native_http
    result=function(None,'get_stranger_info',{'user_id':'123','no_cache':True})
    assert result['status']=='ok' and result['data']['user_id']==123
    assert calls==[('http://127.0.0.1:13000/get_stranger_info',{'user_id':'123','no_cache':True},25)]


@pytest.mark.parametrize('action',['../get_cookies','https://example.invalid','/get_login_info','get_login_info?x=1',''])
def test_action_cannot_escape_native_path(native_http,action):
    """Reject path syntax before issuing any request; never expose a URL passthrough."""
    function,calls=native_http
    assert function(None,action,{})=={}
    assert calls==[]
