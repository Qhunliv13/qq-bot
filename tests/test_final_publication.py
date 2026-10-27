"""Publication is an explicit channel choice, never wording-based deduplication."""

import json

import pytest

from plugin.final_publication import select


@pytest.mark.parametrize("text", ["same", "slightly different", "new substantive update"])
def test_unmarked_completion_after_public_output_remains_draft(text):
    """No classification of similar or dissimilar text controls the result."""
    outcome = select(text, [{"id": "123"}], explicit=True)
    assert outcome["publish"] is False
    assert outcome["draft"] == text
    assert outcome["selection"]["text"] == text


def test_explicit_new_allows_identical_text_and_same_target():
    """A deliberate public action stays available regardless of existing wording."""
    raw = json.dumps({"reply_to": ["123"], "text": "same", "delivery": "new"})
    outcome = select(raw, [{"id": "123"}], explicit=True)
    assert outcome["publish"] is True
    assert outcome["selection"]["reply_to"] == ["123"]
    assert outcome["draft"] is None


def test_foreground_implicit_first_answer_preserves_compatibility():
    """Ordinary chat still has one implicit final public reply before any tool send."""
    assert select("answer", [{"id": "123"}])["publish"] is True


def test_background_requires_explicit_publication_without_fake_current_input():
    """No human message is manufactured to support a background publication."""
    assert select("background thought", [], explicit=True, allow_unattributed=True)["publish"] is False
    raw = json.dumps({"reply_to": [], "text": "background update", "delivery": "new"})
    assert select(raw, [], explicit=True, allow_unattributed=True)["publish"] is True


@pytest.mark.parametrize("payload", [
    {"reply_to": ["999"], "text": "wrong", "delivery": "new"},
    {"reply_to": ["123"], "text": "wrong", "delivery": True},
    {"reply_to": ["123"], "text": "draft", "delivery": "already_delivered"},
])
def test_invalid_or_internal_dispositions_never_send(payload):
    """Existing decoder remains authoritative about target validity and dispositions."""
    assert select(json.dumps(payload), [{"id": "123"}], explicit=True)["publish"] is False
