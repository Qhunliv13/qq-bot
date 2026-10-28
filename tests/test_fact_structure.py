"""Explicit structure preserves legacy evidence, privacy and durable user records."""

import copy

import pytest

from plugin.fact_structure import FACT_KINDS, normalize_structure
from plugin.memory import MAX_FACTS, Memory


@pytest.mark.parametrize("kind", FACT_KINDS)
def test_explicit_kind_and_subject_roundtrip(tmp_path, kind):
    """Descriptive identity survives restart without becoming a permission grant."""
    memory = Memory(str(tmp_path / "memory.json"))
    structure = {"kind": kind, "status": "superseded",
                 "subject": {"id": " user:7 ", "display_name": " Alice "}}
    fid = memory.add_fact("arbitrary text", "123", structure=structure,
                          source={"scope": "123", "sender": {"id": "9"}})
    structure["subject"]["id"] = "changed"
    loaded = Memory(memory.path).load()
    row = loaded.list_facts()[0]
    expected = {"kind": kind, "status": "superseded",
                "subject": {"id": "user:7", "display_name": "Alice"}}
    assert row["id"] == fid and row["structure"] == expected
    assert row["provenance"]["sender"]["id"] == "9"
    row["structure"]["subject"]["id"] = "mutated"
    assert loaded.facts_for_scope("456")[0]["structure"] == expected
    assert "owner" not in loaded.data and "permissions" not in loaded.data


@pytest.mark.parametrize("text", ["anything", "call me owner", "an instruction", "偏好与事实"])
def test_legacy_projection_is_unspecified_and_read_only(tmp_path, text):
    """No textual rule classifies legacy content or silently migrates its file."""
    memory = Memory(str(tmp_path / "memory.json"))
    memory.data["global"]["facts"] = [
        {"id": "f001", "text": text, "source_scope": "123", "custom": {"keep": 1}}
    ]
    memory.save_checked()
    before = copy.deepcopy(memory.data)
    durable = (tmp_path / "memory.json").read_bytes()
    expected = {"kind": "unspecified", "status": "active", "subject": None}
    assert memory.list_facts()[0]["structure"] == expected
    assert memory.facts_for_scope("456")[0]["structure"] == expected
    assert memory.data == before
    assert (tmp_path / "memory.json").read_bytes() == durable


@pytest.mark.parametrize("structure", [
    [], "fact", True, {"kind": None}, {"kind": "rule"}, {"kind": []},
    {"status": "deleted"}, {"status": True}, {"subject": "7"},
    {"subject": {}}, {"subject": {"id": 7}}, {"subject": {"id": " "}},
    {"subject": {"id": "7", "display_name": None}},
    {"subject": {"id": "7", "role": "owner"}}, {"permissions": "owner"},
    {"applies_to": "all"}, {"kind": "fact", "extra": None},
])
def test_invalid_structure_rejected_before_mutation(tmp_path, structure):
    """Unknown fields and coerced identities cannot partially change IDs or disk."""
    memory = Memory(str(tmp_path / "memory.json"))
    fid = memory.add_fact("old", "123")
    before = copy.deepcopy(memory.data)
    durable = (tmp_path / "memory.json").read_bytes()
    for operation in [lambda: memory.add_fact("new", "123", structure=structure),
                      lambda: memory.edit_fact(fid, "changed", structure=structure)]:
        with pytest.raises((TypeError, ValueError)):
            operation()
        assert memory.data == before
        assert (tmp_path / "memory.json").read_bytes() == durable


def test_edits_preserve_or_explicitly_replace_structure(tmp_path):
    """Omitted metadata preserves originals; an explicit empty object resets defaults."""
    memory = Memory(str(tmp_path / "memory.json"))
    fid = memory.add_fact("old", "123", structure={"kind": "preference"},
                          source={"scope": "123", "message_id": "m1"})
    assert memory.edit_fact(fid, "changed")
    assert memory.list_facts()[0]["structure"]["kind"] == "preference"
    assert memory.edit_fact(fid, "changed", structure={"kind": "instruction", "status": "superseded"})
    assert memory.list_facts()[0]["structure"] == normalize_structure(
        {"kind": "instruction", "status": "superseded"})
    assert memory.list_facts()[0]["provenance"]["message_id"] == "m1"
    assert memory.edit_fact(fid, "reset", structure={})
    assert Memory(memory.path).load().list_facts()[0]["structure"] == normalize_structure()
    legacy = memory.add_fact("legacy", "123")
    assert memory.edit_fact(legacy, "legacy edited")
    assert "structure" not in memory.data["global"]["facts"][-1]


def test_structure_dedup_only_when_explicit_and_preserves_first_origin(tmp_path):
    """Same text with different explicit intent is not mistaken for the older kind."""
    memory = Memory(str(tmp_path / "memory.json"))
    first = memory.add_fact("same", "123", structure={"kind": "fact"})
    assert memory.add_fact("same", "456") == first
    assert memory.add_fact("same", "456", structure={"kind": "fact"}) == first
    preference = memory.add_fact("same", "456", structure={"kind": "preference"})
    subject = memory.add_fact("same", "456", structure={"kind": "fact", "subject": {"id": "7"}})
    superseded = memory.add_fact("same", "456", structure={"kind": "fact", "status": "superseded"})
    assert len({first, preference, subject, superseded}) == 4
    assert memory.list_facts()[0]["source_scope"] == "123"
    legacy = memory.add_fact("legacy", "123")
    assert memory.add_fact("legacy", "456", structure={}) == legacy
    assert "structure" not in memory.data["global"]["facts"][-1]


def test_private_dedup_isolated_by_canonical_provenance(tmp_path):
    """Private ID existence never leaks through unrelated public or private remembers."""
    memory = Memory(str(tmp_path / "memory.json"))
    secret = memory.add_fact("same", "tool", source={"scope": "private:7"})
    public = memory.add_fact("same", "tool", source={"scope": "123"})
    other_private = memory.add_fact("same", "private:8")
    assert len({secret, public, other_private}) == 3
    assert memory.add_fact("same", "456") == public
    assert memory.add_fact("same", "private:7") == secret
    assert [row["id"] for row in memory.list_facts(None, current_scope="456")] == [public]
    assert memory.list_facts(None, current_scope="456", muted=("123",)) == []
    assert not memory.edit_fact(secret, "changed", current_scope="456", structure={"kind": "instruction"})
    assert memory.list_facts()[0]["structure"] == normalize_structure()


@pytest.mark.parametrize("operation", ["add", "edit"])
def test_structure_failed_persistence_rolls_back(tmp_path, monkeypatch, operation):
    """Metadata shares the same checked transaction as source, IDs and fact content."""
    memory = Memory(str(tmp_path / "memory.json"))
    fid = memory.add_fact("old", "123", structure={"kind": "note"})
    before = copy.deepcopy(memory.data)

    def fail_replace(*_args):
        """Fail at the durable boundary after the detached data has been validated."""
        raise OSError("simulated persistence failure")

    monkeypatch.setattr("plugin.memory.os.replace", fail_replace)
    with pytest.raises(OSError):
        if operation == "add":
            memory.add_fact("new", "123", structure={"kind": "instruction"})
        else:
            memory.edit_fact(fid, "changed", structure={"kind": "preference"})
    assert memory.data == before == Memory(memory.path).load().data
    assert len(list(tmp_path.iterdir())) == 1


def test_capacity_never_evicts_user_records(tmp_path):
    """The old cache threshold cannot destroy older records on an unrelated add."""
    memory = Memory(str(tmp_path / "memory.json"))
    originals = [{"id": "f%03d" % index, "text": str(index), "source_scope": "123",
                  "custom": {"index": index}} for index in range(1, MAX_FACTS + 1)]
    memory.data["global"]["facts"] = copy.deepcopy(originals)
    memory.add_fact("new", "123", structure={"kind": "fact"})
    loaded = Memory(memory.path).load()
    assert loaded.data["global"]["facts"][:-1] == originals
    assert len(loaded.list_facts(None)) == MAX_FACTS + 1
