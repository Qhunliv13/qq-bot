"""Offline contract checks; no live plugin, framework, or QQ adapter is required."""

import ast
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import unittest

from bot_core import Endpoint, InboundEvent, Principal


class BotCoreContractTests(unittest.TestCase):
    """Verify isolation, opaque identities, immutable content, and provenance shape."""

    def setUp(self) -> None:
        """Build explicitly bound sample identities rather than fixed deployment IDs."""
        self.source = Endpoint("example", "bot-A", "group", "room:opaque")
        self.actor = Principal("example", "bot-A", "sender:opaque")

    def event(self, **changes: object) -> InboundEvent:
        """Build a human envelope with overrides for negative contract tests."""
        values = dict(event_id="evt:opaque", source=self.source, actor=self.actor,
                      origin="human", content_json='{"text":"hello"}')
        values.update(changes)
        return InboundEvent(**values)

    def test_canonical_scope_key(self) -> None:
        """Scope encoding has a stable version, order, and compact JSON syntax."""
        self.assertEqual(self.source.storage_key(),
                         'scope:v1:["example","bot-A","group","room:opaque"]')

    def test_endpoint_kinds_remain_separate(self) -> None:
        """An identical native ID cannot collapse group, private, or local scopes."""
        keys = {Endpoint("example", "bot-A", kind, "12:34").storage_key()
                for kind in ("group", "private", "local")}
        self.assertEqual(len(keys), 3)

    def test_accounts_remain_separate(self) -> None:
        """Receiving bot bindings are part of persistence isolation."""
        self.assertNotEqual(self.source.storage_key(),
                            Endpoint("example", "bot-B", "group", "room:opaque").storage_key())

    def test_platforms_remain_separate(self) -> None:
        """A native ID from another transport cannot alias the same scope."""
        self.assertNotEqual(self.source.storage_key(),
                            Endpoint("another", "bot-A", "group", "room:opaque").storage_key())

    def test_separator_injection_does_not_collide(self) -> None:
        """Delimiters inside opaque IDs cannot shift tuple boundaries."""
        first = Endpoint("example", "bot:A", "private", "B:C")
        second = Endpoint("example", "bot", "private", "A:B:C")
        self.assertNotEqual(first.storage_key(), second.storage_key())
        self.assertEqual(json.loads(first.storage_key()[len("scope:v1:"):]),
                         ["example", "bot:A", "private", "B:C"])

    def test_unicode_is_retained(self) -> None:
        """Unicode identity text survives without ASCII escaping or normalization."""
        endpoint = Endpoint("example", "接收端", "group", "房间:e\u0301")
        self.assertIn("接收端", endpoint.storage_key())
        self.assertEqual(endpoint.native_id, "房间:e\u0301")
        self.assertNotEqual(endpoint, Endpoint("example", "接收端", "group", "房间:é"))

    def test_whitespace_case_and_placeholder_ids_are_not_normalized(self) -> None:
        """Opaque identifiers including NA and leading spaces remain literal."""
        for native_id in ("NA", "N/A", " 001 ", "Sender", "sender"):
            with self.subTest(native_id=native_id):
                endpoint = Endpoint("Example", " Bot ", "private", native_id)
                self.assertEqual(endpoint.native_id, native_id)
                self.assertEqual(endpoint.account, " Bot ")
                self.assertEqual(endpoint.platform, "Example")

    def test_empty_identifiers_rejected(self) -> None:
        """A route always needs explicit platform, binding, and native identity."""
        for field in ("platform", "account", "native_id"):
            values = dict(platform="example", account="bot-A", kind="group", native_id="room")
            values[field] = ""
            with self.subTest(field=field), self.assertRaises(ValueError):
                Endpoint(**values)

    def test_identifier_types_are_strict(self) -> None:
        """Integers, bools, bytes, and null never become identities implicitly."""
        for field in ("platform", "account", "native_id", "kind"):
            for invalid in (True, 123, b"id", None):
                values = dict(platform="example", account="bot-A", kind="group", native_id="room")
                values[field] = invalid
                with self.subTest(field=field, invalid=invalid), self.assertRaises(TypeError):
                    Endpoint(**values)

    def test_identifier_controls_rejected(self) -> None:
        """Cc control characters are invalid in every opaque identity field."""
        for invalid in ("x\x00", "x\n", "x\x7f", "x\x85"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                Principal("example", "bot-A", invalid)

    def test_surrogate_identifiers_rejected(self) -> None:
        """An identity must encode as UTF-8 without replacement characters."""
        with self.assertRaises(ValueError):
            Endpoint("example", "bot-A", "group", "\ud800")

    def test_platform_route_token_validation(self) -> None:
        """Platforms are explicit portable tokens, not silently normalized labels."""
        for invalid in ("space platform", "平台", "x/y", "-example"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                Endpoint(invalid, "bot-A", "private", "sender")

    def test_unknown_or_cased_kind_rejected(self) -> None:
        """Only the three documented channel kinds are accepted exactly."""
        for kind in ("channel", "GROUP", "", "private:123"):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                Endpoint("example", "bot-A", kind, "id")

    def test_local_still_needs_binding(self) -> None:
        """Local transports must supply an explicit namespace rather than a fallback."""
        with self.assertRaises(ValueError):
            Endpoint("local", "", "local", "scheduler")

    def test_endpoint_is_frozen(self) -> None:
        """Route identity cannot mutate after validation."""
        with self.assertRaises(FrozenInstanceError):
            self.source.native_id = "other"

    def test_principal_is_frozen(self) -> None:
        """Actor identity cannot mutate after validation."""
        with self.assertRaises(FrozenInstanceError):
            self.actor.account = "other"

    def test_display_name_not_identity(self) -> None:
        """Presentation names do not affect identity equality or hash."""
        named = Principal("example", "bot-A", "sender:opaque", "owner")
        self.assertEqual(named, self.actor)
        self.assertEqual(hash(named), hash(self.actor))
        self.assertNotEqual(named, Principal("example", "bot-A", "other", "owner"))
        self.assertFalse(hasattr(named, "is_owner"))

    def test_display_name_type_and_utf8(self) -> None:
        """Display fields cannot smuggle objects or unencodable text."""
        with self.assertRaises(TypeError):
            Principal("example", "bot-A", "sender", True)
        with self.assertRaises(ValueError):
            Principal("example", "bot-A", "sender", "\udfff")
        self.assertEqual(Principal("example", "bot-A", "sender", "").display_name, "")

    def test_human_actor_need_not_equal_group_id(self) -> None:
        """The authenticated sender and destination group have distinct identities."""
        self.assertEqual(self.event().actor, self.actor)
        self.assertNotEqual(self.source.native_id, self.actor.native_id)

    def test_human_actor_required(self) -> None:
        """Human provenance without a principal is structurally invalid."""
        with self.assertRaises(ValueError):
            self.event(actor=None)

    def test_actor_platform_mismatch_rejected(self) -> None:
        """Actors cannot cross a transport binding inside an envelope."""
        with self.assertRaises(ValueError):
            self.event(actor=Principal("another", "bot-A", "sender"))

    def test_actor_account_mismatch_rejected(self) -> None:
        """Actors cannot cross the receiving bot-account binding."""
        with self.assertRaises(ValueError):
            self.event(actor=Principal("example", "bot-B", "sender"))

    def test_task_and_system_actor_rejected(self) -> None:
        """Even a principal displayed as owner cannot impersonate a task sender."""
        for origin in ("task", "system"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                self.event(origin=origin, actor=Principal("example", "bot-A", "sender", "owner"))

    def test_task_and_system_without_actor_allowed(self) -> None:
        """Synthetic provenance stays explicit, without fabricated user identity."""
        for origin in ("task", "system"):
            with self.subTest(origin=origin):
                self.assertIsNone(self.event(origin=origin, actor=None).actor)

    def test_invalid_origin_rejected(self) -> None:
        """Origin is a strict enum, not a truthy flag or user-supplied role."""
        for origin in ("owner", "Human", ""):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                self.event(origin=origin)
        with self.assertRaises(TypeError):
            self.event(origin=True)

    def test_envelope_types_and_event_id(self) -> None:
        """Envelope references must be contract objects, never lookalike dicts."""
        for changes, error in ((dict(source={}), TypeError), (dict(actor={}), TypeError),
                               (dict(event_id=1), TypeError), (dict(event_id=""), ValueError),
                               (dict(event_id="evt\n"), ValueError)):
            with self.subTest(changes=changes), self.assertRaises(error):
                self.event(**changes)

    def test_event_is_frozen(self) -> None:
        """The validated envelope cannot change origin after construction."""
        event = self.event()
        with self.assertRaises(FrozenInstanceError):
            event.origin = "system"

    def test_native_segments_and_unknown_content_preserved(self) -> None:
        """Content is not reduced to text or stripped of attachment/quote fields."""
        payload = ' {"segments":[{"type":"image","data":{"file":"opaque-ref","message_id":"NA"}},{"type":"reply","data":{"id":"12:34"}},{"type":"future","data":{"nested":[null,true,7]}}],"text":"原样"} '
        event = self.event(content_json=payload)
        self.assertEqual(event.content_json, payload)
        self.assertEqual(json.loads(event.content_json), json.loads(payload))

    def test_content_cannot_grant_authority(self) -> None:
        """A body claiming owner or another actor never changes envelope identity."""
        event = self.event(content_json='{"actor":"owner","account":"other","role":"owner"}')
        self.assertEqual(event.actor, self.actor)
        self.assertEqual(event.source, self.source)
        self.assertFalse(hasattr(event, "role"))

    def test_invalid_json_and_types_rejected(self) -> None:
        """Bodies are UTF-8 JSON rather than Python repr or non-finite extensions."""
        for payload in ("", "not JSON", "{", "NaN", "[Infinity]", '{"a":-Infinity}'):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.event(content_json=payload)
        with self.assertRaises(TypeError):
            self.event(content_json={})
        with self.assertRaises(ValueError):
            self.event(content_json="\ud800")

    def test_json_scalar_and_null_preserved(self) -> None:
        """The opaque JSON contract does not impose platform-specific body schemas."""
        for payload in ("null", "[]", '"NA"', "42", "true"):
            with self.subTest(payload=payload):
                self.assertEqual(self.event(content_json=payload).content_json, payload)

    def test_core_imports_only_standard_library_and_itself(self) -> None:
        """An AST boundary check prevents a QQ/framework dependency creeping in."""
        root = Path(__file__).resolve().parents[1] / "bot_core"
        allowed = {"dataclasses", "json", "re", "typing", "unicodedata"}
        for name in ("__init__.py", "contracts.py"):
            tree = ast.parse((root / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for imported in node.names:
                        self.assertIn(imported.name.split(".")[0], allowed)
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    self.assertIn(node.module.split(".")[0], allowed)


if __name__ == "__main__":
    unittest.main()
