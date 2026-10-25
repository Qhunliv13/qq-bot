"""Isolated real-model/read-only tool smoke test; never sends to QQ or modifies production memories."""
from pathlib import Path
import argparse
import asyncio
import importlib.util
import json
import sys
import types
import tempfile


def load_plugin(directory):
    """Import the candidate in a separate process; never construct/start the plugin."""
    # Existing service cwd is the framework checkout; explicit path is needed for script execution.
    sys.path.insert(0, str(Path.cwd()))
    package = types.ModuleType("release_probe_plugin")
    package.__path__ = [str(directory)]
    sys.modules[package.__name__] = package
    spec = importlib.util.spec_from_file_location(package.__name__ + ".main", directory / "main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ProbeEvent:
    """A synthetic isolated conversation with no platform send capability."""

    unified_msg_origin = "release-probe"

    def __init__(self, text):
        self.text = text
        self.message_obj = types.SimpleNamespace(message_id="release-probe")

    def get_self_id(self):
        """Never impersonate a real QQ identity in a production test."""
        return ""

    def get_sender_id(self):
        """Use a non-real isolated actor."""
        return "release-probe"

    def get_sender_name(self):
        """Supply an unprivileged test speaker label."""
        return "发布隔离验证"

    def get_group_id(self):
        """No actual group is addressed."""
        return "release-probe"

    def get_messages(self):
        """No external attachments or platform actions."""
        return []

    def get_message_str(self):
        """Return the current test question."""
        return self.text

    async def send(self, chain):
        """Fail closed if integration accidentally tries to post a message."""
        raise RuntimeError("QQ sending forbidden in release probe")


async def probe(directory, output):
    """Exercise real transport/dispatcher and next-instance recall with temporary archive only."""
    module = load_plugin(directory)
    from release_probe_plugin.tool_context import ToolContextArchive
    # Each run starts empty: cached evidence must not masquerade as a new native tool call.
    temporary = Path(tempfile.mkdtemp(prefix="probe_state_", dir=output.parent))
    scope = "release-probe"

    def instance():
        """Use actual production model configuration while bypassing startup and QQ jobs."""
        bot = module.HiganPlugin.__new__(module.HiganPlugin)
        bot.persona = module.HiganPlugin._load_persona()
        bot.context = types.SimpleNamespace(get_using_provider=lambda *args: object())
        bot._pending = {}
        bot._busy = {}
        bot._all_recent = []
        bot._recent = {scope: []}
        bot._recent_images = {}
        bot._member_snapshot = {}
        bot._turn_context = {scope: {"history": (), "clock": bot._clock_context()}}
        bot.manifest = []
        bot._tool_evidence_store = ToolContextArchive(temporary / "observations")
        return bot

    bot = instance()
    called = []
    original = bot._dispatch_tool

    async def readonly_dispatch(name, args, scope="", event=None):
        """Only reviewed non-mutating handlers are callable; no shell/mail/send/config actions."""
        if name not in {"self_status", "web_search", "web_fetch", "tool_observations"}:
            return {"ok": False, "error": "release_probe_readonly_boundary"}
        called.append(name)
        return await original(name, args, scope, event)

    bot._dispatch_tool = readonly_dispatch
    event = ProbeEvent("请调用 self_status 看一下当前状态，只告诉我其中 host_uptime_s 的数值，不要发图片或执行其他动作。")
    first = await bot._decide_tools_first(event, scope, event.text, False)
    records = bot._tool_evidence().recent(scope)
    statuses = [row for row in records if row.get("tool_name") == "self_status"]
    status_record = statuses[-1] if statuses else {}
    result = status_record.get("result") or {}
    uptime = (result.get("runtime") or {}).get("host_uptime_s") if isinstance(result, dict) else None
    second_bot = instance()  # Recreate instance: evidence must be read from disk, not Python locals.
    second_event = ProbeEvent("不要再调用工具。刚才 self_status 返回的 host_uptime_s 原始数值是多少？仅回复那个数值。")
    second = await second_bot._decide_tools_first(second_event, scope, second_event.text, False)
    report = {
        "native_readonly_tools_called": called,
        "self_status_success": bool(isinstance(result, dict) and result.get("ok")),
        "observations_persisted": len(records),
        "first_answer_has_observed_value": uptime is not None and str(uptime) in first,
        "new_instance_followup_has_same_observed_value": uptime is not None and str(uptime) in second,
        "qq_messages_sent": 0,
    }
    search = await original("web_search", {"query": "Python release date"}, scope, event)
    report["web_search"] = {"ok": search.get("ok"), "result_count": len(search.get("results") or []),
                            "status": search.get("status"), "error": search.get("error")}
    fetch = await original("web_fetch", {"url": "https://www.python.org/"}, scope, event)
    report["web_fetch"] = {"ok": fetch.get("ok"), "body_chars": len(fetch.get("body") or ""), "error": fetch.get("error")}
    files = await original("vm_files", {"dir": "files"}, scope, event)
    report["vm_files"] = {"ok": bool(files.get("ok")), "error": files.get("error")}
    history = await original("history_search", {"keyword": "release-probe-no-match", "scope": scope, "limit": 1}, scope, event)
    report["history_search"] = {"ok": bool(history.get("ok")), "count": history.get("count"), "error": history.get("error")}
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if ("self_status" in called and report["self_status_success"]
                 and report["first_answer_has_observed_value"]
                 and report["new_instance_followup_has_same_observed_value"]
                 and report["web_search"]["result_count"] > 0
                 and report["web_fetch"]["ok"]) else 1


def main():
    """Require explicit staged source and output paths; no production plugin file writes."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    return asyncio.run(probe(args.plugin.resolve(), args.output.resolve()))


if __name__ == "__main__":
    raise SystemExit(main())
