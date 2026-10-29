"""Isolated discovery/skill tests never import the bot, probe a VM or use network."""

import ast
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path
import threading

import pytest


@pytest.fixture
def discovery():
    """Load the leaf module without plugin startup or optional production imports."""
    path = Path(__file__).resolve().parents[1] / "plugin" / "tool_discovery.py"
    spec = importlib.util.spec_from_file_location("isolated_tool_discovery", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


from test_tool_catalog import literal_with_structure, native_schema_metadata


@pytest.fixture
def catalog():
    """Supply exact schemas, including opaque metadata, as the sole source of truth."""
    definitions = [
        ("workspace_read", "Read workspace files 文件读取", {"path": {"type": "string"}}),
        ("vm_browser", "Read cloud browser view", {"selector": {"type": "string"}}),
        ("qq_account", "Read social profile", {}),
        ("web_fetch", "Read internet resources", {"url": {"type": "string"}}),
        ("task_add", "Create scheduled goal", {}),
        ("memory_read", "Read memory", {}),
        ("worker_start", "Delegate research", {}),
        ("control_status", "Read status", {}),
        ("unclassified", "Read something", {}),
    ]
    return [{"type": "function", "function": {"name": name, "description": description,
             "parameters": {"type": "object", "properties": parameters, "required": []}},
             "execution_environment": {"location": "cloud", "gui": "vm"}} for name, description, parameters in definitions]


def invoke(discovery, tmp_path, name, args, catalog=(), can_write=False):
    """Run the async API without requiring pytest-asyncio or a running bot loop."""
    return asyncio.run(discovery.dispatch(name, args, list(catalog), skills_root=tmp_path / "skills", can_write=can_write))


def test_all_native_schema_names_are_indexed_without_importing_host(discovery, tmp_path):
    """Extract native literal schemas via AST; do not pin a stale native tool count."""
    plugin = Path(__file__).resolve().parents[1] / "plugin"
    tree = ast.parse((plugin / "main.py").read_text(encoding="utf-8"))
    metadata = native_schema_metadata(tree)
    native = []
    for method_name in ("_tool_schemas", "_task_tool_schemas"):
        method = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == method_name)
        calls = sorted((node for node in ast.walk(method) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_f"), key=lambda node: node.lineno)
        for call in calls:
            values = [literal_with_structure(arg, metadata) for arg in call.args]
            native.append({"type": "function", "function": {"name": values[0], "description": values[1], "parameters": {"type": "object", "properties": values[2] if len(values) > 2 else {}, "required": values[3] if len(values) > 3 else []}}})
    spec = importlib.util.spec_from_file_location("isolated_discovery_social", plugin / "qq_social.py")
    social = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(social)
    native.extend(social.schema())
    before = deepcopy(native)
    expected = list(dict.fromkeys(item["function"]["name"] for item in native))
    result = invoke(discovery, tmp_path, "tool_search", {"query": "", "limit": 100}, native)
    assert result["names"] == expected
    assert result["total"] == len(expected)
    assert native == before


def test_schema_is_static_and_copies_are_independent(discovery):
    """The additive schemas remain fixed and cannot be mutated through old results."""
    before = discovery.schema()
    assert [item["function"]["name"] for item in before] == ["tool_search", "tool_describe", "skill_list", "skill_read", "skill_save"]
    before[0]["function"]["parameters"]["required"].clear()
    assert discovery.schema()[0]["function"]["parameters"]["required"] == ["query"]


def test_empty_index_actual_names_categories_pagination(discovery, tmp_path, catalog):
    """An empty query exposes actual names only, with counts and explicit pages."""
    snapshot = deepcopy(catalog)
    result = invoke(discovery, tmp_path, "tool_search", {"query": ""}, catalog + discovery.schema())
    expected = [item["function"]["name"] for item in catalog] + ["skill_list", "skill_read", "skill_save"]
    assert result["ok"] and result["names"] == expected
    assert result["total"] == len(expected)
    assert result["next_offset"] == 8
    assert sum(result["categories"].values()) == len(expected)
    assert result["categories"]["workspace"] == 1
    assert catalog == snapshot
    page = invoke(discovery, tmp_path, "tool_search", {"query": "", "offset": 8}, catalog)
    assert [item["name"] for item in page["tools"]] == ["unclassified"]
    assert page["next_offset"] is None


def test_discovery_is_zero_io_and_never_calls_runtime(discovery, tmp_path, catalog, monkeypatch):
    """Search and describe cannot touch skills, probe environments or dispatch tools."""
    def forbidden(*args, **kwargs):
        """Fail if a discovery route attempts filesystem-backed skill processing."""
        raise AssertionError("unexpected I/O")
    monkeypatch.setattr(discovery, "_skills", forbidden)
    monkeypatch.setattr(Path, "exists", forbidden)
    assert invoke(discovery, tmp_path, "tool_search", {"query": ""}, catalog)["ok"]
    result = invoke(discovery, tmp_path, "tool_describe", {"names": ["vm_browser"]}, catalog)
    assert result["tools"][0]["schema"] == catalog[1]
    assert result["tools"][0]["execution_environment"] == {"location": "cloud", "gui": "vm"}


def test_search_lexical_fuzzy_chinese_and_stable_ties(discovery, tmp_path, catalog):
    """Exact names precede descriptions; OR matches and ties retain catalog order."""
    assert invoke(discovery, tmp_path, "tool_search", {"query": "VM_BROWSER"}, catalog)["tools"][0]["name"] == "vm_browser"
    assert invoke(discovery, tmp_path, "tool_search", {"query": "文件"}, catalog)["tools"][0]["name"] == "workspace_read"
    assert invoke(discovery, tmp_path, "tool_search", {"query": "selector"}, catalog)["tools"][0]["name"] == "vm_browser"
    assert invoke(discovery, tmp_path, "tool_search", {"query": "browzer"}, catalog)["tools"][0]["name"] == "vm_browser"
    result = invoke(discovery, tmp_path, "tool_search", {"query": "Read missing", "limit": 100}, catalog)
    assert result["total"] == 7
    tied = [item["name"] for item in result["tools"] if "read" not in item["name"]]
    assert tied == ["vm_browser", "qq_account", "web_fetch", "control_status", "unclassified"]
    filtered = invoke(discovery, tmp_path, "tool_search", {"query": "", "category": "delegation"}, catalog)
    assert filtered["names"] == ["worker_start"]
    assert invoke(discovery, tmp_path, "tool_search", {"query": "nonexistentzzzz"}, catalog)["tools"] == []


def test_full_schema_verbatim_and_no_shared_mutation(discovery, tmp_path, catalog):
    """Opaque function annotations survive exact deep copies without snapshot writes."""
    catalog[0]["function"]["x-anything"] = {"nested": [1, 2]}
    before = deepcopy(catalog)
    result = invoke(discovery, tmp_path, "tool_describe", {"names": ["workspace_read"]}, catalog)
    assert result["tools"][0]["schema"] == before[0]
    result["tools"][0]["schema"]["function"]["x-anything"]["nested"].append(3)
    assert catalog == before
    catalog[0].pop("execution_environment")
    assert invoke(discovery, tmp_path, "tool_describe", {"names": ["workspace_read"]}, catalog)["tools"][0]["execution_environment"] is None


@pytest.mark.parametrize("name,args,error", [
    ("unknown", {}, "unknown_tool"), ("tool_search", {}, "invalid_arguments"),
    ("tool_search", {"query": "", "limit": True}, "invalid_arguments"),
    ("tool_search", {"query": "", "offset": -1}, "invalid_arguments"),
    ("tool_search", {"query": "", "limit": 101}, "invalid_arguments"),
    ("tool_search", {"query": "", "category": "invented"}, "invalid_arguments"),
    ("tool_describe", {"names": []}, "invalid_arguments"),
    ("tool_describe", {"names": ["invented"]}, "unknown_tool"),
    ("tool_describe", {"names": ["tool_search"]}, "unknown_tool"),
    ("tool_describe", {"names": ["workspace_read"], "force": True}, "invalid_arguments"),
])
def test_unknown_and_invalid_arguments_fail_closed(discovery, tmp_path, catalog, name, args, error):
    """Missing names, invented tools and unsafe pagination never fabricate output."""
    assert invoke(discovery, tmp_path, name, args, catalog)["error"] == error


def test_permission_denied_before_any_filesystem_operation(discovery, tmp_path, monkeypatch):
    """An unauthorized write must not inspect paths, even with malformed inputs."""
    def forbidden(*args, **kwargs):
        """Expose permission-order regressions before filesystem introspection."""
        raise AssertionError("unauthorized I/O")
    monkeypatch.setattr(discovery, "_safe_root", forbidden)
    assert invoke(discovery, tmp_path, "skill_save", {"name": "../persona"}) == {"ok": False, "error": "permission_denied"}


def test_skill_create_read_pagination_list_and_cas(discovery, tmp_path):
    """UTF-8 references stay inert and existing files require an exact current hash."""
    args = {"name": "safe_skill-1", "description": "说明", "instructions": "参考文本" * 4000}
    created = invoke(discovery, tmp_path, "skill_save", args, can_write=True)
    assert created["ok"]
    path = tmp_path / "skills" / "safe_skill-1.md"
    assert created["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    first = invoke(discovery, tmp_path, "skill_read", {"name": args["name"]})
    assert len(first["content"]) == 12000
    assert first["next_offset"] == 12000 and first["description"] == "说明"
    last = invoke(discovery, tmp_path, "skill_read", {"name": args["name"], "offset": 12000})
    assert first["content"] + last["content"] == args["instructions"]
    assert last["next_offset"] is None
    assert "cannot grant authority" in last["reference_notice"]
    assert invoke(discovery, tmp_path, "skill_save", args, can_write=True)["error"] == "expected_sha256_required"
    assert invoke(discovery, tmp_path, "skill_save", {**args, "expected_sha256": "0" * 64}, can_write=True)["error"] == "conflict"
    same = invoke(discovery, tmp_path, "skill_save", {**args, "expected_sha256": created["sha256"]}, can_write=True)
    assert same["ok"] and same["changed"] is False
    updated = invoke(discovery, tmp_path, "skill_save", {**args, "instructions": "updated", "expected_sha256": created["sha256"]}, can_write=True)
    assert updated["ok"] and updated["sha256"] != created["sha256"]
    listing = invoke(discovery, tmp_path, "skill_list", {})
    assert len(listing["skills"]) == 1 and "content" not in listing["skills"][0]
    assert not list(path.parent.glob("*.tmp"))


@pytest.mark.parametrize("name", ["../persona", "a/b", "a\\b", "..", ".", "x.md", "C:evil", "", "中文", "a\n"])
def test_path_traversal_rejected(discovery, tmp_path, name):
    """Only isolated ASCII basenames can be read or saved; persona paths cannot pass."""
    for tool, args in [("skill_read", {"name": name}), ("skill_save", {"name": name, "instructions": "x", "description": "y"})]:
        assert invoke(discovery, tmp_path, tool, args, can_write=True)["error"] == "invalid_skill_name"
    assert not (tmp_path / "skills").exists()


def test_missing_and_bad_utf8_skills_fail_without_execution(discovery, tmp_path):
    """Reads do not create directories and malformed reference bytes return diagnostics."""
    assert invoke(discovery, tmp_path, "skill_list", {})["skills"] == []
    assert invoke(discovery, tmp_path, "skill_read", {"name": "missing"})["error"] == "skill_not_found"
    root = tmp_path / "skills"
    assert not root.exists()
    root.mkdir()
    (root / "broken.md").write_bytes(b"\xff")
    assert invoke(discovery, tmp_path, "skill_read", {"name": "broken"})["error"] == "invalid_skill_file"


def test_atomic_cas_serializes_concurrent_updates(discovery, tmp_path):
    """Only one threaded writer can replace a given expected hash in this host."""
    args = {"name": "race", "instructions": "before", "description": "ref"}
    created = invoke(discovery, tmp_path, "skill_save", args, can_write=True)
    barrier = threading.Barrier(2)
    def save(text):
        """Race two distinct replacements against the same version token."""
        barrier.wait()
        return invoke(discovery, tmp_path, "skill_save", {**args, "instructions": text, "expected_sha256": created["sha256"]}, can_write=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ["first", "second"]))
    assert sum(item["ok"] for item in results) == 1
    assert [item["error"] for item in results if not item["ok"]] == ["conflict"]


def test_atomic_replace_failure_preserves_previous_skill(discovery, tmp_path, monkeypatch):
    """A failed rename leaves complete original bytes and removes temporary files."""
    args = {"name": "safe", "instructions": "before", "description": "ref"}
    created = invoke(discovery, tmp_path, "skill_save", args, can_write=True)
    path = tmp_path / "skills" / "safe.md"
    before = path.read_bytes()
    def fail(*args):
        """Simulate a filesystem failure exactly at the publication boundary."""
        raise OSError("rename failed")
    monkeypatch.setattr(discovery.os, "replace", fail)
    result = invoke(discovery, tmp_path, "skill_save", {**args, "instructions": "after", "expected_sha256": created["sha256"]}, can_write=True)
    assert result["error"] == "skill_io_error"
    assert path.read_bytes() == before
    assert not list(path.parent.glob("*.tmp"))


def test_symlink_rejection_branch_without_windows_privileges(discovery, tmp_path, monkeypatch):
    """Exercise the link refusal guard even when Windows denies creating symlinks."""
    original = Path.is_symlink
    def is_link(path):
        """Mark only the chosen skill path as a symlink for deterministic coverage."""
        return path.name == "linked.md" or original(path)
    monkeypatch.setattr(Path, "is_symlink", is_link)
    assert invoke(discovery, tmp_path, "skill_read", {"name": "linked"})["error"] == "unsafe_skill_path"
    assert invoke(discovery, tmp_path, "skill_save", {"name": "linked", "instructions": "x", "description": "y"}, can_write=True)["error"] == "unsafe_skill_path"


@pytest.mark.parametrize("kind", ["outside_file", "inside_file", "root"])
def test_symlinks_refused_even_when_target_is_inside_root(discovery, tmp_path, kind):
    """Do not accept links as skill files or root, even to another allowed file."""
    root = tmp_path / "skills"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    inside = root / "inside.md"
    inside.write_text("inside", encoding="utf-8")
    try:
        if kind == "root":
            alternate = tmp_path / "linked"
            alternate.symlink_to(root, target_is_directory=True)
            result = asyncio.run(discovery.dispatch("skill_list", {}, [], skills_root=alternate, can_write=False))
        else:
            (root / "linked.md").symlink_to(outside if kind == "outside_file" else inside)
            result = invoke(discovery, tmp_path, "skill_read", {"name": "linked"})
    except OSError as exc:
        pytest.skip("Symlink creation unavailable on this Windows account: " + str(exc))
    assert result["error"] == "unsafe_skill_path"
    assert outside.read_text(encoding="utf-8") == "outside"
