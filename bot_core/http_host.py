"""Opt-in, standard-library chat-completions transport; no QQ configuration."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import math
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .harness import Harness

# Bound an untrusted provider response before decoding or parsing it.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class ModelRequestError(RuntimeError):
    """Expose only a fixed error, never provider bodies, URLs or credentials."""


class _NoRedirect(HTTPRedirectHandler):
    """Prevent redirect-based credential forwarding or HTTPS downgrades."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Reject redirects instead of forwarding an authorization header."""
        raise ModelRequestError("model request failed")


def validate_base_url(base_url: str) -> str:
    """Accept HTTPS or explicit loopback HTTP, without URL-borne secrets."""
    try:
        parts = urlsplit(base_url)
        local = parts.hostname in {"localhost", "127.0.0.1", "::1"}
        if (not parts.hostname or parts.username is not None
                or parts.password is not None or "?" in base_url or "#" in base_url
                or any(c.isspace() or ord(c) < 32 for c in base_url)
                or (parts.scheme != "https" and not (parts.scheme == "http" and local))):
            raise ValueError("invalid model base URL")
        # Accessing port also validates malformed/out-of-range port numbers.
        _ = parts.port
    except (TypeError, ValueError):
        raise ValueError("invalid model base URL") from None
    return base_url.rstrip("/")


def _reject_constant(value):
    """Reject non-JSON numeric constants without echoing provider content."""
    raise ValueError("invalid JSON constant")


class ChatCompletionHost:
    """Text-only provider adapter; never advertises or executes tools itself."""

    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: float = 60):
        self.base_url = validate_base_url(base_url)
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model is required")
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be positive and finite")
        if not isinstance(api_key, str) or "\r" in api_key or "\n" in api_key:
            raise ValueError("invalid model credential")
        if urlsplit(self.base_url).scheme == "https" and not api_key:
            raise ValueError("model credential is required")
        self.model, self.api_key, self.timeout = model, api_key, timeout

    async def __call__(self, messages: list[dict], allow_tools: bool = True) -> tuple:
        """Snapshot native messages before sending one blocking POST in a thread."""
        # No tool schemas are registered here; calls are still returned honestly
        # for the existing loop to handle (including forced-finalization checks).
        payload = {"model": self.model, "messages": deepcopy(messages)}
        return await asyncio.to_thread(self._request, payload)

    def _request(self, payload: dict) -> tuple:
        """Read bounded strict JSON and normalize only the first message shape."""
        try:
            headers = {"Content-Type": "application/json", "Accept": "application/json"}
            if self.api_key:
                headers["Authorization"] = "Bearer " + self.api_key
            request = Request(
                self.base_url + "/chat/completions",
                data=json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8"),
                headers=headers, method="POST",
            )
            # A private opener avoids process-wide hooks and refuses redirects.
            with build_opener(_NoRedirect()).open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError("response too large")
            data = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
            choices = data.get("choices") if isinstance(data, dict) else None
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                raise ValueError("invalid choices")
            message = choices[0].get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant":
                raise ValueError("invalid message")
            text, calls = message.get("content"), message.get("tool_calls", [])
            if ((text is not None and not isinstance(text, str))
                    or not isinstance(calls, list) or any(not isinstance(c, dict) for c in calls)
                    or (text is None and not calls)):
                raise ValueError("invalid content")
            return text or "", calls
        except Exception:
            # Error text/chaining must not disclose a provider body or secret URL.
            raise ModelRequestError("model request failed") from None


def create_http_harness(*, system: str, base_url: str, model: str,
                        api_key: str = "", timeout: float = 60,
                        max_rounds: int | None = None) -> Harness:
    """Create the existing tool loop with a text-only HTTP host, not QQ tools."""
    host = ChatCompletionHost(base_url, model, api_key, timeout)
    return Harness(system=system, llm_messages=host, max_rounds=max_rounds)
