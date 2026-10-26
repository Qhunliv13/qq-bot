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

# Model-directed completion is the default; callers may opt into a positive limit.
MAX_ROUNDS = None
# Legacy import compatibility only; failures must never become assistant replies.
SAFE_FINAL = "本轮未获得可用的最终回复；已执行步骤以工具记录为准。"
FINAL_INSTRUCTION = (
    "Provide a final response from the available conversation and tool records, "
    "noting any uncertainty. This request is for a text response without tool calls."
)
_LOG = logging.getLogger(__name__)


class ModelCompletionUnavailable(RuntimeError):
    """Expose failed finalization to the host without inventing an assistant reply.

    Only machine-readable reason and actual attempted tool names are retained here;
    the supplied transcript/archive owns observations. Hosts can report task failure
    without replaying tools or mistaking a canned fallback for model completion.
    """

    def __init__(self, reason: str, used_tools: list[str]):
        self.reason = reason
        self.used_tools = tuple(used_tools)
        self.tool_attempt_count = len(self.used_tools)
        super().__init__(f"model_completion_unavailable: {reason}")


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
              max_rounds: int | None = MAX_ROUNDS, *,
              llm_messages: Callable | None = None,
              transcript: list[dict] | None = None,
              observation_callback: Callable | None = None) -> tuple:
    """Run until model completion or an optional positive limit; return text, names.

    None and nonpositive max_rounds leave completion to the model. A positive
    count limits tool-enabled requests before a final text-only request.
    llm_messages receives (messages, allow_tools=bool) and returns (text, calls).
    transcript, when supplied, is populated in place (start with an empty list).
    observation_callback(record) may be sync or async and can persist each actual
    outcome, including argument/runner failures. Archive failures are logged but
    cannot undo successful tools or lose the in-memory evidence. Callers own scope
    selection and archive paths; no observations are promoted into durable facts.
    Failed forced finalization raises ModelCompletionUnavailable, preserving the
    transcript and exception cause instead of appending a fabricated final reply.
    """
    messages = transcript if transcript is not None else []
    messages.extend([{"role": "system", "content": system}, {"role": "user", "content": prompt}])
    used: list[str] = []
    seen_ids: set[str] = set()
    # Nonpositive values deliberately disable the optional caller-selected limit.
    rounds = int(max_rounds) if max_rounds is not None else None
    rounds = rounds if rounds is not None and rounds > 0 else None

    async def ask(allow_tools: bool) -> tuple:
        """Use one transcript algorithm for both native and legacy injected LLMs."""
        if llm_messages is not None:
            return await llm_messages(messages, allow_tools=allow_tools)
        # Legacy transport sees all results and their associations as valid JSON.
        return await llm(system, _json(messages[1:]))

    async def finish() -> tuple:
        """Make one text-only attempt, raising on failure without replaying tools."""
        # Runtime finalization is a system message, never an invented human turn.
        messages.append({"role": "system", "content": FINAL_INSTRUCTION})
        try:
            final_text, final_calls = await ask(False)
        except Exception as exc:
            _LOG.warning("Final no-tools model call failed", exc_info=True)
            # Preserve the provider cause internally, not in a canned user reply.
            raise ModelCompletionUnavailable("provider_error", used) from exc
        # Reject the whole draft when a provider ignores disabled tools; none of
        # these extra requests may execute, even if the text claims success.
        if final_calls:
            raise ModelCompletionUnavailable("unexpected_tool_calls", used)
        final = str(final_text or "").strip()
        if not final:
            # Unlike ordinary model-directed silence, a forced final must resolve
            # the interrupted/limited run; empty output does not prove completion.
            raise ModelCompletionUnavailable("empty_response", used)
        messages.append({"role": "assistant", "content": final})
        return final, used

    _round = 0
    while rounds is None or _round < rounds:
        try:
            text, calls = await ask(True)
        except Exception:
            _LOG.warning("Tool-loop model call failed in round %s", _round, exc_info=True)
            return await finish()
        if not calls:
            # Empty text with no calls is a valid model-directed silent completion.
            final = str(text or "").strip()
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
        _round += 1
    return await finish()
