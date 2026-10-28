"""Disabled chat commands never become framework wake or default-model input."""
import ast
import asyncio
from pathlib import Path

import test_production_integration as integration

production = integration.production


def test_plugin_has_no_registered_chat_commands():
    """Keep implementation helpers while removing all slash command registration."""
    source = Path(__file__).resolve().parents[1] / 'plugin/main.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                   and node.func.attr in {'command', 'command_group'} for node in ast.walk(tree))


def test_slash_input_is_consumed_without_model_or_send(production):
    """A bare slash or disabled command cannot be handed back to the host greeting."""
    for text in ('/', '/help', '/sid', '/表情列表'):
        bot = production()
        event = integration.Event(text)
        event.is_at_or_wake_command = True
        handled = []
        async def handle(*args):
            """Record unexpected model admission without running a provider."""
            handled.append(args)
        bot._handle_one = handle
        asyncio.run(bot.on_message(event))
        assert event.stopped and not event.sent and not handled
        assert not any(bot._pending.values())


def test_framework_wake_flags_do_not_forge_direct_at(production):
    """Only native self mention segments or a direct private route count as wake."""
    bot = production()
    event = integration.Event('/')
    event.is_at_or_wake_command = True
    event.is_wake_command = True
    event.is_at_command = True
    assert not bot._is_woken(event)
    event = integration.Event('', components=[integration.At(qq='90001')])
    assert bot._is_woken(event)
