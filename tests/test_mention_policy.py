"""Offline regressions for persisted mention-only admission and stale queued replies.

The AST fixture compiles production methods unchanged; only transport and model
boundaries are replaced. Every policy file lives under the fixture's HIGAN_ROOT.
"""

import asyncio
from enum import Enum
import json
from types import SimpleNamespace

import pytest

from test_production_integration import At, Event, Reply

# Register the shared AST fixture rather than copying its production loader.
pytest_plugins = ("test_production_integration",)


class ComponentType(Enum):
    """Mirror enum-backed framework types without importing AstrBot startup."""

    At = "at"
    Reply = "reply"


def _policy(root, *, mention_only=(), muted=()):
    """Persist actual restriction inputs in an isolated temporary configuration."""
    (root / "groups.json").write_text(json.dumps({
        "mention_only": list(mention_only), "muted": list(muted),
    }), encoding="utf-8")


async def _empty_forward(event):
    """Keep the entry path offline while preserving all real admission methods."""
    return ""


def _entry(plugin):
    """Disable unrelated forward/wake configuration, never policy or At parsing."""
    plugin._forward_to_text = _empty_forward
    plugin._wake_hit = lambda *args: "word"
    plugin._rate_ok = lambda *args: True


@pytest.mark.parametrize("components", [
    [],
    [At(qq="80001")],
    [Reply(chain=[At(qq="90001")], message_str="@90001")],
    [{"type": "reply", "data": {"message": [{"type": "at", "data": {"qq": "90001"}}]}}],
    [SimpleNamespace(type=ComponentType.Reply, chain=[At(qq="90001")])],
])
def test_wake_quote_and_other_mention_never_authorize_entry(production, tmp_path, components):
    """Framework wake and quoted At are not a current direct self mention."""
    _policy(tmp_path, mention_only=["42"])
    plugin = production()
    _entry(plugin)
    event = Event("wake-word @90001 [CQ:at,qq=90001]", components=components)
    event.is_at_or_wake_command = True
    handled = []

    async def handle(*args):
        """Observe dispatch only; forbidden events must never reach this boundary."""
        handled.append(args)

    plugin._handle_one = handle
    assert plugin._is_woken(event) is False
    assert plugin._directly_mentioned(event) is False
    assert plugin._policy_allows(event, "42") is False
    asyncio.run(plugin.on_message(event))
    assert handled == [] and event.sent == []
    assert not any(plugin._pending.values())
    assert plugin._hist().load("42") and event.text in plugin._hist().load("42")[-1]
    assert event.stopped


@pytest.mark.parametrize("component", [
    At(qq="90001"),
    SimpleNamespace(type=ComponentType.At, qq="90001"),
    {"type": "at", "data": {"qq": "90001"}},
    {"type": ComponentType.At, "data": {"qq": 90001}},
])
def test_actual_top_level_self_mentions_admit_and_send(production, tmp_path, component):
    """Class, enum, and OneBot dictionary mentions share real admission semantics."""
    _policy(tmp_path, mention_only=["42"])
    plugin = production()
    _entry(plugin)
    event = Event("direct question", components=[component])
    decisions = []

    async def decide(event, group, text, has_image):
        """Replace only model I/O, leaving actual entry, handle, and send active."""
        decisions.append((group, text))
        return "accepted direct mention"

    plugin._decide_tools_first = decide
    assert plugin._at_targets(event) == ["90001"]
    assert plugin._directly_mentioned(event) is True
    asyncio.run(plugin.on_message(event))
    assert len(decisions) == 1 and decisions[0][0] == "42"
    assert [record["body"] for record in json.loads(decisions[0][1])["messages"]] == [event.text]
    assert len(event.sent) == 1
    assert plugin._hist().load("42")[-1] == "彼岸酱：accepted direct mention"


def test_at_targets_reads_both_top_level_streams_deduplicates_and_matches_exactly(production):
    """Distinct component streams count, but duplicates, prefixes, and nested quotes do not."""
    plugin = production()
    event = Event(components=[At(qq="190001"), At(qq="900010"), At(qq="all")])
    event.message_obj.message = [At(qq="90001"), At(qq="90001"),
                                 Reply(chain=[At(qq="80001")])]
    assert plugin._at_targets(event) == ["190001", "900010", "90001"]
    assert plugin._directly_mentioned(event)
    event.message_obj.message = [Reply(chain=[At(qq="90001")])]
    assert not plugin._directly_mentioned(event)


@pytest.mark.parametrize("restriction", ["mention_only", "muted"])
def test_queued_handle_rechecks_policy_changed_after_admission(production, tmp_path, restriction):
    """A queued normal message cannot run after persisted restrictions are tightened."""
    plugin = production()
    _entry(plugin)
    first, queued = Event("active first"), Event("queued second", message_id="1235")
    decisions = []

    async def decide(event, group, text, has_image):
        """Queue a second accepted event before restricting the real durable policy."""
        decisions.append([record["body"] for record in json.loads(text)["messages"]])
        assert plugin._policy_allows(queued, "42")
        await plugin.on_message(queued)
        assert len(plugin._pending["42"]) == 1
        _policy(tmp_path, **{restriction: ["42"]})
        return "stale generated reply"

    plugin._decide_tools_first = decide
    asyncio.run(plugin.on_message(first))
    # The first send and second dequeue each recheck independently; neither leaks.
    assert decisions == [["active first"]]
    assert first.sent == queued.sent == []
    assert len(plugin._hist().load("42")) == 2
    assert not any(plugin._pending.values())


@pytest.mark.parametrize("restriction", ["mention_only", "muted"])
def test_send_rechecks_new_persisted_restriction(production, tmp_path, restriction):
    """An earlier admission decision cannot authorize a now-restricted final send."""
    plugin, event = production(), Event()
    assert plugin._policy_allows(event, "42")
    _policy(tmp_path, **{restriction: ["42"]})
    assert asyncio.run(plugin._send(event, "42", "must remain private")) is None
    assert event.sent == [] and plugin._hist().load("42") == []
    assert plugin._last_sent == {} and plugin._last_reply_at == {}


def test_muted_blocks_even_an_actual_direct_self_mention(production, tmp_path):
    """Mute overrides mention admission rather than being bypassed by framework wake."""
    _policy(tmp_path, mention_only=["42"], muted=["42"])
    plugin, event = production(), Event(components=[At(qq="90001")])
    assert plugin._directly_mentioned(event)
    assert not plugin._policy_allows(event, "42")
    assert asyncio.run(plugin._send(event, "42", "muted reply")) is None
    assert event.sent == []


@pytest.mark.parametrize("group", ["42", ""])
def test_default_group_and_private_conversation_preserve_normal_reply(production, tmp_path, group):
    """An absent policy defaults open; group restrictions must not silence private chat."""
    if not group:
        _policy(tmp_path, mention_only=["42", "private:10001"], muted=["42", "private:10001"])
    plugin, event = production(), Event("normal conversation", group=group)
    _entry(plugin)
    decisions = []

    async def decide(event, scope, text, has_image):
        """Expose the actual resolved scope without introducing a policy substitute."""
        decisions.append(scope)
        return "normal visible answer"

    plugin._decide_tools_first = decide
    asyncio.run(plugin.on_message(event))
    scope = group or "private:10001"
    assert decisions == [scope] and len(event.sent) == 1
    assert plugin._hist().load(scope)[-1] == "彼岸酱：normal visible answer"
