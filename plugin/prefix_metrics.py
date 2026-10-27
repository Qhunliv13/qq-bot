"""Privacy-safe diagnostics of local UTF-8 prefixes, never provider cache tokens.

Observation never changes a payload or retains its text. Prefix lengths are
certified matching byte-block lower bounds: a mismatch inside a block is not
resolved, so these measurements must not be labelled exact token/cache matches.
The 256-byte resolution affects diagnostics only, never request/context limits.
``user_bytes`` describes the native envelope's original UTF-8 text, not decoded
or reserialized JSON. ``native_message_bytes`` describes canonical JSON of the
actual entire first-user content, including text/image positions; ``messages_bytes``
describes canonical JSON of all request messages in their original array order.
Object keys (including tool maps) are sorted for diagnostic stability, not wire
fidelity. Provider serialization, tool placement, chat templates and cached tokens
remain unknown; none of these counts establish provider cache hits.
"""

from dataclasses import dataclass
import hashlib
import hmac
import json
import secrets

from .conversation_context import PROTOCOL_VERSION


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
    native_message: _Prefix
    messages: _Prefix


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
        # Correlation is process-local and contains no source identifier.
        self._request_index = 0
        # One additional digest-only baseline compares adjacent requests across scopes.
        self._last_messages = None

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
    def _envelope(text, allow_legacy):
        """Recognize native provenance, allowing old single-text envelopes only.

        Recognition does not confer trust: native participant data explicitly has
        untrusted_data provenance. Arbitrary conversation-shaped extra text must
        never masquerade as the native scope in a multi-text message.
        """
        if not isinstance(text, str):
            return None
        try:
            envelope = json.loads(text)
        except (ValueError, RecursionError):
            return None
        if not isinstance(envelope, dict):
            return None
        # Legacy means genuinely absent protocol metadata, not malformed metadata.
        legacy = "version" not in envelope and "source" not in envelope
        source = envelope.get("source")
        native = (type(envelope.get("version")) is int
                  and envelope["version"] == PROTOCOL_VERSION
                  and isinstance(source, dict)
                  and source.get("name") == "conversation_context"
                  and source.get("trust") == "untrusted_data")
        if not native and not (allow_legacy and legacy):
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
        return scope, history, current, text

    @staticmethod
    def _native_user(payload):
        """Select exactly one native envelope within the actual first user content.

        Inspect all text parts, keeping their original strings and image positions
        untouched. Multiple valid native envelopes are ambiguous even when equal;
        malformed extra text is merely additional source content, not a veto.
        """
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return None
        for message in messages:
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            content = message.get("content")
            text_parts = ([part.get("text") for part in content
                           if isinstance(part, dict) and part.get("type") == "text"]
                          if isinstance(content, list) else [content])
            candidates = [candidate for text in text_parts
                          if (candidate := PrefixTracker._envelope(
                              text, allow_legacy=len(text_parts) == 1)) is not None]
            if len(candidates) != 1:
                return None
            return (*candidates[0], content)
        return None

    def observe(self, payload):
        """Return numeric/boolean local diagnostics without modifying the request.

        User bytes retain the legacy envelope-text meaning. Native-message and
        messages bytes include actual ordered source content as canonical JSON,
        not provider wire/template bytes or cached tokens. Every observation gets
        a process-local index, including unavailable ones. The global comparison
        is adjacent-request only; unavailable requests break that baseline.
        """
        self._request_index += 1
        unavailable = {"available": False, "request_index": self._request_index}
        last_messages = self._last_messages
        # Reset before validation so invalid requests cannot bridge global reuse.
        self._last_messages = None
        if not isinstance(payload, dict):
            return unavailable
        native = self._native_user(payload)
        if native is None:
            return unavailable
        scope, history, current, user, native_content = native
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
            native_message_bytes = _json_bytes(native_content)
            messages_bytes = _json_bytes(messages)
            scope_key = self._digest(scope.encode("utf-8"))
        except (TypeError, ValueError, UnicodeError, RecursionError):
            # Diagnostics cannot substitute strings or silently normalize invalid
            # source values; leave actual request validation to the transport.
            return unavailable
        previous = self._scopes.get(scope_key)
        index = previous.index if previous is not None else len(self._scopes) + 1
        result = {
            "available": True,
            "request_index": self._request_index,
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
            "native_message_bytes": len(native_message_bytes),
            "native_message_prefix_reused_bytes": self._reused(
                previous.native_message, native_message_bytes)
                if previous is not None else 0,
            "messages_bytes": len(messages_bytes),
            "messages_prefix_reused_bytes": self._reused(previous.messages, messages_bytes)
                if previous is not None else 0,
            "global_messages_prefix_reused_bytes": self._reused(last_messages, messages_bytes)
                if last_messages is not None else 0,
            "prefix_resolution_bytes": PREFIX_RESOLUTION_BYTES,
        }
        messages_prefix = self._prefix(messages_bytes)
        self._scopes[scope_key] = _Observation(
            index, system, tools, image_digest,
            self._prefix(history_bytes), self._prefix(user_bytes),
            self._prefix(native_message_bytes), messages_prefix)
        # Share the immutable digest object, not another copy or any request data.
        self._last_messages = messages_prefix
        return result
