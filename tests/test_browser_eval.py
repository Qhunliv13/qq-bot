"""Offline Python/JavaScript contracts for the no-restart CDP compatibility adapter."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from plugin.browser_eval import build_eval_wrapper, decode_eval_response  # noqa: E402


def run_wrapper(tmp_path, expression):
    """Evaluate only local test JS; file-backed stdio avoids sandbox named pipes."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for offline JavaScript semantics tests")
    script = tmp_path / "eval.cjs"
    output = tmp_path / "result.json"
    errors = tmp_path / "errors.txt"
    script.write_text("Promise.resolve(" + build_eval_wrapper(expression) + ").then(value => process.stdout.write(JSON.stringify({ok:true,value}))).catch(error => {console.error(error); process.exitCode=1;});", encoding="utf-8")
    with output.open("w", encoding="utf-8") as stdout, errors.open("w", encoding="utf-8") as stderr:
        completed = subprocess.run([node, str(script)], stdout=stdout, stderr=stderr, timeout=10, check=False)
    assert completed.returncode == 0, errors.read_text(encoding="utf-8")
    return decode_eval_response(json.loads(output.read_text(encoding="utf-8")))


@pytest.mark.parametrize(("expression", "expected"), [("null", None), ("false", False), ("0", 0), ('"雪\\n\\\""', '雪\n"'), ("({a:[1,2], text:'ok'})", {"a": [1, 2], "text": "ok"}), ("Promise.resolve(42)", 42), ("let x = 3; x + 1", 4)])
def test_json_values(tmp_path, expression, expected):
    """Preserve global script completion values and awaited JSON-safe results."""
    result = run_wrapper(tmp_path, expression)
    assert result == {"ok": True, "value": expected, "value_type": "json"}


def test_undefined_is_not_null(tmp_path):
    """An undefined result is explicit rather than inferred as a dropped exception."""
    assert run_wrapper(tmp_path, "void 0") == {"ok": True, "value": None, "value_type": "undefined"}


@pytest.mark.parametrize("expression", ["(()=>{throw new Error('expected failure')})()", "(()", "Promise.reject(new TypeError('rejected'))", "throw 'primitive'", "throw {get message(){throw 1},get name(){throw 2},toString(){throw 3}}"])
def test_errors_are_not_silent_success(tmp_path, expression):
    """Syntax/runtime/rejection and unusual thrown objects must become errors."""
    result = run_wrapper(tmp_path, expression)
    assert result["ok"] is False
    assert result["error_type"] == "evaluation_error"
    assert isinstance(result["exception"]["message"], str)


@pytest.mark.parametrize("expression", ["1n", "NaN", "Infinity", "(()=>{})", "Symbol('x')", "({a:undefined})", "[undefined]", "(()=>{const a={};a.self=a;return a})()"])
def test_non_json_results_are_explicit(tmp_path, expression):
    """Lossy or cyclic values must not be silently converted into null/omitted keys."""
    result = run_wrapper(tmp_path, expression)
    assert result["ok"] is False
    assert result["error_type"] == "serialization_error"


def test_exactly_once_and_global_scope(tmp_path):
    """No retry or wrapper-local eval scope may alter side-effectful expressions."""
    result = run_wrapper(tmp_path, "globalThis.adapterCount=(globalThis.adapterCount||0)+1; ({count:adapterCount,wrapperLocal:typeof marker})")
    assert result["value"] == {"count": 1, "wrapperLocal": "undefined"}


@pytest.mark.parametrize("response", [{"ok": True, "value": None}, {"ok": True, "value": "not JSON"}, {"ok": True, "value": '{}'}, {"ok": True, "value": '{"marker":"higan.browser_eval.v1","ok":true}'}, {"ok": True, "value": '{"marker":"higan.browser_eval.v1","ok":true,"value_type":[],"value":null}'}, {"ok": True, "value": '{"marker":"higan.browser_eval.v1","ok":false,"error_type":{},"exception":{}}'}, None])
def test_missing_or_invalid_envelope_fails_closed(response):
    """A legacy server's missing CDP value cannot become a successful result."""
    assert decode_eval_response(response)["error_type"] == "evaluation_protocol_error"


def test_transport_error_and_metadata_are_preserved():
    """The adapter keeps transport failures intact and retains frame metadata."""
    failure = {"ok": False, "error": "timeout", "frameId": "a"}
    assert decode_eval_response(failure) == failure
    assert decode_eval_response(failure) is not failure
    raw = json.dumps({"marker": "higan.browser_eval.v1", "ok": True, "value_type": "json", "value": 4})
    assert decode_eval_response({"ok": True, "value": raw, "frameId": "a"})["frameId"] == "a"


@pytest.mark.parametrize("expression", ["", " \n", None, 42])
def test_empty_or_invalid_source_rejected(expression):
    """Invalid caller inputs fail locally before any evaluation request is built."""
    with pytest.raises(ValueError):
        build_eval_wrapper(expression)
