"""Durable wake-person records; native observations are never asserted model facts.

Only configured person/scope pairs may be ingested automatically. Unknown legacy
provenance remains on disk but is not exposed through scope-filtered tools.
"""

import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import uuid

# Memory's legacy direct-script self-test needs the same implementation too.
if __package__:
    from .message_batch import _plain_text, message_record, message_segments
else:
    from message_batch import _plain_text, message_record, message_segments

# One process-wide lock serializes load/modify/replace across short-lived stores.
_STORE_LOCK = threading.RLock()


def _scope(value) -> str:
    """Alias only numeric group IDs, never private IDs or digit-bearing prose."""
    value = str(value or "").strip()
    return "group:" + value if value.isascii() and value.isdigit() else value


def is_wake_person(config: dict, qq: str, scope: str) -> bool:
    """Require a real numeric QQ and exact configured membership in this namespace."""
    qq, scope = str(qq or "").strip(), _scope(scope)
    if not qq.isascii() or not qq.isdigit() or not scope:
        return False
    table = config.get("wake_persons") if isinstance(config, dict) else None
    if isinstance(table, dict):
        # Numeric keys and group:<id> are equivalent; arbitrary suffixes are not.
        return any(_scope(key) == scope and isinstance(values, list)
                   and qq in [str(item) for item in values] for key, values in table.items())
    # Preserve legacy global-list configuration for groups, not private chats.
    return (scope.startswith("group:") and isinstance(table, list)
            and qq in [str(item) for item in table])


def _validate(data: dict) -> dict:
    """Reject malformed stores instead of silently resetting or repairing their bytes."""
    if (not isinstance(data, dict) or type(data.get("version")) is not int
            or data["version"] not in (1, 2)):
        raise ValueError("wake_person_store_invalid_version")
    persons = data.get("persons")
    if not isinstance(persons, dict):
        raise ValueError("wake_person_store_invalid_persons")
    for qq, rows in persons.items():
        if not isinstance(qq, str) or not isinstance(rows, list):
            raise ValueError("wake_person_store_invalid_bucket")
        ids = set()
        for row in rows:
            if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                    or not row["id"] or row["id"] in ids or not isinstance(row.get("text"), str)):
                raise ValueError("wake_person_store_invalid_record")
            ids.add(row["id"])
            if "kind" in row and row["kind"] not in ("observed_message", "model_fact"):
                raise ValueError("wake_person_store_invalid_kind")
            if "source" in row:
                source = row["source"]
                if (not isinstance(source, dict) or not isinstance(source.get("scope"), str)
                        or not isinstance(source.get("message_ids"), list)
                        or not all(isinstance(mid, str) and mid for mid in source["message_ids"])
                        or not isinstance(source.get("messages"), list)):
                    raise ValueError("wake_person_store_invalid_source")
                for message in source["messages"]:
                    if (not isinstance(message, dict) or not isinstance(message.get("id"), str)
                            or not isinstance(message.get("body"), str)
                            or not isinstance(message.get("sender"), dict)
                            or not isinstance(message["sender"].get("id"), str)):
                        raise ValueError("wake_person_store_invalid_message")
            if row.get("kind") == "observed_message":
                source = row.get("source") or {}
                messages = source.get("messages") or []
                if (not source.get("scope") or not source.get("message_ids") or len(messages) != 1
                        or source["message_ids"] != [messages[0]["id"]]
                        or messages[0]["sender"]["id"] != qq):
                    raise ValueError("wake_person_store_invalid_observation")
    return data


class WakePersonFacts:
    """Keep person-keyed records with atomic persistence and explicit provenance.

    Records are not silently evicted: the old 200-row display limit was not a
    durable retention policy. Existing v1 records survive the first v2 write.
    """

    def __init__(self, path: str) -> None:
        """Bind an isolated store; every mutation reloads it while holding the lock."""
        self.path = str(path)
        self.data = {"version": 2, "persons": {}}

    def load(self) -> "WakePersonFacts":
        """Read authoritative disk state; missing is empty, corrupt/unreadable raises."""
        with _STORE_LOCK:
            try:
                with open(self.path, encoding="utf-8") as handle:
                    loaded = json.load(handle)
            except FileNotFoundError:
                loaded = {"version": 2, "persons": {}}
            self.data = _validate(loaded)
        return self

    def save(self) -> None:
        """Flush a same-directory temporary file before atomic replacement or raise."""
        with _STORE_LOCK:
            _validate(self.data)
            # Even direct save must not overwrite an unreadable/corrupt existing file.
            try:
                with open(self.path, encoding="utf-8") as handle:
                    _validate(json.load(handle))
            except FileNotFoundError:
                pass
            directory = os.path.dirname(self.path) or "."
            os.makedirs(directory, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".wake-person-", suffix=".tmp", dir=directory)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(self.data, handle, ensure_ascii=False, allow_nan=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
            finally:
                # Remove only this operation's abandoned temporary snapshot.
                if os.path.exists(temporary):
                    os.unlink(temporary)

    @staticmethod
    def source(scope: str, record: dict | None = None) -> dict:
        """Detach minimal actual message evidence, excluding quoted/forwarded utterances."""
        source = {"scope": str(scope), "message_ids": [], "messages": []}
        if record is not None:
            sender = record.get("sender") or {}
            native = {"id": str(record.get("id") or ""),
                      "sender": {"id": str(sender.get("id") or ""),
                                 "display_name": str(sender.get("display_name") or "")},
                      "body": str(record.get("body") or "")}
            # Timestamps are evidence only when the native parser supplied them.
            for key in ("native_time", "time_source"):
                if key in record:
                    native[key] = copy.deepcopy(record[key])
            source["messages"] = [native]
            if native["id"]:
                source["message_ids"] = [native["id"]]
        return source

    @staticmethod
    def _visible(row: dict, current_scope: str | None) -> bool:
        """Scope-filter both groups and private namespaces, hiding unknown legacy origin."""
        if current_scope is None:
            return True
        source = row.get("source") or {}
        return isinstance(source, dict) and bool(source.get("scope")) and _scope(source["scope"]) == _scope(current_scope)

    def add(self, qq: str, text: str, *, source: dict | None = None,
            kind: str = "model_fact") -> str:
        """Persist a model fact or observed utterance with scope/source-id idempotence."""
        qq, clean = str(qq or "").strip(), str(text or "").strip()
        if not qq.isascii() or not qq.isdigit() or not clean:
            return ""
        if kind not in ("model_fact", "observed_message"):
            raise ValueError("wake_person_invalid_kind")
        if source is not None and not isinstance(source, dict):
            raise ValueError("wake_person_invalid_source")
        provenance = copy.deepcopy(source) if source is not None else None
        if kind == "observed_message" and (not provenance or not provenance.get("scope")
                                           or not provenance.get("message_ids")):
            raise ValueError("wake_person_observation_requires_native_id")
        with _STORE_LOCK:
            self.load()
            bucket = self.data["persons"].get(qq, [])
            for row in bucket:
                old = row.get("source") or {}
                same_scope = _scope(old.get("scope")) == _scope((provenance or {}).get("scope"))
                if not same_scope or row.get("kind", "model_fact") != kind:
                    continue
                if kind == "observed_message":
                    # Retransmits retain the first actual body and do not rewrite bytes.
                    duplicate = old.get("message_ids") == provenance["message_ids"]
                else:
                    duplicate = row["text"] == clean
                if duplicate:
                    return row["id"]
            fid = "w" + uuid.uuid4().hex
            row = {"id": fid, "text": clean, "at": int(time.time()), "kind": kind}
            if provenance is not None:
                row["source"] = provenance
            previous = copy.deepcopy(self.data)
            self.data["version"] = 2
            self.data["persons"].setdefault(qq, []).append(row)
            try:
                self.save()
            except Exception:
                # Failed persistence must not leave a fake in-memory success receipt.
                self.data = previous
                raise
            return fid

    def observe(self, qq: str, record: dict, *, scope: str, config: dict) -> str:
        """Persist only this configured person's actual native message, not a quote."""
        sender = record.get("sender") or {}
        if str(sender.get("id") or "") != str(qq) or not is_wake_person(config, qq, scope):
            return ""
        if not record.get("id") or not str(record.get("body") or "").strip():
            # No synthetic ID/body: image-only metadata stays in normal history.
            return ""
        return self.add(qq, record["body"], source=self.source(scope, record), kind="observed_message")

    def list(self, qq: str, *, current_scope: str | None = None) -> list:
        """Return detached visible records without creating person buckets on reads."""
        return [copy.deepcopy(row) for row in self.data["persons"].get(str(qq), [])
                if self._visible(row, current_scope)]

    def search(self, qq: str, query: str, *, current_scope: str | None = None) -> list:
        """Search only records readable in the actual current namespace."""
        query = str(query or "").strip()
        return [row for row in self.list(qq, current_scope=current_scope) if not query or query in row["text"]]

    def delete(self, qq: str, fid: str = "", keyword: str = "", *,
               current_scope: str | None = None) -> int:
        """Delete matching visible records atomically; repeated deletion is a no-op."""
        with _STORE_LOCK:
            self.load()
            bucket = self.data["persons"].get(str(qq), [])
            keep = [row for row in bucket if not (self._visible(row, current_scope)
                    and ((fid and row["id"] == fid) or (keyword and keyword in row["text"])))]
            removed = len(bucket) - len(keep)
            if removed:
                previous = copy.deepcopy(self.data)
                self.data["persons"][str(qq)] = keep
                try:
                    self.save()
                except Exception:
                    self.data = previous
                    raise
            return removed

    def count(self, qq: str, *, current_scope: str | None = None) -> int:
        """Count only records visible in the requested namespace."""
        return len(self.list(qq, current_scope=current_scope))


def native_record(event) -> dict:
    """Use only actual top-level Plain text; expanded forwards/quotes are not speech."""
    segments = message_segments(event)
    # The caller's text may already include forward expansion or component JSON.
    # Canonical Plain segments win; a minimal text-only adapter may have no stream.
    body = _plain_text(segments) if segments else str(event.get_message_str() or "")
    record = message_record(event, body)
    record["body"] = body.strip()
    return record


def dispatch_person_facts(name: str, args: dict, *, root: str, current_scope: str,
                          event=None, can_write: bool = False) -> dict:
    """Apply actual membership/privacy gates to existing person-fact tools in one place."""
    qq = str(args.get("qq") or "").strip()
    if name in ("wp_fact_add", "wp_fact_delete", "wp_fact_move") and not can_write:
        return {"ok": False, "error": "wake_person_write_not_authorized"}
    with open(Path(root) / "groups.json", encoding="utf-8") as handle:
        config = json.load(handle)
    if not is_wake_person(config, qq, current_scope):
        return {"ok": False, "error": "wake_person_not_in_current_scope"}
    store = WakePersonFacts(str(Path(root) / "memory" / "wake_persons.json")).load()
    if name == "wp_fact_add":
        if event is None:
            return {"ok": False, "error": "wake_person_write_not_authorized"}
        fid = store.add(qq, str(args.get("text") or ""),
                        source=store.source(current_scope, native_record(event)))
        return {"ok": bool(fid), "id": fid, "persisted": bool(fid),
                "kind": "model_fact", "count": store.count(qq, current_scope=current_scope)}
    if name == "wp_fact_list":
        return {"ok": True, "qq": qq, "facts": store.list(qq, current_scope=current_scope)}
    if name == "wp_fact_search":
        return {"ok": True, "qq": qq, "hits": store.search(
            qq, str(args.get("query") or ""), current_scope=current_scope)}
    if name == "wp_fact_delete":
        return {"ok": True, "deleted": store.delete(qq, str(args.get("id") or ""),
                str(args.get("keyword") or ""), current_scope=current_scope), "persisted": True}
    if name == "wake_person_info":
        rows = store.list(qq, current_scope=current_scope)
        conf = (config.get("wake_person_conf") or {}).get(qq) or {}
        wake_at = config.get("wake_at") or {}
        # Scope-local read tools do not disclose other group memberships or timers.
        timers = {str(key): value for key, value in wake_at.items()
                  if _scope(key) == _scope(current_scope)}
        return {"ok": True, "qq": qq, "is_wake_person_in": [str(current_scope)],
                "conf": {"pct": conf.get("pct", 100), "words": conf.get("words") or []},
                "wake_at": timers, "facts_count": len(rows), "facts": rows}
    if name == "wp_fact_move":
        # Import locally: memory re-exports this store for legacy callers.
        from .memory import Memory
        memory = Memory(str(Path(root) / "memory" / "memory.json")).load()
        fid = str(args.get("id") or "").strip()
        found = next((row for row in memory.list_facts(None, current_scope=current_scope)
                      if str(row.get("id")) == fid), None)
        if found is None:
            return {"ok": False, "error": "fact_not_found"}
        if found.get("images"):
            return {"ok": False, "error": "destination_does_not_support_images"}
        from .fact_structure import normalize_structure
        if found.get("structure") != normalize_structure() or found.get("categories"):
            return {"ok": False, "error": "destination_does_not_support_fact_metadata"}
        # Memory's tool projection calls the native source object provenance;
        # its legacy source field is only a scope string, not message evidence.
        source = found.get("provenance")
        source_scope = (source.get("scope") if isinstance(source, dict) else None) or found.get("source_scope")
        if not source_scope or _scope(source_scope) != _scope(current_scope):
            return {"ok": False, "error": "wake_person_source_scope_mismatch"}
        provenance = copy.deepcopy(source) if isinstance(source, dict) else store.source(source_scope)
        provenance.setdefault("message_ids", [])
        provenance.setdefault("messages", [])
        new_id = store.add(qq, found["text"], source=provenance)
        if not new_id:
            return {"ok": False, "error": "wake_person_empty_fact"}
        # Never remove the source until the destination has a durable success.
        memory.delete_fact(fid, current_scope=current_scope, checked=True)
        return {"ok": True, "moved": fid, "to_person": qq, "new_id": new_id,
                "persisted": True, "count": store.count(qq, current_scope=current_scope)}
    raise ValueError("wake_person_unknown_tool")


def observe_wake_person_message(root: str, scope: str, event, text: str) -> str:
    """Use native intake evidence before reply probability; never infer person identity."""
    with open(Path(root) / "groups.json", encoding="utf-8") as handle:
        config = json.load(handle)
    record = native_record(event)
    qq = str((record.get("sender") or {}).get("id") or "")
    if not is_wake_person(config, qq, scope):
        return ""
    return WakePersonFacts(str(Path(root) / "memory" / "wake_persons.json")).load().observe(
        qq, record, scope=scope, config=config)
