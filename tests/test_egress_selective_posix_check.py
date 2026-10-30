"""Unit guards and structural assertions only; never run namespace/kernel probes."""

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest


SPEC = importlib.util.spec_from_file_location("selective_check", Path(__file__).with_name("egress_selective_posix_check.py"))
fixture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture)


def test_guard_precedes_compile_apply(capsys):
    """An unsafe namespace must prevent compilation, executable lookup and apply."""
    with patch.object(fixture.baseline, "configure", side_effect=fixture.baseline.CheckFailure("parent_namespace")), patch.object(fixture, "build_rules") as compile_mock, patch.object(fixture, "command") as command_mock, patch.object(fixture.nft, "verified_nft") as executable:
        assert fixture.main(["--parent-netns", "net:[1]"]) == 1
    compile_mock.assert_not_called()
    command_mock.assert_not_called()
    executable.assert_not_called()
    assert json.loads(capsys.readouterr().out)["stage"] == "parent_namespace"


def document():
    """Construct exact expected nft JSON, with no access to a live nft executable."""
    table = fixture.TABLE
    items = [{"table": {"family": "inet", "name": table}},
             {"chain": {"family": "inet", "table": table, "name": "output", "hook": "output", "type": "filter", "prio": 0, "policy": "drop"}}]
    for index, (version, literal, port) in enumerate(ENDPOINTS):
        for protocol in ("tcp", "udp"):
            count = 1 if protocol == "udp" else 0
            expr = [{"match": {"op": "==", "left": {"payload": {"protocol": "ip" if version == 4 else "ip6", "field": "daddr"}}, "right": literal}},
                    {"match": {"op": "==", "left": {"payload": {"protocol": protocol, "field": "dport"}}, "right": port}},
                    {"counter": {"packets": count, "bytes": count * 64}}, {"accept": None}]
            items.append({"rule": {"family": "inet", "table": table, "chain": "output", "comment": f"qq_egress_v{version}_{protocol}_{index}", "expr": expr}})
    items.append({"rule": {"family": "inet", "table": table, "chain": "output", "comment": fixture.DROP_COMMENT,
                           "expr": [{"counter": {"packets": 4, "bytes": 256}}, {"drop": None}]}})
    return {"nftables": items}


ENDPOINTS = [(4, "8.8.8.8", 40001), (6, "2001:4860:4860::8888", 40002)]


def test_exact_counter_structure():
    """Accept only exact peers/ports/verdicts, with positive UDP and zero TCP."""
    assert len(fixture.counters(document(), ENDPOINTS)) == 5
    for field, value in (("right", "1.1.1.1"), ("op", "!=")):
        changed = document()
        changed["nftables"][2]["rule"]["expr"][0]["match"][field] = value
        with pytest.raises(fixture.baseline.CheckFailure):
            fixture.counters(changed, ENDPOINTS)
    changed = document()
    changed["nftables"][3]["rule"]["expr"][2]["counter"]["packets"] = 0
    with pytest.raises(fixture.baseline.CheckFailure):
        fixture.counters(changed, ENDPOINTS)
