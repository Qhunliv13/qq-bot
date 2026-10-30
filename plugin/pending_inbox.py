"""Durable native QQ admission, never a retry queue for started work.

Instantiate once at startup with ``recover_started=True`` to demote abandoned
``running`` rows to ``uncertain``; callers must claim a complete batch before work.
SQLite owns cross-process locking and FULL-synchronous commits; a per-path RLock
also serializes threads. Stored source metadata is input evidence, not authority.
No history, raw platform payload, response capability or role is persisted.
"""

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit

from .message_batch import _data, _field, _kind, message_record, message_segments
from .runtime_paths import data_root
from .task_event import TaskEvent


# Bounds reject whole inputs, never silently shorten a human message or metadata.
MAX_PAYLOAD_BYTES = 2 * 1024 * 1024
# A fixed schema identity prevents adopting another application's valid SQLite DB.
_APPLICATION_ID = 0x4847494E
_SCHEMA_VERSION = 1
_STATUSES = frozenset({"pending", "running", "uncertain", "completed", "failed", "blocked"})
_TERMINAL = frozenset({"completed", "failed", "blocked"})
_LOCKS = {}
_LOCKS_GUARD = threading.RLock()
# Only explicit current top-level attachment fields are durable source evidence.
_ATTACHMENT_KINDS = frozenset({"image", "file", "audio", "record", "video", "mface", "face"})
_ATTACHMENT_FIELDS = ("id", "name", "file_name", "size", "duration", "summary", "sub_type",
                      "emoji_id", "emoji_package_id")
_REFERENCE_FIELDS = ("url", "file", "path", "imageURL", "image_url")
_PAYLOAD_KEYS = frozenset({"scope", "origin", "platform", "self_id", "message_record",
                           "text", "has_image", "woken", "attachments"})


class InboxError(RuntimeError):
    """Report a closed storage boundary without leaking a message or SQLite path."""


class _Unsupported(ValueError):
    """Carry a fixed reason code for a non-replayable incoming event."""


def _lock_for(path):
    """Share one process reentrant lock for all handles to the resolved DB path."""
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(str(path), threading.RLock())


def _id(value, *, native=False, empty=False):
    """Validate QQ IDs as ASCII numbers; signed native message IDs are legitimate."""
    if value == "" and empty:
        return ""
    if type(value) not in (str, int):
        raise _Unsupported("invalid_identity")
    value = str(value)
    pattern = r"-?[0-9]+" if native else r"[0-9]+"
    if len(value) > 40 or re.fullmatch(pattern, value) is None or not int(value):
        raise _Unsupported("invalid_identity")
    return value


def _getter(event, name):
    """Require actual source getters rather than accepting supplied identity hints."""
    getter = getattr(event, name, None)
    if not callable(getter):
        raise _Unsupported("source_getter_missing")
    try:
        return getter()
    except Exception as exc:
        raise _Unsupported("source_getter_failed") from exc


def _binding(scope, origin, platform, self_id, sender_id, group_id):
    """Bind exact origin, native account and real speaker to one native destination."""
    if not isinstance(scope, str) or not isinstance(origin, str) or not isinstance(platform, str):
        raise _Unsupported("invalid_route")
    if re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", platform) is None:
        raise _Unsupported("invalid_platform")
    if platform.lower() in {"local", "webchat"}:
        raise _Unsupported("transient_platform")
    _id(self_id)
    _id(sender_id)
    private = scope.startswith("private:")
    destination = _id(scope[8:] if private else scope)
    expected = "FriendMessage" if private else "GroupMessage"
    if origin != f"{platform}:{expected}:{destination}":
        raise _Unsupported("origin_scope_mismatch")
    if (private and (group_id or destination != sender_id)) or (not private and group_id != destination):
        raise _Unsupported("sender_scope_mismatch")


def _identity(value, *, optional=False):
    """Permit identity/display metadata only; stored role-like fields never survive."""
    if not isinstance(value, dict) or set(value) != {"id", "display_name"}:
        raise _Unsupported("invalid_record")
    _id(value["id"], empty=optional)
    if not isinstance(value["display_name"], str):
        raise _Unsupported("invalid_record")


def _record(value):
    """Validate the shared message_record shape without introducing a second parser."""
    required = {"id", "sender", "body", "mentions", "quote"}
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - {"native_time", "time_source", "forward_ids"}:
        raise _Unsupported("invalid_record")
    _id(value["id"], native=True)
    _identity(value["sender"])
    if not isinstance(value["body"], str) or not isinstance(value["mentions"], list):
        raise _Unsupported("invalid_record")
    for mention in value["mentions"]:
        # Native @all is attribution metadata, not an invented numeric identity.
        if isinstance(mention, dict) and mention.get("id") == "all":
            if set(mention) != {"id", "display_name"} or not isinstance(mention["display_name"], str):
                raise _Unsupported("invalid_record")
        else:
            _identity(mention)
    quote = value["quote"]
    if quote is not None:
        if not isinstance(quote, dict) or set(quote) != {"id", "sender", "body"} or not isinstance(quote["body"], str):
            raise _Unsupported("invalid_record")
        _id(quote["id"], native=True, empty=True)
        _identity(quote["sender"], optional=True)
    if "native_time" in value:
        if type(value["native_time"]) not in (int, float) or (type(value["native_time"]) is float and not math.isfinite(value["native_time"])):
            raise _Unsupported("invalid_record")
        if value.get("time_source") not in {"onebot.time", "message_obj.timestamp", "message_obj.time"}:
            raise _Unsupported("invalid_record")
    elif "time_source" in value:
        raise _Unsupported("invalid_record")
    if "forward_ids" in value:
        if not isinstance(value["forward_ids"], list):
            raise _Unsupported("invalid_record")
        for ident in value["forward_ids"]:
            # Forward resource IDs are opaque native IDs, not QQ account numbers.
            if not ((type(ident) is str and ident) or type(ident) is int):
                raise _Unsupported("invalid_record")


def _reference(value):
    """Keep non-secret source locations, never credential-bearing URL capabilities.

    URL query strings/fragments and userinfo are deliberately not durable. Their
    absence is explicit source metadata, not a promise that media can be fetched.
    Inline payloads are not metadata and are marked unavailable, not persisted.
    """
    if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
        raise _Unsupported("invalid_attachment")
    if value.lower().startswith(("data:", "base64:")):
        return {"unavailable": "inline_payload_not_persisted"}
    try:
        parsed = urlsplit(value)
        # Userinfo is secret-bearing for file/protocol-relative URLs as well.
        if parsed.username or parsed.password:
            return {"unavailable": "credential_url_not_persisted"}
        if parsed.scheme.lower() in {"http", "https"}:
            if not parsed.hostname:
                return {"unavailable": "credential_url_not_persisted"}
            if parsed.query or parsed.fragment:
                return {"location": urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")),
                        "unavailable": "url_capability_not_persisted"}
            return {"location": value}
    except ValueError as exc:
        raise _Unsupported("invalid_attachment") from exc
    if parsed.scheme.lower() == "file" or not parsed.scheme or re.match(r"^[A-Za-z]:[\\/]", value):
        return {"location": value}
    return {"unavailable": "unsupported_reference"}


def _attachments(event, record):
    """Project safe top-level source metadata only, excluding quoted/raw payloads."""
    attachments = []
    for segment in message_segments(event):
        kind = _kind(segment)
        if kind not in _ATTACHMENT_KINDS:
            continue
        data = _data(segment)
        item = {"type": kind, "source": {"message_id": record["id"],
                "sender": dict(record["sender"]), "relation": "current"}, "metadata": {}, "references": {}}
        for key in _ATTACHMENT_FIELDS:
            value = _field(data, key)
            if value is not None:
                if type(value) not in (str, int, float, bool) or (type(value) is float and not math.isfinite(value)):
                    raise _Unsupported("invalid_attachment")
                item["metadata"][key] = value
        for key in _REFERENCE_FIELDS:
            value = _field(data, key)
            if value:
                item["references"][key] = _reference(value)
        attachments.append(item)
    return attachments


def _no_url_capabilities(value):
    """Reject secret-bearing prose URLs rather than silently rewriting exact input.

    Attachments already project unavailable/sanitized references. Original text,
    quote prose and names must stay exact, so a URL userinfo/query/fragment there
    makes the entire input explicitly unsupported instead of leaking credentials.
    """
    if isinstance(value, str):
        for match in re.finditer(r"https?://[^\s<>\"']+", value, flags=re.IGNORECASE):
            try:
                parsed = urlsplit(match.group())
                capability = parsed.username or parsed.password or parsed.query or parsed.fragment
            except ValueError as exc:
                raise _Unsupported("invalid_url") from exc
            if capability:
                raise _Unsupported("secret_url_not_persisted")
    elif isinstance(value, dict):
        for child in value.values():
            _no_url_capabilities(child)
    elif isinstance(value, list):
        for child in value:
            _no_url_capabilities(child)


def _validate(payload):
    """Revalidate every read/restore; the file cannot smuggle a new scope or role."""
    if not isinstance(payload, dict) or set(payload) != _PAYLOAD_KEYS:
        raise _Unsupported("invalid_payload")
    record = payload["message_record"]
    _record(record)
    _no_url_capabilities(payload["text"])
    _no_url_capabilities(record)
    scope = payload["scope"]
    group = "" if isinstance(scope, str) and scope.startswith("private:") else scope
    _binding(scope, payload["origin"], payload["platform"], payload["self_id"], record["sender"]["id"], group)
    if not isinstance(payload["text"], str) or type(payload["has_image"]) is not bool or type(payload["woken"]) is not bool:
        raise _Unsupported("invalid_payload")
    if not isinstance(payload["attachments"], list):
        raise _Unsupported("invalid_attachment")
    for attachment in payload["attachments"]:
        if not isinstance(attachment, dict) or set(attachment) != {"type", "source", "metadata", "references"}:
            raise _Unsupported("invalid_attachment")
        source = attachment["source"]
        expected = {"message_id": record["id"], "sender": record["sender"], "relation": "current"}
        if attachment["type"] not in _ATTACHMENT_KINDS or source != expected:
            raise _Unsupported("invalid_attachment")
        metadata, refs = attachment["metadata"], attachment["references"]
        if not isinstance(metadata, dict) or set(metadata) - set(_ATTACHMENT_FIELDS) or not isinstance(refs, dict) or set(refs) - set(_REFERENCE_FIELDS):
            raise _Unsupported("invalid_attachment")
        for value in metadata.values():
            if type(value) not in (str, int, float, bool) or (type(value) is float and not math.isfinite(value)):
                raise _Unsupported("invalid_attachment")
            _no_url_capabilities(value)
        for ref in refs.values():
            if not isinstance(ref, dict) or not ref or set(ref) - {"location", "unavailable"}:
                raise _Unsupported("invalid_attachment")
            if "unavailable" in ref and ref["unavailable"] not in {"inline_payload_not_persisted", "credential_url_not_persisted", "url_capability_not_persisted", "unsupported_reference"}:
                raise _Unsupported("invalid_attachment")
            if "location" in ref and _reference(ref["location"]) != {"location": ref["location"]}:
                raise _Unsupported("invalid_attachment")
    return payload


def _encode(payload):
    """Bound serialized bytes exactly, preserving Unicode and rejecting non-JSON data."""
    try:
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        size = len(encoded.encode("utf-8"))
    except (ValueError, TypeError, OverflowError, RecursionError, UnicodeError) as exc:
        raise _Unsupported("invalid_payload") from exc
    if size > MAX_PAYLOAD_BYTES:
        raise _Unsupported("payload_too_large")
    return encoded


def _snapshot(scope, event, text, has_image, woken):
    """Build the one canonical admission snapshot, rejecting unverifiable sources."""
    if getattr(event, "restored_input", False):
        raise _Unsupported("restored_input_not_admissible")
    adapter = _getter(event, "get_platform_name")
    if adapter != "aiocqhttp":
        raise _Unsupported("unsupported_platform")
    origin = getattr(event, "unified_msg_origin", None)
    if not isinstance(origin, str):
        raise _Unsupported("invalid_route")
    platform = origin.split(":", 1)[0]
    platform_getter = getattr(event, "get_platform_id", None)
    if callable(platform_getter) and _getter(event, "get_platform_id") != platform:
        raise _Unsupported("platform_mismatch")
    sender_id = _id(_getter(event, "get_sender_id"))
    self_id = _id(_getter(event, "get_self_id"))
    group_id = _getter(event, "get_group_id")
    group_id = "" if group_id in (None, "") else _id(group_id)
    _binding(scope, origin, platform, self_id, sender_id, group_id)
    if not isinstance(text, str) or type(has_image) is not bool or type(woken) is not bool:
        raise _Unsupported("invalid_payload")
    record = message_record(event, text)
    if record["sender"]["id"] != sender_id:
        raise _Unsupported("sender_mismatch")
    payload = _validate({"scope": scope, "origin": origin, "platform": platform,
                         "self_id": self_id, "message_record": record, "text": text,
                         "has_image": has_image, "woken": woken,
                         "attachments": _attachments(event, record)})
    _encode(payload)
    return payload


def snapshot_event(scope, event, text, has_image, woken):
    """Expose validated native binding to orchestration without any storage write.

    ``None`` explicitly means this input is not a supported replayable native
    source. Enqueue returns the detailed safe receipt; this projection exists only
    to match restored pending work to a currently observed native account.
    """
    try:
        return _snapshot(scope, event, text, has_image, woken)
    except _Unsupported:
        return None


class PendingInbox:
    """Own durable admission/claim transitions; never run a model, tool or send."""

    def __init__(self, path=None, *, recover_started=False):
        self.path = Path(path) if path is not None else data_root() / "pending_inbox.sqlite3"
        self.path = self.path.resolve()
        self._lock = _lock_for(self.path)
        if type(recover_started) is not bool:
            raise ValueError("pending_inbox_invalid_recovery_flag")
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                created = False
                try:
                    # Exclusive creation distinguishes new state from a truncated DB.
                    descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                except FileExistsError:
                    # Existing empty/foreign/corrupt state must never be adopted.
                    pass
                else:
                    os.close(descriptor)
                    created = True
                with self._transaction(initialize=True, allow_empty=created) as db:
                    # Explicit startup recovery must not interrupt another live handle.
                    if recover_started:
                        db.execute("UPDATE pending_inbox SET status='uncertain' WHERE status='running'")
                if created and os.name == "posix":
                    # SQLite fsyncs content; persist the new directory entry as well.
                    directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
        except (OSError, sqlite3.Error, _Unsupported) as exc:
            raise InboxError("pending_inbox_unavailable") from exc

    @contextmanager
    def _transaction(self, *, initialize=False, allow_empty=False):
        """Serialize writes across threads/processes with SQLite's durable journal."""
        with self._lock:
            db = None
            try:
                # mode=rw never recreates a disappeared/truncated store implicitly.
                db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=10, isolation_level=None)
                db.row_factory = sqlite3.Row
                # EXTRA includes FULL's fsync guarantees plus journal-directory sync.
                db.execute("PRAGMA synchronous=EXTRA")
                if db.execute("PRAGMA journal_mode").fetchone()[0] not in {"delete", "truncate", "persist", "wal"}:
                    raise InboxError("pending_inbox_unsafe_journal")
                db.execute("PRAGMA busy_timeout=10000")
                db.execute("BEGIN IMMEDIATE")
                if initialize:
                    self._initialize(db, allow_empty=allow_empty)
                yield db
                db.commit()
            except (sqlite3.Error, OSError, _Unsupported, ValueError, TypeError) as exc:
                if db is not None and db.in_transaction:
                    db.rollback()
                raise InboxError("pending_inbox_unavailable") from exc
            finally:
                if db is not None:
                    db.close()

    @staticmethod
    def _initialize(db, *, allow_empty=False):
        """Create only a truly new DB; reject corrupt, foreign or unknown schemas."""
        application = db.execute("PRAGMA application_id").fetchone()[0]
        version = db.execute("PRAGMA user_version").fetchone()[0]
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if allow_empty and application == 0 and version == 0 and not tables:
            db.execute("""CREATE TABLE pending_inbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL, origin TEXT NOT NULL, platform TEXT NOT NULL,
                self_id TEXT NOT NULL, native_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending','running','uncertain','completed','failed','blocked')),
                payload TEXT NOT NULL, digest TEXT NOT NULL,
                UNIQUE(scope,origin,self_id,native_id))""")
            db.execute(f"PRAGMA application_id={_APPLICATION_ID}")
            db.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
        elif application != _APPLICATION_ID or version != _SCHEMA_VERSION or tables != {"pending_inbox", "sqlite_sequence"}:
            raise InboxError("pending_inbox_schema_invalid")
        columns = [row[1] for row in db.execute("PRAGMA table_info(pending_inbox)")]
        if columns != ["id", "scope", "origin", "platform", "self_id", "native_id", "status", "payload", "digest"]:
            raise InboxError("pending_inbox_schema_invalid")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise InboxError("pending_inbox_corrupt")
        # Validate dormant/terminal rows as well, not just rows currently replayable.
        for row in db.execute("SELECT * FROM pending_inbox"):
            PendingInbox._trusted(row)

    @staticmethod
    def _trusted(row):
        """Check routing mirrors and a corruption digest before returning source data."""
        raw = row["payload"]
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            raise InboxError("pending_inbox_corrupt")
        if hashlib.sha256(raw.encode("utf-8")).hexdigest() != row["digest"]:
            raise InboxError("pending_inbox_corrupt")
        try:
            payload = _validate(json.loads(raw))
        except (ValueError, TypeError, KeyError, RecursionError) as exc:
            raise InboxError("pending_inbox_corrupt") from exc
        for key in ("scope", "origin", "platform", "self_id"):
            if row[key] != payload[key]:
                raise InboxError("pending_inbox_corrupt")
        if row["native_id"] != payload["message_record"]["id"] or row["status"] not in _STATUSES:
            raise InboxError("pending_inbox_corrupt")
        return dict(payload, id=row["id"], status=row["status"])

    def enqueue(self, scope, event, text, has_image, woken, history=None):
        """Snapshot a real native source; history is intentionally reloaded by caller.

        ``history`` exists only for compatibility with the five-field memory queue.
        It is not source evidence and can include tools/secrets/roles, so never store
        it. Unsupported inputs return an explicit non-replayable receipt.
        """
        try:
            payload = _snapshot(scope, event, text, has_image, woken)
            encoded = _encode(payload)
        except _Unsupported as exc:
            return {"id": None, "status": "unsupported", "duplicate": False, "reason": str(exc)}
        origin, platform, self_id = payload["origin"], payload["platform"], payload["self_id"]
        record = payload["message_record"]
        with self._transaction() as db:
            row = db.execute("SELECT * FROM pending_inbox WHERE scope=? AND origin=? AND self_id=? AND native_id=?",
                             (scope, origin, self_id, record["id"])).fetchone()
            if row is not None:
                # Native IDs remain tombstoned across every terminal/uncertain state.
                trusted = self._trusted(row)
                return {"id": trusted["id"], "status": trusted["status"], "duplicate": True}
            cursor = db.execute("INSERT INTO pending_inbox(scope,origin,platform,self_id,native_id,status,payload,digest) VALUES(?,?,?,?,?,'pending',?,?)",
                                (scope, origin, platform, self_id, record["id"], encoded,
                                 hashlib.sha256(encoded.encode("utf-8")).hexdigest()))
            return {"id": cursor.lastrowid, "status": "pending", "duplicate": False}

    def pending(self):
        """Return only validated, still-unstarted native input in durable FIFO order."""
        with self._transaction() as db:
            return [self._trusted(row) for row in db.execute("SELECT * FROM pending_inbox WHERE status='pending' ORDER BY id")]

    @staticmethod
    def _ids(ids):
        """Reject ambiguous identifiers rather than partially claiming a batch."""
        if not isinstance(ids, (list, tuple)) or any(type(ident) is not int or ident <= 0 for ident in ids):
            raise ValueError("pending_inbox_invalid_ids")
        if len(set(ids)) != len(ids):
            raise ValueError("pending_inbox_duplicate_ids")
        return ids

    def _transition(self, ids, target, *, discard=False):
        """Perform all-or-nothing transitions, validating every row before mutation."""
        ids = self._ids(ids)
        with self._transaction() as db:
            rows = []
            for ident in ids:
                row = db.execute("SELECT * FROM pending_inbox WHERE id=?", (ident,)).fetchone()
                if row is None:
                    return False
                rows.append(self._trusted(row))
            accepted = ({"pending", "blocked"} if discard else
                        {"pending"} if target == "running" else {"running", target})
            if any(row["status"] not in accepted for row in rows):
                return False
            for row in rows:
                db.execute("UPDATE pending_inbox SET status=? WHERE id=?", (target, row["id"]))
            return True

    def claim(self, ids):
        """Commit an entire pending batch to running before any external side effect."""
        return self._transition(ids, "running")

    def complete(self, ids, status="completed"):
        """Tombstone running work; never revive failed, blocked or uncertain work."""
        if status not in _TERMINAL:
            raise ValueError("pending_inbox_invalid_status")
        return self._transition(ids, status)

    def discard(self, ids, reason="policy_rejected"):
        """Block rejected unstarted input without granting execution or sending it."""
        if reason not in {"policy_rejected", "account_mismatch", "restore_invalid", "input_oversized"}:
            raise ValueError("pending_inbox_invalid_reason")
        return self._transition(ids, "blocked", discard=True)


def restore_event(plugin, metadata, *, platform, self_id):
    """Require the connected account/platform before constructing a native adapter."""
    return RestoredEvent(plugin, metadata, platform=platform, self_id=self_id)


class RestoredEvent(TaskEvent):
    """Restore actual source attribution, never a role or synthetic owner grant.

    Parent admission still decides authorization from current configuration and
    real stored_sender.id. Only TaskEvent's native serializer/send path is reused;
    no WebChat response queue or local transient capability is reconstructed.
    """

    def __init__(self, plugin, metadata, *, platform, self_id):
        try:
            if not isinstance(metadata, dict) or set(metadata) != _PAYLOAD_KEYS | {"id", "status"}:
                raise _Unsupported("invalid_payload")
            if type(metadata["id"]) is not int or metadata["id"] <= 0 or metadata["status"] != "pending":
                raise _Unsupported("input_not_pending")
            # Bound the durable payload, not extra row metadata, so a maximal
            # admitted message remains restorable. Detach all mutable source data.
            payload = _validate(json.loads(_encode({key: metadata[key] for key in _PAYLOAD_KEYS})))
            if payload["platform"] != platform or payload["self_id"] != _id(self_id):
                raise _Unsupported("connected_account_mismatch")
        except (ValueError, TypeError, KeyError) as exc:
            raise InboxError("pending_inbox_restore_invalid") from exc
        super().__init__(plugin, {"scope": payload["scope"], "origin": payload["origin"]})
        self.restored_input = True
        self.inbox_id = metadata["id"]
        self._payload = payload
        self.stored_sender = dict(payload["message_record"]["sender"])
        self.stored_message_record = payload["message_record"]
        self.attachment_sources = payload["attachments"]
        self.message_obj = SimpleNamespace(message_id=self.stored_message_record["id"],
                                          sender=SimpleNamespace(user_id=self.stored_sender["id"],
                                                                 nickname=self.stored_sender["display_name"]),
                                          self_id=payload["self_id"], message=self._segments())

    def _segments(self):
        """Rebuild only canonical source components, never any raw platform payload."""
        record = self.stored_message_record
        result = [{"type": "text", "data": {"text": record["body"]}}]
        result.extend({"type": "at", "data": {"qq": item["id"], "name": item["display_name"]}}
                      for item in record["mentions"])
        if record["quote"] is not None:
            quote = record["quote"]
            result.append({"type": "reply", "data": {"id": quote["id"], "sender": {
                "user_id": quote["sender"]["id"], "nickname": quote["sender"]["display_name"]},
                "body": quote["body"]}})
        for item in self.attachment_sources:
            fields = dict(item["metadata"])
            # Unavailable signed/media capabilities remain evidence, never fetches.
            fields.update({key: ref["location"] for key, ref in item["references"].items()
                           if "location" in ref and "unavailable" not in ref})
            result.append({"type": item["type"], "data": fields})
        return result

    def get_sender_id(self):
        """Expose the actual stored human ID so caller policy can re-evaluate it."""
        return self.stored_sender["id"]

    def get_user_id(self):
        """Expose the same real source ID for callers using the user getter spelling."""
        return self.get_sender_id()

    def get_sender_name(self):
        """Return display text only, without treating names/prose as privileges."""
        return self.stored_sender["display_name"]

    def get_self_id(self):
        """Return the native account explicitly validated against the connected one."""
        return self._payload["self_id"]

    def get_platform_id(self):
        """Keep restored routing bound to the original configured platform ID."""
        return self._payload["platform"]

    def get_platform_name(self):
        """Identify only the supported native QQ adapter, never WebChat/local."""
        return "aiocqhttp"

    def get_messages(self):
        """Expose projected source components for the existing metadata adapters."""
        return self.message_obj.message

    def get_message_str(self):
        """Preserve admitted input text exactly; never promote it to a role."""
        return self._payload["text"]
