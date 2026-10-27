"""Exercise offline publication probe media cases with production AST methods.

The installed-framework deployment probe retains native constructors; these local
contracts reuse the existing framework transport doubles and never open sockets.
"""

import asyncio
import copy
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_production_integration import Image

pytest_plugins = ("test_production_integration",)


@pytest.mark.parametrize("explicit", [False, True])
def test_offline_probe_tool_media_cases_use_actual_dispatch_and_send(
        production, tmp_path, monkeypatch, explicit):
    """An implicit final stays draft while explicit new permits the same image again."""
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "tools"))
    probe = importlib.import_module("explicit_publication_probe")

    def isolated_instance(module, root, replies):
        """Replace probe bootstrap only; production turn/dispatch/media/sender stay real."""
        root.mkdir(parents=True, exist_ok=True)
        bot = production()
        requests = []
        scripted = iter(replies)

        async def provider(system, prompt, images, messages=None, allow_tools=True, tool_schemas=None):
            """Replay two exact provider responses without any external transport."""
            requests.append(copy.deepcopy(messages))
            return next(scripted)

        bot._llm_tools = provider
        bot._turn_context["42"] = {"messages": [], "history": [], "image_refs": []}
        return bot, requests

    monkeypatch.setattr(probe, "instance", isolated_instance)
    module = SimpleNamespace(__package__="plugin", Image=Image)
    result = asyncio.run(probe.foreground_case(module, tmp_path / "media", explicit, tool_media=True))
    assert result["name"] == ("tool_media_explicit_new" if explicit else "tool_media_raw_marker")
    assert result["provider_callback_count"] == 2
    assert result["paired_tool_result_count"] == 1
    assert result["simulated_send_count"] == (2 if explicit else 1)
    assert result["receipt_message_ids"] == ["900101"]
