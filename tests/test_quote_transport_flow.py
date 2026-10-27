"""Offline native quote transport lexical-boundary regressions."""
import asyncio

import pytest
import test_production_integration as integration

production = integration.production


@pytest.mark.parametrize("body, visible, quote_id", [
    ("[quote:0xfeed] harmless body", "harmless body", None),
    ("[quote:1234] quoted body", "quoted body", "1234"),
    ("[quote:0xfeed] [quote:1234] quoted body", "quoted body", "1234"),
    ("Teach literal [quote:1234] syntax", "Teach literal [quote:1234] syntax", None),
    ("`[quote:1234]` is an example", "`[quote:1234]` is an example", None),
    ('"[quote:1234]" is quoted text', '"[quote:1234]" is quoted text', None),
    ("```text\n[quote:1234]\n```", "```text\n[quote:1234]\n```", None),
])
def test_real_sender_consumes_only_raw_leading_quote_controls(production, body, visible, quote_id):
    """Invalid controls never leak or become fabricated targets; literal examples remain prose."""
    plugin, event = production(), integration.Event()
    result = asyncio.run(plugin._send(event, "42", body))
    assert result == visible
    assert len(event.sent) == 1
    replies = [part.id for part in event.sent[0].chain if isinstance(part, integration.Reply)]
    assert replies == ([] if quote_id is None else [quote_id])
    assert plugin._hist().load("42") == ["彼岸酱：" + visible]
    if body.startswith("[quote:"):
        assert "[quote:" not in result
