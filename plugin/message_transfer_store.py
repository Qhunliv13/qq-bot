"""Private, conversation-scoped persistence for verified native QQ messages.

Only the dispatcher supplies scope and verifies source authorization. This module
never fetches messages, sends network requests, logs content, or shares clipboards
between scopes. Raw media URLs remain private database data, not public receipts.
"""

import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import uuid


def _scope(value):
    """Require an explicit conversation scope without normalizing ownership keys."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("conversation scope required")
    return value


def _json_value(value):
    """Reject lossy JSON coercions while preserving native, finite JSON values."""
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _json_value(item)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _json_value(item)
        return
    # Tuples, non-string dictionary keys, custom objects and non-finite floats
    # would otherwise be silently changed by some JSON encoders.
    raise ValueError("message must contain only finite JSON values")


def _payload(message):
    """Snapshot a verified message without inventing or dropping native metadata."""
    if type(message) is not dict:
        raise ValueError("message object required")
    for key in ("source_group_id", "source_message_id"):
        value = message.get(key)
        if type(value) not in (int, str) or (isinstance(value, str) and not value.strip()):
            raise ValueError("source identifiers required")
    if type(message.get("message")) is not list:
        raise ValueError("native message segments required")
    try:
        # Encode first so cycles fail deterministically before recursive validation.
        encoded = json.dumps(message, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        _json_value(message)
        encoded.encode("utf-8")
    except (TypeError, ValueError, RecursionError, UnicodeError) as error:
        raise ValueError("message must contain only finite JSON values") from error
    return encoded


class TransferStore:
    """Store immutable snapshots behind random IDs with exact-scope ownership.

    Repeating put with the same scope and complete JSON payload returns the first
    receipt, including across process restarts. Changed native metadata constitutes
    a new snapshot. IDs are opaque UUID4 values, not source IDs or content hashes.
    """

    def __init__(self, path):
        self.path = Path(path)

    def _connect(self):
        """Open the dedicated database privately before creating any SQLite journal."""
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        if os.name == "posix":
            # Tighten pre-existing files too; SQLite rollback journals inherit the
            # database permissions and live alongside it, never in shared temp.
            self.path.chmod(0o600)
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("CREATE TABLE IF NOT EXISTS message_clipboards (clip_id TEXT PRIMARY KEY, scope TEXT NOT NULL, fingerprint TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(scope, fingerprint))")
        except Exception:
            connection.close()
            raise
        return connection

    def put(self, scope, message):
        """Idempotently capture an authorized message and return content-free metadata."""
        scope = _scope(scope)
        payload = _payload(message)
        # JSON framing avoids delimiter ambiguity between scope and message text.
        identity = json.dumps([scope, payload], ensure_ascii=False, separators=(",", ":"))
        fingerprint = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        connection = self._connect()
        try:
            with connection:
                # The unique scope/fingerprint constraint resolves concurrent puts
                # atomically; the losing random ID is never exposed or persisted.
                connection.execute("INSERT OR IGNORE INTO message_clipboards (clip_id, scope, fingerprint, payload) VALUES (?, ?, ?, ?)", (uuid.uuid4().hex, scope, fingerprint, payload))
                row = connection.execute("SELECT clip_id, payload FROM message_clipboards WHERE scope=? AND fingerprint=?", (scope, fingerprint)).fetchone()
            snapshot = json.loads(row[1])
            return {"clipboard_id": row[0], "clip_id": row[0], "source_group_id": snapshot["source_group_id"], "source_message_id": snapshot["source_message_id"]}
        finally:
            connection.close()

    def get(self, scope, clipboard_id):
        """Read a detached private snapshot only under its original exact scope."""
        scope = _scope(scope)
        if not isinstance(clipboard_id, str) or not clipboard_id:
            raise ValueError("clipboard ID required")
        connection = self._connect()
        try:
            row = connection.execute("SELECT payload FROM message_clipboards WHERE scope=? AND clip_id=?", (scope, clipboard_id)).fetchone()
            # A foreign ID is indistinguishable from an unknown ID; knowledge of
            # the opaque ID alone never grants access to another conversation.
            return json.loads(row[0]) if row is not None else None
        finally:
            connection.close()
