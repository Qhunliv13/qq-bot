"""Explicit numeric TCP connection only; no DNS, listeners, activation or isolation.

Factories, clocks, cancellation events and the returned socket owner are trusted.
Importing this module performs no network I/O. No QQ protocol or TLS is implemented.
"""

import ipaddress
import math
import socket
import sys
import threading
import time

from tools.qq_egress_policy import validate_destination_plan


class ConnectorError(Exception):
    """Expose a fixed code only, never an underlying network error or destination."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _validated_plan(plan, allowed_ports):
    """Revalidate every field: a frozen public constructor is not authorization."""
    try:
        return validate_destination_plan(plan, allowed_ports)
    except Exception:
        # Preserve the connector's fixed public error contract across extraction.
        raise ConnectorError("invalid_plan") from None


def _peer_matches(peer, address, port):
    """Require native numeric peer identity and exact unscoped socket tuple shape."""
    length = 2 if address.version == 4 else 4
    if type(peer) is not tuple or len(peer) != length:
        return False
    if type(peer[0]) is not str or "%" in peer[0]:
        return False
    if type(peer[1]) is not int or peer[1] != port:
        return False
    # Native IPv6 only; no scoped endpoints or implicit bool/int equivalence.
    if length == 4 and any(type(value) is not int or value != 0 for value in peer[2:]):
        return False
    try:
        return ipaddress.ip_address(peer[0]) == address
    except ValueError:
        return False


def connect_pinned(plan, *, timeout_seconds, allowed_ports, address_index=0,
                   socket_factory=socket.socket, clock=time.monotonic, stop_event=None):
    """Connect once to one revalidated literal and transfer raw socket ownership.

    ``allowed_ports`` is required: pass the original caller's bound, or explicitly
    None for unrestricted valid ports. Plans do not retain this bound. Timeout is
    an exact int/float in 1..30 seconds, covering validation through peer checking.
    No retry/fallback occurs, even when other addresses exist. The caller must close
    the successful socket (its native context manager is supported); its remaining
    connect timeout stays set and is not an application-I/O lifetime guarantee.

    Cancellation is cooperative before/after connect, not an interruption of a
    blocking syscall. Trusted injected callbacks must not block indefinitely, do
    resolution, return preconnected sockets, or lie about time/peer identity. The
    deadline cannot preempt those callbacks or OS scheduling delays; late success
    is closed and rejected. Failure cleanup attempts close even on BaseException.
    """
    if (type(timeout_seconds) not in (int, float)
            or not 1 <= timeout_seconds <= 30):
        raise ConnectorError("invalid_timeout")
    if not callable(socket_factory) or not callable(clock):
        raise ConnectorError("invalid_dependency")
    if stop_event is not None and not isinstance(stop_event, threading.Event):
        raise ConnectorError("invalid_stop_event")

    sock = None
    transferred = False
    stage = "clock_failed"
    try:
        started = clock()
        if type(started) not in (int, float) or not math.isfinite(started):
            raise ConnectorError("clock_failed")
        deadline = started + timeout_seconds
        last = started

        def remaining():
            """Check cancellation and monotonic deadline at each ownership boundary."""
            nonlocal last
            if stop_event is not None and stop_event.is_set():
                raise ConnectorError("cancelled")
            try:
                now = clock()
            except Exception:
                raise ConnectorError("clock_failed") from None
            if (type(now) not in (int, float) or not math.isfinite(now)
                    or now < last):
                raise ConnectorError("clock_failed")
            last = now
            budget = deadline - now
            if budget <= 0:
                raise ConnectorError("deadline_exceeded")
            return budget

        checked = _validated_plan(plan, allowed_ports)
        if type(address_index) is not int or not 0 <= address_index < len(checked.addresses):
            raise ConnectorError("invalid_address_index")
        literal = checked.addresses[address_index]
        address = ipaddress.ip_address(literal)
        family = socket.AF_INET if isinstance(address, ipaddress.IPv4Address) else socket.AF_INET6
        # inet_pton accepts only a literal in this fixed family: never a resolver
        # or create_connection fallback. The canonical policy snapshot is used.
        stage = "invalid_plan"
        socket.inet_pton(family, literal)
        remaining()
        stage = "factory_failed"
        sock = socket_factory(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        stage = "connect_failed"
        sock.settimeout(remaining())
        sock.connect((literal, checked.port))
        remaining()
        stage = "peer_failed"
        if not _peer_matches(sock.getpeername(), address, checked.port):
            raise ConnectorError("peer_mismatch")
        remaining()
        transferred = True
        return sock
    except ConnectorError:
        raise
    except TimeoutError:
        raise ConnectorError("deadline_exceeded") from None
    except Exception:
        raise ConnectorError(stage) from None
    finally:
        if sock is not None and not transferred:
            # A close failure cannot establish release; report only its fixed code.
            # Preserve interrupt/SystemExit propagation while still attempting close.
            active_error = sys.exc_info()[1]
            interrupted = active_error is not None and not isinstance(active_error, Exception)
            try:
                sock.close()
            except Exception:
                if not interrupted:
                    raise ConnectorError("close_failed") from None
