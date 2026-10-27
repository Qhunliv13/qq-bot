"""Strict offline action dispatch with injected handlers and durable claims.

This module implements no QQ login, native protocol, network, or default action.
A registered callback is a caller-supplied capability, not backend verification.
"""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .store import copy_json, validate_account, validate_action, validate_identifier


class Journal(Protocol):
    """Synchronous durable claim contract; persistence must precede execution."""

    def claim(self, account: str, operation_id: str, action: str,
              parameters: dict) -> dict:
        """Atomically claim a request or return its previous state/conflict."""
        ...

    def complete(self, account: str, operation_id: str, response: dict) -> None:
        """Persist one terminal response, never overwriting a different result."""
        ...


@dataclass(frozen=True)
class Handler:
    """Explicit async capability; mutating actions require a durable operation ID."""

    callback: Callable[[str, dict], Awaitable[dict]]
    mutating: bool = False


def _failure(code: int, reason: str, *, unknown: bool = False) -> dict:
    """Build a safe local failure without leaking callback or storage exceptions."""
    return {"status": "failed", "retcode": code, "data": None,
            "outcome": "unknown" if unknown else "failed", "error": reason}


def _native_response(result: Any) -> dict:
    """Accept only strict native success/failure; never infer delivery from IDs."""
    result = copy_json(result)
    if not isinstance(result, dict) or not {"status", "retcode", "data"} <= result.keys():
        raise ValueError("invalid_native_response")
    code = result["retcode"]
    # bool is a Python int subclass but not a protocol integer return code.
    if type(code) is not int:
        raise ValueError("invalid_native_response")
    status = result["status"]
    if not ((status == "ok" and code == 0) or (status == "failed" and code != 0)):
        raise ValueError("invalid_native_response")
    # Only public envelope fields survive; arbitrary native diagnostics may leak secrets.
    return {"status": status, "retcode": code, "data": result["data"],
            "outcome": "accepted" if status == "ok" else "failed"}


class ActionDispatcher:
    """Dispatch only explicit capabilities, preserving at-most-once mutation claims.

    The supplied journal owns durability across processes and restarts. An unknown
    outcome is never retried automatically, including after cancellation or timeout.
    Read-only handlers may run without a journal claim; they must truly be read-only.
    """

    def __init__(self, handlers: Mapping[str, Handler], journal: Journal):
        self._handlers = dict(handlers)
        self._journal = journal
        for action, handler in self._handlers.items():
            validate_action(action)
            if (not isinstance(handler, Handler) or not callable(handler.callback)
                    or type(handler.mutating) is not bool):
                raise ValueError("invalid_handler_registration")

    async def dispatch(self, account: str, action: str, params: dict, *,
                       operation_id: str | None = None, echo: Any = None) -> dict:
        """Validate and detach input before awaiting, then return one safe envelope."""
        copied_echo = None
        try:
            # A shared strict JSON codec rejects nonfinite numbers, non-string keys,
            # unsupported types, and invalid Unicode without silently normalizing.
            copied_echo = copy_json(echo)
            parameters = copy_json(params)
            validate_account(account)
            validate_action(action)
            if not isinstance(parameters, dict):
                raise ValueError("invalid_request")
            if operation_id is not None:
                validate_identifier(operation_id)
        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            # Preserve valid correlation on invalid requests, never an invalid echo.
            return dict(_failure(1400, "invalid_request"), echo=copied_echo)

        handler = self._handlers.get(action)
        if handler is None:
            return dict(_failure(1404, "unsupported_action"), echo=copied_echo)
        if handler.mutating and operation_id is None:
            return dict(_failure(1400, "operation_id_required"), echo=copied_echo)

        claimed = False
        if handler.mutating:
            try:
                claim = self._journal.claim(account, operation_id, action, parameters)
                if claim["conflict"]:
                    return dict(_failure(1409, "operation_id_conflict"), echo=copied_echo)
                if not claim["claimed"]:
                    if claim["state"] == "complete" and isinstance(claim["response"], dict):
                        return dict(copy_json(claim["response"]), echo=copied_echo)
                    return dict(_failure(1504, "native_status_not_confirmed", unknown=True),
                                echo=copied_echo)
                claimed = True
            except Exception:
                # No callback is invoked unless persistence positively confirmed claim.
                return dict(_failure(1504, "journal_unavailable", unknown=True), echo=copied_echo)

        try:
            response = _native_response(await handler.callback(account, parameters))
        except asyncio.CancelledError:
            if claimed:
                try:
                    self._journal.complete(account, operation_id,
                        _failure(1504, "native_status_not_confirmed", unknown=True))
                except Exception:
                    # A failed completion leaves the durable pending claim unreplayable.
                    pass
            raise
        except Exception:
            # An exception or malformed acknowledgement cannot prove a mutation failed.
            response = _failure(1500, "native_status_not_confirmed" if claimed
                                else "handler_failed", unknown=claimed)

        if claimed:
            try:
                self._journal.complete(account, operation_id, response)
            except Exception:
                # Returning accepted after failed persistence would weaken safe replay.
                response = _failure(1504, "journal_unavailable", unknown=True)
        return dict(copy_json(response), echo=copied_echo)
