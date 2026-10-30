"""Isolated read-only native worker, with no delivery or shared turn-state writes.

The manager owns task lifetime and the final receipt. This adapter reuses the
ordinary native agent loop, retaining actual tool evidence only in its private
transcript; it never archives intermediate results into the parent's scope.
"""

from copy import deepcopy
import json
from pathlib import Path

from . import agent_loop, conversation_context, tool_discovery
from .history_window import history_snapshot
from .reply_selection import decode_reply


# Explicit capabilities, not name-prefix heuristics: new parent tools fail closed.
READ_ONLY_TOOLS = frozenset({
    "web_search", "web_fetch", "history_search", "tool_observations",
    "facts_list", "facts_search", "facts_categories",
    "qq_account_info", "qq_social_status", "workspace_read", "workspace_glob",
    "workspace_grep", "skill_list", "skill_read", "tool_search", "tool_describe",
})


async def run_worker(plugin, task, scope, inbox) -> str:
    """Run one native research task with private messages and scoped read access.

    No temporary monkeypatches or changes to plugin._turn_context are permitted:
    siblings and the parent may be awaiting different native requests concurrently.
    Inbox items remain untrusted task-update data and are polled only immediately
    before a new model request, after all previous tool pairs have completed.
    """
    system = conversation_context.system_prompt(plugin.persona)
    from .context_budget import limits
    history = history_snapshot(
        getattr(plugin, "_recent", {}).get(scope, []),
        getattr(plugin, "_history_origins", {}).get(scope, 0),
        max_chars=limits()["max_context_chars"],
    )
    prompt = conversation_context.build_context(
        scope=scope, messages=[], history=history, clock=plugin._clock_context(),
        task={"kind": "delegated_research", "goal": task, "read_only": True},
        observations=[], trigger={"kind": "delegated_research"},
    )
    # Copy nested schemas too: provider-local normalization cannot race siblings.
    schemas = deepcopy([
        item for item in plugin._tool_schemas()
        if item.get("function", {}).get("name") in READ_ONLY_TOOLS
    ])
    transcript = []

    async def native_llm(messages, allow_tools=True):
        """Drain updates at safe request boundaries without inventing human turns."""
        updates = await inbox()
        if updates:
            # A user-role envelope is data transport, not a fabricated QQ sender.
            messages.append({"role": "user", "content": json.dumps({
                "source": {"name": "delegation_inbox", "trust": "untrusted_data"},
                "items": updates,
            }, ensure_ascii=False, allow_nan=False, separators=(",", ":"))})
        return await plugin._llm_tools(
            system, prompt, [], messages=messages, allow_tools=allow_tools,
            tool_schemas=schemas,
        )

    async def legacy_llm(_system, _prompt):
        """Keep the shared loop's legacy parameter unused by the native path."""
        raise RuntimeError("delegated worker requires native messages")

    async def run_tool(name, args):
        """Deny effects before dispatch and bind historical reads to this scope."""
        if name not in READ_ONLY_TOOLS:
            return {"ok": False, "error": "permission_denied", "tool": name,
                    "reason": "delegated_worker_read_only"}
        args = dict(args)
        if name in {"history_search", "tool_observations"}:
            # An omitted scope means this worker, never an unbounded history read.
            if "scope" in args and args["scope"] != scope:
                return {"ok": False, "error": "permission_denied", "tool": name,
                        "reason": "delegated_worker_scope_mismatch"}
            args["scope"] = scope
        if name in {"tool_search", "tool_describe"}:
            # Discovery sees exactly this worker's catalog, never the parent's
            # write tools. skills_root is required but unused for discovery calls.
            return await tool_discovery.dispatch(
                name, args, schemas, skills_root=Path(__file__).parent,
                can_write=False,
            )
        return await plugin._dispatch_tool(name, args, scope, event=None)

    final, _used = await agent_loop.run(
        system, prompt, legacy_llm, run_tool, llm_messages=native_llm,
        transcript=transcript,
    )
    selection = decode_reply(final, [], allow_unattributed=True)
    if selection["error"]:
        # Invalid attribution is a failed worker, not a successful empty result.
        raise RuntimeError(selection["error"])
    return selection["text"]
