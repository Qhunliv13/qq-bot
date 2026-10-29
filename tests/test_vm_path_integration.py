"""Offline model/image/gate/runtime contracts; no live VM or installed path backend."""

import asyncio
import copy
import hashlib
import io
import json

from PIL import Image
import pytest

import test_vm_click_integration as click_integration
from test_vm_click_integration import Event, _call, _results, _image_bytes, _confirm

# Export fixture objects explicitly so pytest discovers them without unused imports.
click_host = click_integration.click_host
production = click_integration.production
from plugin import vm_click_gate
from plugin.vm_pointer_runtime import execute_pointer_path


def action():
    """Return detached wire intent in the fixture's actual screenshot coordinates."""
    return {"version": 1, "button": "left", "width": 160, "height": 120,
            "points": [{"x": 30, "y": 40, "t_ms": 0},
                       {"x": 70, "y": 60, "t_ms": 100},
                       {"x": 90, "y": 70, "t_ms": 200}]}


def confirmation(preview):
    """Bind the complete returned path rather than reconstructing partial intent."""
    return {key: copy.deepcopy(preview[key]) for key in
            ("ticket", "action", "screen_sha256")} | {"viewed": True}


@pytest.fixture
def path_host(click_host):
    """Install a trusted async adapter to the real deterministic runtime with fake IO."""
    host, source, clicks, shots = click_host
    commands, received = [], []
    now = [0.0]

    def runner(argv, *, timeout_s):
        """Record exact argv and enforce the adapter's acknowledged-command contract."""
        assert timeout_s > 0
        commands.append(tuple(argv))
        return 0

    def sleep(seconds):
        """Advance deterministic time without real sleeps or desktop access."""
        now[0] += seconds

    async def trusted_adapter(canonical):
        """Accept immutable gate authority and reuse the sole pointer executor."""
        received.append(canonical)
        with pytest.raises(AttributeError):
            canonical.width = 1
        with pytest.raises(AttributeError):
            canonical.points[0].x = 0
        wire = canonical._asdict()
        wire["points"] = [point._asdict() for point in canonical.points]
        return execute_pointer_path(wire, runner=runner, sleep=sleep, clock=lambda: now[0])

    host._vm_pointer_executor = trusted_adapter
    return host, source, clicks, shots, commands, received


@pytest.mark.parametrize("failure,expected,shot_count", [
    (None, None, 2),
    ("authorized", None, 2),
    ("wrong_action", "preview_confirmation_mismatch", 1),
    ("missing_vision", "preview_image_not_seen_by_model", 1),
    ("changed_screen", "screen_changed", 2),
    ("unavailable", "backend_unavailable", 1),
])
def test_native_path_visual_gate_to_real_runtime(path_host, failure, expected, shot_count):
    """Only the exact observed unchanged path reaches real runtime command planning."""
    host, source, clicks, shots, commands, received = path_host
    requests = []
    raw = source.read_bytes()
    if failure == "missing_vision":
        host._as_llm_url = lambda _ref: ""
    if failure == "unavailable":
        host._vm_pointer_executor = None
    if failure == "authorized":
        legacy = host._vm_pointer_executor

        class AuthorizedAdapter:
            """Prove main selects the explicit lease method without signature fallback."""

            async def __call__(self, canonical):
                """Reject accidental use of the legacy invocation route."""
                raise AssertionError("legacy route must not run")

            async def execute_authorized(self, canonical, *, lease):
                """Inspect trusted timing authority before the existing fake runtime."""
                assert type(lease) is vm_click_gate.ExecutionLease
                assert lease.deadline > lease.clock()
                lease.check()
                return await legacy(canonical)

        host._vm_pointer_executor = AuthorizedAdapter()

    async def model(_system, _prompt, _refs, messages, allow_tools):
        """Observe transported PNG pixels then confirm in a distinct successful request."""
        requests.append(copy.deepcopy(messages))
        if len(requests) == 1:
            return "", [_call("vm_path_preview", {"action": action(), "label": "path"}, "preview")]
        if len(requests) == 2:
            preview = _results(messages)[0]["result"]
            assert preview["ok"] and preview["action"] == action()
            assert source.read_bytes() == raw
            pixels = _image_bytes(messages)
            if failure != "missing_vision":
                assert pixels and pixels[-1] != raw
                with Image.open(io.BytesIO(pixels[-1])) as marker:
                    assert marker.size == (160, 120)
                    assert marker.convert("RGB").getpixel((70, 60)) != (192, 192, 192)
            else:
                assert not pixels
            args = confirmation(preview)
            if failure == "wrong_action":
                args["action"]["points"][1]["t_ms"] += 1
            if failure == "changed_screen":
                Image.new("RGB", (160, 120), "black").save(source)
            return "", [_call("vm_path_confirm", args, "confirm"),
                        _call("vm_path_confirm", args, "replay")]
        return json.dumps({"reply_to": [], "text": "Path result observed."}), []

    host._llm_tools = model
    asyncio.run(host._decide_tools_first(Event(), "42", "path", False))
    results = _results(requests[-1])
    assert results[2]["result"] == {"ok": False, "error": "no_pending_preview"}
    assert clicks == [] and len(shots) == shot_count
    if expected:
        assert results[1]["result"] == {"ok": False, "error": expected}
        assert commands == received == []
    else:
        assert results[1]["result"] == {"ok": True, "receipt": {
            "ok": True, "version": 1, "points_executed": 3}}
        assert len(received) == 1
        assert commands == [("xdotool", "mousemove", "30", "40"),
                            ("xdotool", "mousedown", "1"),
                            ("xdotool", "mousemove", "70", "60"),
                            ("xdotool", "mousemove", "90", "70"),
                            ("xdotool", "mouseup", "1")]


def test_path_same_model_batch_cannot_claim_vision(path_host, monkeypatch):
    """Even a known valid ticket/hash cannot manufacture a visual model request."""
    host, source, clicks, shots, commands, received = path_host
    monkeypatch.setattr(vm_click_gate.secrets, "token_urlsafe", lambda _size: "batch-ticket")
    with Image.open(source) as image:
        digest = hashlib.sha256(b"160x120:" + image.convert("RGB").tobytes()).hexdigest()
    requests = []

    async def model(_system, _prompt, _refs, messages, allow_tools):
        """Return both calls before any marked pixels can enter a provider request."""
        requests.append(copy.deepcopy(messages))
        if len(requests) == 1:
            return "", [_call("vm_path_preview", {"action": action()}, "preview"),
                        _call("vm_path_confirm", {"ticket": "batch-ticket", "action": action(),
                              "screen_sha256": digest, "viewed": True}, "confirm")]
        return json.dumps({"reply_to": [], "text": "Denied."}), []

    host._llm_tools = model
    asyncio.run(host._decide_tools_first(Event(), "42", "path", False))
    assert _results(requests[-1])[-1]["result"] == {
        "ok": False, "error": "preview_image_not_seen_by_model"}
    assert commands == received == clicks == [] and len(shots) == 1


@pytest.mark.parametrize("first", ["click", "path"])
def test_click_and_path_share_one_revoking_slot(path_host, first):
    """A preview of either kind revokes the other kind, including its observed token."""
    host, _source, clicks, shots, commands, received = path_host
    requests, previews = [], {}
    second = "path" if first == "click" else "click"

    def preview_call(kind):
        """Build distinct valid actions so a second gate would wrongly preserve the first."""
        args = {"action": action()} if kind == "path" else {"x": 45, "y": 55}
        return _call(f"vm_{kind}_preview", args, kind)

    async def model(_system, _prompt, _refs, messages, allow_tools):
        """Observe both previews in separate requests then attempt the revoked first one."""
        requests.append(copy.deepcopy(messages))
        if len(requests) == 1:
            return "", [preview_call(first)]
        if len(requests) == 2:
            assert _image_bytes(messages)
            previews[first] = _results(messages)[0]["result"]
            return "", [preview_call(second)]
        if len(requests) == 3:
            assert len(_image_bytes(messages)) == 2
            old = previews[first]
            args = confirmation(old) if first == "path" else _confirm(old)
            return "", [_call(f"vm_{first}_confirm", args, "old-confirm")]
        return json.dumps({"reply_to": [], "text": "Revoked."}), []

    host._llm_tools = model
    asyncio.run(host._decide_tools_first(Event(), "42", "path", False))
    assert _results(requests[-1])[-1]["result"] == {
        "ok": False, "error": "preview_confirmation_mismatch"}
    assert commands == received == clicks == [] and len(shots) == 2


@pytest.mark.parametrize("name", ["vm_path_preview", "vm_path_confirm"])
def test_path_raw_entrypoints_deny_even_verified_click(production, name):
    """Neither direct dispatch nor a click-only internal flag creates path authority."""
    host = production()
    expected = {"ok": False, "error": "preview_and_confirm_required"}
    assert asyncio.run(host._dispatch_tool(name, {"action": action()}, "42", Event())) == expected
    for verified in (False, True):
        assert asyncio.run(host._vm_tool(name, {"action": action()}, verified_click=verified)) == expected


def test_path_schema_and_default_capability_are_honest(production):
    """Full catalog and discovery expose exact bounded intent, never endpoint controls."""
    host = production()
    assert getattr(host, "_vm_pointer_executor", None) is None
    schemas = {item["function"]["name"]: item["function"] for item in host._tool_schemas()}
    for name in ("vm_path_preview", "vm_path_confirm"):
        schema = schemas[name]
        assert "backend_unavailable" in schema["description"]
        contract = schema["parameters"]["properties"]["action"]
        assert contract["additionalProperties"] is False
        assert contract["properties"]["points"]["items"]["additionalProperties"] is False
        assert contract["properties"]["points"]["maxItems"] == 128
        assert contract["properties"]["points"]["items"]["properties"]["t_ms"]["maximum"] == 10000
    described = asyncio.run(host._dispatch_tool("tool_describe", {
        "names": ["vm_path_preview", "vm_path_confirm"]}, "42", Event()))
    assert "backend_unavailable" in json.dumps(described, ensure_ascii=False)
