"""Offline dispatcher contract tests; no live or native backend is exercised."""

import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from qq_transport import ActionDispatcher, Handler
from qq_transport.store import OperationJournal, copy_json


class MemoryJournal:
    """Test double for the separately implemented durable journal contract."""

    def __init__(self):
        self.records = {}

    def claim(self, account, operation_id, action, parameters):
        """Detach persisted test state and recognize exact duplicate operations."""
        key = (account, operation_id)
        fingerprint = copy_json([action, parameters])
        if key in self.records:
            record = self.records[key]
            return {"claimed": False, "conflict": record["fingerprint"] != fingerprint,
                    "state": record["state"], "response": copy_json(record["response"])}
        self.records[key] = {"fingerprint": fingerprint, "state": "pending", "response": None}
        return {"claimed": True, "conflict": False, "state": "pending", "response": None}

    def complete(self, account, operation_id, response):
        """Retain a detached terminal result for replay assertions."""
        self.records[(account, operation_id)].update(state="complete", response=copy_json(response))


class ActionTests(unittest.IsolatedAsyncioTestCase):
    """Validate strict routing, genuine acknowledgements, and safe mutation replay."""

    async def asyncSetUp(self):
        """Build a fresh offline handler and operation journal for every test."""
        self.calls = []
        self.journal = MemoryJournal()

        async def native(account, parameters):
            """Record exactly the caller-supplied account and detached parameters."""
            self.calls.append((account, copy_json(parameters)))
            return {"status": "ok", "retcode": 0, "data": {"message_id": 17},
                    "secret_diagnostic": "must not be forwarded"}

        self.dispatcher = ActionDispatcher({"send_msg": Handler(native, mutating=True),
                                            "get.data": Handler(native)}, self.journal)

    async def test_native_ack_and_echo(self):
        """Native success means accepted, not delivery, with copied JSON echo."""
        echo = {"id": [1]}
        result = await self.dispatcher.dispatch("123", "send_msg", {}, operation_id="op", echo=echo)
        self.assertEqual(result, {"status": "ok", "retcode": 0, "data": {"message_id": 17},
                                  "outcome": "accepted", "echo": echo})
        echo["id"].append(2)
        self.assertEqual(result["echo"], {"id": [1]})
        self.assertNotIn("echo", self.journal.records[("123", "op")]["response"])

    async def test_replay_conflict_and_account_isolation(self):
        """A replay ignores echo, conflicts never execute, and account scopes differ."""
        first = await self.dispatcher.dispatch("123", "send_msg", {"x": 1}, operation_id="op")
        first["data"]["message_id"] = 999
        repeat = await self.dispatcher.dispatch("123", "send_msg", {"x": 1}, operation_id="op", echo=2)
        conflict = await self.dispatcher.dispatch("123", "send_msg", {"x": 2}, operation_id="op")
        await self.dispatcher.dispatch("124", "send_msg", {"x": 1}, operation_id="op")
        self.assertEqual(repeat["data"], {"message_id": 17})
        self.assertEqual(repeat["echo"], 2)
        self.assertEqual(conflict["retcode"], 1409)
        self.assertEqual(len(self.calls), 2)

    async def test_pending_is_unknown_without_replay(self):
        """An unfinished durable claim cannot be mistaken for permission to retry."""
        self.journal.claim("123", "op", "send_msg", {})
        result = await self.dispatcher.dispatch("123", "send_msg", {}, operation_id="op")
        self.assertEqual((result["retcode"], result["outcome"]), (1504, "unknown"))
        self.assertEqual(self.calls, [])

    async def test_required_operation_id_and_no_alias_fallback(self):
        """Mutation IDs are mandatory and async aliases are never manufactured."""
        result = await self.dispatcher.dispatch("123", "send_msg", {})
        alias = await self.dispatcher.dispatch("123", "send_msg_async", {}, operation_id="op")
        self.assertEqual(result["error"], "operation_id_required")
        self.assertEqual(alias["retcode"], 1404)
        self.assertEqual(self.calls, [])

    async def test_strict_requests(self):
        """Reject coercions, non-JSON input, invalid Unicode, and malformed identifiers."""
        invalid = [(True, "get.data", {}), ("0000", "get.data", {}),
                   ("１２３", "get.data", {}), ("123\n", "get.data", {}),
                   ("123", "x/y", {}), ("123", "a" * 257, {}),
                   ("123", "get.data", []), ("123", "get.data", {1: "x"}),
                   ("123", "get.data", {"x": float("nan")}),
                   ("123", "get.data", {"x": "\ud800"})]
        for account, action, parameters in invalid:
            with self.subTest(account=account, action=action, parameters=parameters):
                result = await self.dispatcher.dispatch(account, action, parameters)
                self.assertEqual(result["retcode"], 1400)
        result = await self.dispatcher.dispatch("123", "get.data", {}, echo=float("inf"))
        self.assertEqual(result["retcode"], 1400)
        self.assertEqual(self.calls, [])

    async def test_explicit_native_failure_and_null_data(self):
        """Preserve native failure/data without treating null successful data as invalid."""
        for native in [{"status": "failed", "retcode": 12, "data": [1, None]},
                       {"status": "ok", "retcode": 0, "data": None}]:
            async def callback(account, params):
                """Return the current native acknowledgement unchanged."""
                return native
            dispatcher = ActionDispatcher({"x": Handler(callback)}, self.journal)
            result = await dispatcher.dispatch("123", "x", {})
            self.assertEqual(result["data"], native["data"])
            self.assertEqual(result["retcode"], native["retcode"])

    async def test_malformed_native_response_never_success(self):
        """Malformed acknowledgements are terminal unknown mutations, not false success."""
        for index, native in enumerate([{}, {"status": "ok", "retcode": False, "data": {}},
                {"status": "ok", "retcode": 1, "data": {}},
                {"status": "failed", "retcode": 0, "data": {}},
                {"status": "async", "retcode": 0, "data": {}},
                {"status": "ok", "retcode": 0, "data": float("inf")} ]):
            async def callback(account, params):
                """Supply an invalid native acknowledgement for validation."""
                return native
            dispatcher = ActionDispatcher({"x": Handler(callback, True)}, self.journal)
            result = await dispatcher.dispatch("123", "x", {}, operation_id=str(index))
            self.assertEqual((result["status"], result["outcome"]), ("failed", "unknown"))

    async def test_exception_unknown_is_persisted_and_not_retried(self):
        """Runtime failures do not leak exception text or automatically repeat mutations."""
        calls = []
        async def callback(account, params):
            """Model a native timeout after an unobservable possible side effect."""
            calls.append(1)
            raise TimeoutError("secret credential")
        dispatcher = ActionDispatcher({"x": Handler(callback, True)}, self.journal)
        first = await dispatcher.dispatch("123", "x", {}, operation_id="op")
        repeat = await dispatcher.dispatch("123", "x", {}, operation_id="op")
        self.assertEqual(first, repeat)
        self.assertEqual(first["outcome"], "unknown")
        self.assertNotIn("secret", str(first))
        self.assertEqual(calls, [1])

    async def test_cancel_persists_unknown_and_propagates(self):
        """Cancellation remains observable while its mutation claim cannot be replayed."""
        started = asyncio.Event()
        async def callback(account, params):
            """Wait until cancelled after callback entry."""
            started.set()
            await asyncio.Event().wait()
        dispatcher = ActionDispatcher({"x": Handler(callback, True)}, self.journal)
        task = asyncio.create_task(dispatcher.dispatch("123", "x", {}, operation_id="op"))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        result = await dispatcher.dispatch("123", "x", {}, operation_id="op")
        self.assertEqual(result["outcome"], "unknown")
        self.assertEqual(self.journal.records[("123", "op")]["state"], "complete")

    async def test_parameters_detached_before_callback_await(self):
        """Caller and handler mutations cannot change the operation fingerprint."""
        started, release = asyncio.Event(), asyncio.Event()
        async def callback(account, params):
            """Mutate the detached callback input after the durable claim."""
            started.set()
            await release.wait()
            self.assertEqual(params, {"items": [1]})
            params["items"].append(3)
            return {"status": "ok", "retcode": 0, "data": None}
        dispatcher = ActionDispatcher({"x": Handler(callback, True)}, self.journal)
        params = {"items": [1]}
        task = asyncio.create_task(dispatcher.dispatch("123", "x", params, operation_id="op"))
        await started.wait()
        params["items"].append(2)
        release.set()
        await task
        result = await dispatcher.dispatch("123", "x", {"items": [1]}, operation_id="op")
        self.assertEqual(result["outcome"], "accepted")

    async def test_journal_errors_fail_closed(self):
        """Unavailable durable claims prevent callbacks; completion failures stay unknown."""
        class BrokenJournal(MemoryJournal):
            """Inject storage failures without changing dispatcher behavior."""
            def claim(self, *args):
                """Reject claims before a native callback can run."""
                raise OSError("private path")
        dispatcher = ActionDispatcher(self.dispatcher._handlers, BrokenJournal())
        result = await dispatcher.dispatch("123", "send_msg", {}, operation_id="op")
        self.assertEqual(result["outcome"], "unknown")
        self.assertEqual(self.calls, [])

    async def test_durable_restart_replay_and_unknown(self):
        """Actual SQLite journal prevents mutation replay across dispatcher restarts."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "operations.sqlite3"
            dispatcher = ActionDispatcher(self.dispatcher._handlers, OperationJournal(path))
            accepted = await dispatcher.dispatch("123", "send_msg", {"x": 1}, operation_id="op")
            restarted = ActionDispatcher(self.dispatcher._handlers, OperationJournal(path))
            replay = await restarted.dispatch("123", "send_msg", {"x": 1}, operation_id="op")
            self.assertEqual(accepted, replay)
            self.assertEqual(len(self.calls), 1)
            OperationJournal(path).claim("123", "pending", "send_msg", {})
            pending = await restarted.dispatch("123", "send_msg", {}, operation_id="pending")
            self.assertEqual(pending["outcome"], "unknown")
            self.assertEqual(len(self.calls), 1)

    async def test_concurrent_duplicate_never_executes_twice(self):
        """A duplicate arriving while the owner awaits observes pending, not replay."""
        started, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def callback(account, params):
            """Hold the first callback open so a duplicate can observe its claim."""
            calls.append(1)
            started.set()
            await release.wait()
            return {"status": "ok", "retcode": 0, "data": None}
        dispatcher = ActionDispatcher({"x": Handler(callback, True)}, self.journal)
        owner = asyncio.create_task(dispatcher.dispatch("123", "x", {}, operation_id="op"))
        await started.wait()
        duplicate = await dispatcher.dispatch("123", "x", {}, operation_id="op")
        release.set()
        accepted = await owner
        self.assertEqual(duplicate["outcome"], "unknown")
        self.assertEqual(accepted["outcome"], "accepted")
        self.assertEqual(calls, [1])

    async def test_completion_failure_leaves_pending(self):
        """Even a native acknowledgement is not reported accepted if journaling fails."""
        class BrokenCompletion(MemoryJournal):
            """Keep durable pending state when terminal persistence is unavailable."""
            def complete(self, *args):
                """Model disk failure after a native callback acknowledgement."""
                raise OSError("private path")
        journal = BrokenCompletion()
        dispatcher = ActionDispatcher(self.dispatcher._handlers, journal)
        result = await dispatcher.dispatch("123", "send_msg", {}, operation_id="op")
        repeat = await dispatcher.dispatch("123", "send_msg", {}, operation_id="op")
        self.assertEqual(result["outcome"], "unknown")
        self.assertEqual(repeat["outcome"], "unknown")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(journal.records[("123", "op")]["state"], "pending")

    async def test_leading_zero_account_is_not_normalized(self):
        """Positive ASCII account spelling is preserved rather than silently rewritten."""
        await self.dispatcher.dispatch("00123", "get.data", {})
        self.assertEqual(self.calls[0][0], "00123")

    async def test_full_inventory_names_can_be_explicitly_registered(self):
        """Grammar covers inventoried names without claiming their native implementation."""
        inventory = json.loads((Path(__file__).resolve().parents[1] / "docs" /
                                "qq-installed-action-inventory.json").read_text(encoding="utf-8"))
        names = {item["name"] for item in inventory["actions"]}
        names.update(inventory["generated_aliases"]["names"])
        self.assertEqual(len(names), 540)
        calls = []
        async def callback(account, params):
            """Return an injected offline acknowledgement, not a real backend result."""
            calls.append(account)
            return {"status": "ok", "retcode": 0, "data": None}
        dispatcher = ActionDispatcher({name: Handler(callback) for name in names}, self.journal)
        self.assertEqual(calls, [])
        for name in names:
            result = await dispatcher.dispatch("123", name, {})
            self.assertEqual(result["retcode"], 0, name)
        self.assertEqual(len(calls), 540)
        for invalid in (".", "..", "bad/name", "a\n"):
            with self.assertRaises(ValueError):
                ActionDispatcher({invalid: Handler(callback)}, self.journal)

    async def test_empty_registry_has_no_default_backend(self):
        """No metadata action or supposed native capability exists implicitly."""
        dispatcher = ActionDispatcher({}, self.journal)
        result = await dispatcher.dispatch("123", "get_version_info", {})
        self.assertEqual(result["retcode"], 1404)
