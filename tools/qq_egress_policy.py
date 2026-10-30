"""Offline destination policy, NOT a network isolation or enforcement mechanism.

Consumers must connect to the returned literal addresses, never resolve host again.
No resolver, socket, connector, endpoint defaults, or environment configuration lives here.
"""

from dataclasses import dataclass
import ipaddress
import re


# Explicit conservative bounds avoid relying solely on Python's changing IANA tables.
_V4_DENY = tuple(ipaddress.ip_network(cidr) for cidr in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8",
    "169.254.0.0/16", "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24",
    "192.88.99.0/24", "192.168.0.0/16", "198.18.0.0/15",
    "198.51.100.0/24", "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4",
))
_V6_DENY = tuple(ipaddress.ip_network(cidr) for cidr in (
    "::/96", "::ffff:0:0/96", "64:ff9b::/96", "64:ff9b:1::/48",
    "100::/64", "2001::/23", "2001:db8::/32", "2002::/16",
    "3fff::/20", "fc00::/7", "fe80::/10", "fec0::/10", "ff00::/8",
))
# Only native global unicast allocation is eligible; special subranges still fail.
_V6_UNICAST = ipaddress.ip_network("2000::/3")
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_NUMERIC_PART = re.compile(r"(?:[0-9]+|0x[0-9a-f]+)\Z", re.IGNORECASE)


@dataclass(frozen=True)
class DestinationPlan:
    """Immutable connection intent; host is context, not a connector authority."""

    host: str
    port: int
    addresses: tuple[str, ...]


def _port(value):
    """Require an exact integer so bool, strings and coercible objects cannot pass."""
    if type(value) is not int or not 1 <= value <= 65535:
        raise ValueError("port must be an exact integer in 1..65535")
    return value


def _public_address(value):
    """Parse strict literal text and reject special, local and transition addresses."""
    if type(value) is not str or not value or len(value) > 45 or "%" in value:
        raise ValueError("address must be an unscoped IP literal string")
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError("invalid or ambiguous IP literal") from exc
    # ipaddress rejects octal/short/hex IPv4; exact spelling also pins canonical IPv4.
    if address.version == 4:
        denied = str(address) != value or any(address in net for net in _V4_DENY)
    else:
        denied = address not in _V6_UNICAST or any(address in net for net in _V6_DENY)
    if denied or not address.is_global or any((
        address.is_private, address.is_loopback, address.is_link_local,
        address.is_multicast, address.is_unspecified, address.is_reserved,
    )):
        raise ValueError("address is not permitted public native unicast")
    return address


def _host(value):
    """Return canonical bare host plus optional literal; reject IDNA reinterpretation."""
    if type(value) is not str or not value or len(value) > 253:
        raise ValueError("host must be an explicit bare hostname or IP literal")
    # Colons select the literal path, never authority parsing; zones are forbidden.
    if ":" in value:
        address = _public_address(value)
        return str(address), address
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        address = None
    if address is not None:
        address = _public_address(value)
        return str(address), address
    try:
        canonical = value.encode("idna").decode("ascii").lower()
        decoded = canonical.encode("ascii").decode("idna")
    except UnicodeError as exc:
        raise ValueError("host is not strict IDNA") from exc
    # A-label input is accepted only with a valid round trip. Unicode input may
    # case-fold but cannot silently map sharp-s, fullwidth digits, or alternate dots.
    if decoded.encode("idna").decode("ascii").lower() != canonical:
        raise ValueError("host IDNA round trip failed")
    if not value.isascii() and decoded.lower() != value.lower():
        raise ValueError("lossy IDNA host mapping is forbidden")
    labels = canonical.split(".")
    if len(canonical) > 253 or any(not _LABEL.fullmatch(label) for label in labels):
        raise ValueError("host must contain strict DNS labels without authority syntax")
    # Numeric-only labels cover legacy inet_aton decimal, octal, short and hex forms.
    if all(_NUMERIC_PART.fullmatch(label) for label in labels):
        raise ValueError("ambiguous numeric host is forbidden")
    # Decoded A-labels must not hide an IP-like representation either.
    if all(_NUMERIC_PART.fullmatch(label) for label in decoded.split(".")):
        raise ValueError("IDNA numeric host is forbidden")
    return canonical, None


def validate_resolved_destination(host, port, resolved_addresses, allowed_ports=None):
    """Validate all supplied answers and return an immutable, pinned destination plan.

    Inputs are data only: a bare host, exact int port, list/tuple of 1..16 literal
    address strings, and optional set/frozenset of exact int ports. None permits
    any valid port; an empty allowed set permits none. Bounds precede deduplication.
    Literal hosts require every candidate to equal that literal. A hostname is
    context only: this function neither obtains nor authenticates DNS answers.
    Raise ValueError on any invalid input; never filter away a rejected answer.
    """
    port = _port(port)
    if allowed_ports is not None:
        if type(allowed_ports) not in (set, frozenset):
            raise ValueError("allowed_ports must be an explicit set or frozenset")
        for candidate in allowed_ports:
            _port(candidate)
        if port not in allowed_ports:
            raise ValueError("port is not in allowed_ports")
    canonical_host, literal = _host(host)
    # Concrete bounded containers avoid callbacks, lazy resolvers and infinite iterators.
    if type(resolved_addresses) not in (list, tuple) or not 1 <= len(resolved_addresses) <= 16:
        raise ValueError("resolved_addresses must be a list/tuple of 1..16 literals")
    addresses = []
    for candidate in resolved_addresses:
        address = _public_address(candidate)
        if literal is not None and address != literal:
            raise ValueError("literal host and candidate addresses do not match")
        canonical = str(address)
        if canonical not in addresses:
            addresses.append(canonical)
    return DestinationPlan(canonical_host, port, tuple(addresses))


def validate_destination_plan(plan, allowed_ports):
    """Revalidate every field and require canonical immutable intent, without I/O.

    A public frozen constructor is not authorization. Keep this shared by consumers
    so duplicate answers or mutable/noncanonical fields cannot silently change a
    caller-selected address index. ``allowed_ports`` retains the resolver policy's
    explicit None semantics; stricter consumers may require a concrete bound.
    """
    if type(plan) is not DestinationPlan:
        raise ValueError("plan must be an exact DestinationPlan")
    checked = validate_resolved_destination(
        plan.host, plan.port, plan.addresses, allowed_ports=allowed_ports)
    if (type(plan.addresses) is not tuple or plan.host != checked.host
            or plan.port != checked.port or plan.addresses != checked.addresses):
        raise ValueError("noncanonical plan")
    return checked
