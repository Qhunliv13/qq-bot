#!/usr/bin/env python3
"""Offline, isolated advertised-tool route audit (not an end-to-end capability claim).

Reuse the existing AST production fixture; never import plugin.main or initialize
AstrBot. Actual dispatch, VM request mapping, foreground group-file routing and
provider-loop observations execute. Only external edges are simulated. No VM code,
shell, browser JavaScript, downloads or message delivery is actually executed.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import builtins
from collections import Counter
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import urllib.request

# This command intentionally depends on pytest: it reuses the tested fixture and
# its reversible MonkeyPatch rather than maintaining a second production loader.
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
import test_production_integration as integration  # noqa: E402
from plugin import browser_eval, memory, tasks  # noqa: E402
from plugin.autonomy import AutonomousController  # noqa: E402
from plugin.tool_catalog import NATIVE_TOOL_COUNT  # noqa: E402
from plugin.qq_social import schema as social_schema  # noqa: E402
from plugin.tool_context import redact  # noqa: E402

# Every distinct advertised name must be reviewed here; schema additions cannot
# silently acquire a generic mocked success. Strings resembling code are inert
# payloads only: all VM transports and task execution are blocked before use.
CASES = {
    "autonomy_status": {},
    "autonomy_schedule": {"scope": "42", "interval_seconds": 600, "enabled": True},
    "qq_account_info": {},
    "qq_set_signature": {"signature": "contract signature", "operation_id": "audit-signature"},
    "qq_publish_post": {"content": "contract post", "visibility": "private", "operation_id": "audit-post"},
    "qq_set_profile": {"nickname": "contract fixture", "operation_id": "audit-profile"},
    "qq_social_status": {},
    "web_search": {"query": "contract nonce"},
    "web_fetch": {"url": "https://audit.invalid/page"},
    "tool_observations": {"limit": 8},
    "vm_shot": {}, "vm_browse": {"url": "https://audit.invalid/page"},
    "vm_click_confirm": {}, "vm_click_preview": {"x": 12, "y": 15},
    "vm_click_text": {"text": "contract"}, "vm_click": {"x": 12, "y": 15},
    "vm_type": {"text": "contract"}, "vm_key": {"key": "Return"},
    "vm_job_start": {"code": "# inert audit payload", "lang": "python"},
    "vm_job_status": {"job_id": "01234567-89ab-cdef-0123-456789abcdef"},
    "vm_job_cancel": {"job_id": "01234567-89ab-cdef-0123-456789abcdef"},
    "vm_exec": {"code": "# inert audit payload", "lang": "python"},
    "vm_files": {"dir": "files"}, "vm_recall": {"count": 1},
    "vm_eval": {"js": "null"},
    "vm_slider_gap": {"bg_url": "https://audit.invalid/bg", "piece_url": "https://audit.invalid/piece"},
    "mail_recent": {"limit": 2}, "mail_list": {"limit": 2},
    "mail_web_login": {}, "mail_web_read": {"limit": 20}, "mail_code": {"limit": 2},
    "group_files": {"limit": 2}, "group_file_download": {"id": "file-contract"},
    "group_policy": {"mode": "show", "group": "42"}, "self_status": {},
    "send_sticker": {"file": "contract.png"}, "send_file": {"path": "files/contract.txt"},
    "wake_person_info": {"qq": "10001"},
    "wake_person_set": {"qq": "10001", "pct": 50, "words": ["contract"], "hours": [8, 20]},
    "wake_person_show": {"qq": "10001"}, "wake_list": {"mode": "group", "group": "42"},
    "wake_add": {"qq": "10001", "group": "42"},
    "wake_remove": {"qq": "10001", "group": "42"}, "wake_search": {"query": "contract"},
    "wp_fact_move": {"id": "SEEDED", "qq": "10001"},
    "wp_fact_add": {"qq": "10001", "text": "contract person fact"},
    "wp_fact_list": {"qq": "10001"}, "wp_fact_search": {"qq": "10001", "query": "contract"},
    "wp_fact_delete": {"qq": "10001", "keyword": "contract"},
    "facts_list": {}, "facts_search": {"query": "contract"},
    "facts_edit": {"id": "SEEDED", "text": "contract edited fact"},
    "facts_delete": {"keyword": "contract"}, "remember": {"text": "contract remembered fact"},
    "vm_fetch": {"url": "https://audit.invalid/file", "name": "contract.txt"},
    "send_shot": {}, "qq_send": {"kind": "group", "text": "contract", "group_id": 42},
    "vm_hold_drag": {"x": 12, "y": 15, "distance": 30, "hold_ms": 10},
    "vm_frame_eval": {"js": "null", "match": "contract"},
    "vm_slider_drag": {"x": 12, "y": 15, "distance": 30},
    "task_add": {"title": "contract add", "goal": "inert stored task"},
    "read_forward": {"id": "forward-contract"}, "forward_expand": {"id": "forward-contract"},
    "history_search": {"keyword": "contract", "scope": "42"},
    "task_list": {}, "task_done": {"id": "SEEDED"}, "task_cancel": {"id": "SEEDED"},
    "task_add_script": {"title": "contract scripted", "trigger_script": "# never executed"},
    "task_pause": {"id": "SEEDED"}, "task_resume": {"id": "SEEDED"},
    "task_update": {"id": "SEEDED", "brief": "contract updated"},
}
FOREGROUND = {"group_files", "group_file_download"}
# These expectations are independent of the social router and its schema metadata.
EXPECTED_SOCIAL_ACTIONS = {
    "qq_account_info": ["get_login_info", "get_stranger_info"],
    "qq_set_signature": ["set_self_longnick", "get_login_info", "get_stranger_info"],
    "qq_publish_post": ["send_qzone_msg"],
    "qq_set_profile": ["set_qq_profile"],
    "qq_social_status": [],
}
# Expected boundary contracts are independent of the production mapping AST: a
# typo that routes one valid name to a different valid endpoint must be visible.
EXPECTED_VM_PATHS = {
    "vm_shot": "/vm/shot", "vm_browse": "/vm/browse",
    "vm_click_confirm": "/vm/click_confirm", "vm_click_preview": "/vm/click_preview",
    "vm_click_text": "/vm/click_text", "vm_click": "/vm/click", "vm_type": "/vm/type",
    "vm_key": "/vm/key", "vm_job_start": "/vm/exec", "vm_job_status": "/vm/exec",
    "vm_job_cancel": "/vm/exec", "vm_exec": "/vm/exec", "vm_files": "/vm/files?dir=files",
    "vm_recall": "/vm/qq/recall", "vm_eval": "/vm/eval", "vm_slider_gap": "/vm/slider/gap",
    "mail_recent": "/vm/mail/recent", "mail_list": "/vm/mail/list",
    "mail_web_login": "/vm/mail/web_login", "mail_web_read": "/vm/mail/web_read",
    "mail_code": "/vm/mail/code", "send_file": "/vm/qq/send_file", "vm_fetch": "/vm/download",
    "send_shot": "/vm/qq/send_shot", "vm_hold_drag": "/vm/hold_drag",
    "vm_frame_eval": "/vm/frame_eval", "vm_slider_drag": "/vm/slider/drag",
}


def schema_errors(schema):
    """Validate the supported schema contract recursively without network resolvers."""
    errors = []
    if not isinstance(schema, dict):
        return ["schema_not_object"]
    function = schema.get("function")
    if schema.get("type") != "function" or not isinstance(function, dict):
        return ["not_function_schema"]
    if not isinstance(function.get("name"), str) or not function["name"]:
        errors.append("invalid_name")
    if not isinstance(function.get("description"), str) or not function["description"]:
        errors.append("missing_description")

    def check(node, path):
        """Check nested types, required names and enum shape deterministically."""
        if isinstance(node, dict) and "anyOf" in node:
            # Imported social target IDs deliberately support string/integer unions.
            if not isinstance(node["anyOf"], list) or not node["anyOf"]:
                errors.append(path + ":invalid_any_of")
            else:
                for index, branch in enumerate(node["anyOf"]):
                    check(branch, path + ".anyOf[" + str(index) + "]")
            return
        if not isinstance(node, dict) or node.get("type") not in {
                "object", "array", "string", "integer", "number", "boolean", "null"}:
            errors.append(path + ":invalid_type")
            return
        if "enum" in node and (not isinstance(node["enum"], list) or not node["enum"]):
            errors.append(path + ":invalid_enum")
        if node["type"] == "object":
            props, required = node.get("properties", {}), node.get("required", [])
            if not isinstance(props, dict) or not isinstance(required, list):
                errors.append(path + ":invalid_properties_or_required")
                return
            if any(not isinstance(key, str) or key not in props for key in required):
                errors.append(path + ":required_not_in_properties")
            if len(required) != len(set(str(key) for key in required)):
                errors.append(path + ":duplicate_required")
            for key, value in props.items():
                check(value, path + "." + key)
        if node["type"] == "array" and "items" in node:
            check(node["items"], path + "[]")

    parameters = function.get("parameters", {})
    if not isinstance(parameters, dict) or parameters.get("type") != "object":
        errors.append("parameters_not_object")
    check(parameters, "parameters")
    return errors


def argument_errors(schema, arguments):
    """Check required sample parameters and basic JSON types before executing routes."""
    params = schema["function"]["parameters"]
    errors = ["missing:" + key for key in params.get("required", []) if key not in arguments]
    types = {"string": str, "integer": int, "number": (int, float), "boolean": bool,
             "array": list, "object": dict, "null": type(None)}
    for key, value in arguments.items():
        spec = params.get("properties", {}).get(key)
        if spec is None:
            errors.append("unadvertised_argument:" + key)
        elif not isinstance(value, types.get(spec.get("type"), object)) or (
                spec.get("type") in {"integer", "number"} and isinstance(value, bool)):
            errors.append("wrong_type:" + key)
        elif "enum" in spec and value not in spec["enum"]:
            errors.append("not_in_enum:" + key)
    return errors


def _handler_inventory():
    """Derive native handler/mapping names from unchanged AST, not a duplicate router."""
    tree = ast.parse((ROOT / "plugin" / "main.py").read_text(encoding="utf-8"))
    klass = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "HiganPlugin")
    result = {}
    for method in klass.body:
        if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if method.name == "_dispatch_tool":
            for node in ast.walk(method):
                if isinstance(node, ast.If):
                    tests = node.test.values if isinstance(node.test, ast.BoolOp) else [node.test]
                    for test in tests:
                        if isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) and test.left.id == "name":
                            for value in ast.walk(test.comparators[0]):
                                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                                    result.setdefault(value.value, {"handler": "_dispatch_tool", "branch_line": node.lineno,
                                        "body_lines": [child.lineno for child in node.body]})
        if method.name == "_vm_tool":
            for node in ast.walk(method):
                if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "mapping" for target in node.targets):
                    for key in node.value.keys:
                        result.setdefault(key.value, {"handler": "_dispatch_tool -> _vm_tool", "branch_line": key.lineno})
    for name in FOREGROUND:
        result[name] = {"handler": "_decide_tools_first._execute_tool (OneBot foreground)"}
    # The imported schema owns its dispatcher; the native parent branch is shared.
    social_branch = next(node for node in ast.walk(klass)
                         if isinstance(node, ast.If) and "social_schema" in ast.dump(node.test))
    for item in social_schema():
        result[item["function"]["name"]] = {
            "handler": "_dispatch_tool", "branch_line": social_branch.lineno,
            "body_lines": [child.lineno for child in social_branch.body]}
    return result


class AuditHarness:
    """Own isolated state and reversible edge stubs while retaining production routes."""

    def __init__(self, root, monkeypatch):
        self.root, self.patch = Path(root), monkeypatch
        self.root.mkdir(parents=True, exist_ok=True)
        self.edges, self.provider_replies, self.requests = [], [], []
        self.denied = []
        self.signature = "contract signature"
        self.make_plugin = integration.production.__wrapped__(self.root, monkeypatch)
        self.plugin = self.make_plugin()
        self.controller = AutonomousController(self.root / "autonomy.json")
        self.plugin._autonomy_controller = lambda: self.controller
        namespace = self.plugin._dispatch_tool.__globals__
        namespace.update(BASE_DIR=str(ROOT / "plugin"), STICKER_DIR=self.root / "stickers")
        namespace["open"] = self.safe_open
        self.memory_path = self.root / "memory" / "memory.json"
        self.person_path = self.root / "memory" / "wake_persons.json"
        (self.root / "stickers").mkdir(exist_ok=True)
        (self.root / "stickers" / "contract.png").write_bytes(b"offline fixture, not an image")
        (self.root / "groups.json").write_text(json.dumps({"wake_persons": {"42": ["10001"]},
            "wake_words": {"42": ["contract"]}}), encoding="utf-8")
        self.plugin.manifest = [{"file": "contract.png", "desc": "contract"}]
        self.patch.setattr(tasks, "ROOT", str(self.root / "tasks"))
        self.patch.setattr(tasks, "STORE", str(self.root / "tasks" / "tasks.json"))
        self.plugin._tasks_mod = lambda: tasks
        self.plugin._web_search = self.search
        self.plugin._fetch_page = self.fetch
        self.plugin._onebot_http = self.onebot
        self.plugin._qq_send = self.qq_send
        self.plugin._deliver_media = self.deliver
        self.patch.setattr(urllib.request, "urlopen", self.urlopen)
        self.patch.setattr(socket.socket, "connect", self.forbidden)
        self.patch.setattr(socket, "create_connection", self.forbidden)
        self.patch.setattr(subprocess, "Popen", self.forbidden)
        self.patch.setattr(subprocess, "run", self.forbidden)
        self.patch.setattr(os, "system", self.forbidden)
        self.patch.setattr(tasks, "run_trigger_script", self.forbidden)
        # Only the legacy memory dynamic-import paths are substituted. Other
        # imports (notably agent_loop) still load the existing production source.
        self.original_spec = importlib.util.spec_from_file_location
        self.patch.setattr(importlib.util, "spec_from_file_location", self.memory_spec)

    def forbidden(self, *args, **kwargs):
        """Fail closed at every forbidden process/network boundary, even if caught."""
        self.denied.append("forbidden external execution")
        raise AssertionError("audit forbids real network/process execution")

    def safe_open(self, file, mode="r", *args, **kwargs):
        """Redirect legacy hardcoded state files, denying other non-temp data reads."""
        text = str(file).replace("\\", "/")
        if text == "/proc/uptime":
            return io.StringIO("123.0 0.0")
        if text.startswith("/root/higan/"):
            file = self.root / text.removeprefix("/root/higan/")
        path = Path(file).resolve()
        if not path.is_relative_to(self.root.resolve()):
            self.denied.append("non-temp data file")
            raise AssertionError("non-temporary state file access forbidden")
        return builtins.open(path, mode, *args, **kwargs)

    def memory_spec(self, name, location, *args, **kwargs):
        """Substitute only memory constructors; real stores receive temp paths only."""
        if str(location).replace("\\", "/").endswith("/memory.py"):
            harness = self

            class Loader:
                """Provide path-bound real memory classes without loading legacy paths."""

                def create_module(self, spec):
                    """Let importlib allocate an ordinary temporary module."""
                    return None

                def exec_module(self, module):
                    """Ignore legacy constructor paths before any real I/O occurs."""
                    module.Memory = lambda path: memory.Memory(str(harness.memory_path))
                    module.WakePersonFacts = lambda path: memory.WakePersonFacts(str(harness.person_path))

            return importlib.util.spec_from_loader(name, Loader())
        return self.original_spec(name, location, *args, **kwargs)

    def search(self, query):
        """Return simulated public evidence at the search boundary only."""
        self.edges.append({"edge": "web_search", "query": query})
        return [{"title": "contract nonce", "url": "https://audit.invalid/page", "snippet": "offline evidence"}]

    def fetch(self, url):
        """Return local text without resolving or opening a URL."""
        self.edges.append({"edge": "web_fetch", "url": url})
        return "contract nonce offline page"

    def onebot(self, action, params):
        """Represent the synchronous OneBot edge without a socket."""
        self.edges.append({"edge": "onebot_http", "action": action, "params": copy.deepcopy(params)})
        if action == "set_self_longnick":
            self.signature = params["longNick"]
        social_data = {
            "get_login_info": {"user_id": 90001, "nickname": "fixture"},
            "get_stranger_info": {"long_nick": self.signature},
            "set_self_longnick": None, "set_qq_profile": None,
            "send_qzone_msg": {"tid": "offline-contract-post"},
        }
        if action in social_data:
            return {"status": "ok", "retcode": 0, "data": copy.deepcopy(social_data[action])}
        return {"messages": [{"sender": {"nickname": "fixture"}, "message": [{"type": "text", "data": {"text": "contract"}}]}]}

    async def qq_send(self, *args):
        """Record intended delivery only, never contact a messaging platform."""
        self.edges.append({"edge": "qq_send"})
        return {"ok": True, "simulated": True, "message_id": "offline-contract"}

    async def deliver(self, *args):
        """Acknowledge the stubbed media edge without opening attachment bytes."""
        self.edges.append({"edge": "media_delivery"})
        return True

    def urlopen(self, request, timeout=None):
        """Simulate provider and VM HTTP responses; reject every other endpoint."""
        url = request if isinstance(request, str) else request.full_url
        if url == "https://offline.invalid/v1/chat/completions":
            self.requests.append(json.loads(request.data.decode("utf-8")))
            if not self.provider_replies:
                raise AssertionError("unexpected provider continuation")
            text, calls = self.provider_replies.pop(0)
            result = {"choices": [{"message": {"content": text, "tool_calls": calls}}]}
        elif url.startswith("http://127.0.0.1:18003/vm/"):
            self.edges.append({"edge": "vm_http_stub", "url": url,
                "method": "GET" if isinstance(request, str) else request.get_method()})
            result = {"ok": True, "simulated": True, "output": json.dumps({"ok": True, "simulated": True, "job_id": "offline-job"})}
            if url.endswith(("/eval", "/frame_eval")):
                result["value"] = json.dumps({"marker": browser_eval._ENVELOPE_MARKER,
                    "ok": True, "value_type": "json", "value": "offline-evaluation"})
        else:
            self.denied.append("unapproved HTTP endpoint")
            raise AssertionError("unapproved audit URL")
        return io.BytesIO(json.dumps(result).encode("utf-8"))

    def prepare(self, name):
        """Seed independent local state so mutation routes do not depend on tool order."""
        args = copy.deepcopy(CASES[name])
        store = memory.Memory(str(self.memory_path)).load()
        store.add_fact("contract seeded fact", "audit")
        fact_id = store.list_facts()[0]["id"]
        memory.WakePersonFacts(str(self.person_path)).load().add("10001", "contract seeded person")
        if name in {"facts_edit", "wp_fact_move"}:
            args["id"] = str(fact_id)
        if name in {"task_done", "task_cancel", "task_pause", "task_resume", "task_update"}:
            task = tasks.add("seed " + name, "inert", "42")
            if name == "task_resume":
                tasks.set_status(task["id"], "", "paused", "audit fixture")
            args["id"] = task["id"]
        event = integration.Event("contract audit")
        # Identity is supplied only by the trusted fixture event, never tool arguments.
        # This source owner ID remains independent of the production OWNER_QQS set.
        if name in {"qq_set_signature", "qq_publish_post", "qq_set_profile", "autonomy_schedule"}:
            event.get_sender_id = lambda: "2084526117"
        event.action_results = {
            "get_group_root_files": {"files": [{"file_name": "contract-nonce.txt", "file_id": "file-contract", "file_size": 10}], "folders": []},
            "get_group_file_url": {"url": "https://audit.invalid/file"},
            "get_forward_msg": self.onebot("fixture", {}),
            # Social handlers require full native envelopes, not data-only stubs.
            "get_login_info": {"status": "ok", "retcode": 0, "data": {"user_id": 90001, "nickname": "fixture"}},
            "get_stranger_info": {"status": "ok", "retcode": 0, "data": {"long_nick": "contract signature"}},
            "set_self_longnick": {"status": "ok", "retcode": 0, "data": None},
            "set_qq_profile": {"status": "ok", "retcode": 0, "data": None},
            "send_qzone_msg": {"status": "ok", "retcode": 0, "data": {"tid": "offline-contract-post"}},
        }
        return args, event


def _quality(result):
    """Describe structure, never interpret stubbed success as a real side effect."""
    try:
        json.dumps(result, allow_nan=False)
        serializable = True
    except (TypeError, ValueError):
        serializable = False
    return {"object": isinstance(result, dict), "json_serializable": serializable,
            "boolean_ok": isinstance(result, dict) and isinstance(result.get("ok"), bool),
            "keys": sorted(result) if isinstance(result, dict) else [],
            "ok": result.get("ok") if isinstance(result, dict) else None,
            "error": result.get("error") if isinstance(result, dict) else None}


def audit(root, monkeypatch, batch_size=30):
    """Create asyncio infrastructure before fail-closed socket guards are installed."""
    if not 1 <= batch_size <= 100:
        raise ValueError("batch_size must be between 1 and 100")
    return asyncio.run(_audit(root, monkeypatch, batch_size))


async def _audit(root, monkeypatch, batch_size):
    """Execute every advertised name and verify native result IDs/retention in batches."""
    harness = AuditHarness(root, monkeypatch)
    raw = harness.plugin._tool_schemas()
    namespace = harness.plugin._dispatch_tool.__globals__
    effective = namespace["_dedupe_tool_schemas"](raw)
    counts = Counter(schema["function"]["name"] for schema in raw)
    inventory = _handler_inventory()
    names = [schema["function"]["name"] for schema in effective]
    report = {"scope": "isolated executed-route contracts; external effects simulated, not validated",
        "canonical_schema_count": NATIVE_TOOL_COUNT,
        "raw_schema_count": len(raw), "effective_schema_count": len(effective),
        "duplicate_schema_names": {name: count for name, count in counts.items() if count > 1},
        "unmapped_cases": sorted(set(names) - CASES.keys()), "stale_cases": sorted(CASES.keys() - set(names)),
        "schema_errors": [{"index": i, "name": schema.get("function", {}).get("name"), "errors": schema_errors(schema)}
                          for i, schema in enumerate(raw) if schema_errors(schema)],
        "tools": [], "provider_batches": [], "gaps": []}
    schemas = {schema["function"]["name"]: schema for schema in effective}
    # Execute the unmodified dispatcher, including real VM name->endpoint mapping.
    # sys.settrace observes executed route lines but does not replace any handler.
    for name in names:
        entry = {"name": name, "schema_occurrences": counts[name], "handler": inventory.get(name),
                 "executed": False, "exception": None}
        report["tools"].append(entry)
        if name not in CASES:
            continue
        args, event = harness.prepare(name)
        entry["argument_errors"] = argument_errors(schemas[name], args)
        start = len(harness.edges)
        executed_lines = set()

        def trace(frame, action, arg):
            """Record only real dispatcher/VM source lines for this one invocation."""
            if action == "line" and frame.f_code.co_name in {"_dispatch_tool", "_vm_tool", "_execute_tool"}:
                executed_lines.add((frame.f_code.co_name, frame.f_lineno))
            return trace

        previous = sys.gettrace()
        try:
            sys.settrace(trace)
            if name in FOREGROUND:
                harness.provider_replies = [("", [integration._call(name, args, "route-" + name)]), ("Audit complete.", [])]
                await harness.plugin._decide_tools_first(event, "42", event.text, False)
                observed = harness.plugin._tool_evidence().recent("42", 1)
                result = observed[-1]["result"] if observed else None
            else:
                result = await harness.plugin._dispatch_tool(name, args, "42", event=event)
            entry["executed"] = True
            # Grade the actual receipt before redaction, retaining no native secrets.
            entry["result_quality"] = _quality(result)
            entry["result"] = redact(result)
        except Exception as exc:
            entry["exception"] = {"type": type(exc).__name__, "message": str(exc)[:240]}
        finally:
            sys.settrace(previous)
        entry["executed_source_lines"] = [{"function": function, "line": line} for function, line in sorted(executed_lines)]
        entry["stubbed_edges"] = harness.edges[start:]
        entry["onebot_actions"] = [action for action, _ in event.actions]
        if name in EXPECTED_SOCIAL_ACTIONS:
            native_edges = [edge for edge in entry["stubbed_edges"] if edge["edge"] == "onebot_http"]
            entry["boundary_contract_matches"] = [edge["action"] for edge in native_edges] == EXPECTED_SOCIAL_ACTIONS[name]
            if name == "qq_set_signature":
                entry["boundary_contract_matches"] = entry["boundary_contract_matches"] and native_edges[0]["params"] == {"longNick": args["signature"]} and result.get("verification") == {"state": "matched", "matched": True}
            elif name == "qq_publish_post":
                entry["boundary_contract_matches"] = entry["boundary_contract_matches"] and native_edges[0]["params"] == {"content": args["content"], "ugc_right": 64} and result.get("outcome") == "accepted" and result.get("tid") == "offline-contract-post"
            elif name == "qq_set_profile":
                entry["boundary_contract_matches"] = entry["boundary_contract_matches"] and native_edges[0]["params"] == {"nickname": args["nickname"]} and result.get("outcome") == "accepted"
        elif name in EXPECTED_VM_PATHS:
            actual_paths = [edge["url"].removeprefix("http://127.0.0.1:18003")
                            for edge in entry["stubbed_edges"] if edge["edge"] == "vm_http_stub"]
            entry["boundary_contract_matches"] = actual_paths == [EXPECTED_VM_PATHS[name]]
        elif name in FOREGROUND:
            expected_action = "get_group_root_files" if name == "group_files" else "get_group_file_url"
            entry["boundary_contract_matches"] = entry["onebot_actions"] == [expected_action]
        else:
            entry["boundary_contract_matches"] = True
        handler = inventory.get(name) or {}
        if handler.get("handler") == "_dispatch_tool":
            entry["handler_body_executed"] = any(("_dispatch_tool", line) in executed_lines for line in handler["body_lines"])
        elif name in FOREGROUND:
            entry["handler_body_executed"] = bool(event.actions)
        else:
            entry["handler_body_executed"] = any(edge["edge"] == "vm_http_stub" for edge in entry["stubbed_edges"])
    # Each native provider batch starts a fresh process-like plugin/archive scope;
    # the <=100 archive retention bound cannot silently hide early observations.
    for offset in range(0, len(names), batch_size):
        batch = [name for name in names[offset:offset + batch_size] if name in CASES]
        plugin = harness.make_plugin()
        for attribute in ("manifest", "_autonomy_controller", "_tasks_mod", "_web_search", "_fetch_page", "_onebot_http", "_qq_send", "_deliver_media"):
            setattr(plugin, attribute, getattr(harness.plugin, attribute))
        scope = str(4200 + offset)
        calls = [integration._call(name, harness.prepare(name)[0], "batch-%s-%s" % (offset, name)) for name in batch]
        event = harness.prepare("group_files")[1]
        # The mixed batch contains owner-only mutations; use a genuine event identity.
        event.get_sender_id = lambda: "2084526117"
        event.group = scope
        harness.provider_replies = [("discard tool draft", calls), ("Audit complete.", [])]
        request_start = len(harness.requests)
        exception = None
        try:
            answer = await plugin._decide_tools_first(event, scope, event.text, False)
        except Exception as exc:
            answer, exception = None, type(exc).__name__ + ": " + str(exc)[:160]
        requests = harness.requests[request_start:]
        paired = [message for message in (requests[-1]["messages"] if requests else []) if message["role"] == "tool"]
        observations = plugin._tool_evidence().recent(scope, 100)
        expected = [call["id"] for call in calls]
        report["provider_batches"].append({"names": batch, "call_count": len(calls), "exception": exception,
            "final_answer": answer, "provider_requests": len(requests),
            "paired_result_ids": [message["tool_call_id"] for message in paired],
            "exact_id_pairing": [message["tool_call_id"] for message in paired] == expected,
            "retained_observation_names": [row["tool_name"] for row in observations],
            "all_observations_retained": [row["tool_name"] for row in observations] == batch,
            "paired_results_match_archive": [json.loads(message["content"])["result"] for message in paired] == [row["result"] for row in observations]})
    unknown = await harness.plugin._dispatch_tool("__contract_unknown_tool__", {}, "42")
    report["unknown_tool"] = {"result": unknown, "rejected": unknown.get("ok") is False and "unknown tool" in unknown.get("error", "")}
    report["forbidden_io_attempts"] = harness.denied
    for entry in report["tools"]:
        quality = entry.get("result_quality", {})
        if not entry["executed"] or entry["exception"] or not entry.get("handler_body_executed") or not entry.get("boundary_contract_matches") or entry.get("argument_errors") or not all(quality.get(key) for key in ("object", "json_serializable", "boolean_ok")) or quality.get("ok") is not True:
            report["gaps"].append({"name": entry["name"], "reason": "route, fixture arguments, exception or result quality failed"})
    report["route_contracts_pass"] = not (report["gaps"] or report["unmapped_cases"] or report["stale_cases"] or report["schema_errors"] or harness.denied) and report["unknown_tool"]["rejected"] and all(
        batch["exact_id_pairing"] and batch["all_observations_retained"] and batch["paired_results_match_archive"] and not batch["exception"] for batch in report["provider_batches"])
    report["raw_schemas_unique"] = not report["duplicate_schema_names"]
    return report


def main(argv=None):
    """Print JSON by default; an optional output must be an explicitly chosen file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional local JSON report path (default: stdout)")
    parser.add_argument("--batch-size", type=int, default=30)
    options = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="higan-tool-contract-") as directory, pytest.MonkeyPatch.context() as patch:
        report = audit(Path(directory), patch, options.batch_size)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if options.output:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0 if report["route_contracts_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
