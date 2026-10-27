"""Regression coverage for fact provenance, complete retrieval, and visibility."""

import json

import pytest

from plugin.memory import MAX_FACTS, Memory


def test_source_snapshot_round_trip_and_detached_listing(tmp_path):
    """Actual records survive persistence without becoming mutable trust state."""
    path = tmp_path / "memory.json"
    memory = Memory(str(path))
    source = {"scope": "123", "message_id": "m1", "sender": {"id": "7"},
              "ts": 1234, "role": "owner", "tool": "remember"}
    fact_id = memory.add_fact("  full text " + "x" * 400, "tool", source=source)
    source["sender"]["id"] = "changed"
    loaded = Memory(str(path)).load()
    row = loaded.list_facts()[0]
    assert row["id"] == fact_id
    assert len(row["text"]) > 200
    assert row["source"] == row["source_scope"] == "tool"
    assert row["provenance"]["sender"]["id"] == "7"
    assert "role" not in loaded.data
    row["provenance"]["sender"]["id"] = "mutated"
    assert loaded.list_facts()[0]["provenance"]["sender"]["id"] == "7"


@pytest.mark.parametrize("source", [{"bad": object()}, {"bad": float("nan")}, []])
def test_invalid_source_fails_before_mutation(tmp_path, source):
    """Unserializable metadata cannot poison persistence or partially create facts."""
    path = tmp_path / "memory.json"
    memory = Memory(str(path))
    before = json.dumps(memory.data)
    with pytest.raises((TypeError, ValueError)):
        memory.add_fact("fact", "tool", source=source)
    assert json.dumps(memory.data) == before
    assert not path.exists()


def test_first_origin_dedup_cannot_publish_private_fact(tmp_path):
    """Cross-scope duplicate text retains its original private provenance only."""
    memory = Memory(str(tmp_path / "memory.json"))
    fid = memory.add_fact("same fact", "private:7", source={"scope": "private:7", "message_id": "secret"})
    assert memory.add_fact("same fact", "123", source={"scope": "123", "message_id": "public"}) == fid
    assert memory.list_facts(None, current_scope="123") == []
    private = memory.list_facts(None, current_scope="private:7")
    assert private[0]["provenance"]["message_id"] == "secret"
    assert len(memory.list_facts()) == 1


def test_scope_filter_muting_unknown_legacy_and_detached_results(tmp_path):
    """Public groups share facts; private facts remain local and unknown stays unknown."""
    memory = Memory(str(tmp_path / "memory.json"))
    memory.add_fact("group", "tool", source={"scope": "123", "sender": {"id": "7"}})
    memory.add_fact("private", "tool", source={"scope": "private:7"})
    memory.add_fact("legacy", "tool")
    memory.add_fact("unknown", "historical-import")
    rows = memory.list_facts(None, current_scope="456")
    assert [row["text"] for row in rows] == ["group", "legacy", "unknown"]
    assert "provenance" not in rows[1]
    assert [row["text"] for row in memory.list_facts(None, current_scope="456", muted=("123",))] == ["legacy", "unknown"]
    visible = memory.facts_for_scope("456")
    visible[0]["source"]["sender"]["id"] = "changed"
    assert memory.facts_for_scope("456")[0]["source"]["sender"]["id"] == "7"
    assert len(memory.list_facts(None)) == 4


def test_full_listing_and_search_preserve_existing_or_ranking(tmp_path):
    """The 500-row presentation default does not truncate complete factual retrieval."""
    memory = Memory(str(tmp_path / "memory.json"))
    memory.data["global"]["facts"] = [
        {"id": "f%03d" % index, "text": "ordinary %d" % index, "source_scope": "123", "at": index}
        for index in range(1, 701)
    ]
    memory.data["global"]["facts"][-1]["text"] = "needle both"
    memory.add_fact("needle", "456")
    assert len(memory.list_facts()) == 500
    assert len(memory.list_facts(None)) == 701
    assert memory.search_facts("needle both", 10) == ["needle both", "needle"]
    assert memory.search_facts("needle", allowed_scopes=("123",)) == ["needle both"]
    assert memory.search_facts("needle", allowed_scopes=()) == []
    assert memory.search_facts("") == []


def test_storage_cap_is_preserved(tmp_path):
    """Complete listing never increases the established 2000-entry storage budget."""
    memory = Memory(str(tmp_path / "memory.json"))
    memory.data["global"]["facts"] = [
        {"id": "f%03d" % index, "text": str(index), "source_scope": "tool", "at": index}
        for index in range(1, MAX_FACTS + 1)
    ]
    memory.add_fact("new", "123")
    rows = memory.list_facts(None)
    assert len(rows) == MAX_FACTS
    assert rows[0]["text"] == "2"
    assert rows[-1]["text"] == "new"
