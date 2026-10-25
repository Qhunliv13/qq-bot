"""Real-model selective batch probe with synthetic records and all tool effects forbidden."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import types
from production_probe import load_plugin


class Event:
    """Synthetic group event without any QQ delivery capability."""

    def __init__(self, text, ident, nickname, mid, mentions=None):
        self.text, self.ident, self.nickname = text, ident, nickname
        self.unified_msg_origin = "isolated-batch-probe"
        self.message_obj = types.SimpleNamespace(message_id=mid, message=mentions or [])

    def get_self_id(self):
        """Stable synthetic bot ID."""
        return "90001"

    def get_sender_id(self):
        """Synthetic source ID independent of display text."""
        return self.ident

    def get_sender_name(self):
        """A user-controlled display name, never interpreted as an instruction."""
        return self.nickname

    def get_messages(self):
        """Expose only top-level segments."""
        return self.message_obj.message

    def get_message_str(self):
        """Return synthetic current text."""
        return self.text

    async def send(self, chain):
        """No platform messages may leave this test."""
        raise RuntimeError("real_qq_delivery_forbidden")


async def probe(plugin, output):
    """Run actual prompt/native loop over one coalesced opportunity per scenario, no forced choice."""
    module = load_plugin(plugin)
    from release_probe_plugin.message_batch import collect_batch
    from release_probe_plugin.tool_context import ToolContextArchive, redact
    persona = module.HiganPlugin._load_persona()
    root = Path(tempfile.mkdtemp(prefix="batch-model-probe-", dir=output.parent))
    module.HIGAN_ROOT = str(root)
    report = {"real_model": True, "all_tools_simulated": True,
              "code_sha256": hashlib.sha256((plugin / "main.py").read_bytes()).hexdigest(),
              "scenarios": [], "qq_messages_sent": 0}
    cases = [
        ("nickname_not_ad", [Event("大家帮忙看看这个文件。", "91001", "小桥 收显卡出的私", "n1",
            [{"type": "at", "data": {"qq": "91002", "name": "海风 收显示器的私"}}])]),
        ("pick_relevant_not_each", [Event("小李，晚上七点到食堂找我，不用回复。", "91001", "小桥", "m1"),
            Event("彼岸酱，17乘23是多少？", "91002", "海风", "m2", [{"type": "at", "data": {"qq": "90001"}}]),
            Event("小李：收到。", "91003", "新同学", "m3")]),
        ("answer_multiple_one_message", [Event("彼岸酱，17乘23是多少？", "91001", "小桥", "a1", [{"type": "at", "data": {"qq": "90001"}}]),
            Event("再帮我算一下8乘9，一起说就行。", "91001", "小桥", "a2")]),
        ("all_unrelated_can_silence", [Event("小王，作业第三题你先和小李聊。", "91001", "小桥", "s1"),
            Event("好的，咱们自己讨论，不用别人接话。", "91002", "海风", "s2")]),
        ("silence_even_mentioned", [Event("这条不用回，我只是记一下数字，别做任何事。", "91001", "小桥", "s3", [{"type": "at", "data": {"qq": "90001"}}])]),
    ]
    for index, (label, events) in enumerate(cases):
        scope = str(99880000+index)
        bot = module.HiganPlugin.__new__(module.HiganPlugin)
        bot.persona = persona
        bot.context = types.SimpleNamespace(get_using_provider=lambda *args: object())
        bot._pending, bot._busy, bot._all_recent = {}, {}, []
        bot._recent, bot._recent_images, bot._member_snapshot, bot.manifest = {scope: []}, {}, {}, []
        bot._tool_evidence_store = ToolContextArchive(root / "observations")
        batch = collect_batch([(event, event.text, False, bot._directly_mentioned(event), ()) for event in events])
        bot._turn_context = {scope: {"history": (), "clock": bot._clock_context(), "messages": batch["records"], "image_refs": []}}
        bot._image_refs = lambda event: []
        bot._refs_for = no_refs
        calls = []

        async def no_effects(name, args, scope="", event=None):
            """No actual dispatcher and no side effects during real-model choice test."""
            calls.append(name)
            return {"ok": False, "error": "batch_probe_tools_unavailable", "simulated": True}

        bot._dispatch_tool = no_effects
        answer = await asyncio.wait_for(bot._decide_tools_first(batch["event"], scope, batch["text"], False), 720)
        row = {"name": label, "records": batch["records"], "answer": answer, "silent": not bool(answer),
               "tool_calls": calls, "message_count": len(events), "model_opportunities": 1}
        report["scenarios"].append(row)
        output.write_text(json.dumps(redact(report), ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"scenario": label, "silent": row["silent"], "answer": answer, "tool_calls": calls}, ensure_ascii=False), flush=True)


async def no_refs(*args):
    """No production screenshot/image lookup."""
    return [], ""


def main():
    """Require explicit isolated plugin/output paths."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(probe(args.plugin, args.output))


if __name__ == "__main__":
    main()
