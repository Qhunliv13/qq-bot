"""Offline, transport-neutral contract prototype, not an extracted bot runtime.

Only standard-library types are used. Adapters must authenticate an actor before
constructing an event: constructor validation checks consistency, NOT authenticity
or authorization. No role, owner, or permission is inferred from a display name,
origin, endpoint, or content. Tasks and system events never carry a human actor.

``account`` always identifies the receiving bot's account/binding namespace, not
the remote sender's account. Even a local endpoint needs an explicit namespace.
Native identifiers are opaque, case-sensitive strings: no numeric coercion,
trimming, Unicode normalization, or legacy QQ scope conversion is performed.

``content_json`` preserves an adapter's complete JSON content verbatim, including
unknown/native segments and attachment references. It is data, never an authority
source. This prototype defines no media processing, storage migration, delivery,
framework event adapter, or production execution behavior.
"""

from dataclasses import dataclass, field
import json
import re
from typing import Literal
import unicodedata


# Route tokens are portable ASCII labels, but case remains significant.
_PLATFORM_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*", re.ASCII)
EndpointKind = Literal["group", "private", "local"]
EventOrigin = Literal["human", "task", "system"]


def _validate_string(value: str, name: str, *, identifier: bool = True) -> None:
    """Reject coercion and invalid UTF-8; opaque identifiers also reject Cc controls."""
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    # Strict encoding detects lone surrogates without changing the original value.
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise ValueError(f"{name} must be valid UTF-8") from error
    if identifier and (not value or any(unicodedata.category(c) == "Cc" for c in value)):
        raise ValueError(f"{name} must be nonempty and contain no control characters")


def _validate_binding(platform: str, account: str, native_id: str) -> None:
    """Apply one shared identity rule without importing a transport's validators."""
    for name, value in (("platform", platform), ("account", account), ("native_id", native_id)):
        _validate_string(value, name)
    if _PLATFORM_TOKEN.fullmatch(platform) is None:
        raise ValueError("platform must be an ASCII route token")


def _reject_json_constant(value: str) -> None:
    """Disallow JavaScript NaN/Infinity extensions so the envelope contains JSON."""
    raise ValueError(f"content_json contains a non-JSON constant: {value}")


@dataclass(frozen=True, slots=True)
class Endpoint:
    """An explicit channel route, isolated by platform, bot account, kind, and ID."""

    platform: str
    account: str
    kind: EndpointKind
    native_id: str

    def __post_init__(self) -> None:
        """Validate route fields while preserving all accepted identity bytes."""
        _validate_binding(self.platform, self.account, self.native_id)
        _validate_string(self.kind, "kind")
        if self.kind not in ("group", "private", "local"):
            raise ValueError("kind must be group, private, or local")

    def storage_key(self) -> str:
        """Encode an unambiguous versioned scope, not a transport operation codec."""
        return "scope:v1:" + json.dumps(
            [self.platform, self.account, self.kind, self.native_id],
            ensure_ascii=False,
            separators=(",", ":"),
        )


@dataclass(frozen=True, slots=True)
class Principal:
    """Adapter-supplied identity in a bot binding; construction proves no authority.

    ``native_id`` identifies the remote actor. ``display_name`` is optional
    presentation only and deliberately excluded from identity equality/hash.
    """

    platform: str
    account: str
    native_id: str
    display_name: str | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        """Keep actor identity opaque and separate from non-authoritative display text."""
        _validate_binding(self.platform, self.account, self.native_id)
        if self.display_name is not None:
            _validate_string(self.display_name, "display_name", identifier=False)


@dataclass(frozen=True, slots=True)
class InboundEvent:
    """Immutable envelope with explicit provenance and an uninterpreted JSON body.

    Human events require an actor authenticated by the future adapter in the
    source's platform/account binding. Actor native ID need NOT equal channel ID.
    Task/system events must use ``actor=None``; delegated task authorization is
    intentionally not modeled here. The body must not be used to override these
    fields, and this constructor cannot verify an adapter's provenance claims.
    """

    event_id: str
    source: Endpoint
    actor: Principal | None
    origin: EventOrigin
    content_json: str

    def __post_init__(self) -> None:
        """Reject inconsistent provenance shapes, not purported users' permissions."""
        _validate_string(self.event_id, "event_id")
        if type(self.source) is not Endpoint:
            raise TypeError("source must be an Endpoint")
        if self.actor is not None and type(self.actor) is not Principal:
            raise TypeError("actor must be a Principal or None")
        _validate_string(self.origin, "origin")
        if self.origin not in ("human", "task", "system"):
            raise ValueError("origin must be human, task, or system")
        if self.origin == "human":
            # Matching the channel ID would wrongly identify a group as its sender.
            if self.actor is None:
                raise ValueError("human events require an actor")
            if (self.actor.platform, self.actor.account) != (self.source.platform, self.source.account):
                raise ValueError("actor and source must share a platform/account binding")
        elif self.actor is not None:
            # Synthetic events cannot impersonate a person, even a named owner.
            raise ValueError("task/system events must not carry an actor")
        _validate_string(self.content_json, "content_json", identifier=False)
        # Parse only to validate JSON; retaining the string avoids lossy re-encoding.
        json.loads(self.content_json, parse_constant=_reject_json_constant)
