#!/usr/bin/env python3
"""Offline, isolated advertised-tool route audit (not an end-to-end capability claim).

Reuse the existing AST production fixture; never import plugin.main or initialize
AstrBot. Actual dispatch, VM request mapping, foreground group-file routing and
provider-loop observations execute. Workspace Python helpers execute locally only
under a temporary runtime root. Other VM code, shell, browser JavaScript, downloads
and message delivery remain simulated; no production endpoints are contacted.
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
import html
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import urllib.request
from urllib.parse import parse_qs, urlsplit

# This command intentionally depends on pytest: it reuses the tested fixture and
# its reversible MonkeyPatch rather than maintaining a second production loader.
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
import test_production_integration as integration  # noqa: E402
from plugin import browser_eval, memory, tasks, workspace_tools, tool_discovery, delegation, task_planning, image_search, qq_native_actions, qq_requests  # noqa: E402
import base64
import hashlib
from plugin.autonomy import AutonomousController  # noqa: E402
from plugin.tool_catalog import NATIVE_TOOL_COUNT  # noqa: E402
from plugin.qq_social import schema as social_schema  # noqa: E402
from plugin.tool_context import redact  # noqa: E402
from plugin.message_batch import message_record  # noqa: E402

# Every distinct advertised name must be reviewed here; schema additions cannot
# silently acquire a generic mocked success. Strings resembling code are inert
# payloads only except exact generated workspace helpers: other VM execution and
# task-trigger execution remain blocked or explicitly simulated at the transport.
CASES = {
    "workspace_read": {"path": "contract.txt"},
    "workspace_write": {"path": "contract.txt", "content": "contract written\n"},
    "workspace_edit": {"path": "contract.txt", "old_string": "baseline", "new_string": "edited", "expected_sha256": "SEEDED"},
    "workspace_glob": {"pattern": "*.txt"},
    "workspace_grep": {"pattern": "contract", "include": "*.txt"},
    "workspace_cwd": {},
    "workspace_present": {"paths": ["contract.txt"]},
    "workspace_read_image": {"path": "contract.png"},
    "tool_search": {"query": "vm_shot"},
    "tool_describe": {"names": ["vm_shot"]},
    "skill_list": {}, "skill_read": {"name": "contract"},
    "skill_save": {"name": "contract-save", "instructions": "Offline reference.", "description": "Contract reference"},
    "agent_start": {"task": "Offline contract research", "label": "contract"},
    "agent_status": {"id": "SEEDED"}, "agent_list": {},
    "agent_message": {"id": "SEEDED", "message": "Offline update"},
    "agent_interrupt": {"id": "SEEDED"},
    "task_plan_read": {"task_id": "SEEDED"},
    "task_plan_update": {"task_id": "SEEDED", "expected_revision": 0,
                         "steps": [{"id": "step1", "content": "Contract work", "status": "in_progress"}]},
    "vm_job_list": {},
    # Plain ASCII avoids marker/media side paths while exercising the real sender.
    "reply_now": {"text": "Contract interim reply.", "reply_to": ["1234"]},
    "autonomy_status": {},
    "autonomy_schedule": {"scope": "42", "interval_seconds": 600, "enabled": True},
    "qq_account_info": {},
    "qq_set_signature": {"signature": "contract signature", "operation_id": "audit-signature"},
    "qq_publish_post": {"content": "contract post", "visibility": "private", "operation_id": "audit-post"},
    "qq_set_profile": {"nickname": "contract fixture", "operation_id": "audit-profile"},
    "qq_social_status": {},
    "qq_request_capabilities": {},
    "qq_requests": {"kind": "all", "limit": 50},
    "qq_request_decide": {"request_id": "SEEDED", "approve": True, "operation_id": "SEEDED"},
    "qq_join_group": {"group_id": 42, "message": "offline contract"},
    "qq_answer_group_question": {"request_id": "offline-request", "answer": "offline answer"},
    "qq_add_friend": {"user_id": 10001, "comment": "offline contract"},
    # Generic non-topic-specific input checks query forwarding, not semantic relevance.
    "image_search": {"query": "contract image nonce", "limit": 1},
    "page_images": {"url": "https://example.org/article", "limit": 1},
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
# These three unavailable capabilities are expected negative receipts, not route gaps.
EXPECTED_UNAVAILABLE = {
    "qq_join_group": "proactive_group_join",
    "qq_answer_group_question": "group_join_question_answer",
    "qq_add_friend": "proactive_friend_add",
}
REQUEST_NAMES = {"qq_request_capabilities", "qq_requests", "qq_request_decide", *EXPECTED_UNAVAILABLE}
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
    "vm_job_list": "/vm/exec",
    "vm_key": "/vm/key", "vm_job_start": "/vm/exec", "vm_job_status": "/vm/exec",
    "vm_job_cancel": "/vm/exec", "vm_exec": "/vm/exec", "vm_files": "/vm/files?dir=files",
    "vm_eval": "/vm/eval", "vm_slider_gap": "/vm/slider/gap",
    "mail_recent": "/vm/mail/recent", "mail_list": "/vm/mail/list",
    "mail_web_login": "/vm/mail/web_login", "mail_web_read": "/vm/mail/web_read",
    "mail_code": "/vm/mail/code", "vm_fetch": "/vm/download",
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
    # Imported routers are observed at the actual native membership branch.
    for builder, marker in ((workspace_tools.schema, "workspace_schema"),
                            (tool_discovery.schema, "discovery_schema"),
                            (delegation.schema, "delegation_schema"),
                            (task_planning.schema, "planning_schema"),
                            (qq_requests.schema, "request_schema")):
        branch = next(node for node in ast.walk(klass)
                      if isinstance(node, ast.If) and marker in ast.dump(node.test))
        for item in builder():
            result[item["function"]["name"]] = {
                "handler": "_dispatch_tool", "branch_line": branch.lineno,
                "body_lines": [child.lineno for child in branch.body]}
    return result


class AuditHarness:
    """Own isolated state and reversible edge stubs while retaining production routes."""

    def __init__(self, root, monkeypatch):
        self.root, self.patch = Path(root), monkeypatch
        self.root.mkdir(parents=True, exist_ok=True)
        self.edges, self.provider_replies, self.requests = [], [], []
        self.denied = []
        self.signature = "contract signature"
        self.native_popen = subprocess.Popen
        self.workspace_root = self.root / "vm-runtime"
        self.workspace_dir = self.workspace_root / "workspace"
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.patch.setattr(workspace_tools, "_ROOT", str(self.workspace_root))
        self.patch.setattr(delegation, "_MANAGERS", {})
        self.helper_count = 0
        self.request_sequence = 0
        self.worker_calls = []
        self.worker_gate = asyncio.Event()
        self.make_plugin = integration.production.__wrapped__(self.root, monkeypatch)
        self.plugin = self.make_plugin()
        self.bind_worker(self.plugin)
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
        # Replace only public HTML transport: both native image parsers and their
        # async parent routes execute unchanged, with no DNS/image-byte requests.
        self.patch.setattr(image_search, "get_public", self.image_metadata_fetch)
        self.plugin._onebot_http = self.onebot
        self.patch.setattr(qq_native_actions, "prepare_container_file", self.prepare_upload)
        # Keep the actual native sender; only its final OneBot transport is fake.
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

    def bind_worker(self, plugin):
        """Stub only child model transport; native worker and manager remain real."""
        original = plugin._llm_tools

        async def llm(*args, **kwargs):
            """Hold children at a deterministic request boundary until cleanup."""
            if kwargs.get("tool_schemas") is not None:
                self.worker_calls.append({"scope": "native-child", "schemas": len(kwargs["tool_schemas"])})
                await self.worker_gate.wait()
                return "Offline worker complete.", []
            return await original(*args, **kwargs)

        plugin._llm_tools = llm

    async def resolve_args(self, plugin, name, args, scope, event):
        """Resolve real scope-bound IDs immediately before a native invocation."""
        if name in {"task_plan_read", "task_plan_update"}:
            # Each actual scope gets an existing scheduler task, not a second store.
            task = tasks.add("seed " + name, "inert planning goal", scope)
            args["task_id"] = task["id"]
            if name == "task_plan_update":
                receipt = await plugin._dispatch_tool("task_plan_read", {"task_id": task["id"]}, scope, event=event)
                args["expected_revision"] = receipt["revision"]
        if name in {"agent_status", "agent_message", "agent_interrupt"}:
            started = await plugin._dispatch_tool("agent_start", {"task": "Contract seeded worker"}, scope, event=event)
            await asyncio.sleep(0)
            args["id"] = started["id"]
        return args

    def execute_helper(self, code):
        """Run only the exact generated workspace runtime against a temporary root."""
        if not code.startswith(workspace_tools._RUNTIME + "\nimport base64\nREQUEST = "):
            raise AssertionError("unreviewed helper code")
        token = ast.literal_eval(code.split("base64.b64decode(", 1)[1].split(").decode", 1)[0])
        request = json.loads(base64.b64decode(token))
        if Path(request["root"]).resolve() != self.workspace_root.resolve():
            raise AssertionError("workspace helper escaped temp root")
        if code != workspace_tools.build_script(request["operation"], request["args"]):
            raise AssertionError("workspace helper differs from native generator")
        self.helper_count += 1
        script = self.root / ("workspace-helper-%s.py" % self.helper_count)
        script.write_text(code, encoding="utf-8")
        # File-backed stdio works under Windows without spawning captured pipes.
        output = self.root / ("workspace-output-%s.txt" % self.helper_count)
        with output.open("w", encoding="utf-8") as stream:
            process = self.native_popen([sys.executable, str(script)], cwd=str(self.root),
                                       stdout=stream, stderr=stream)
            process.wait(timeout=20)
        text = output.read_text(encoding="utf-8")
        if process.returncode:
            raise AssertionError(text)
        self.edges.append({"edge": "workspace_helper", "operation": request["operation"], "executed_locally": True})
        return {"ok": True, "output": text}

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

    def image_metadata_fetch(self, url):
        """Supply escaped index/page HTML at the module's sole HTTP transport seam."""
        self.edges.append({"edge": "image_metadata_http", "url": url})
        if url.startswith("https://www.bing.com/images/search?"):
            # Bing stores JSON inside an HTML attribute; parsing either layer is
            # required to recover the original URL and source-page provenance.
            record = {"murl": "https://cdn.example.org/contract-image.jpg",
                      "turl": "https://cdn.example.org/contract-thumb.jpg",
                      "purl": "https://example.org/article", "t": "Contract & image"}
            body = '<a class="iusc" m="' + html.escape(json.dumps(record), quote=True) + '"></a>'
        elif url == "https://example.org/article":
            body = '<html><head><meta property="og:image" content="/contract-page.jpg"></head></html>'
        else:
            # An unexpected endpoint is an honest audit failure, not generic HTML.
            self.denied.append("unapproved image metadata endpoint")
            raise AssertionError("unexpected image metadata URL")
        return {"body": body, "final_url": url, "content_type": "text/html"}

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
            "send_group_msg": {"message_id": 71001},
            "send_private_msg": {"message_id": 71002},
            "get_group_msg_history": {"messages": [
                {"message_id": 81001, "sender": {"user_id": 90001}},
                {"message_id": 81002, "sender": {"user_id": 10001}},
                {"message_id": 81003, "sender": {"user_id": 90001}}]},
            "delete_msg": None, "upload_group_file": None, "upload_private_file": None,
            "set_friend_add_request": None, "set_group_add_request": None,
        }
        if action in social_data:
            return {"status": "ok", "retcode": 0, "data": copy.deepcopy(social_data[action])}
        return {"messages": [{"sender": {"nickname": "fixture"}, "message": [{"type": "text", "data": {"text": "contract"}}]}]}

    async def prepare_upload(self, path, container="napcat"):
        """Simulate only container staging; validate the fixture file without Docker IO."""
        local = Path(path).resolve()
        if local.parent != self.root.resolve() or not local.is_file() or container != "napcat":
            self.denied.append("unapproved upload staging source")
            raise AssertionError("upload escaped isolated fixture")
        self.edges.append({"edge": "container_file_staging", "source": str(local),
                           "staged": "/tmp/offline-upload.txt"})
        return "/tmp/offline-upload.txt"

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
            payload = json.loads(request.data.decode("utf-8")) if not isinstance(request, str) and request.data else {}
            code = payload.get("code", "")
            if code.startswith(workspace_tools._RUNTIME + "\nimport base64\nREQUEST = "):
                result = self.execute_helper(code)
            else:
                # Legacy VM/job endpoints remain explicit external-edge simulations.
                result = {"ok": True, "simulated": True, "output": json.dumps({"ok": True, "simulated": True, "job_id": "offline-job", "jobs": []})}
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
        if name in {"qq_requests", "qq_request_decide"}:
            # Capture through the actual SQLite store, retaining native tokens only
            # inside the trusted fixture and the final fake OneBot boundary.
            from plugin.qq_request_store import RequestStore
            self.request_sequence += 1
            store = RequestStore(self.root / "qq_requests" / "requests.sqlite")
            for kind in ("friend", "group"):
                native = {"post_type": "request", "request_type": kind,
                          "user_id": 10001, "self_id": 90001,
                          "flag": "offline-%s-%s" % (kind, self.request_sequence),
                          "comment": "Untrusted captured contract request", "time": 100}
                if kind == "group":
                    native.update(group_id=42, sub_type="add")
                record = store.capture(native)
                if name == "qq_request_decide" and kind == "friend":
                    args["request_id"] = record["request_id"]
                    args["operation_id"] = "audit-request-%s" % self.request_sequence
        if name == "history_search":
            self.plugin._hist().append("42", "contract historical source", record={
                "id": "contract-history-42", "sender": {"id": "10001", "role": "member"},
                "body": "contract historical source"})
            self.plugin._hist().append("private:80001", "contract foreign private secret")
        if name == "send_file":
            # The native upload validates an actual local file before its fake edge.
            upload = self.root / "contract-upload.txt"
            upload.write_text("isolated upload contract\n", encoding="utf-8")
            args["path"] = str(upload.resolve())
        if name.startswith("workspace_"):
            if name in {"workspace_write", "workspace_edit"}:
                args["path"] = name + ".txt"
            baseline = self.workspace_dir / args.get("path", "contract.txt")
            if baseline.suffix == ".png":
                baseline = self.workspace_dir / "contract.txt"
            baseline.write_text("contract baseline\n", encoding="utf-8")
            (self.workspace_dir / "contract.png").write_bytes(base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6LwAAAABJRU5ErkJggg=="))
            if name in {"workspace_write", "workspace_edit"}:
                # The actual helper establishes the observation before any overwrite.
                receipt = json.loads(self.execute_helper(workspace_tools.build_script(
                    "workspace_read", {"path": args["path"]}))["output"])
                args["expected_sha256"] = receipt["sha256"]
        if name.startswith("skill_"):
            skill_root = self.root / "skills"
            skill_root.mkdir(exist_ok=True)
            (skill_root / "contract.md").write_text("Offline reference.\n", encoding="utf-8")
            if name == "skill_save" and (skill_root / "contract-save.md").exists():
                args["expected_sha256"] = hashlib.sha256((skill_root / "contract-save.md").read_bytes()).hexdigest()
        store = memory.Memory(str(self.memory_path)).load()
        seed_text = "contract seeded fact" + (" for " + name if name in {"facts_edit", "wp_fact_move"} else "")
        store.add_fact(seed_text, "audit")
        fact_id = next(row["id"] for row in store.list_facts() if row["text"] == seed_text)
        memory.WakePersonFacts(str(self.person_path)).load().add("10001", "contract seeded person")
        if name in {"facts_edit", "wp_fact_move"}:
            args["id"] = str(fact_id)
        if name in {"task_done", "task_cancel", "task_pause", "task_resume", "task_update"}:
            task = tasks.add("seed " + name, "inert", "42")
            if name == "task_resume":
                tasks.set_status(task["id"], "", "paused", "audit fixture")
            args["id"] = task["id"]
        event = integration.Event("contract audit")
        if name == "reply_now":
            # Attribution belongs to the actual current turn, not the tool payload.
            # Event.send is the fixture's recording transport; _send stays native.
            self.plugin._turn_context["42"] = {"messages": [message_record(event, event.text)]}
        # Identity is supplied only by the trusted fixture event, never tool arguments.
        # This source owner ID remains independent of the production OWNER_QQS set.
        if name in {"qq_set_signature", "qq_publish_post", "qq_set_profile", "autonomy_schedule", "skill_save"} | REQUEST_NAMES:
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
            # Native approval null ACK means accepted, never verified membership.
            "set_friend_add_request": {"status": "ok", "retcode": 0, "data": None},
            "set_group_add_request": {"status": "ok", "retcode": 0, "data": None},
        }
        return args, event


def _quality(result, name=None):
    """Describe structure, never interpret stubbed success as a real side effect."""
    try:
        json.dumps(result, allow_nan=False)
        serializable = True
    except (TypeError, ValueError):
        serializable = False
    # Only exact reviewed unsupported receipts qualify; arbitrary errors remain gaps.
    unavailable = (name in EXPECTED_UNAVAILABLE and isinstance(result, dict)
                   and result.get("ok") is False
                   and result.get("error") == "native_capability_unavailable"
                   and result.get("supported") is False
                   and result.get("capability") == EXPECTED_UNAVAILABLE[name])
    accepted = isinstance(result, dict) and (result.get("ok") is True or unavailable)
    return {"object": isinstance(result, dict), "json_serializable": serializable,
            "contract_satisfied": accepted, "expected_unavailable": unavailable,
            "classification": "capability_unavailable" if unavailable else (
                "acknowledged" if isinstance(result, dict) and result.get("ok") is True else "error"),
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
        args = await harness.resolve_args(harness.plugin, name, args, "42", event)
        entry["argument_errors"] = argument_errors(schemas[name], args)
        start = len(harness.edges)
        executed_lines = set()

        def trace(frame, action, arg):
            """Record only real dispatcher/VM source lines for this one invocation."""
            if action == "line" and frame.f_code.co_name in {"_dispatch_tool", "_vm_tool", "_execute_tool", "_send", "_qq_send"}:
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
            entry["result_quality"] = _quality(result, name)
            entry["result"] = redact(result)
        except Exception as exc:
            entry["exception"] = {"type": type(exc).__name__, "message": str(exc)[:240]}
        finally:
            sys.settrace(previous)
        entry["executed_source_lines"] = [{"function": function, "line": line} for function, line in sorted(executed_lines)]
        entry["stubbed_edges"] = harness.edges[start:]
        entry["onebot_actions"] = [action for action, _ in event.actions]
        if name == "reply_now":
            # A generic success stub cannot pass: the real sender must execute and
            # the fixture event must receive the same body as the native receipt.
            sent_text = ["".join(getattr(part, "text", "") for part in chain.chain)
                         for chain in event.sent]
            entry["actual_send_executed"] = any(function == "_send" for function, _ in executed_lines)
            entry["event_delivery_simulated"] = True
            entry["sent_text"] = sent_text
            entry["boundary_contract_matches"] = (
                entry["actual_send_executed"] and sent_text == [args["text"]]
                and isinstance(result, dict) and result.get("delivered") is True
                and result.get("text") == args["text"] and result.get("quote_id") is None)
        elif name in {"image_search", "page_images"}:
            # Native parsing must produce the exact declared URL, not fabricated
            # success or automatic media delivery. Only HTML transport is fake.
            native_edges = [edge for edge in entry["stubbed_edges"] if edge["edge"] == "image_metadata_http"]
            expected_url = ("https://cdn.example.org/contract-image.jpg" if name == "image_search"
                            else "https://example.org/contract-page.jpg")
            images = result.get("images", []) if isinstance(result, dict) else []
            entry["boundary_contract_matches"] = (
                len(native_edges) == 1 and len(images) == 1
                and images[0].get("image_url") == expected_url
                and images[0].get("source_page") == "https://example.org/article"
                and images[0].get("verified_image_bytes") is False
                and not event.sent and not entry["onebot_actions"]
                and not any(edge["edge"] == "media_delivery" for edge in entry["stubbed_edges"]))
            if name == "image_search":
                entry["boundary_contract_matches"] &= (bool(images) and bool(native_edges)
                    and result.get("query") == args["query"]
                    and parse_qs(urlsplit(native_edges[0]["url"]).query).get("q") == [args["query"]]
                    and images[0].get("thumbnail_url") == "https://cdn.example.org/contract-thumb.jpg"
                    and images[0].get("title") == "Contract & image")
            else:
                entry["boundary_contract_matches"] &= bool(native_edges) and native_edges[0]["url"] == args["url"]
        elif name == "history_search":
            hits = result.get("hits", []) if isinstance(result, dict) else []
            entry["boundary_contract_matches"] = (
                bool(hits) and all(hit.get("scope") == "42" for hit in hits)
                and any(hit.get("record", {}).get("id") == "contract-history-42" for hit in hits)
                and all(hit.get("source") == {"name": "cross_scope_history", "trust": "untrusted_data"} for hit in hits)
                and "foreign private secret" not in json.dumps(hits))
        elif name == "vm_recall":
            native_edges = [edge for edge in entry["stubbed_edges"] if edge["edge"] == "onebot_http"]
            entry["boundary_contract_matches"] = (
                native_edges == [
                    {"edge": "onebot_http", "action": "get_login_info", "params": {}},
                    {"edge": "onebot_http", "action": "get_group_msg_history", "params": {"group_id": 42, "count": 20}},
                    {"edge": "onebot_http", "action": "delete_msg", "params": {"message_id": 81003}}]
                and isinstance(result, dict) and result.get("ok") is True
                and result.get("recall_count") == 1
                and result.get("results") == [{"message_id": 81003, "ok": True, "status": "confirmed"}]
                and not any(edge["edge"] == "vm_http_stub" for edge in entry["stubbed_edges"]))
        elif name == "send_file":
            native_edges = [edge for edge in entry["stubbed_edges"] if edge["edge"] == "onebot_http"]
            entry["boundary_contract_matches"] = (
                len(native_edges) == 1 and native_edges[0]["action"] == "upload_group_file"
                and native_edges[0]["params"].get("group_id") == 42
                and native_edges[0]["params"].get("name") == "contract-upload.txt"
                and native_edges[0]["params"].get("file") == "/tmp/offline-upload.txt"
                and len([edge for edge in entry["stubbed_edges"] if edge["edge"] == "container_file_staging"]) == 1
                and isinstance(result, dict) and result.get("ok") is True
                and result.get("accepted") is True and result.get("delivered") is False
                and result.get("delivery_confirmation") == "native_upload_acknowledged"
                and "message_id" not in result
                and not any(edge["edge"] == "vm_http_stub" for edge in entry["stubbed_edges"]))
        elif name == "qq_send":
            # The real sender must emit a native action and retain its message ID;
            # neither a generic VM success nor status=ok/null can prove delivery.
            native_edges = [edge for edge in entry["stubbed_edges"] if edge["edge"] == "onebot_http"]
            entry["native_sender_executed"] = any(function == "_qq_send" for function, _ in executed_lines)
            entry["delivery_transport_simulated"] = True
            entry["boundary_contract_matches"] = (
                entry["native_sender_executed"]
                and native_edges == [{"edge": "onebot_http", "action": "send_group_msg",
                                      "params": {"group_id": 42, "message": [
                                          {"type": "text", "data": {"text": args["text"]}}]}}]
                and isinstance(result, dict) and result.get("ok") is True
                and result.get("delivered") is True and result.get("message_id") == "71001"
                and result.get("action") == "send_group_msg"
                and not any(edge["edge"] == "vm_http_stub" for edge in entry["stubbed_edges"]))
        elif name in REQUEST_NAMES:
            # All request routes execute native parent branches. Only decisions may
            # call OneBot, and even accepted approvals never assert friendship.
            if name in EXPECTED_UNAVAILABLE:
                entry["boundary_contract_matches"] = (
                    _quality(result, name)["expected_unavailable"]
                    and not event.actions and not entry["stubbed_edges"])
            elif name == "qq_request_capabilities":
                entry["boundary_contract_matches"] = (
                    result.get("runtime_verified") is False
                    and result.get("owner_enabled") is True
                    and all(result.get("capabilities", {}).get(tool, {}).get("supported") is False
                            for tool in EXPECTED_UNAVAILABLE) and not event.actions)
            elif name == "qq_requests":
                records = result.get("requests", [])
                entry["boundary_contract_matches"] = (
                    {record.get("kind") for record in records} == {"friend", "group"}
                    and all("flag" not in record for record in records)
                    and result.get("source") == "captured_native_request_events"
                    and result.get("native_backlog_complete") is False and not event.actions)
            else:
                entry["boundary_contract_matches"] = (
                    entry["stubbed_edges"] == [{"edge": "onebot_http", "action": "set_friend_add_request",
                        "params": {"flag": "offline-friend-%s" % harness.request_sequence, "approve": True}}]
                    and not event.actions
                    and result.get("outcome") == "accepted"
                    and result.get("verification") == {"state": "not_verified"}
                    and result.get("membership") == "unknown")
        elif name in EXPECTED_SOCIAL_ACTIONS:
            native_edges = [edge for edge in entry["stubbed_edges"] if edge["edge"] == "onebot_http"]
            entry["boundary_contract_matches"] = [edge["action"] for edge in native_edges] == EXPECTED_SOCIAL_ACTIONS[name]
            if name == "qq_set_signature":
                entry["boundary_contract_matches"] = entry["boundary_contract_matches"] and native_edges[0]["params"] == {"longNick": args["signature"]} and result.get("verification") == {"state": "matched", "matched": True}
            elif name == "qq_publish_post":
                entry["boundary_contract_matches"] = entry["boundary_contract_matches"] and native_edges[0]["params"] == {"content": args["content"], "ugc_right": 64} and result.get("outcome") == "accepted" and result.get("tid") == "offline-contract-post"
            elif name == "qq_set_profile":
                entry["boundary_contract_matches"] = entry["boundary_contract_matches"] and native_edges[0]["params"] == {"nickname": args["nickname"]} and result.get("outcome") == "accepted"
        elif name.startswith("workspace_"):
            helper_edges = [edge for edge in entry["stubbed_edges"] if edge["edge"] == "workspace_helper"]
            entry["boundary_contract_matches"] = len(helper_edges) == 1 and helper_edges[0]["operation"] == name
            if name in {"workspace_write", "workspace_edit"}:
                expected_text = "contract written\n" if name == "workspace_write" else "contract edited\n"
                actual = (harness.workspace_dir / args["path"]).read_text(encoding="utf-8")
                entry["boundary_contract_matches"] &= actual == expected_text and result.get("sha256") == hashlib.sha256((harness.workspace_dir / args["path"]).read_bytes()).hexdigest()
            elif name == "workspace_present":
                entry["boundary_contract_matches"] &= result.get("delivery") == "references_only" and len(result.get("files", [])) == 1
            elif name == "workspace_read_image":
                entry["boundary_contract_matches"] &= isinstance(result.get("image"), dict)
        elif name in {"tool_search", "tool_describe"}:
            found = result.get("tools", [])
            entry["boundary_contract_matches"] = bool(found) and found[0].get("name") == "vm_shot" and found[0].get("schema") == schemas["vm_shot"]
        elif name.startswith("agent_"):
            entry["boundary_contract_matches"] = bool(harness.worker_calls)
            if name == "agent_start":
                entry["boundary_contract_matches"] = result.get("status") == "running" and result.get("attached") is True
            elif name == "agent_status":
                entry["boundary_contract_matches"] &= result.get("id") == args["id"] and result.get("status") == "running"
            elif name == "agent_message":
                entry["boundary_contract_matches"] &= result.get("status") == "queued"
            elif name == "agent_interrupt":
                entry["boundary_contract_matches"] &= result.get("status") in {"cancelling", "cancel_requested", "cancelled"}
            else:
                entry["boundary_contract_matches"] &= bool(result.get("agents"))
        elif name in {"task_plan_read", "task_plan_update"}:
            expected_steps = [] if name == "task_plan_read" else args["steps"]
            expected_revision = 0 if name == "task_plan_read" else args["expected_revision"] + 1
            current = tasks.read_plan(args["task_id"], "42")
            entry["boundary_contract_matches"] = (result.get("task_id") == args["task_id"]
                and result.get("revision") == expected_revision and result.get("steps") == expected_steps
                and current == {"task_id": args["task_id"], "revision": expected_revision, "steps": expected_steps})
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
        harness.bind_worker(plugin)
        for attribute in ("manifest", "_autonomy_controller", "_tasks_mod", "_web_search", "_fetch_page", "_onebot_http", "_deliver_media"):
            setattr(plugin, attribute, getattr(harness.plugin, attribute))
        scope = str(4200 + offset)
        calls = []
        event = harness.prepare("group_files")[1]
        for name in batch:
            args, _ = harness.prepare(name)
            args = await harness.resolve_args(plugin, name, args, scope, event)
            calls.append(integration._call(name, args, "batch-%s-%s" % (offset, name)))
        # The mixed batch contains owner-only mutations; use a genuine event identity.
        event.get_sender_id = lambda: "2084526117"
        event.group = scope
        # Native entry normally freezes this event's current-batch records before
        # the provider loop; exercise reply attribution with that same local input.
        plugin._turn_context[scope] = {"messages": [message_record(event, event.text)]}
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
            "failed_results": [{"name": row["tool_name"], "result": row["result"]} for row in observations
                               if not _quality(row["result"], row["tool_name"])["contract_satisfied"]],
            "expected_unavailable_count": sum(_quality(row["result"], row["tool_name"])["expected_unavailable"] for row in observations),
            "all_results_successful": all(row["result"].get("ok") is True for row in observations),
            "all_result_contracts_satisfied": all(_quality(row["result"], row["tool_name"])["contract_satisfied"] for row in observations),
            "paired_results_match_archive": [json.loads(message["content"])["result"] for message in paired] == [row["result"] for row in observations]})
    unknown = await harness.plugin._dispatch_tool("__contract_unknown_tool__", {}, "42")
    report["unknown_tool"] = {"result": unknown, "rejected": unknown.get("ok") is False and "unknown tool" in unknown.get("error", "")}
    report["forbidden_io_attempts"] = harness.denied
    for entry in report["tools"]:
        quality = entry.get("result_quality", {})
        if not entry["executed"] or entry["exception"] or not entry.get("handler_body_executed") or not entry.get("boundary_contract_matches") or entry.get("argument_errors") or not all(quality.get(key) for key in ("object", "json_serializable", "boolean_ok")) or quality.get("contract_satisfied") is not True:
            report["gaps"].append({"name": entry["name"], "reason": "route, fixture arguments, exception or result quality failed"})
    report["route_contracts_pass"] = not (report["gaps"] or report["unmapped_cases"] or report["stale_cases"] or report["schema_errors"] or harness.denied) and report["unknown_tool"]["rejected"] and all(
        batch["exact_id_pairing"] and batch["all_observations_retained"] and batch["paired_results_match_archive"] and batch["all_result_contracts_satisfied"] and not batch["exception"] for batch in report["provider_batches"])
    report["result_quality_counts"] = dict(Counter(
        entry.get("result_quality", {}).get("classification", "error") for entry in report["tools"]))
    report["raw_schemas_unique"] = not report["duplicate_schema_names"]
    harness.worker_gate.set()
    workers = [job["worker"] for owner in delegation._MANAGERS.values()
               if owner.root.is_relative_to(harness.root.resolve()) for job in owner._jobs.values()]
    if workers:
        await asyncio.gather(*workers, return_exceptions=True)
    report["workspace_helpers_executed"] = harness.helper_count
    report["native_worker_calls"] = harness.worker_calls
    report["native_worker_receipts"] = [{key: record.get(key) for key in ("id", "status", "result", "error")}
                                        for path in (harness.root / "delegation").glob("*.json")
                                        for record in [json.loads(path.read_text(encoding="utf-8"))]]
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
