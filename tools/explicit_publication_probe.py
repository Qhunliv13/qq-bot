"""Offline native publication probe; simulated receipts are never QQ deliveries.

Run inside the candidate's installed framework environment. No plugin startup,
framework ingress, live policy, real provider, or real platform is exercised.
"""

import argparse
import asyncio
import copy
import importlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import types
from unittest.mock import patch

from production_probe import ProbeEvent, load_plugin


class RecordingEvent(ProbeEvent):
    """Retain native sender behavior while replacing only event transport."""

    def __init__(self):
        super().__init__("Offline publication fixture")
        self.unified_msg_origin = "isolated:GroupMessage:42"
        self.message_obj.message_id = "1234"
        self.sent = []

    def get_self_id(self):
        """Provide a synthetic bot identity for native delivery provenance."""
        return "90001"

    def get_group_id(self):
        """Bind every synthetic event to the isolated fixture scope."""
        return "42"

    async def send(self, chain):
        """Record the actual native chain and supply a deterministic fake receipt."""
        self.sent.append(chain)
        return [{"message_id": 900100 + len(self.sent)}]


class FakeController:
    """Supply due opportunities without starting a live autonomy controller."""

    def __init__(self):
        self.trigger = {"event_id": "fixture-opportunity", "scope": "42",
                        "kind": "interval", "origin": "isolated:GroupMessage:42"}
        self.acks = 0

    def origin(self, scope):
        """Return only the origin bound to the fixture destination."""
        return "isolated:GroupMessage:42" if scope == "42" else ""

    def opportunities(self, policy):
        """Expose a single deterministic, still-due opportunity."""
        return [self.trigger]

    def ack(self, trigger):
        """Consume the exact fixture identity once without persistent scheduling."""
        assert trigger == self.trigger
        self.acks += 1
        return True


def forbidden(*args, **kwargs):
    """Fail closed before any unexpected network or provider fallback can run."""
    raise RuntimeError("external_transport_forbidden_in_publication_probe")


async def forbidden_async(*args, **kwargs):
    """Reject model fallbacks instead of hiding failure behind a second path."""
    forbidden()


async def no_refs(*args, **kwargs):
    """Keep attachments empty so image download paths cannot perform IO."""
    return [], ""


def instance(module, root, replies):
    """Bootstrap only native turn fields; substitute the provider callback alone."""
    archive = importlib.import_module(module.__package__ + ".tool_context")
    bot = module.HiganPlugin.__new__(module.HiganPlugin)
    bot.persona = "Offline deterministic publication fixture"
    bot.context = types.SimpleNamespace(get_using_provider=lambda *args: object())
    bot._pending, bot._busy, bot._all_recent = {}, {}, []
    bot._recent, bot._recent_images, bot._member_snapshot = {"42": []}, {}, {}
    bot._last_sent, bot._history_origins, bot.manifest = {}, {}, []
    bot._last_reply_at, bot._reply_times = {}, {}
    # Give every case an independent history root, not merely a new delivery ledger.
    root.mkdir(parents=True, exist_ok=True)
    module.HIGAN_ROOT = str(root)
    bot._turn_context = {"42": {"history": [], "clock": bot._clock_context(),
        "messages": [], "image_refs": [], "image_sources": []}}
    bot._execution_lock = asyncio.Lock()
    bot._tool_evidence_store = archive.ToolContextArchive(root / "observations")
    # This is a deliberate local permission stub, not evidence of live policy.
    bot._policy_allows = lambda event, scope: True
    bot._group_policy = lambda scope: {}
    bot._refs_for, bot._onebot_http = no_refs, forbidden
    bot._llm_plain, bot._decide = forbidden_async, forbidden_async
    requests = []
    scripted = iter(replies)

    async def provider(system, prompt, images, messages=None, allow_tools=True, tool_schemas=None):
        """Capture actual native provider requests and return exact scripted replies."""
        assert messages is not None, "native transcript callback required"
        requests.append(copy.deepcopy(messages))
        return next(scripted)

    bot._llm_tools = provider
    return bot, requests


async def foreground_case(module, root, explicit, *, tool_media=False):
    """Exercise native public sends, paired receipts and explicit final publication."""
    selection_mod = importlib.import_module(module.__package__ + ".reply_selection")
    batch_mod = importlib.import_module(module.__package__ + ".message_batch")
    event = RecordingEvent()
    records = [batch_mod.message_record(event, event.text)]
    image_path = root / "fixture-image.png"
    body = "[发图:%s]" % image_path if tool_media else "A separate final result."
    final = json.dumps({"reply_to": [records[0]["id"]], "text": body, "delivery": "new"}) if explicit else body
    call = {"id": "fixture-call", "type": "function", "function": {
        "name": "send_shot" if tool_media else "reply_now",
        "arguments": json.dumps({"path": str(image_path)} if tool_media else
                                {"text": "First public result."})}}
    bot, requests = instance(module, root, [("", [call]), (final, [])])
    if tool_media:
        # A valid local image feeds the real framework component constructor;
        # only its VM response and outgoing transport are simulated.
        import base64
        image_path.write_bytes(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="))

        async def simulated_vm(name, arguments):
            """Return a plugin-delivery request without any VM or platform IO."""
            assert name == "vm_send_shot" and arguments == {"path": str(image_path)}
            return {"ok": True, "deliver": "plugin", "path": str(image_path), "send_as": "image"}

        bot._vm_tool = simulated_vm
    bot._turn_context["42"]["messages"] = records
    raw = await bot._decide_tools_first(event, "42", event.text, False)
    assert len(requests) == 2
    transcript = requests[1]
    assistants = [row for row in transcript if row.get("tool_calls")]
    results = [row for row in transcript if row.get("role") == "tool"]
    assert len(assistants) == len(results) == 1
    assert assistants[0]["tool_calls"][0]["id"] == results[0]["tool_call_id"] == "fixture-call"
    result = json.loads(results[0]["content"])["result"]
    assert result["ok"] is True and result["delivered"] is True
    first_delivery = bot._turn_context["42"]["_deliveries"][0]["delivery"]
    assert first_delivery["status"] == "native_confirmed"
    assert first_delivery["message_ids"] == ["900101"]
    assert first_delivery["input_message_ids"] == [records[0]["id"]]
    if tool_media:
        assert result["path"] == str(image_path)
        assert not bot._turn_context["42"]["_interim_replies"]
    else:
        assert result["delivery"] == first_delivery
    assert len(event.sent) == 1
    if explicit:
        # Decode once, just as the real caller does; never send a JSON envelope as text.
        assert raw == final
        selected = selection_mod.decode_reply(raw, records)
        assert not selected["error"] and selected["text"] == body
        delivered = await bot._send(event, "42", selected["text"], selected["quote_id"], decoded=True)
        assert delivered == ("[图片]" if tool_media else body) and len(event.sent) == 2
    else:
        assert raw == "" and len(event.sent) == 1
    deliveries = bot._turn_context["42"]["_deliveries"]
    assert len(deliveries) == (2 if explicit else 1)
    if tool_media:
        # Inspect native image components, not the sender's lossy [图片] history label.
        image_type = module.Image
        assert all(sum(isinstance(part, image_type) for part in chain.chain) == 1
                   for chain in event.sent)
    name = ("tool_media_explicit_new" if explicit else "tool_media_raw_marker") if tool_media else (
        "foreground_explicit_new" if explicit else "foreground_raw_paraphrase")
    return {"name": name,
            "provider_callback_count": len(requests), "paired_tool_result_count": len(results),
            "simulated_send_count": len(event.sent), "delivery_record_count": len(deliveries),
            "receipt_message_ids": first_delivery["message_ids"],
            "final_returned": bool(raw)}


async def autonomy_case(module, root, explicit):
    """Run actual autonomous adapter, native loop, sender, and framework serializer."""
    adapter = importlib.import_module(module.__package__ + ".autonomous_turn")
    body = "Autonomous explicit result."
    final = json.dumps({"reply_to": [], "text": body, "delivery": "new"}) if explicit else body
    bot, requests = instance(module, root, [(final, [])])
    recorded = []

    def simulated_http(action, parameters):
        """Record the native serializer output instead of making any platform request."""
        assert action == "send_group_msg" and parameters["group_id"] == 42
        segments = parameters["message"]
        assert isinstance(segments, list) and segments
        text = "".join(segment["data"]["text"] for segment in segments if segment["type"] == "text")
        assert text == body
        recorded.append({"action": action, "segment_count": len(segments)})
        return {"status": "ok", "retcode": 0, "data": {"message_id": 900201}}

    bot._onebot_http = simulated_http
    controller = FakeController()
    outcome = await adapter.run_turn(bot, "42", [controller.trigger], controller)
    assert outcome["status"] == "completed", outcome
    assert outcome["delivered"] is explicit
    assert len(recorded) == int(explicit) and len(requests) == controller.acks == 1
    assert len(bot._last_sent.get("42", [])) == int(explicit)
    return {"name": "autonomy_explicit_new" if explicit else "autonomy_raw_text",
            "provider_callback_count": len(requests), "simulated_send_count": len(recorded),
            "native_serializer_exercised": explicit, "opportunity_ack_count": controller.acks,
            "native_turn_completed": True}


async def probe(directory):
    """Load actual candidate after temp-root setup and deny socket-level networking."""
    previous_root = os.environ.get("HIGAN_ROOT")
    previous_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    with tempfile.TemporaryDirectory(prefix="explicit_publication_probe_") as temporary:
        os.environ["HIGAN_ROOT"] = temporary
        try:
            # Even import-time accidental traffic must fail before it reaches a peer.
            with patch.object(socket.socket, "connect", forbidden), patch.object(socket.socket, "connect_ex", forbidden), patch.object(socket, "create_connection", forbidden):
                module = load_plugin(directory)
                root = Path(temporary)
                cases = []
                for index, explicit in enumerate((False, True)):
                    cases.append(await foreground_case(module, root / str(index), explicit))
                for index, explicit in enumerate((False, True), 2):
                    cases.append(await autonomy_case(module, root / str(index), explicit))
                for index, explicit in enumerate((False, True), 4):
                    cases.append(await foreground_case(module, root / str(index), explicit, tool_media=True))
                catalog_bot = module.HiganPlugin.__new__(module.HiganPlugin)
                count = len(catalog_bot._tool_schemas())
                assert [case["provider_callback_count"] for case in cases] == [2, 2, 1, 1, 2, 2]
                assert [case["simulated_send_count"] for case in cases] == [1, 2, 0, 1, 1, 2]
                return {"ok": True, "offline_only": True, "test_count": len(cases),
                        "tool_count": count, "tool_count_is_106": count == 106,
                        "cases": cases, "source_platform_sends": 0, "model_calls": 0,
                        "network_calls": 0, "ingress_adapter_live_tested": False,
                        "live_policy_tested": False, "controller_is_fixture": True}
        finally:
            sys.dont_write_bytecode = previous_bytecode
            # Restore process configuration even when an assertion fails.
            if previous_root is None:
                os.environ.pop("HIGAN_ROOT", None)
            else:
                os.environ["HIGAN_ROOT"] = previous_root


def main():
    """Require explicit paths and write only metadata to the requested report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = asyncio.run(probe(args.plugin.resolve()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
