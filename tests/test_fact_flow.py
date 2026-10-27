"""Offline fact category, exact deletion, privacy, and durable mutation contracts."""

import asyncio
import copy
import json

import pytest

from plugin.fact_tools import dispatch, schemas
from plugin.memory import Memory
from test_production_integration import Event

pytest_plugins = ("test_production_integration",)


def _store(tmp_path):
    """Seed independent scopes using arbitrary labels, not inferred roles or truths."""
    store = Memory(str(tmp_path / "memory.json"))
    store.add_fact("public shared item", "42", categories=["reader-defined"])
    store.add_fact("another public item", "43")
    store.add_fact("private item", "private:100", categories=["private-only"])
    return store


def _invoke(store, name, args=None, *, scope="42", can_write=True, muted=()):
    """Use the real helper with explicit caller authority and no model/network edges."""
    return dispatch(name, args or {}, memory=store, current_scope=scope,
                    can_write=can_write, muted=muted)


def test_categories_are_readonly_visible_counts_and_arbitrary_labels(tmp_path):
    """Category reads count visible public facts without publishing private labels."""
    store = _store(tmp_path)
    before = copy.deepcopy(store.data)
    result = _invoke(store, "facts_categories", can_write=False)
    assert result == {"ok": True, "categories": [{"category": "reader-defined", "count": 1}],
                      "total": 2, "uncategorized": 1}
    assert store.data == before
    assert "private-only" not in json.dumps(result)
    assert _invoke(store, "facts_categories", muted=["43"])["total"] == 1
    private = _invoke(store, "facts_categories", scope="private:100", can_write=False)
    assert private["total"] == 3


def test_classify_replaces_clears_deduplicates_and_survives_reload(tmp_path):
    """Explicit full label arrays replace rather than merge, including empty clears."""
    store = _store(tmp_path)
    ids = [row["id"] for row in store.list_facts(None, current_scope="42")]
    source = copy.deepcopy(store.data["global"]["facts"][0])
    result = _invoke(store, "facts_classify", {"ids": ids + ids[:1],
                      "categories": ["自选类别", "unrestricted-custom", "自选类别"]})
    assert result["ok"] and result["updated"] == 2
    assert result["ids"] == ids
    assert result["categories"] == ["自选类别", "unrestricted-custom"]
    assert all(row["categories"] == result["categories"] for row in
               Memory(store.path).load().list_facts(None, current_scope="42"))
    repeated = _invoke(store, "facts_classify", {"ids": ids, "categories": result["categories"]})
    assert repeated["ok"] and repeated["updated"] == 0
    changed = store.data["global"]["facts"][0]
    assert {key: value for key, value in changed.items() if key != "categories"} == {
        key: value for key, value in source.items() if key != "categories"}
    assert _invoke(store, "facts_classify", {"ids": ids, "categories": []})["updated"] == 2
    assert _invoke(store, "facts_categories")["uncategorized"] == 2


@pytest.mark.parametrize("args", [
    {"ids": [], "categories": ["x"]}, {"ids": "f001", "categories": ["x"]},
    {"ids": [1], "categories": ["x"]}, {"ids": ["f001"], "categories": "x"},
    {"ids": ["f001"], "categories": [""]}, {"ids": ["f001"], "categories": [1]},
    {"ids": ["f001"], "categories": None}, {"ids": ["f001"]},
])
def test_invalid_classification_never_mutates(tmp_path, args):
    """Malformed inputs cannot partially change labels or gain coerced authority."""
    store = _store(tmp_path)
    before = copy.deepcopy(store.data)
    assert _invoke(store, "facts_classify", args)["ok"] is False
    assert store.data == before


def test_hidden_missing_or_muted_id_rejects_entire_classification(tmp_path):
    """A mixed batch must validate every ID before touching any visible fact."""
    store = _store(tmp_path)
    rows = store.list_facts(None)
    before = copy.deepcopy(store.data)
    for forbidden in [rows[2]["id"], "unknown-id", rows[1]["id"]]:
        muted = ["43"] if forbidden == rows[1]["id"] else []
        result = _invoke(store, "facts_classify", {"ids": [rows[0]["id"], forbidden],
                         "categories": ["new"]}, muted=muted)
        assert result["ok"] is False and result["updated"] == 0
        assert store.data == before


@pytest.mark.parametrize("name,args", [
    ("facts_classify", {"ids": ["f001"], "categories": ["proposed"]}),
    ("facts_delete", {"id": "f001"}),
])
def test_readonly_worker_context_cannot_apply_mutations(tmp_path, name, args):
    """Worker proposals stay read-only even if tool arguments request a mutation."""
    store = _store(tmp_path)
    before = copy.deepcopy(store.data)
    assert _invoke(store, name, args, can_write=False)["ok"] is False
    assert store.data == before


def test_delete_one_prefers_exact_id_and_replay_never_hits_new_fact(tmp_path):
    """An explicit nonexistent ID cannot fall back to a broad matching keyword."""
    store = _store(tmp_path)
    rows = store.list_facts(None)
    target = rows[0]["id"]
    assert _invoke(store, "facts_delete", {"id": target, "keyword": "item"}) == {"ok": True, "removed": 1}
    assert len(store.list_facts(None)) == 2
    store.add_fact("new public item", "42")
    assert target not in {row["id"] for row in store.list_facts(None)}
    assert _invoke(store, "facts_delete", {"id": target, "keyword": "item"}) == {"ok": True, "removed": 0}
    assert _invoke(store, "facts_delete", {"id": rows[2]["id"]}) == {"ok": True, "removed": 0}
    assert _invoke(store, "facts_delete", {"keyword": "item"}) == {"ok": True, "removed": 2}
    assert Memory(store.path).load().list_facts(None)[0]["id"] == rows[2]["id"]


@pytest.mark.parametrize("name,args", [
    ("facts_classify", {"ids": ["f001"], "categories": ["new"]}),
    ("facts_delete", {"id": "f001"}),
])
def test_failed_storage_has_no_success_receipt_or_inmemory_mutation(tmp_path, monkeypatch, name, args):
    """Failed persistence rolls back the object and leaves durable facts unchanged."""
    store = _store(tmp_path)
    before = copy.deepcopy(store.data)

    def fail_save():
        """Model a denied atomic replacement at the actual checked store seam."""
        raise OSError("fixture disk write denied")

    monkeypatch.setattr(store, "save_checked", fail_save)
    assert _invoke(store, name, args)["ok"] is False
    assert store.data == before
    assert Memory(store.path).load().data == before


def test_production_fact_routes_share_store_and_deny_eventless_writes(production, tmp_path):
    """Native parent applies proposals; eventless worker reads cannot mutate the same file."""
    plugin = production()
    store = Memory(str(tmp_path / "memory" / "memory.json"))
    store.add_fact("routing public fixture", "42")
    store.add_fact("routing private fixture", "private:100", categories=["hidden"])
    fact_id = store.list_facts(None)[0]["id"]
    event = Event()

    async def scenario():
        """Exercise actual AST-loaded dispatcher rather than a duplicate test router."""
        args = {"ids": [fact_id], "categories": ["parent-applied"]}
        denied = await plugin._dispatch_tool("facts_classify", args, "42", event=None)
        assert denied["ok"] is False
        assert await plugin._dispatch_tool("facts_categories", {}, "42", event=None) == {
            "ok": True, "categories": [], "total": 1, "uncategorized": 1}
        applied = await plugin._dispatch_tool("facts_classify", args, "42", event=event)
        assert applied["ok"] is True and applied["updated"] == 1
        for name in ("facts_list", "facts_search"):
            response = await plugin._dispatch_tool(name, {"query": "routing"}, "42", event=None)
            assert response["ok"] is True
            assert len(response["facts"]) == 1
            assert response["facts"][0]["categories"] == ["parent-applied"]
        denied = await plugin._dispatch_tool("facts_delete", {"id": fact_id}, "42", event=None)
        assert denied["ok"] is False
        deleted = await plugin._dispatch_tool("facts_delete", {"id": fact_id}, "42", event=event)
        assert deleted == {"ok": True, "removed": 1}

    asyncio.run(scenario())
    remaining = Memory(store.path).load().list_facts(None)
    assert len(remaining) == 1 and remaining[0]["source_scope"] == "private:100"


def test_category_schemas_add_two_tools_without_duplicate_delete():
    """Deletion retains its existing native schema; classifications accept arbitrary strings."""
    catalog = {row["function"]["name"]: row["function"] for row in schemas()}
    assert set(catalog) == {"facts_classify", "facts_categories"}
    params = catalog["facts_classify"]["parameters"]
    assert set(params["required"]) == {"ids", "categories"}
    assert params["properties"]["categories"]["items"]["type"] == "string"
    assert "enum" not in params["properties"]["categories"]["items"]
