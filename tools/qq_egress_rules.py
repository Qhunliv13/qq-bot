"""Pure selective nft compiler; never apply, resolve, discover, or activate rules.

The trusted namespace owner supplies a complete local-host address snapshot. This
module cannot establish that snapshot's completeness or provide isolation itself.
Only exact destination/port/protocol tuples are accepted, including TCP ACKs to
that same peer; there is no established/related, reply, or loopback bypass.
"""

import ipaddress

from tools.qq_egress_policy import validate_destination_plan


# Fixed syntax and bounded output are deliberately not caller-configurable.
TABLE = "qq_native_egress"
DROP_COMMENT = "qq_egress_drop"
MAX_PLANS = 16
MAX_ENDPOINTS = 64
MAX_BLOCKED_ADDRESSES = 256


def _blocked_snapshot(values):
    """Copy explicit literal host addresses, allowing private denies but no callbacks."""
    if type(values) not in (set, frozenset) or len(values) > MAX_BLOCKED_ADDRESSES:
        raise ValueError("blocked_addresses must be an explicit bounded set")
    blocked = set()
    for value in values:
        if type(value) is not str or not value or len(value) > 45 or "%" in value:
            raise ValueError("blocked address must be an unscoped literal")
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            raise ValueError("blocked address must be an unscoped literal") from None
        # Native canonical literals prevent scoped/mapped aliases obscuring ownership.
        if str(address) != value or (address.version == 6 and address.ipv4_mapped is not None):
            raise ValueError("blocked address must be canonical native literal")
        blocked.add(address)
    return blocked


def build_rules(plans, *, allowed_ports, blocked_addresses, protocols):
    """Return deterministic ASCII nft source from explicitly authorized plan data.

    Require a list/tuple of 1..16 canonical DestinationPlans, a set/frozenset of
    allowed ports (None is forbidden), a set/frozenset of at most 256 canonical
    local-host IP strings from a trusted owner, and a nonempty exact set/frozenset
    subset of {'tcp', 'udp'}. No callable or lazy input is accepted. All fields
    are revalidated by the shared destination policy before rendering anything.

    Limit the sum of plan addresses to 64 BEFORE deduplication; each plan is also
    bounded by policy to 16 addresses. Deduplicate numeric (family, IP, port)
    endpoints, sort numerically, then emit at most 128 accept rules. Reject the
    entire input if any candidate is blocked, never silently filter an answer.
    Empty blocked_addresses is an explicit owner's assertion, not discovery.
    No mutation, DNS, sockets, environment, subprocess, CLI or default activation.
    """
    if type(plans) not in (list, tuple) or not 1 <= len(plans) <= MAX_PLANS:
        raise ValueError("plans must be a list/tuple of 1..16 plans")
    if type(allowed_ports) not in (set, frozenset):
        raise ValueError("allowed_ports must be an explicit set")
    if (type(protocols) not in (set, frozenset) or not 1 <= len(protocols) <= 2
            or any(type(value) is not str or value not in ("tcp", "udp") for value in protocols)):
        raise ValueError("protocols must be an explicit tcp/udp subset")
    blocked = _blocked_snapshot(blocked_addresses)
    endpoints = set()
    supplied = 0
    for plan in plans:
        checked = validate_destination_plan(plan, allowed_ports)
        supplied += len(checked.addresses)
        if supplied > MAX_ENDPOINTS:
            raise ValueError("too many supplied endpoints")
        for literal in checked.addresses:
            address = ipaddress.ip_address(literal)
            if address in blocked:
                raise ValueError("destination is a blocked local-host address")
            endpoints.add((address.version, int(address), checked.port))
    lines = [f"create table inet {TABLE}",
             f"add chain inet {TABLE} output {{ type filter hook output priority 0; policy drop; }}"]
    for index, (version, numeric, port) in enumerate(sorted(endpoints)):
        address = str(ipaddress.IPv4Address(numeric) if version == 4 else ipaddress.IPv6Address(numeric))
        family = "ip" if version == 4 else "ip6"
        for protocol in sorted(protocols):
            # Only canonical numeric data and fixed tokens enter executable syntax.
            label = f"qq_egress_v{version}_{protocol}_{index}"
            lines.append(f'add rule inet {TABLE} output {family} daddr {address} '
                         f'{protocol} dport {port} counter accept comment "{label}"')
    lines.append(f'add rule inet {TABLE} output counter drop comment "{DROP_COMMENT}"')
    return "\n".join(lines) + "\n"
