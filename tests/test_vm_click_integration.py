"""Offline native-loop contracts for the production two-step VM click path."""

import asyncio
import base64
import copy
import hashlib
import io
import json

from PIL import Image
import pytest

import test_production_integration as integration
from plugin import vm_click_gate
from plugin.reply_selection import decode_reply


production = integration.production
Event = integration.Event
_call = integration._call


@pytest.fixture
def click_host(production, tmp_path, monkeypatch):
    """Use AST-loaded production methods with only local image and VM fakes."""
    host = production()
    monkeypatch.setitem(host._dispatch_tool.__func__.__globals__, "HIGAN_ROOT", str(tmp_path))
    images = tmp_path / "images"
    images.mkdir()
    source = images / "current.png"
    Image.new("RGB", (160, 120), (192, 192, 192)).save(source)
    clicks = []
    shots = []

    async def fake_vm(name, args, *, verified_click=False):
        """Record genuine screenshot/click requests without touching the VM or QQ."""
        if name == "vm_shot":
            shots.append(source.read_bytes())
            return {"ok": True, "path": str(source)}
        if name == "vm_click":
            assert verified_click is True, "native clicks must pass the verified gate"
            clicks.append((args["x"], args["y"]))
            return {"ok": True}
        raise AssertionError(f"unexpected VM call: {name}")

    # The original guard is exercised separately before replacing the HTTP edge.
    host._vm_tool = fake_vm
    def image_url(ref):
        """Encode real marker pixels as the fake model's image_url input."""
        with open(ref, "rb") as image_file:
            return "data:image/png;base64," + base64.b64encode(image_file.read()).decode("ascii")

    host._as_llm_url = image_url
    return host, source, clicks, shots


def _results(messages):
    """Return real native-loop tool observations in their paired call order."""
    return [json.loads(message["content"]) for message in messages if message["role"] == "tool"]


def _image_bytes(messages):
    """Extract actual image_url pixels sent to the model, never a metadata path."""
    urls = [part["image_url"]["url"] for message in messages
            if isinstance(message.get("content"), list)
            for part in message["content"] if part.get("type") == "image_url"]
    return [base64.b64decode(url.split(",", 1)[1]) for url in urls
            if url.startswith("data:image/png;base64,")]


def _confirm(preview):
    """Build a confirmation from the observed result, not a predictable token."""
    return {key: preview[key] for key in ("ticket", "x", "y", "screen_sha256")} | {"viewed": True}


def _assert_unattributed_final(raw, expected):
    """Decode direct native-loop JSON without inventing a current QQ message ID."""
    result = decode_reply(raw, [], allow_unattributed=True)
    assert result["structured"] and result["error"] is None
    assert result["reply_to"] == [] and result["text"] == expected


def test_native_preview_image_then_confirm_clicks_once(click_host):
    """The next model request sees marked pixels before one unchanged-screen click."""
    host, source, clicks, shots = click_host
    requests = []
    raw = source.read_bytes()

    async def model(_system, _prompt, _refs, messages, allow_tools):
        """React to the real preview observation in a later model round."""
        assert allow_tools
        requests.append(copy.deepcopy(messages))
        if len(requests) == 1:
            return "", [_call("vm_click_preview", {"x": 45, "y": 55, "label": "target"}, "preview")]
        if len(requests) == 2:
            observation = _results(messages)[0]
            assert observation["tool_name"] == "vm_click_preview"
            preview = observation["result"]
            assert preview["ok"] and preview["ticket"] and preview["screen_sha256"]
            assert preview["path"].startswith(str(source.parent))
            assert source.read_bytes() == raw
            markers = _image_bytes(messages)
            assert markers and markers[-1] != raw
            with Image.open(io.BytesIO(markers[-1])) as marker:
                assert marker.size == (160, 120)
                assert marker.convert("RGB").getpixel((45, 55)) != (192, 192, 192)
            return "", [_call("vm_click_confirm", _confirm(preview), "confirm")]
        return json.dumps({"reply_to": [], "text": "Clicked after visual review."}), []

    host._llm_tools = model
    _assert_unattributed_final(asyncio.run(host._decide_tools_first(Event(), "42", "current question", False)),
                               "Clicked after visual review.")
    assert clicks == [(45, 55)] and len(shots) == 2
    assert source.read_bytes() == raw and len(requests) == 3
    paired = _results(requests[-1])
    assert [row["tool_name"] for row in paired] == ["vm_click_preview", "vm_click_confirm"]
    assert paired[-1]["result"]["ok"] is True
    assert [message["tool_call_id"] for message in requests[-1]
            if message["role"] == "tool"] == ["preview", "confirm"]


def test_native_confirm_denies_changed_screen(click_host):
    """Even a visually observed ticket cannot authorize changed current pixels."""
    host, source, clicks, shots = click_host
    observed = []

    async def model(_system, _prompt, _refs, messages, allow_tools):
        """"Mutate the screenshot after model observation but before verification."""
        observed.append(copy.deepcopy(messages))
        if len(observed) == 1:
            return "", [_call("vm_click_preview", {"x": 30, "y": 40}, "preview")]
        if len(observed) == 2:
            assert _image_bytes(messages)
            Image.new("RGB", (160, 120), (32, 32, 32)).save(source)
            return "", [_call("vm_click_confirm", _confirm(_results(messages)[0]["result"]), "confirm")]
        return json.dumps({"reply_to": [], "text": "No click performed."}), []

    host._llm_tools = model
    _assert_unattributed_final(asyncio.run(host._decide_tools_first(Event(), "42", "current question", False)),
                               "No click performed.")
    assert clicks == [] and len(shots) == 2
    assert _results(observed[-1])[-1]["result"]["error"] == "screen_changed"


def test_native_confirm_without_visual_pixels_denied(click_host):
    """A preview's JSON metadata is not evidence of image_url model access."""
    host, _source, clicks, shots = click_host
    host._as_llm_url = lambda _ref: ""  # Conversion fails: no pixel-bearing image_url.
    requests = []

    async def model(_system, _prompt, _refs, messages, allow_tools):
        """"Use a valid ticket despite receiving no visible preview pixels."""
        requests.append(copy.deepcopy(messages))
        if len(requests) == 1:
            return "", [_call("vm_click_preview", {"x": 30, "y": 40}, "preview")]
        if len(requests) == 2:
            assert not _image_bytes(messages)
            return "", [_call("vm_click_confirm", _confirm(_results(messages)[0]["result"]), "confirm")]
        return json.dumps({"reply_to": [], "text": "No visual confirmation."}), []

    host._llm_tools = model
    _assert_unattributed_final(asyncio.run(host._decide_tools_first(Event(), "42", "current question", False)),
                               "No visual confirmation.")
    assert clicks == [] and len(shots) == 1
    assert _results(requests[-1])[-1]["result"]["error"] == "preview_image_not_seen_by_model"


def test_same_native_batch_cannot_claim_preview_was_seen(click_host, monkeypatch):
    """Even a matching ticket in the preview batch lacks a later visual model turn."""
    host, source, clicks, shots = click_host
    monkeypatch.setattr(vm_click_gate.secrets, "token_urlsafe", lambda _size: "known-only-for-batch-test")
    with Image.open(source) as image:
        digest = hashlib.sha256(b"160x120:" + image.convert("RGB").tobytes()).hexdigest()
    requests = []

    async def model(_system, _prompt, _refs, messages, allow_tools):
        """"Return both calls before any preview marker can reach a new request."""
        requests.append(copy.deepcopy(messages))
        if len(requests) == 1:
            confirmation = {"ticket": "known-only-for-batch-test", "x": 45, "y": 55,
                            "screen_sha256": digest, "viewed": True}
            return "", [_call("vm_click_preview", {"x": 45, "y": 55}, "preview"),
                        _call("vm_click_confirm", confirmation, "confirm")]
        return json.dumps({"reply_to": [], "text": "Batch confirmation denied."}), []

    host._llm_tools = model
    _assert_unattributed_final(asyncio.run(host._decide_tools_first(Event(), "42", "current question", False)),
                               "Batch confirmation denied.")
    assert clicks == [] and len(shots) == 1
    assert _results(requests[-1])[-1]["result"]["error"] == "preview_image_not_seen_by_model"
    assert not _image_bytes(requests[0]) and _image_bytes(requests[-1])


def test_original_vm_guard_denies_direct_and_text_clicks(production):
    """The real native VM route blocks bypass calls before any HTTP request."""
    host = production()
    for name, args in (("vm_click", {"x": 45, "y": 55}),
                       ("vm_click_text", {"text": "button"}),
                       ("vm_click_preview", {"x": 45, "y": 55}),
                       ("vm_click_confirm", {})):
        assert asyncio.run(host._vm_tool(name, args)) == {"ok": False, "error": "preview_and_confirm_required"}
