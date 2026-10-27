"""Real read-only delegated fact research followed by explicit native parent writes."""

import asyncio
from copy import deepcopy
import json

from plugin.delegated_runner import run_worker
from plugin.memory import Memory
from test_production_integration import Event, _call

pytest_plugins = ("test_production_integration",)


def test_native_worker_reads_proposes_parent_classifies_without_child_mutations(production, tmp_path):
    """A child can organize evidence, but only the actual parent event applies labels."""
    plugin = production()
    store = Memory(str(tmp_path / "memory" / "memory.json"))
    store.add_fact("visible alpha research fact", "42")
    store.add_fact("visible beta research fact", "43")
    store.add_fact("private hidden research fact", "private:99", categories=["secret-label"])
    ids = [row["id"] for row in store.list_facts(None, current_scope="42")]
    proposal = {"ids": ids, "categories": ["model-proposed custom label"]}
    responses = iter([
        ("", [_call("facts_list", call_id="read-list"),
              _call("facts_search", {"query": "research"}, "read-search"),
              _call("facts_categories", call_id="read-categories")]),
        ("", [_call("facts_classify", proposal, "denied-classify"),
              _call("facts_delete", {"id": ids[0]}, "denied-delete")]),
        (json.dumps({"reply_to": [], "text": json.dumps(proposal)}), []),
    ])
    requests, dispatched = [], []
    original_dispatch = plugin._dispatch_tool
    before = deepcopy(store.data)
    parent_context = {"parent": {"messages": [{"id": "untouched"}]}}
    plugin._turn_context = deepcopy(parent_context)

    async def model(system, prompt, images, *, messages, allow_tools, tool_schemas):
        """Mock only native model transport, retaining real worker routing and loop."""
        requests.append(deepcopy({"messages": messages, "schemas": tool_schemas}))
        return next(responses)

    async def dispatch(name, args, scope, event=None):
        """Observe actual production dispatch calls without replacing fact handlers."""
        dispatched.append((name, scope, event))
        return await original_dispatch(name, args, scope, event=event)

    async def inbox():
        """Provide an empty real-shape manager inbox without invented task updates."""
        return []

    async def scenario():
        """Exercise worker proposal receipt before applying it in the parent context."""
        plugin._llm_tools = model
        plugin._dispatch_tool = dispatch
        result = await run_worker(plugin, "Read visible facts and propose categories", "42", inbox)
        assert json.loads(result) == proposal
        assert Memory(store.path).load().data == before
        assert plugin._turn_context == parent_context
        assert dispatched == [(name, "42", None) for name in
                              ("facts_list", "facts_search", "facts_categories")]
        advertised = {row["function"]["name"] for row in requests[0]["schemas"]}
        assert {"facts_list", "facts_search", "facts_categories"} <= advertised
        assert not {"facts_classify", "facts_delete", "facts_edit", "remember"} & advertised
        first_tools = [json.loads(row["content"]) for row in requests[1]["messages"]
                       if row.get("role") == "tool"]
        assert len(first_tools) == 3
        serialized = json.dumps(first_tools)
        assert "private hidden" not in serialized and "secret-label" not in serialized
        denied = [json.loads(row["content"]) for row in requests[2]["messages"]
                  if row.get("tool_call_id") in {"denied-classify", "denied-delete"}]
        assert len(denied) == 2 and all("permission_denied" in json.dumps(row) for row in denied)
        applied = await original_dispatch("facts_classify", json.loads(result), "42", event=Event())
        assert applied["ok"] and applied["updated"] == 2

    asyncio.run(scenario())
    visible = Memory(store.path).load().list_facts(None, current_scope="42")
    assert len(visible) == 2 and all(row["categories"] == proposal["categories"] for row in visible)
    hidden = Memory(store.path).load().list_facts(None)[-1]
    assert hidden["categories"] == ["secret-label"]
