"""Offline strict-input and bounded-source tests; never execute nft or sockets."""

from dataclasses import replace
import socket
from unittest.mock import patch

import pytest

from tools.qq_egress_policy import DestinationPlan, validate_destination_plan, validate_resolved_destination
from tools.qq_egress_rules import build_rules


PLAN = validate_resolved_destination("offline.invalid", 443, ["8.8.8.8", "2001:4860:4860::8888"], {443})


def compile_plans(plans=(PLAN,), **changes):
    """Supply explicit safe test bounds without introducing production defaults."""
    kwargs = dict(allowed_ports={443}, blocked_addresses=set(), protocols={"tcp", "udp"})
    kwargs.update(changes)
    return build_rules(plans, **kwargs)


def test_exact_deterministic_and_no_io():
    """Ordering, dedup and caller ownership remain deterministic without DNS."""
    plans = [PLAN, PLAN]
    ports, blocked, protocols = {443}, {"127.0.0.1"}, {"udp", "tcp"}
    with patch.object(socket, "getaddrinfo", side_effect=AssertionError), patch.object(socket, "socket", side_effect=AssertionError):
        result = build_rules(plans, allowed_ports=ports, blocked_addresses=blocked, protocols=protocols)
    assert result == compile_plans((PLAN,), blocked_addresses=blocked)
    assert result.count("counter accept") == 4
    assert result.count("counter drop") == 1
    assert "ip daddr 8.8.8.8 tcp dport 443" in result
    assert "ip6 daddr 2001:4860:4860::8888 udp dport 443" in result
    assert "ct " not in result and "oif" not in result
    assert plans == [PLAN, PLAN] and ports == {443} and blocked == {"127.0.0.1"} and protocols == {"tcp", "udp"}


@pytest.mark.parametrize("plans", [None, [], (), [PLAN] * 17, {PLAN}, lambda: PLAN, [object()]])
def test_plan_container(plans):
    """Concrete bounded plans exclude lazy execution and subclasses/forgeries."""
    with pytest.raises(ValueError):
        compile_plans(plans)


@pytest.mark.parametrize("field,value", [
    ("allowed_ports", None), ("allowed_ports", []), ("allowed_ports", {True}),
    ("allowed_ports", {444}), ("allowed_ports", {443, "443"}),
    ("protocols", set()), ("protocols", ["tcp"]), ("protocols", {"TCP"}),
    ("protocols", {"tcp; accept"}), ("protocols", {True}), ("protocols", lambda: {"tcp"}),
    ("blocked_addresses", None), ("blocked_addresses", []), ("blocked_addresses", {1}),
    ("blocked_addresses", {"fe80::1%lo"}), ("blocked_addresses", {"::ffff:8.8.8.8"}),
    ("blocked_addresses", {"2001:4860:4860:0:0:0:0:8888"}),
    ("blocked_addresses", {"host.invalid"}), ("blocked_addresses", {"8.8.8.8"}),
    ("blocked_addresses", {"2001:4860:4860::8888"}),
])
def test_explicit_bounds(field, value):
    """Never interpret caller strings as syntax, discover hosts or filter bad peers."""
    with pytest.raises(ValueError):
        compile_plans(**{field: value})


@pytest.mark.parametrize("plan", [
    replace(PLAN, host="OFFLINE.invalid"), replace(PLAN, port=True),
    replace(PLAN, addresses=list(PLAN.addresses)),
    replace(PLAN, addresses=("8.8.8.8", "8.8.8.8")),
    replace(PLAN, addresses=("127.0.0.1",)),
    DestinationPlan("8.8.8.8", 443, ("1.1.1.1",)),
])
def test_shared_validation(plan):
    """Compiler and public validation reject the same forged fields."""
    with pytest.raises(ValueError):
        validate_destination_plan(plan, {443})
    with pytest.raises(ValueError):
        compile_plans([plan])


def test_pre_dedup_endpoint_bound_and_block_bound():
    """Resource ceilings apply before dedup, even when repeated plans collapse."""
    addresses = tuple(f"8.8.8.{i}" for i in range(1, 17))
    plan = DestinationPlan("offline.invalid", 443, addresses)
    assert compile_plans([plan] * 4).count("counter accept") == 32
    with pytest.raises(ValueError):
        compile_plans([plan] * 5)
    with pytest.raises(ValueError):
        compile_plans(blocked_addresses={f"10.0.{i // 256}.{i % 256}" for i in range(257)})


def test_required_arguments_and_tcp_only():
    """Every authority input is required; TCP mode adds no UDP authorization."""
    for missing in ("allowed_ports", "blocked_addresses", "protocols"):
        kwargs = dict(allowed_ports={443}, blocked_addresses=set(), protocols={"tcp"})
        del kwargs[missing]
        with pytest.raises(TypeError):
            build_rules([PLAN], **kwargs)
    assert "udp" not in compile_plans(protocols={"tcp"})
