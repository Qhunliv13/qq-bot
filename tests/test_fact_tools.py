"""Scoped labels, exact-ID deletion, persistence and read-only mutation regressions."""

import copy

import pytest

from plugin.fact_tools import dispatch, schemas
from plugin.memory import Memory


def invoke(memory, name, args=None, **context):
    """Exercise the same trusted-context dispatcher used by the main tool boundary."""
    return dispatch(name, args or {}, memory=memory, current_scope=context.get("scope", "123"),
                    can_write=context.get("write", True), muted=context.get("muted", ()))


def test_labels_roundtrip_detached_and_duplicate_preserves_first(tmp_path):
    """Trim/deduplicate labels without allowing duplicate remembers to overwrite them."""
    memory = Memory(str(tmp_path / "memory.json"))
    labels = [" work ", "work", "个人"]
    fid = memory.add_fact("one", "123", categories=labels)
    labels.append("later")
    assert memory.add_fact("one", "123", categories=["replace"]) == fid
    loaded = Memory(memory.path).load()
    row = loaded.list_facts()[0]
    assert row["categories"] == ["work", "个人"]
    row["categories"].append("detached")
    assert loaded.list_facts()[0]["categories"] == ["work", "个人"]
    loaded.data["global"]["facts"][0].pop("categories")
    assert loaded.list_facts()[0]["categories"] == []


@pytest.mark.parametrize("labels", ["work", [""], [" "], [1], {}, [None]])
def test_invalid_categories_do_not_mutate(tmp_path, labels):
    """Reject coercion before ID allocation or any persistence takes place."""
    memory = Memory(str(tmp_path / "memory.json"))
    before = copy.deepcopy(memory.data)
    with pytest.raises((TypeError, ValueError)):
        memory.add_fact("one", "123", categories=labels)
    assert memory.data == before
    assert not (tmp_path / "memory.json").exists()


def test_batch_replace_clear_and_idempotency(tmp_path):
    """A repeated exact-label replacement changes neither storage nor timestamps."""
    memory = Memory(str(tmp_path / "memory.json"))
    ids = [memory.add_fact(text, "123") for text in ["one", "two"]]
    result = invoke(memory, "facts_classify", {"ids": ids + ids[:1], "categories": [" a ", "a", "b"]})
    assert result["updated"] == 2
    before = copy.deepcopy(memory.data)
    assert invoke(memory, "facts_classify", {"ids": ids, "categories": ["a", "b"]})["updated"] == 0
    assert memory.data == before
    assert all(row["categories"] == ["a", "b"] for row in Memory(memory.path).load().list_facts())
    assert invoke(memory, "facts_classify", {"ids": ids, "categories": []})["updated"] == 2
    assert all(row["categories"] == [] for row in memory.list_facts())


def test_visibility_all_or_none_and_category_counts(tmp_path):
    """A guessed private ID and nonexistent ID both reject an entire visible batch."""
    memory = Memory(str(tmp_path / "memory.json"))
    public = memory.add_fact("public", "123", categories=["visible", "owner"])
    private = memory.add_fact("private", "private:7", categories=["secret"])
    muted = memory.add_fact("muted", "456", categories=["muted"])
    memory.add_fact("legacy", "tool")
    for hidden in [private, "f999999"]:
        before = copy.deepcopy(memory.data)
        result = invoke(memory, "facts_classify", {"ids": [public, hidden], "categories": ["new"]})
        assert result["ok"] is False and result["updated"] == 0
        assert memory.data == before
    counts = invoke(memory, "facts_categories", muted=("456",))
    assert counts == {"ok": True, "total": 2, "uncategorized": 1,
                      "categories": [{"category": "owner", "count": 1},
                                     {"category": "visible", "count": 1}]}
    assert invoke(memory, "facts_classify", {"ids": [muted], "categories": ["no"]}, muted=("456",))["ok"] is False


@pytest.mark.parametrize("ids", [[], "f001", [1], [" "], None])
def test_invalid_ids_rejected(tmp_path, ids):
    """Invalid bulk payloads never quietly become a no-op or whole-store rewrite."""
    memory = Memory(str(tmp_path / "memory.json"))
    memory.add_fact("one", "123")
    before = copy.deepcopy(memory.data)
    assert invoke(memory, "facts_classify", {"ids": ids, "categories": []})["ok"] is False
    assert memory.data == before


def test_readonly_and_no_scope_cannot_mutate(tmp_path):
    """Labels and tool args cannot grant a read-only agent mutation authority."""
    memory = Memory(str(tmp_path / "memory.json"))
    fid = memory.add_fact("one", "123")
    for name, args in [("facts_classify", {"ids": [fid], "categories": ["owner"], "can_write": True}),
                       ("facts_delete", {"id": fid})]:
        assert invoke(memory, name, args, write=False)["ok"] is False
        assert invoke(memory, name, args, scope="")["ok"] is False
    assert invoke(memory, "facts_categories", write=False)["ok"] is True
    assert len(memory.list_facts()) == 1


def test_exact_delete_has_priority_and_ids_are_not_recycled(tmp_path):
    """Replaying an explicit-ID deletion never expands to keyword or a later fact."""
    memory = Memory(str(tmp_path / "memory.json"))
    first = memory.add_fact("common one", "123")
    second = memory.add_fact("common two", "123")
    assert invoke(memory, "facts_delete", {"id": first, "keyword": "common"})["removed"] == 1
    memory = Memory(memory.path).load()
    third = memory.add_fact("common three", "123")
    assert third not in [first, second]
    assert invoke(memory, "facts_delete", {"id": first, "keyword": "common"})["removed"] == 0
    assert len(memory.list_facts()) == 2
    private = memory.add_fact("common private", "private:7")
    assert invoke(memory, "facts_delete", {"id": private})["removed"] == 0
    assert invoke(memory, "facts_delete", {"keyword": "common"})["removed"] == 2
    assert [row["id"] for row in memory.list_facts()] == [private]


@pytest.mark.parametrize("name", ["facts_delete", "facts_classify"])
def test_failed_persistence_does_not_report_success(tmp_path, monkeypatch, name):
    """Atomic replace failure restores RAM, leaves durable data, and cleans temporary files."""
    memory = Memory(str(tmp_path / "memory.json"))
    fid = memory.add_fact("one", "123")
    before = copy.deepcopy(memory.data)

    def fail_replace(*_args):
        """Simulate failure at the durable commit boundary rather than before mutation."""
        raise OSError("sensitive filesystem path")

    monkeypatch.setattr("plugin.memory.os.replace", fail_replace)
    args = {"id": fid} if name == "facts_delete" else {"ids": [fid], "categories": ["new"]}
    result = invoke(memory, name, args)
    assert result["ok"] is False
    assert "sensitive" not in str(result)
    assert memory.data == before == Memory(memory.path).load().data
    assert len(list(tmp_path.iterdir())) == 1


def test_deleting_highest_legacy_id_preserves_high_water(tmp_path):
    """Old stores acquire their counter before the highest surviving ID is deleted."""
    memory = Memory(str(tmp_path / "memory.json"))
    memory.data["global"]["facts"] = [{"id": "f200", "text": "legacy", "source_scope": "123"}]
    memory.save_checked()
    assert invoke(memory, "facts_delete", {"id": "f200"})["removed"] == 1
    memory = Memory(memory.path).load()
    assert memory.add_fact("new", "123") == "f201"
    assert invoke(memory, "facts_delete", {"id": "f200"})["removed"] == 0


def test_schemas_only_add_new_tools_and_unknown_is_unhandled(tmp_path):
    """Avoid reintroducing duplicate delete metadata or consuming unrelated dispatch paths."""
    assert {tool["function"]["name"] for tool in schemas()} == {"facts_classify", "facts_categories"}
    memory = Memory(str(tmp_path / "memory.json"))
    assert invoke(memory, "other") is None
    assert invoke(memory, "facts_classify", {"ids": ["f001"]})["ok"] is False
