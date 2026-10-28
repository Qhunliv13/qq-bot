"""Independent injected execution, not a QQ host or authentication framework.

The host supplies model/tool adapters and retains responsibility for identity,
authorization, conversation context and login lifetime. This module reads no
configuration, credentials or persona files and installs no tools (including the
production QQ tool set). Observations/callbacks do not grant identity or authority.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from typing import Awaitable, Callable

from plugin.agent_loop import run


@dataclass
class RunResult:
    """Return actual completion and detached JSON-ready evidence, not history.

    The list/dicts are intentionally mutable; used_tools preserves attempt order.
    """

    text: str
    used_tools: tuple[str, ...]
    transcript: list[dict]


async def _unused_legacy(system: str, prompt: str) -> tuple:
    """Satisfy the legacy positional contract without hiding adapter mistakes."""
    raise RuntimeError("native message adapter must be used")


async def _unavailable_tool(name: str, args: dict) -> dict:
    """Report missing host tooling honestly instead of fabricating success."""
    return {"ok": False, "error": "tool_unavailable"}


class Harness:
    """Serialize complete turns through the existing, single agent-loop engine.

    asyncio.Lock grants FIFO acquisition among waiting tasks. A supplied lock is
    reused unchanged so a host can share its existing execution boundary. No
    persistence, replay, forced completion policy or conversation memory is added.
    """

    def __init__(
        self, *, system: str,
        llm: Callable[[str, str], Awaitable[tuple]] | None = None,
        llm_messages: Callable | None = None,
        run_tool: Callable[[str, dict], Awaitable[dict]] | None = None,
        observation_callback: Callable | None = None,
        lock: asyncio.Lock | None = None,
        max_rounds: int | None = None,
    ):
        if not isinstance(system, str):
            raise TypeError("system must be a str")
        # Reject invalid adapters at construction, not after any tool side effect.
        if llm is not None and not callable(llm):
            raise TypeError("llm must be callable")
        if llm_messages is not None and not callable(llm_messages):
            raise TypeError("llm_messages must be callable")
        if llm is None and llm_messages is None:
            raise TypeError("llm or llm_messages is required")
        if run_tool is not None and not callable(run_tool):
            raise TypeError("run_tool must be callable")
        self._system = system
        self._llm = llm if llm is not None else _unused_legacy
        self._llm_messages = llm_messages
        self._run_tool = run_tool if run_tool is not None else _unavailable_tool
        self._observation_callback = observation_callback
        self._lock = lock if lock is not None else asyncio.Lock()
        self._max_rounds = max_rounds

    async def run(
        self, prompt: str, *, completion_check: Callable | None = None,
        observation_question: str | None = None,
        transcript: list[dict] | None = None,
    ) -> RunResult:
        """Execute one turn; optionally populate caller-owned failure evidence.

        Pass an empty transcript list to inspect partial evidence after the
        original agent-loop exception propagates. Reusing nonempty history is
        rejected; hosts explicitly include any authorized context in the prompt.
        Successful result evidence is deep-copied, without extra redaction.
        """
        if not isinstance(prompt, str):
            raise TypeError("prompt must be a str")
        if transcript is not None and not isinstance(transcript, list):
            raise TypeError("transcript must be an empty list")
        evidence = transcript if transcript is not None else []
        async with self._lock:
            # Check under the same lock: two callers cannot silently mix turns.
            if evidence:
                raise ValueError("transcript must be empty for each turn")
            text, used = await run(
                self._system, prompt, self._llm, self._run_tool,
                max_rounds=self._max_rounds,
                llm_messages=self._llm_messages, transcript=evidence,
                observation_callback=self._observation_callback,
                completion_check=completion_check,
                observation_question=observation_question,
            )
            return RunResult(text, tuple(used), deepcopy(evidence))
