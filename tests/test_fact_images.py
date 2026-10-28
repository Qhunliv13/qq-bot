"""Persistent image facts preserve exact bytes, privacy and transactional storage."""

import base64
import copy
import json

import pytest

from plugin.fact_images import MAX_IMAGE_BYTES, images_metadata, normalize_images
from plugin.memory import Memory

# A real one-pixel PNG keeps tests independent of Pillow and network access.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aP3sAAAAASUVORK5CYII="
)


def image_ref(payload=PNG, mime="image/png"):
    """Build an explicit caller snapshot; storage must never fetch a reference."""
    return "data:" + mime + ";base64," + base64.b64encode(payload).decode("ascii")


def test_normalization_canonicalizes_mime_deduplicates_and_detaches():
    """Converters' generic JPEG labels must not lose the true downloaded PNG format."""
    refs = [image_ref(mime="image/jpeg"), image_ref()]
    assert normalize_images(None) == []
    assert normalize_images(refs) == [image_ref()]
    normalized = normalize_images(refs)
    refs.clear()
    assert normalized == [image_ref()]
    metadata = images_metadata(normalized)
    assert metadata == [{"index": 0, "mime": "image/png", "bytes": len(PNG)}]
    metadata[0]["mime"] = "changed"
    assert images_metadata(normalized)[0]["mime"] == "image/png"


@pytest.mark.parametrize("payload,mime", [
    (b"\xff\xd8\xff\xe0jpeg", "image/jpeg"),
    (b"GIF87aimage", "image/gif"),
    (b"GIF89aimage", "image/gif"),
    (b"RIFF\x08\x00\x00\x00WEBPimage", "image/webp"),
])
def test_supported_signatures_are_canonical(payload, mime):
    """Signature detection needs no optional decoder and overrides image subtype hints."""
    assert normalize_images([image_ref(payload, "image/x-test")]) == [image_ref(payload, mime)]


@pytest.mark.parametrize("images", [
    "data:image/png;base64,abcd", (), {}, [None], [1], [True],
    ["https://example.invalid/temporary.png"], ["C:\\temporary.png"],
    ["file:///tmp/image.png"], ["data:text/html;base64,PGh0bWw+"],
    ["data:image/octet-stream;base64,iVBORw0KGgo="],
    ["data:image/octet;base64,iVBORw0KGgo="],
    ["data:image/html;base64,iVBORw0KGgo="],
    ["data:image/png;charset=utf-8;base64,iVBORw0KGgo="],
    ["data:image/png;base64,"], ["data:image/png;base64,%%%"],
    ["data:image/png;base64,AAA"], ["data:image/png;base64,YQ==junk"],
    ["data:image/png;base64,iVBO\nRw0KGgo="],
    [image_ref(b"<html>not an image</html>")], [image_ref(b"a secret token")],
])
def test_invalid_images_fail_before_add_or_edit_mutation(tmp_path, images):
    """Reject malformed references and disguised arbitrary files without touching IDs or disk."""
    memory = Memory(str(tmp_path / "memory.json"))
    fid = memory.add_fact("existing", "123", images=[image_ref()])
    before = copy.deepcopy(memory.data)
    durable = (tmp_path / "memory.json").read_bytes()
    for operation in [lambda: memory.add_fact("new", "123", images=images),
                      lambda: memory.edit_fact(fid, "changed", images=images)]:
        with pytest.raises((TypeError, ValueError)):
            operation()
        assert memory.data == before
        assert (tmp_path / "memory.json").read_bytes() == durable


def test_per_image_threshold_without_arbitrary_count_cap():
    """Retain the existing exact 4 MiB limit but do not invent a six-image cap."""
    boundary = PNG + b"\0" * (MAX_IMAGE_BYTES - len(PNG))
    assert images_metadata([image_ref(boundary)])[0]["bytes"] == MAX_IMAGE_BYTES
    with pytest.raises(ValueError):
        normalize_images([image_ref(boundary + b"x")])
    many = [image_ref(PNG + bytes([index])) for index in range(7)]
    assert normalize_images(many) == many


def test_restart_preserves_exact_bytes_source_categories_and_image_only_fact(tmp_path):
    """Pure-image memories survive restart without temporary URLs or caller aliasing."""
    path = tmp_path / "memory.json"
    memory = Memory(str(path))
    refs = [image_ref(mime="image/jpeg")]
    source = {"scope": "123", "message_id": "m1", "sender": {"id": "7"}}
    fid = memory.add_fact("  ", "123", source=source, categories=[" evidence "], images=refs)
    refs.clear()
    source["sender"]["id"] = "changed"
    loaded = Memory(str(path)).load()
    result = loaded.get_fact_image(fid, 0, current_scope="456")
    assert result == {"ref": image_ref(), "index": 0, "mime": "image/png", "bytes": len(PNG)}
    assert base64.b64decode(result["ref"].split(",", 1)[1]) == PNG
    assert loaded.list_facts()[0]["text"] == ""
    assert loaded.list_facts()[0]["categories"] == ["evidence"]
    assert loaded.list_facts()[0]["provenance"]["sender"]["id"] == "7"
    assert loaded.data["global"]["facts"][0]["images"] == [image_ref()]


def test_dedup_uses_text_and_images_preserving_first_private_origin(tmp_path):
    """A public repeat cannot publish a private image fact or replace its labels."""
    memory = Memory(str(tmp_path / "memory.json"))
    fid = memory.add_fact("same", "private:7", source={"scope": "private:7"},
                          categories=["first"], images=[image_ref()])
    before = copy.deepcopy(memory.data)
    assert memory.add_fact(" same ", "123", source={"scope": "123"},
                           categories=["later"], images=[image_ref(mime="image/jpeg")]) == fid
    assert memory.data == before
    assert memory.list_facts(None, current_scope="123") == []
    other = memory.add_fact("same", "123", images=[image_ref(PNG + b"other")])
    text_only = memory.add_fact("same", "123")
    assert len({fid, other, text_only}) == 3
    memory.data["global"]["facts"][-1].pop("images")
    assert memory.add_fact("same", "456", images=[]) == text_only
    assert memory.list_facts()[-1]["images"] == []


def test_listing_is_detached_metadata_only_and_complete_lookup(tmp_path):
    """No inline payload enters listings; retrieval is not limited to the first 500 facts."""
    memory = Memory(str(tmp_path / "memory.json"))
    memory.data["global"]["facts"] = [
        {"id": "f%03d" % index, "text": "legacy", "source_scope": "123"}
        for index in range(1, 502)
    ]
    fid = memory.add_fact("image fact", "123", images=[image_ref()])
    assert len(memory.list_facts()) == 500
    rows = memory.list_facts(None)
    assert rows[-1]["images"] == images_metadata([image_ref()])
    assert all(row["images"] == [] for row in rows[:-1])
    assert "base64" not in json.dumps(rows)
    assert base64.b64encode(PNG).decode("ascii") not in json.dumps(rows)
    rows[-1]["images"][0]["bytes"] = -1
    assert memory.list_facts(None)[-1]["images"][0]["bytes"] == len(PNG)
    assert memory.get_fact_image(fid, 0, current_scope="456")["ref"] == image_ref()


@pytest.mark.parametrize("index", [True, False, -1, 0.0, "0", None])
def test_invalid_index_is_rejected_even_for_missing_facts(tmp_path, index):
    """Do not coerce booleans, strings or negative indices into image access."""
    memory = Memory(str(tmp_path / "memory.json"))
    with pytest.raises(ValueError):
        memory.get_fact_image("missing", index)


def test_hidden_missing_muted_and_missing_index_are_indistinguishable(tmp_path):
    """Guessed private IDs cannot reveal existence through reads or edits."""
    memory = Memory(str(tmp_path / "memory.json"))
    private = memory.add_fact("private", "private:7", images=[image_ref()])
    public = memory.add_fact("public", "123", images=[image_ref()])
    before = copy.deepcopy(memory.data)
    for fid in [private, "missing"]:
        assert memory.get_fact_image(fid, 0, current_scope="456") is None
        assert memory.edit_fact(fid, "changed", images=[], current_scope="456") is False
    assert memory.get_fact_image(public, 0, current_scope="456", muted=("123",)) is None
    assert memory.edit_fact(public, "changed", current_scope="456", muted=("123",)) is False
    assert memory.get_fact_image(public, 1, current_scope="456") is None
    assert memory.data == before
    assert memory.get_fact_image(private, 0, current_scope="private:7") is not None


def test_edit_preserves_replaces_and_clears_images_with_original_metadata(tmp_path, monkeypatch):
    """None preserves attachments, [] clears them, and an empty fact is never committed."""
    memory = Memory(str(tmp_path / "memory.json"))
    fid = memory.add_fact("old", "123", source={"scope": "123", "message_id": "m"},
                          categories=["label"], images=[image_ref()])
    original = copy.deepcopy(memory.data["global"]["facts"][0])
    monkeypatch.setattr("plugin.memory.time.time", lambda: 123456)
    assert memory.edit_fact(fid, "", current_scope="456") is True
    edited = memory.data["global"]["facts"][0]
    assert edited["images"] == original["images"]
    assert edited["id"] == original["id"] and edited["at"] == 123456
    assert edited["source"] == original["source"] and edited["categories"] == ["label"]
    before = copy.deepcopy(memory.data)
    with pytest.raises(ValueError):
        memory.edit_fact(fid, " ", images=[])
    assert memory.data == before
    replacement = image_ref(PNG + b"replacement")
    assert memory.edit_fact(fid, "new", images=[replacement]) is True
    assert Memory(memory.path).load().get_fact_image(fid, 0)["ref"] == replacement
    assert memory.edit_fact(fid, "text remains", images=[]) is True
    assert memory.get_fact_image(fid, 0) is None
    assert Memory(memory.path).load().list_facts()[0]["images"] == []


@pytest.mark.parametrize("operation", ["add", "edit"])
def test_failed_atomic_replace_rolls_back_every_field(tmp_path, monkeypatch, operation):
    """Failed disk commits restore counters, attachments and source, cleaning temp files."""
    memory = Memory(str(tmp_path / "memory.json"))
    fid = memory.add_fact("old", "123", images=[image_ref()])
    before = copy.deepcopy(memory.data)

    def fail_replace(*_args):
        """Inject the failure after the temporary file has actually been written."""
        raise OSError("simulated replace failure")

    monkeypatch.setattr("plugin.memory.os.replace", fail_replace)
    with pytest.raises(OSError):
        if operation == "add":
            memory.add_fact("new", "456", images=[image_ref()])
        else:
            memory.edit_fact(fid, "edited", images=[])
    assert memory.data == before == Memory(memory.path).load().data
    assert len(list(tmp_path.iterdir())) == 1


def test_legacy_missing_id_duplicate_is_persisted_without_losing_data(tmp_path):
    """Backfill a legacy duplicate ID while retaining unknown fields and first provenance."""
    memory = Memory(str(tmp_path / "memory.json"))
    memory.data["global"]["facts"] = [
        {"text": "legacy", "source_scope": "historical-import", "custom": {"keep": 1}}
    ]
    memory.data["scopes"]["private:7"] = {"summary": "untouched", "turns": 3}
    fid = memory.add_fact("legacy", "123", categories=["not-a-replacement"], images=[])
    loaded = Memory(memory.path).load()
    assert loaded.data == memory.data
    assert loaded.data["global"]["facts"] == [
        {"id": fid, "text": "legacy", "source_scope": "historical-import", "custom": {"keep": 1}}
    ]
    assert loaded.data["scopes"]["private:7"] == {"summary": "untouched", "turns": 3}
    assert loaded.list_facts()[0]["images"] == []


def test_empty_text_without_images_retains_legacy_noop(tmp_path):
    """Existing text-only callers can still submit an empty fact without creating state."""
    memory = Memory(str(tmp_path / "memory.json"))
    before = copy.deepcopy(memory.data)
    assert memory.add_fact("  ", images=[]) is None
    assert memory.data == before
    assert not (tmp_path / "memory.json").exists()


def test_invalid_source_or_categories_do_not_mutate_image_fact(tmp_path):
    """Image support cannot bypass the original validation ordering or source constraints."""
    memory = Memory(str(tmp_path / "memory.json"))
    before = copy.deepcopy(memory.data)
    for kwargs in [{"source": {"bad": object()}}, {"categories": [""]}]:
        with pytest.raises((TypeError, ValueError)):
            memory.add_fact("", "123", images=[image_ref()], **kwargs)
        assert memory.data == before
        assert not (tmp_path / "memory.json").exists()
