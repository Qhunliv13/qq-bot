#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Injected tool loop with one cumulative native transcript and safe finalization.

The original llm(system, prompt) and run_tool(name, args) contract remains usable.
Hosts can supply llm_messages(messages, allow_tools=True) for native assistant/tool
pairing; legacy callers receive the same transcript as JSON, never overwritten
last-result text. No network, framework import, or implicit fact promotion occurs.
"""

from __future__ import annotations

import inspect
import json
import logging
from typing import Awaitable, Callable
import uuid

from .tool_context import make_observation, redact

# Hard ceiling protects callers that accidentally pass an enormous round count.
MAX_ROUNDS = 3
MAX_SAFE_ROUNDS = 20
SAFE_FINAL = "工具执行已结束，暂时无法生成可靠结论。请稍后重试；未执行任何额外操作。"
FINAL_INSTRUCTION = (
    "Tool execution budget is exhausted. No more execution is allowed. "
    "Return a final answer based only on the observed results, explicitly noting "
    "failures or uncertainty; do not claim any unexecuted action succeeded. "
    "Do not emit tool calls."
)
_LOG = logging.getLogger(__name__)


def _json(value: object) -> str:
    """Serialize shared safe JSON without slicing structured evidence."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _normalize_call(call: object, seen_ids: set[str]) -> tuple[dict, str, object]:
    """Pair every request with an id while keeping original arguments for execution."""
    source = call if isinstance(call, dict) else {}
    function = source.get("function")
    function = function if isinstance(function, dict) else {}
    name = function.get("name")
    name = name.strip() if isinstance(name, str) else ""
    raw_args = function.get("arguments")
    call_id = source.get("id")
    # Missing/duplicate ids cannot safely pair native tool replies; generate once.
    if not isinstance(call_id, str) or not call_id or call_id in seen_ids:
        call_id = "call_" + uuid.uuid4().hex
    seen_ids.add(call_id)
    try:
        parsed_args = _parse_arguments(raw_args)
    except (ValueError, TypeError):
        safe_args = redact(raw_args)
    else:
        # Redact parsed keys before serializing JSON-string argument payloads.
        safe_args = redact(parsed_args)
    normalized = {
        "id": call_id,
        "type": "function",
        "function": {"name": name or "invalid_tool", "arguments": safe_args if isinstance(safe_args, str) else _json(safe_args)},
    }
    return normalized, name, raw_args


def _parse_arguments(raw_args: object) -> dict:
    """Require a JSON object; never silently turn malformed input into {}."""
    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    if not isinstance(args, dict):
        raise ValueError("tool arguments must be a JSON object")
    return args


async def run(system: str, prompt: str,
              llm: Callable[[str, str], Awaitable[tuple]],
              run_tool: Callable[[str, dict], Awaitable[dict]],
              max_rounds: int = MAX_ROUNDS, *,
              llm_messages: Callable | None = None,
              transcript: list[dict] | None = None,
              observation_callback: Callable | None = None) -> tuple:
    """Execute bounded rounds, then get a fresh no-tools answer; return text, names.

    llm_messages receives (messages, allow_tools=bool) and returns (text, calls).
    transcript, when supplied, is populated in place (start with an empty list).
    observation_callback(record) may be sync or async and can persist each actual
    outcome, including argument/runner failures. Archive failures are logged but
    cannot undo successful tools or lose the in-memory evidence. Callers own scope
    selection and archive paths; no observations are promoted into durable facts.
    """
    messages = transcript if transcript is not None else []
    messages.extend([{"role": "system", "content": system}, {"role": "user", "content": prompt}])
    used: list[str] = []
    seen_ids: set[str] = set()
    rounds = max(0, min(MAX_SAFE_ROUNDS, int(max_rounds)))

    async def ask(allow_tools: bool) -> tuple:
        """Use one transcript algorithm for both native and legacy injected LLMs."""
        if llm_messages is not None:
            return await llm_messages(messages, allow_tools=allow_tools)
        # Legacy transport sees all results and their associations as valid JSON.
        return await llm(system, _json(messages[1:]))

    async def finish() -> tuple:
        """Never return an unexecuted pre-tool draft after exhausting the budget."""
        messages.append({"role": "user", "content": FINAL_INSTRUCTION})
        try:
            final_text, final_calls = await ask(False)
            # A provider ignoring disabled tools has not supplied a safe final.
            final = SAFE_FINAL if final_calls else str(final_text or "").strip() or SAFE_FINAL
        except Exception:
            _LOG.warning("Final no-tools model call failed", exc_info=True)
            final = SAFE_FINAL
        messages.append({"role": "assistant", "content": final})
        return final, used

    for _round in range(rounds):
        try:
            text, calls = await ask(True)
        except Exception:
            _LOG.warning("Tool-loop model call failed in round %s", _round, exc_info=True)
            return await finish()
        if not calls:
            final = str(text or "").strip() or SAFE_FINAL
            messages.append({"role": "assistant", "content": final})
            return final, used
        if not isinstance(calls, (list, tuple)):
            # Invalid provider protocol is not executable evidence.
            return await finish()
        normalized = [_normalize_call(call, seen_ids) for call in calls]
        messages.append({"role": "assistant", "content": str(redact(text or "")),
                         "tool_calls": [item[0] for item in normalized]})
        for call, name, raw_args in normalized:
            try:
                args = _parse_arguments(raw_args)
                if not name:
                    raise ValueError("tool name is required")
            except (ValueError, TypeError):
                args = raw_args
                result = {"ok": False, "error": "invalid_tool_arguments", "message": "Tool name and JSON object arguments are required; tool was not invoked."}
            else:
                used.append(name)
                try:
                    result = await run_tool(name, args)
                except Exception as exc:
                    # The observation remains failure evidence, not a claimed success.
                    result = {"ok": False, "error": "tool_execution_failed", "message": str(exc)}
            observation = make_observation(prompt, name or "invalid_tool", args, result,
                                           observation_id=call["id"])
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "content": _json(observation)})
            if observation_callback is not None:
                try:
                    pending = observation_callback(observation)
                    if inspect.isawaitable(pending):
                        await pending
                except Exception:
                    _LOG.warning("Tool observation persistence failed for %s", call["id"], exc_info=True)
    return await finish()
