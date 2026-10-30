"""Exercise the actual recorder factory without loading framework or live state."""
import ast
from pathlib import Path
from types import SimpleNamespace

from plugin.history import Recorder


def test_plugin_factory_reuses_normal_module_and_process_lock(tmp_path):
    """Plugin reloads cannot create private copies of the shared history append lock."""
    source = Path(__file__).resolve().parents[1] / "plugin" / "main.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    plugin = next(item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == "HiganPlugin")
    factory = next(item for item in plugin.body if isinstance(item, ast.FunctionDef) and item.name == "_hist")
    module = ast.Module(body=[factory], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"__package__": "plugin", "Path": Path, "HIGAN_ROOT": str(tmp_path)}
    exec(compile(module, str(source), "exec"), namespace)
    first, second = SimpleNamespace(), SimpleNamespace()
    one = namespace["_hist"](first)
    two = namespace["_hist"](second)
    assert type(one) is type(two) is Recorder
    assert namespace["_hist"](first) is one
    assert one.append_checked("42", "one", record={"id": "1"})["duplicate"] is False
    assert two.append_checked("42", "again", record={"id": "1"})["duplicate"] is True
    assert len(two.load_records("42", None)) == 1
