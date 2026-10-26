"""Privacy-safe diagnostics of local UTF-8 prefixes, never provider cache tokens.

Observation never changes a payload or retains its text. Prefix lengths are
certified matching byte-block lower bounds: a mismatch inside a block is not
resolved, so these measurements must not be labelled exact token/cache matches.
The 256-byte resolution affects diagnostics only, never request/context limits.
"""

from dataclasses import dataclass
import hashlib
import hmac
import json
import secrets


# Diagnostic granularity, not a context size, truncation, or batching policy.
PREFIX_RESOLUTION_BYTES = 256


def _json_bytes(value):
    """Canonicalize JSON object keys without changing any ordered list elements."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class _Prefix:
    """Retain only keyed block digests and lengths, never source byte strings."""

    size: int
    blocks: tuple


@dataclass(frozen=True)
class _Observation:
    """Hold irreversible per-scope comparisons without payloads or identifiers."""

    index: int
    system: bytes
    tools: bytes
    images: bytes
    history: _Prefix
    user: _Prefix


class PrefixTracker:
    """Compare each native scope with its last observation within this process.

    Scope keys and content digests use a random process-local HMAC key, which is
    never returned or logged. No content, QQ identifiers, URLs, credentials,
    payload references, or arbitrary provider response fields enter the output.
    Scopes are not evicted: diagnostics introduce no new conversation limits.
    """

    def __init__(self):
        self._key = secrets.token_bytes(32)
        self._scopes = {}

    def _digest(self, content):
        """Make low-entropy source material resistant to offline digest guessing."""
        return hmac.digest(self._key, content, hashlib.sha256)

    def _prefix(self, content):
        """Hash every byte block, retaining the final partial block's length."""
        return _Prefix(len(content), tuple(
            (len(content[start:start + PREFIX_RESOLUTION_BYTES]),
             self._digest(content[start:start + PREFIX_RESOLUTION_BYTES]))
            for start in range(0, len(content), PREFIX_RESOLUTION_BYTES)))

    def _reused(self, previous, content):
        """Certify previous blocks against current bytes without storing old text.

        A prior partial tail can be checked even when the new text is longer.
        If either text ends inside an unmatched block, only earlier full blocks
        are certified; no estimate is invented for that unobserved suffix.
        """
        matched = 0
        for size, digest in previous.blocks:
            if len(content) < matched + size:
                break
            if self._digest(content[matched:matched + size]) != digest:
                break
            matched += size
        return matched

    @staticmethod
    def _native_user(payload):
        """Find the first user envelope without treating arbitrary text as scope.

        Multipart text is accepted only when it has a single text part; images
        remain separate and are never concatenated into or stored as user text.
        Malformed or non-native messages produce an unavailable observation.
        """
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return None
        for message in messages:
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, list):
                text = [part.get("text") for part in content
                        if isinstance(part, dict) and part.get("type") == "text"]
                if len(text) != 1:
                    return None
                content = text[0]
            if not isinstance(content, str):
                return None
            try:
                envelope = json.loads(content)
            except (ValueError, RecursionError):
                return None
            if not isinstance(envelope, dict):
                return None
            conversation = envelope.get("conversation")
            if not isinstance(conversation, dict):
                return None
            scope = conversation.get("scope")
            history = conversation.get("history")
            current = conversation.get("messages")
            if (not isinstance(scope, str) or not scope
                    or not isinstance(history, list) or not isinstance(current, list)):
                return None
            return scope, history, current, content
        return None

    def observe(self, payload):
        """Return numeric/boolean local diagnostics without modifying the request.

        User bytes refer to the actual first-user text, not its HTTP JSON escape
        representation. History bytes use canonical JSON of the history array.
        Provider-side tool placement, chat templates, tokenization, backend
        affinity and cache retention cannot be inferred from these byte counts.
        """
        if not isinstance(payload, dict):
            return {"available": False}
        native = self._native_user(payload)
        if native is None:
            return {"available": False}
        scope, history, current, user = native
        try:
            messages = payload["messages"]
            system = self._digest(_json_bytes([
                message for message in messages if isinstance(message, dict)
                and message.get("role") in ("system", "developer")]))
            tools = self._digest(_json_bytes(payload.get("tools", [])))
            images = [part for message in messages if isinstance(message, dict)
                      and isinstance(message.get("content"), list)
                      for part in message["content"] if isinstance(part, dict)
                      and part.get("type") in ("image_url", "input_image")]
            image_digest = self._digest(_json_bytes(images))
            history_bytes = _json_bytes(history)
            user_bytes = user.encode("utf-8")
            scope_key = self._digest(scope.encode("utf-8"))
        except (TypeError, ValueError, UnicodeError, RecursionError):
            # Diagnostics cannot substitute strings or silently normalize invalid
            # source values; leave actual request validation to the transport.
            return {"available": False}
        previous = self._scopes.get(scope_key)
        index = previous.index if previous is not None else len(self._scopes) + 1
        result = {
            "available": True,
            "scope_index": index,
            "first_observation": previous is None,
            "system_unchanged": previous is not None and previous.system == system,
            "tools_unchanged": previous is not None and previous.tools == tools,
            "images_unchanged": previous is not None and previous.images == image_digest,
            "image_count": len(images),
            "input_history_entries": len(history),
            "current_batch_entries": len(current),
            "history_bytes": len(history_bytes),
            "user_bytes": len(user_bytes),
            "history_prefix_reused_bytes": self._reused(previous.history, history_bytes)
                if previous is not None else 0,
            "user_prefix_reused_bytes": self._reused(previous.user, user_bytes)
                if previous is not None else 0,
            "prefix_resolution_bytes": PREFIX_RESOLUTION_BYTES,
        }
        self._scopes[scope_key] = _Observation(
            index, system, tools, image_digest,
            self._prefix(history_bytes), self._prefix(user_bytes))
        return result
